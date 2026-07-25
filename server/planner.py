"""
Persephone Task Planner — user-authored scheduled prompts.

Runs alongside the background-worker swarm (`server/workers.py`). Both share
the same `_run_lock` from workers so a scheduled task can't fight the active
chat model (or a Memory-Curator run) for unified memory.

Task shape (persisted in `planned_tasks`):
    id, name, prompt, model,
    schedule_kind ∈ {once, daily, weekly, every_n_min, cron},
    schedule_value  (see _parse_schedule),
    tool_ids       (JSON list of MCP tool names allowed for this task),
    skill_names    (JSON list of skill names to inject),
    enabled, next_run_ts, last_run_ts, last_status, last_conv_id

Missed-run policy: if the app was closed when a run was due, we DO NOT
back-fire. On startup + on every tick we simply recompute next_run_ts to
the next future occurrence and move on.

Each successful run creates a fresh chat conversation titled
`{task.name} · {timestamp}` containing:
  1. the task prompt as the user message
  2. the model's reply as the assistant message
so users can re-open the run in the normal chat surface.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from croniter import croniter

log = logging.getLogger("planner")

# ── Scheduler state ─────────────────────────────────────────────────────────
_TICK_S     = 5.0
_task: asyncio.Task | None = None

# Hooks injected by main.py so we don't create an import cycle.
# `db` and `workers` come from main's module scope; `run_chat_turn` is main's
# non-streaming reusable chat helper.
_db = None
_workers = None
_run_chat_turn = None


def install_hooks(*, db, workers, run_chat_turn) -> None:
    """Called once from main.py before start(). Wire in shared dependencies."""
    global _db, _workers, _run_chat_turn
    _db = db
    _workers = workers
    _run_chat_turn = run_chat_turn


# ── Schedule parsing / next-run math ────────────────────────────────────────
_WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


@dataclass
class _Schedule:
    kind:  str            # 'once' | 'daily' | 'weekly' | 'every_n_min' | 'cron'
    once_dt:    datetime | None = None
    time_of_day: tuple[int, int] | None = None   # (hh, mm)
    weekday:     int | None = None               # 0-6, Mon=0
    interval_min: int | None = None
    cron_expr:    str = ""


def validate_schedule(kind: str, value: str) -> None:
    """Raise ValueError if (kind, value) is not a valid schedule spec."""
    _parse_schedule(kind, value)


def _parse_schedule(kind: str, value: str) -> _Schedule:
    """Turn the (kind, value) stored in the row into a normalised _Schedule."""
    value = (value or "").strip()
    if kind == "once":
        # ISO 8601 datetime, either naive-local or with tz. We treat naive
        # strings as local time — matches the datetime-local HTML input.
        dt = datetime.fromisoformat(value)
        return _Schedule(kind="once", once_dt=dt)

    if kind == "daily":
        hh, mm = _parse_hhmm(value)
        return _Schedule(kind="daily", time_of_day=(hh, mm))

    if kind == "weekly":
        # 'MON 09:30' or 'monday 09:30'
        parts = value.split()
        if len(parts) < 2:
            raise ValueError(f"weekly schedule needs '<DAY> HH:MM', got {value!r}")
        day_raw, tod = parts[0].strip().upper()[:3], parts[1]
        if day_raw not in _WEEKDAYS:
            raise ValueError(f"weekly schedule day {day_raw!r} not one of {_WEEKDAYS}")
        hh, mm = _parse_hhmm(tod)
        return _Schedule(kind="weekly", weekday=_WEEKDAYS.index(day_raw),
                         time_of_day=(hh, mm))

    if kind == "every_n_min":
        n = int(value)
        if n < 1:
            raise ValueError(f"every_n_min needs a positive integer, got {value!r}")
        return _Schedule(kind="every_n_min", interval_min=n)

    if kind == "cron":
        # Validate up-front so a bad expression fails at save-time not run-time.
        croniter(value)  # raises on invalid
        return _Schedule(kind="cron", cron_expr=value)

    raise ValueError(f"unknown schedule kind: {kind}")


def _parse_hhmm(s: str) -> tuple[int, int]:
    hh, mm = s.split(":")
    hh, mm = int(hh), int(mm)
    if not (0 <= hh < 24 and 0 <= mm < 60):
        raise ValueError(f"invalid time {s!r}")
    return hh, mm


def compute_next_run(kind: str, value: str, from_ts: float) -> float | None:
    """
    Given the schedule spec and a starting timestamp, return the unix ts of
    the next firing (strictly > from_ts), or None if the schedule has no
    future firings (spent one-shot).
    """
    try:
        sched = _parse_schedule(kind, value)
    except Exception as exc:
        log.warning("cannot parse schedule (%s, %r): %s", kind, value, exc)
        return None

    now = datetime.fromtimestamp(from_ts)

    if sched.kind == "once":
        return sched.once_dt.timestamp() if sched.once_dt and sched.once_dt.timestamp() > from_ts else None

    if sched.kind == "daily":
        hh, mm = sched.time_of_day
        candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if candidate.timestamp() <= from_ts:
            candidate += timedelta(days=1)
        return candidate.timestamp()

    if sched.kind == "weekly":
        hh, mm = sched.time_of_day
        target_wd = sched.weekday
        candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        days_ahead = (target_wd - candidate.weekday()) % 7
        if days_ahead == 0 and candidate.timestamp() <= from_ts:
            days_ahead = 7
        candidate += timedelta(days=days_ahead)
        return candidate.timestamp()

    if sched.kind == "every_n_min":
        return from_ts + sched.interval_min * 60

    if sched.kind == "cron":
        return croniter(sched.cron_expr, now).get_next(datetime).timestamp()

    return None


def describe_schedule(kind: str, value: str) -> str:
    """Human-readable one-line summary for the UI. Never raises."""
    try:
        sched = _parse_schedule(kind, value)
    except Exception:
        return f"{kind} {value}"
    if sched.kind == "once":
        return f"Once at {sched.once_dt.strftime('%Y-%m-%d %H:%M')}"
    if sched.kind == "daily":
        hh, mm = sched.time_of_day
        return f"Daily at {hh:02d}:{mm:02d}"
    if sched.kind == "weekly":
        hh, mm = sched.time_of_day
        return f"Weekly on {_WEEKDAYS[sched.weekday]} at {hh:02d}:{mm:02d}"
    if sched.kind == "every_n_min":
        n = sched.interval_min
        if n < 60:
            return f"Every {n} minute{'s' if n != 1 else ''}"
        h, m = divmod(n, 60)
        return f"Every {h}h {m}m" if m else f"Every {h}h"
    if sched.kind == "cron":
        return f"Cron: {sched.cron_expr}"
    return f"{kind} {value}"


# ── Task execution ──────────────────────────────────────────────────────────
async def run_task_now(task: dict) -> dict:
    """
    Execute one task synchronously, persisting a run record and creating a
    conversation with the output. Callable from the scheduler loop OR from
    the 'Run now' endpoint.

    Returns {'ok': bool, 'conv_id': str, 'run_id': str, 'error': str | None}.
    """
    assert _db is not None and _run_chat_turn is not None, "planner hooks not installed"

    task_id  = task["id"]
    run_id   = f"prun-{uuid.uuid4().hex[:14]}"
    started  = time.time()

    # 1. Create the destination conversation with the user prompt already in it.
    conv_id  = f"pconv-{uuid.uuid4().hex[:14]}"
    title    = f"{task.get('name') or 'Scheduled task'} · {datetime.fromtimestamp(started).strftime('%Y-%m-%d %H:%M')}"
    await _db.upsert_conversation({
        "id": conv_id,
        "title": title,
        "model": task.get("model", ""),
        "createdAt": started * 1000,
        "updatedAt": started * 1000,
    })
    await _db.upsert_message(conv_id, {
        "id":        f"pmsg-user-{uuid.uuid4().hex[:12]}",
        "role":      "user",
        "content":   task.get("prompt", ""),
        "model":     "",
        "timestamp": started * 1000,
        "meta":      {"planner_task_id": task_id, "planner_run_id": run_id},
    })

    await _db.record_task_run_start({
        "id":         run_id,
        "task_id":    task_id,
        "started_at": started,
        "status":     "running",
        "conv_id":    conv_id,
    })

    # 2. Run the chat turn under the shared workers lock so we don't fight the
    # active chat model. Idle-gate does NOT apply — planned tasks fire on time
    # whether the user is idle or actively chatting (the lock is enough).
    result: dict = {"content": "", "error": "runner unavailable"}
    lock = getattr(_workers, "_run_lock", None) if _workers else None
    try:
        if lock is not None:
            async with lock:
                result = await _run_chat_turn(
                    task["model"],
                    [{"role": "user", "content": task["prompt"]}],
                    allowed_tool_names = task.get("toolIds")   or None,
                    forced_skill_names = task.get("skillNames") or None,
                    options            = {"num_predict": 4096},
                    timeout_s          = 600.0,
                )
        else:
            result = await _run_chat_turn(
                task["model"],
                [{"role": "user", "content": task["prompt"]}],
                allowed_tool_names = task.get("toolIds")   or None,
                forced_skill_names = task.get("skillNames") or None,
                options            = {"num_predict": 4096},
                timeout_s          = 600.0,
            )
    except Exception as exc:
        log.exception("task %s run failed", task_id)
        result = {"content": "", "error": f"unexpected: {exc}"}

    finished = time.time()
    text     = (result.get("content") or "").strip()
    err      = result.get("error")

    # 3. Persist the assistant reply into the conversation.
    if text:
        await _db.upsert_message(conv_id, {
            "id":        f"pmsg-asst-{uuid.uuid4().hex[:12]}",
            "role":      "assistant",
            "content":   text,
            "model":     task.get("model", ""),
            "timestamp": finished * 1000,
            "meta":      {"planner_task_id": task_id, "planner_run_id": run_id},
        })
    elif err:
        # Post the error as an assistant message too so the user can see what
        # happened when they open the conv.
        await _db.upsert_message(conv_id, {
            "id":        f"pmsg-asst-{uuid.uuid4().hex[:12]}",
            "role":      "assistant",
            "content":   f"⚠ Task failed: {err}",
            "model":     task.get("model", ""),
            "timestamp": finished * 1000,
            "meta":      {"planner_task_id": task_id, "planner_run_id": run_id, "error": True},
        })

    status = "failed" if err or not text else "succeeded"
    await _db.record_task_run_finish(
        run_id,
        status         = status,
        conv_id        = conv_id,
        output_preview = text[:500],
        error          = err or "",
    )

    # 4. Recompute next_run_ts for recurring schedules; once-schedules go to
    # None so they never fire again.
    next_ts: float | None = None
    if task["scheduleKind"] != "once":
        next_ts = compute_next_run(task["scheduleKind"], task["scheduleValue"], finished)

    await _db.update_planned_task_run_bookkeeping(
        task_id,
        next_run_ts  = next_ts,
        last_run_ts  = finished,
        last_status  = status,
        last_conv_id = conv_id,
    )

    return {"ok": status == "succeeded", "conv_id": conv_id, "run_id": run_id, "error": err}


# ── Scheduler loop ──────────────────────────────────────────────────────────
async def _tick() -> None:
    """One scheduler pass. Runs due tasks in serial (via shared lock)."""
    if _db is None:
        return
    now = time.time()
    due = await _db.list_due_planned_tasks(now)
    for task in due:
        # Missed-run policy: if we're > 2× the tick interval behind and this
        # is a recurring task, silently roll forward without firing. Prevents
        # a startup burst when the app was closed. `once` tasks always fire
        # (their whole purpose is to catch up to their target moment).
        overdue_s = now - (task["nextRunTs"] or now)
        if task["scheduleKind"] != "once" and overdue_s > _TICK_S * 2:
            next_ts = compute_next_run(task["scheduleKind"], task["scheduleValue"], now)
            await _db.update_planned_task_run_bookkeeping(
                task["id"],
                next_run_ts  = next_ts,
                last_run_ts  = task["lastRunTs"] or 0.0,
                last_status  = task["lastStatus"] or "",
                last_conv_id = task["lastConvId"] or "",
            )
            log.info("planner: skipped stale run for %s (overdue %.1fs), next=%s",
                     task["id"], overdue_s, next_ts)
            continue

        log.info("planner: firing task %s (%r)", task["id"], task.get("name"))
        try:
            await run_task_now(task)
        except Exception:
            log.exception("planner: run_task_now crashed for %s", task["id"])


async def _scheduler_loop() -> None:
    log.info("planner scheduler started (tick=%ss)", _TICK_S)
    try:
        while True:
            try:
                await _tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("planner: tick crashed (continuing)")
            await asyncio.sleep(_TICK_S)
    except asyncio.CancelledError:
        log.info("planner scheduler cancelled")
        raise


async def start() -> None:
    global _task
    if _task is not None:
        return
    # On startup, roll forward any recurring tasks whose next_run_ts is in the
    # past (missed while the app was closed) so they don't back-fire.
    if _db is not None:
        now = time.time()
        for t in await _db.list_planned_tasks():
            if (
                t["enabled"]
                and t["scheduleKind"] != "once"
                and t["nextRunTs"] is not None
                and t["nextRunTs"] < now - _TICK_S * 2
            ):
                next_ts = compute_next_run(t["scheduleKind"], t["scheduleValue"], now)
                await _db.update_planned_task_run_bookkeeping(
                    t["id"],
                    next_run_ts  = next_ts,
                    last_run_ts  = t["lastRunTs"] or 0.0,
                    last_status  = t["lastStatus"] or "",
                    last_conv_id = t["lastConvId"] or "",
                )
                log.info("planner startup: rolled forward %s, next=%s", t["id"], next_ts)
    _task = asyncio.create_task(_scheduler_loop())


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
