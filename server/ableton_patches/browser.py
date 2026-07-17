"""
Persephone-authored extension to AbletonOSC — exposes Live's Browser tree so
external tools can auto-load instruments onto tracks.

This file is NOT part of upstream AbletonOSC. It's dropped into
`AbletonOSC/abletonosc/browser.py` by Persephone's install helper and
registered by the sibling __init__.py + manager.py patches.

New OSC endpoints:
    /live/browser/get/instruments
        No args. Reply: flat tuple (name1, uri1, name2, uri2, ...) for every
        direct child of the browser's Instruments category.

    /live/browser/get/drums
        Same shape, for drum kits under the Drums category.

    /live/browser/load_item     <track_index:int> <uri:str>
        Walks the Browser, finds the item with that URI, selects the target
        track, calls browser.load_item(). Reply: ("ok", uri) or
        ("error", "...").

    /live/browser/load_named    <track_index:int> <category:str> <name:str>
        Ergonomic path — no URI juggling required. `category` is one of
        "instruments" | "drums" | "sounds" | "samples". `name` is
        matched case-insensitively against the first N depth levels; the
        first match wins. Great for "load Wavetable" or "load Kit-Core 909".

Threading: Live schedules OSC handler callbacks on its main audio thread,
so all Live API access here is safe.
"""

from typing import Any, Optional, Tuple
import traceback
import Live

from .handler import AbletonOSCHandler


def _safe(fn):
    """
    Wrap an OSC handler so it always replies, even when Live throws (which
    it does often — LimitationError, RuntimeError from the C layer, etc).
    Without this the OSC client sees an empty timeout instead of a real
    error message.
    """
    def wrapped(self, params, *rest):
        try:
            return fn(self, params, *rest)
        except BaseException as exc:
            # Log the FULL traceback into AbletonOSC's log file for postmortem.
            try:
                self.logger.error(
                    "handler %s raised %s: %s\n%s",
                    fn.__name__, type(exc).__name__, exc,
                    traceback.format_exc(),
                )
            except Exception:
                pass
            return ("error", "%s: %s" % (type(exc).__name__, exc))
    wrapped.__name__ = fn.__name__
    return wrapped


class BrowserHandler(AbletonOSCHandler):
    def __init__(self, manager):
        super().__init__(manager)
        self.class_identifier = "browser"

    # ── Lifecycle ────────────────────────────────────────────────────────────
    def init_api(self):
        add = self.osc_server.add_handler
        add("/live/browser/get/instruments", self._get_instruments)
        add("/live/browser/get/drums",       self._get_drums)
        add("/live/browser/load_item",       self._load_item_by_uri)
        add("/live/browser/load_named",      self._load_named)
        add("/live/browser/load_first",      self._load_first_in_category)

    # ── Internal ─────────────────────────────────────────────────────────────
    # NOTE: methods must NOT be named `_song` or `_browser` — AbletonOSC's
    # Component base class already binds those names as instance attributes
    # pointing at the actual Song / Browser objects. Overriding them here as
    # methods gets shadowed at instance-attribute-lookup time, and calling
    # `self._song()` raises `TypeError: 'Song' object is not callable`.
    def _get_browser(self):
        return Live.Application.get_application().browser

    def _get_song(self):
        return Live.Application.get_application().get_document()

    def _select_track(self, track_index: int) -> bool:
        song = self._get_song()
        try:
            track = song.tracks[track_index]
        except IndexError:
            return False
        try:
            song.view.selected_track = track
        except Exception as exc:
            self.logger.warning("select_track %d failed: %s" % (track_index, exc))
            return False
        return True

    def _direct_children(self, root) -> Tuple:
        """Flatten direct children to (name1, uri1, name2, uri2, ...)."""
        flat = []
        try:
            for c in root.children:
                try:
                    flat.append(str(c.name))
                    flat.append(str(c.uri))
                except Exception:
                    continue
        except Exception as exc:
            self.logger.error("direct_children failed: %s" % exc)
        return tuple(flat)

    def _find_by_uri(self, root, uri: str, max_depth: int = 6, depth: int = 0):
        if depth > max_depth or root is None:
            return None
        try:
            children = list(root.children)
        except Exception:
            return None
        for c in children:
            try:
                if str(c.uri) == uri:
                    return c
                if getattr(c, "is_folder", False):
                    r = self._find_by_uri(c, uri, max_depth, depth + 1)
                    if r is not None:
                        return r
            except Exception:
                continue
        return None

    def _walk_loadable(self, root, max_depth: int = 6, depth: int = 0):
        """
        Depth-first generator yielding every loadable BrowserItem under root.
        Deliberately robust: swallows per-item exceptions so a single missing
        preset can't break the traversal.
        """
        if depth > max_depth or root is None:
            return
        try:
            children = list(root.children)
        except Exception:
            return
        for c in children:
            try:
                if getattr(c, "is_loadable", False):
                    yield c
            except Exception:
                pass
            try:
                if getattr(c, "is_folder", False):
                    for nested in self._walk_loadable(c, max_depth, depth + 1):
                        yield nested
            except Exception:
                pass

    def _find_by_name(self, root, name: str, max_depth: int = 6):
        """
        Try to find a browser item whose *loadable descendant* best matches
        `name`. Old versions only looked for exact loadable matches — but in
        Live's browser many top-level names (e.g. 'Wavetable') are FOLDERS
        containing the loadable preset. That's why auto-load was returning 0.

        Resolution order:
          1. Direct-child loadable, exact name match.
          2. Direct-child folder, exact name match → first loadable descendant.
          3. Any-depth loadable, exact name match.
          4. Any-depth loadable, name-contains match.
        Returns None if nothing suitable is found.
        """
        target = name.strip().lower()
        if not target or root is None:
            return None
        try:
            children = list(root.children)
        except Exception:
            return None

        # 1. exact loadable direct child
        for c in children:
            try:
                if str(c.name).lower() == target and getattr(c, "is_loadable", False):
                    return c
            except Exception:
                continue

        # 2. exact folder direct child → first loadable inside
        for c in children:
            try:
                if str(c.name).lower() == target and getattr(c, "is_folder", False):
                    for nested in self._walk_loadable(c, max_depth):
                        return nested
            except Exception:
                continue

        # 3. any-depth loadable exact match
        for it in self._walk_loadable(root, max_depth):
            try:
                if str(it.name).lower() == target:
                    return it
            except Exception:
                continue

        # 4. any-depth loadable contains-match
        for it in self._walk_loadable(root, max_depth):
            try:
                if target in str(it.name).lower():
                    return it
            except Exception:
                continue
        return None

    # ── Handlers (all wrapped by _safe so timeouts surface real errors) ─────
    @_safe
    def _get_instruments(self, params):
        return self._direct_children(self._get_browser().instruments)

    @_safe
    def _get_drums(self, params):
        return self._direct_children(self._get_browser().drums)

    def _do_load(self, item) -> tuple[bool, str]:
        """
        Attempt browser.load_item and return (ok, message). Different Live
        versions have different requirements for a "valid load context":
        some need a hot-swap target set, some load into the selected track
        directly. We try in-place first; if it raises, we log and report.
        """
        try:
            self._get_browser().load_item(item)
            return (True, str(getattr(item, "name", "")))
        except BaseException as exc:
            self.logger.error("browser.load_item(%r) raised: %s\n%s",
                              getattr(item, "name", "?"), exc, traceback.format_exc())
            return (False, "%s: %s" % (type(exc).__name__, exc))

    @_safe
    def _load_item_by_uri(self, params):
        if len(params) < 2:
            return ("error", "expected (track_index, uri)")
        try:
            track_index = int(params[0])
            uri         = str(params[1])
        except (TypeError, ValueError):
            return ("error", "bad params")
        if not self._select_track(track_index):
            return ("error", "invalid track_index")
        browser = self._get_browser()
        # Search across categories most likely to hold instruments / drums.
        roots = [browser.instruments, browser.drums, browser.sounds,
                 browser.samples]
        # max_for_live and plugins may not exist on all Live editions/versions.
        for extra in ("max_for_live", "plugins"):
            r = getattr(browser, extra, None)
            if r is not None:
                roots.append(r)
        for root in roots:
            item = self._find_by_uri(root, uri)
            if item is not None:
                ok, msg = self._do_load(item)
                return ("ok", uri) if ok else ("error", msg)
        return ("error", "uri not found: %s" % uri)

    def _category_root(self, category: str):
        browser = self._get_browser()
        return {
            "instruments": getattr(browser, "instruments", None),
            "drums":       getattr(browser, "drums", None),
            "sounds":      getattr(browser, "sounds", None),
            "samples":     getattr(browser, "samples", None),
        }.get(category)

    @_safe
    def _load_named(self, params):
        if len(params) < 3:
            return ("error", "expected (track_index, category, name)")
        try:
            track_index = int(params[0])
        except (TypeError, ValueError):
            return ("error", "bad track_index")
        category = str(params[1]).lower()
        name     = str(params[2])
        if not self._select_track(track_index):
            return ("error", "invalid track_index")
        root = self._category_root(category)
        if root is None:
            return ("error", "unknown category: %s" % category)
        item = self._find_by_name(root, name)
        if item is None:
            return ("error", "not found: %s (%s)" % (name, category))
        ok, msg = self._do_load(item)
        return ("ok", str(item.name)) if ok else ("error", msg)

    @_safe
    def _load_first_in_category(self, params):
        """
        Last-resort loader: pick the first loadable item in a category and
        drop it on `track_index`. Used by Persephone when every candidate
        name for a role failed to match.
        """
        if len(params) < 2:
            return ("error", "expected (track_index, category)")
        try:
            track_index = int(params[0])
        except (TypeError, ValueError):
            return ("error", "bad track_index")
        category = str(params[1]).lower()
        if not self._select_track(track_index):
            return ("error", "invalid track_index")
        root = self._category_root(category)
        if root is None:
            return ("error", "unknown category: %s" % category)
        first_err: str = "no loadable items under category %s" % category
        for item in self._walk_loadable(root):
            ok, msg = self._do_load(item)
            if ok:
                return ("ok", str(item.name))
            # Keep the first error string in case NOTHING loads — we'd rather
            # surface that "load_item threw LimitationError" than pretend the
            # category is empty.
            if not first_err.startswith("load_item"):
                first_err = msg
        return ("error", first_err)
