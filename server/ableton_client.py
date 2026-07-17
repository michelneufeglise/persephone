"""
Async OSC client for talking to a running AbletonOSC bridge.

Wire model:
    Persephone (this file)  ────UDP────►  AbletonOSC (in Live)
        listens on 11001              listens on 11000
        replies come back here

AbletonOSC uses the fire-and-reply pattern typical of OSC servers — request
and response share the same address (`/live/song/get/tempo`) with the reply
carrying the value(s). We wrap that in a request/reply layer with per-message
correlation via the OSC address itself + a future map, so multiple concurrent
callers don't get their replies crossed.

Public API:
    AbletonClient(send_port=11000, recv_port=11001)
    await client.start()
    await client.ping(timeout=1.0)                                     -> bool
    await client.request(address, args=..., reply=address, timeout=..) -> tuple
    await client.stop()
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

log = logging.getLogger("ableton_client")

DEFAULT_SEND_PORT = 11000   # AbletonOSC listens here (Live-side)
DEFAULT_RECV_PORT = 11001   # AbletonOSC sends replies here (us)
DEFAULT_HOST      = "127.0.0.1"


class AbletonClient:
    def __init__(
        self,
        host:      str = DEFAULT_HOST,
        send_port: int = DEFAULT_SEND_PORT,
        recv_port: int = DEFAULT_RECV_PORT,
    ) -> None:
        self.host      = host
        self.send_port = send_port
        self.recv_port = recv_port
        self._client:   SimpleUDPClient | None = None
        self._server:   AsyncIOOSCUDPServer | None = None
        self._transport: asyncio.DatagramTransport | None = None
        # address → deque[asyncio.Future] — one waiter per pending call to
        # that address. AbletonOSC replies FIFO so this stays correct even
        # with multiple concurrent calls on the same address.
        self._waiters: dict[str, list[asyncio.Future]] = {}

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def start(self) -> None:
        if self._client is not None:
            return
        self._client = SimpleUDPClient(self.host, self.send_port)

        dispatcher = Dispatcher()
        dispatcher.set_default_handler(self._on_message)

        loop = asyncio.get_running_loop()
        self._server = AsyncIOOSCUDPServer((self.host, self.recv_port), dispatcher, loop)
        self._transport, _ = await self._server.create_serve_endpoint()
        log.info(
            "Ableton OSC client ready — sending :%d, listening :%d",
            self.send_port, self.recv_port,
        )

    async def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        self._server = None
        self._client = None
        # Fail any still-pending waiters so callers unblock.
        for lst in self._waiters.values():
            for f in lst:
                if not f.done():
                    f.set_exception(RuntimeError("AbletonClient stopped"))
        self._waiters.clear()

    # ── send/receive primitives ──────────────────────────────────────────────
    def _send(self, address: str, args: Any = None) -> None:
        if self._client is None:
            raise RuntimeError("AbletonClient not started")
        if args is None:
            self._client.send_message(address, [])
        elif isinstance(args, (list, tuple)):
            self._client.send_message(address, list(args))
        else:
            self._client.send_message(address, [args])

    def _on_message(self, address: str, *osc_args: Any) -> None:
        """Any inbound OSC message → complete the oldest waiter on that address."""
        lst = self._waiters.get(address)
        if not lst:
            log.debug("[unmatched osc reply] %s %s", address, osc_args)
            return
        fut = lst.pop(0)
        if not lst:
            self._waiters.pop(address, None)
        if not fut.done():
            fut.set_result(osc_args)

    async def request(
        self,
        address:   str,
        args:      Any = None,
        reply:     str | None = None,
        timeout:   float = 2.0,
    ) -> tuple:
        """
        Send an OSC message and await the reply.

        `reply` defaults to `address` — that's AbletonOSC's convention: the
        reply comes back on the same address as the query. Pass a different
        string for cases where the server replies on a companion path (e.g.
        `/live/song/get/tempo` returns on `/live/song/get/tempo`).
        """
        if self._client is None:
            raise RuntimeError("AbletonClient not started")
        reply_addr = reply or address
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._waiters.setdefault(reply_addr, []).append(fut)
        try:
            self._send(address, args)
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            # Clean up the abandoned waiter.
            lst = self._waiters.get(reply_addr)
            if lst and fut in lst:
                lst.remove(fut)
                if not lst:
                    self._waiters.pop(reply_addr, None)
            raise

    # ── the one call we actually need for Phase 1 ────────────────────────────
    async def ping(self, timeout: float = 1.0) -> bool:
        """
        Ask the bridge to echo /live/test. Returns True if AbletonOSC is
        running, listening, and responsive. Any error → False.
        """
        try:
            await self.request("/live/test", reply="/live/test", timeout=timeout)
            return True
        except (asyncio.TimeoutError, OSError, RuntimeError):
            return False

    async def application_version(self, timeout: float = 1.0) -> tuple[int, int, int] | None:
        """Best-effort read of the live-side Live application version."""
        try:
            args = await self.request(
                "/live/application/get/version",
                reply="/live/application/get/version",
                timeout=timeout,
            )
        except Exception:
            return None
        if len(args) >= 3:
            try:
                return int(args[0]), int(args[1]), int(args[2])
            except (TypeError, ValueError):
                return None
        return None

    # ── Composer OSC operations ──────────────────────────────────────────────
    # Fire-and-forget (no reply expected). Small pause between sends keeps the
    # AbletonOSC queue happy on very long batches (creating N tracks + N*M
    # clips + K*M*P notes). Callers can override with a per-call cadence.

    async def set_tempo(self, bpm: float) -> None:
        self._send("/live/song/set/tempo", [float(bpm)])

    async def set_time_signature(self, num: int, den: int) -> None:
        self._send("/live/song/set/signature_numerator",   [int(num)])
        self._send("/live/song/set/signature_denominator", [int(den)])

    async def get_num_tracks(self, timeout: float = 1.0) -> int:
        try:
            args = await self.request(
                "/live/song/get/num_tracks",
                reply="/live/song/get/num_tracks",
                timeout=timeout,
            )
            return int(args[0]) if args else 0
        except Exception:
            return 0

    async def delete_all_tracks(self, timeout: float = 3.0) -> int:
        """Delete every track (indexed from the right so indices stay stable).
        Returns the number of tracks deleted. Return-tracks + master are left.
        """
        n = await self.get_num_tracks()
        for i in range(n - 1, -1, -1):
            self._send("/live/song/delete_track", [int(i)])
            await asyncio.sleep(0.02)
        return n

    async def create_midi_track(self, index: int = -1) -> None:
        """Create a MIDI track at `index` (-1 = at the end)."""
        self._send("/live/song/create_midi_track", [int(index)])

    async def set_track_name(self, track_idx: int, name: str) -> None:
        self._send("/live/track/set/name", [int(track_idx), str(name)])

    async def set_track_volume_db(self, track_idx: int, volume_db: float) -> None:
        """Live's volume slider is normalised 0-1. Convert dB → normalised."""
        # 0 dB ≈ 0.85 on Live's mixer, -6 dB ≈ 0.71, -12 dB ≈ 0.60, -inf = 0.
        # This is an approximation of Live's actual curve; good enough for a
        # first mix and easy to iterate on when we ship real Ableton browser
        # integration in Phase 5.
        normalised = max(0.0, min(1.0, 0.85 + volume_db / 40.0))
        self._send("/live/track/set/volume", [int(track_idx), float(normalised)])

    async def set_track_pan(self, track_idx: int, pan: float) -> None:
        """pan ∈ [-1, +1]. Live's panning parameter is on the same range."""
        pan = max(-1.0, min(1.0, float(pan)))
        self._send("/live/track/set/panning", [int(track_idx), pan])

    async def create_clip(
        self, track_idx: int, clip_slot: int, length_beats: float,
    ) -> None:
        """Create an empty MIDI clip in the given slot with `length_beats` length."""
        self._send(
            "/live/clip_slot/create_clip",
            [int(track_idx), int(clip_slot), float(length_beats)],
        )

    async def add_notes(
        self, track_idx: int, clip_slot: int,
        notes: list[tuple[int, float, float, int]],
    ) -> None:
        """
        Batch-add notes to a clip.

        `notes` is a list of (pitch, start_beats, length_beats, velocity).
        AbletonOSC accepts flat tuples of (pitch, start, length, velocity,
        mute_flag) per note — we default mute_flag to 0 (unmuted).
        """
        if not notes:
            return
        flat: list[float] = []
        for pitch, start, length, vel in notes:
            flat.extend([int(pitch), float(start), float(length), int(vel), 0])
        self._send(
            "/live/clip/add/notes",
            [int(track_idx), int(clip_slot), *flat],
        )
        # Tiny pause on the sender side lets AbletonOSC digest large batches.
        await asyncio.sleep(0.03)

    async def stop_all(self) -> None:
        self._send("/live/song/stop_playing", [])

    async def start_all(self) -> None:
        self._send("/live/song/start_playing", [])

    async def fire_scene(self, scene_index: int = 0) -> None:
        """
        Trigger a Session-view scene. Fires every clip on that row.
        Live's transport starts automatically; use stop_all() to halt.
        """
        self._send("/live/scene/fire", [int(scene_index)])

    async def stop_all_clips(self) -> None:
        self._send("/live/song/stop_all_clips", [])

    # ── Per-track/clip control (track-first composer workflow) ──────────────
    async def fire_clip_slot(self, track_idx: int, slot_idx: int) -> None:
        """
        Trigger a single clip slot in Session view. Unlike fire_scene which
        launches every clip on a row, this fires just one — used for the
        per-track ▶ preview in Persephone's composer.
        """
        self._send("/live/clip_slot/fire", [int(track_idx), int(slot_idx)])

    async def stop_clip(self, track_idx: int, slot_idx: int) -> None:
        """Stop a specific clip. Complement to fire_clip_slot."""
        self._send("/live/clip/stop", [int(track_idx), int(slot_idx)])

    async def set_track_solo(self, track_idx: int, solo: bool) -> None:
        """Solo/un-solo a track. Used for solo-preview mode."""
        self._send("/live/track/set/solo", [int(track_idx), 1 if solo else 0])

    async def set_track_mute(self, track_idx: int, mute: bool) -> None:
        """Mute/un-mute a track."""
        self._send("/live/track/set/mute", [int(track_idx), 1 if mute else 0])

    async def set_clip_trigger_quantization(self, level: int) -> None:
        """
        Ableton's global clip-launch quantise. 0 = None (fires immediately),
        4 = 1 Bar, etc. We set this to 0 before per-track preview so ▶ is
        instant — otherwise Live waits up to a bar before starting the clip.
        """
        self._send("/live/song/set/clip_trigger_quantization", [int(level)])

    async def clear_all_solos(self, timeout: float = 1.5) -> None:
        """
        Un-solo every track. Called when Stop is hit so the user isn't left
        with tracks silently soloed after a preview. We fetch the track count
        first so we don't rely on knowing it up front.
        """
        try:
            n = await self.get_num_tracks(timeout=timeout)
        except Exception:
            return
        for i in range(n):
            self._send("/live/track/set/solo", [i, 0])
            await asyncio.sleep(0.005)

    async def delete_track(self, track_idx: int) -> None:
        """Remove a single track from the Live set (for per-track UI deletes)."""
        self._send("/live/song/delete_track", [int(track_idx)])

    async def delete_clip(self, track_idx: int, slot_idx: int) -> None:
        """Delete just one clip slot's contents (used when re-applying a track)."""
        self._send("/live/clip_slot/delete_clip", [int(track_idx), int(slot_idx)])

    # ── Browser (Persephone-patched AbletonOSC — Phase 3.5) ──────────────────
    async def get_instruments(self, timeout: float = 2.0) -> list[tuple[str, str]]:
        """
        Return every direct child of Live's Browser → Instruments as
        [(name, uri), ...]. Empty list if the browser patch isn't installed
        or Live isn't responding.
        """
        try:
            args = await self.request(
                "/live/browser/get/instruments",
                reply="/live/browser/get/instruments",
                timeout=timeout,
            )
        except Exception:
            return []
        return [(str(args[i]), str(args[i + 1])) for i in range(0, len(args) - 1, 2)]

    async def get_drums(self, timeout: float = 2.0) -> list[tuple[str, str]]:
        try:
            args = await self.request(
                "/live/browser/get/drums",
                reply="/live/browser/get/drums",
                timeout=timeout,
            )
        except Exception:
            return []
        return [(str(args[i]), str(args[i + 1])) for i in range(0, len(args) - 1, 2)]

    async def load_instrument_named(
        self,
        track_index: int,
        category:    str,   # "instruments" | "drums" | "sounds" | "samples"
        name:        str,
        timeout:     float = 3.0,
    ) -> tuple[bool, str]:
        """
        Load a browser item by human-readable name onto a specific track.
        Returns (success, message). `message` is either the name of what got
        loaded (on success) or the error string from Live (on failure).
        """
        try:
            args = await self.request(
                "/live/browser/load_named",
                args=[int(track_index), str(category), str(name)],
                reply="/live/browser/load_named",
                timeout=timeout,
            )
        except Exception as exc:
            return False, f"call failed: {exc}"
        if len(args) < 2:
            return False, "empty reply"
        status = str(args[0])
        detail = str(args[1])
        return (status == "ok"), detail

    async def load_first_in_category(
        self,
        track_index: int,
        category:    str,
        timeout:     float = 3.0,
    ) -> tuple[bool, str]:
        """
        Last-resort loader — asks the (Persephone-patched) AbletonOSC to
        load the *first* loadable browser item in the given category. Useful
        when specific instrument names for the current Live version aren't
        matched by the role-defaults ladder.
        """
        try:
            args = await self.request(
                "/live/browser/load_first",
                args=[int(track_index), str(category)],
                reply="/live/browser/load_first",
                timeout=timeout,
            )
        except Exception as exc:
            return False, f"call failed: {exc}"
        if len(args) < 2:
            return False, "empty reply"
        return (str(args[0]) == "ok"), str(args[1])

    async def has_browser_patch(self, timeout: float = 1.0) -> bool:
        """
        Quick check for whether AbletonOSC has our browser patch loaded.
        We send a get/instruments and see if we get *anything* back.
        """
        items = await self.get_instruments(timeout=timeout)
        return bool(items)


# ── Module-level singleton so FastAPI handlers share one UDP socket ───────────
_singleton: AbletonClient | None = None
_singleton_lock = asyncio.Lock()


async def get() -> AbletonClient:
    """Return a started AbletonClient, sharing one across the whole app."""
    global _singleton
    async with _singleton_lock:
        if _singleton is None:
            _singleton = AbletonClient()
            await _singleton.start()
        return _singleton


async def close() -> None:
    global _singleton
    async with _singleton_lock:
        if _singleton is not None:
            await _singleton.stop()
            _singleton = None
