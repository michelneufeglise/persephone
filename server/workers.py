"""
Background-worker swarm for Persephone.

The design is deliberately conservative for Apple Silicon:
  * Workers run IN-PROCESS as asyncio tasks (no separate processes).
  * At most ONE worker runs at a time (`_run_lock`) so we never fight the
    active chat model for unified memory.
  * Workers only run when the user is idle (>60s since the last chat request).
    `touch_user_activity()` is called on every /api/chat hit; workers check
    `is_user_idle()` on every scheduler tick.
  * Each worker declares an `interval_seconds` for how often it wants to run;
    the scheduler is a simple 5-second tick loop that fires whichever workers
    are due AND idle AND enabled.
  * State persistence: last_run_ts, last_result, enabled → one JSON file per
    process boot. Cheap, no migrations.

Public entry points:
    async def start()             — launch the scheduler task at app startup
    async def stop()              — cancel it at shutdown
    def touch_user_activity()     — call from every user-triggered request
    def is_user_idle() -> bool    — helpful for /api/status endpoints
    def status() -> dict          — snapshot of all workers' state
    async def enable(id, on) -> None
    async def run_now(id) -> None — trigger immediately (bypass idle gate)
    def logs() -> list[dict]      — recent activity events
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from paths import data_dir

log = logging.getLogger("workers")


# ── Scheduler state ─────────────────────────────────────────────────────────
_IDLE_THRESHOLD_S      = 60.0     # user must be quiet this long before workers wake
_TICK_S                = 5.0      # how often the scheduler checks who's due
_LOG_RING_MAX          = 200      # keep the last N events in memory

_last_user_activity_ts = time.monotonic()
_run_lock              = asyncio.Lock()
_log_ring: deque[dict] = deque(maxlen=_LOG_RING_MAX)
_task: asyncio.Task | None = None


def touch_user_activity() -> None:
    """Call from every user-triggered request. Resets the idle clock."""
    global _last_user_activity_ts
    _last_user_activity_ts = time.monotonic()


def is_user_idle() -> bool:
    return (time.monotonic() - _last_user_activity_ts) > _IDLE_THRESHOLD_S


def _log(worker_id: str, level: str, msg: str, **extra) -> None:
    """Emit a structured event to both the ring buffer and stdlib logger."""
    evt = {
        "ts":      time.time(),
        "worker":  worker_id,
        "level":   level,
        "message": msg,
        **extra,
    }
    _log_ring.append(evt)
    getattr(log, level, log.info)("[%s] %s", worker_id, msg)


def logs(limit: int = 100) -> list[dict]:
    """Return recent events, newest last."""
    items = list(_log_ring)
    return items[-limit:]


# ── Worker registry ─────────────────────────────────────────────────────────
WorkerFn = Callable[[], Awaitable[dict]]


@dataclass
class Worker:
    id:               str
    name:             str
    description:      str
    interval_seconds: float
    fn:               WorkerFn
    enabled:          bool  = True
    # Runtime state (persisted to disk).
    last_run_ts:      float = 0.0
    last_duration_s:  float = 0.0
    last_result:      dict  = field(default_factory=dict)
    last_error:       str   = ""

    def is_due(self) -> bool:
        return (time.time() - self.last_run_ts) >= self.interval_seconds

    def to_dict(self) -> dict:
        return {
            "id":                self.id,
            "name":              self.name,
            "description":       self.description,
            "interval_seconds":  self.interval_seconds,
            "enabled":           self.enabled,
            "last_run_ts":       self.last_run_ts,
            "last_duration_s":   self.last_duration_s,
            "last_result":       self.last_result,
            "last_error":        self.last_error,
            "next_due_ts":       self.last_run_ts + self.interval_seconds,
            "seconds_until_due": max(0.0, (self.last_run_ts + self.interval_seconds) - time.time()),
        }


_workers: dict[str, Worker] = {}


def register(worker: Worker) -> None:
    _workers[worker.id] = worker


def status() -> dict:
    return {
        "user_idle":                 is_user_idle(),
        "seconds_since_last_active": time.monotonic() - _last_user_activity_ts,
        "idle_threshold_seconds":    _IDLE_THRESHOLD_S,
        "workers": [w.to_dict() for w in _workers.values()],
    }


# ── Persistence ─────────────────────────────────────────────────────────────
def _state_path() -> Path:
    p = data_dir() / "workers"
    p.mkdir(parents=True, exist_ok=True)
    return p / "state.json"


def _save_state() -> None:
    """Best-effort JSON dump — swallow OS errors, workers never critical."""
    try:
        payload = {
            wid: {
                "enabled":         w.enabled,
                "last_run_ts":     w.last_run_ts,
                "last_duration_s": w.last_duration_s,
                "last_result":     w.last_result,
                "last_error":      w.last_error,
            }
            for wid, w in _workers.items()
        }
        tmp = _state_path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(_state_path())
    except OSError:
        pass


def _load_state() -> None:
    try:
        raw = _state_path().read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return
    for wid, saved in (data or {}).items():
        w = _workers.get(wid)
        if not w or not isinstance(saved, dict):
            continue
        w.enabled         = bool(saved.get("enabled", w.enabled))
        w.last_run_ts     = float(saved.get("last_run_ts",     0.0))
        w.last_duration_s = float(saved.get("last_duration_s", 0.0))
        w.last_result     = saved.get("last_result") or {}
        w.last_error      = str(saved.get("last_error", ""))


# ── Public API surface ──────────────────────────────────────────────────────
async def enable(worker_id: str, on: bool) -> bool:
    w = _workers.get(worker_id)
    if not w:
        return False
    w.enabled = on
    _save_state()
    _log(worker_id, "info", f"{'enabled' if on else 'disabled'}")
    return True


async def run_now(worker_id: str) -> dict:
    """
    Fire a worker immediately, bypassing both the idle gate and the enabled
    flag. Still respects the single-run lock so it queues behind whatever's
    currently in flight.
    """
    w = _workers.get(worker_id)
    if not w:
        return {"ok": False, "error": f"unknown worker: {worker_id}"}
    async with _run_lock:
        return await _run_worker(w, forced=True)


# ── Runner ──────────────────────────────────────────────────────────────────
async def _run_worker(w: Worker, *, forced: bool = False) -> dict:
    """
    Execute one worker turn. Always saves state after — success or failure —
    so the scheduler doesn't hot-loop on a broken worker.
    """
    t0 = time.monotonic()
    _log(w.id, "info", f"start {'(manual)' if forced else '(scheduled)'}")
    try:
        result = await w.fn()
        w.last_duration_s = time.monotonic() - t0
        w.last_run_ts     = time.time()
        w.last_result     = result or {}
        w.last_error      = ""
        _log(w.id, "info", f"ok in {w.last_duration_s:.1f}s", result=result)
        _save_state()
        return {"ok": True, "duration_s": w.last_duration_s, "result": result}
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        w.last_duration_s = time.monotonic() - t0
        w.last_run_ts     = time.time()
        w.last_error      = f"{type(exc).__name__}: {exc}"
        _log(w.id, "error", w.last_error)
        _save_state()
        return {"ok": False, "duration_s": w.last_duration_s, "error": w.last_error}


async def _scheduler_loop() -> None:
    _log("scheduler", "info", "started")
    try:
        while True:
            await asyncio.sleep(_TICK_S)
            if not is_user_idle():
                continue
            # Pick the next due-and-enabled worker. Sort by "how overdue" so
            # a worker that's been waiting longest gets priority — prevents
            # starvation when two share a schedule.
            due = sorted(
                (w for w in _workers.values() if w.enabled and w.is_due()),
                key=lambda w: -(time.time() - (w.last_run_ts + w.interval_seconds)),
            )
            if not due:
                continue
            worker = due[0]
            async with _run_lock:
                # Re-check idle inside the lock — user may have started typing.
                if not is_user_idle():
                    continue
                await _run_worker(worker)
    except asyncio.CancelledError:
        _log("scheduler", "info", "stopped")
        raise


async def start() -> None:
    """Called from FastAPI lifespan. Idempotent."""
    global _task
    if _task and not _task.done():
        return
    _load_state()
    _task = asyncio.create_task(_scheduler_loop(), name="worker-scheduler")


async def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
    _save_state()
