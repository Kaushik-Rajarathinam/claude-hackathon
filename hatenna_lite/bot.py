# hatenna_lite/bot.py — Modules 1–3: Listener, Supervisor, Resume.
#
# Listener   tails the inbox forever, one event at a time; bad input is
#            logged and skipped, never fatal.
# Supervisor runs each helper as its own task with its own queue; a crash is
#            recorded and the helper restarted with backoff while the others
#            carry on. Queued work for the crashed helper is kept.
# Resume     after an event is fully handled, its id and the inbox bookmark
#            are saved together. A restart continues from the bookmark; an id
#            seen before is skipped. A kill mid-event re-runs that one event
#            (at-least-once), which is why the helpers' writes are idempotent.
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

from .helpers import ROSTER, Analyst, Helper
from .ledger import Ledger
from .memory import Memory
from .spend_guard import SpendGuard

HEARTBEAT_S = 1.0
POLL_S = 0.2
CRASH_WINDOW_S = 60.0
MAX_BACKOFF_S = 5.0


class Paths:
    def __init__(self, var: Path):
        self.var = var
        self.inbox = var / "inbox.jsonl"
        self.db = var / "memory.db"
        self.ledger = var / "ledger.jsonl"
        self.feed = var / "feed.jsonl"
        self.status = var / "status.json"


def default_var() -> Path:
    return Path(os.getenv("HATENNA_LITE_VAR", Path.cwd() / "var"))


def append_feed(paths: Paths, who: str, text: str) -> None:
    paths.var.mkdir(parents=True, exist_ok=True)
    with paths.feed.open("a") as f:
        f.write(json.dumps({"ts": time.time(), "who": who, "text": text}) + "\n")


class Bot:
    def __init__(self, paths: Paths, budget: Optional[int] = None, use_ollama: Optional[bool] = None):
        self.paths = paths
        self.memory = Memory(paths.db)
        self.ledger = Ledger(paths.ledger)
        kw = {} if budget is None else {"budget": budget}
        self.guard = SpendGuard(self.memory, self.ledger, use_ollama=use_ollama, **kw)
        self.helpers: dict[str, Helper] = {}
        self.queues: dict[str, asyncio.Queue] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.health: dict[str, dict] = {}
        self.crashes: dict[str, list[float]] = {}
        self.stopping: Optional[asyncio.Event] = None  # made inside the loop (py3.9)
        self.boot = 0
        self.started = time.time()
        self.handled_this_boot = 0

    # --- output ---
    def say(self, who: str, text: str) -> None:
        print(f"{time.strftime('%H:%M:%S')}  {who:<10} {text}", flush=True)
        append_feed(self.paths, who, text)

    # --- the bus ---
    def publish(self, event: dict) -> None:
        event.setdefault("id", f"i-{uuid.uuid4().hex[:8]}")
        event.setdefault("ts", time.time())
        if event["kind"] == "chaos":
            target = event["payload"]["target"]
            if target in self.queues:
                self.queues[target].put_nowait({"kind": "_crash", "payload": {}})
            return
        for name, helper in self.helpers.items():
            if event["kind"] in helper.handles:
                self.queues[name].put_nowait(event)

    # --- Module 2: Supervisor ---
    async def _work(self, name: str) -> None:
        helper, queue = self.helpers[name], self.queues[name]
        self.health[name]["state"] = "up"
        while True:
            event = await queue.get()
            try:
                if event["kind"] == "_crash":
                    raise RuntimeError("injected fault")
                result = helper.handle(event)
                if asyncio.iscoroutine(result):
                    await result
                self.health[name]["handled"] += 1
            finally:
                queue.task_done()

    def _start(self, name: str) -> None:
        task = asyncio.create_task(self._work(name))
        task.add_done_callback(lambda t, n=name: self._on_exit(n, t))
        self.tasks[name] = task

    def _on_exit(self, name: str, task: asyncio.Task) -> None:
        if task.cancelled() or self.stopping.is_set():
            return
        err = task.exception()
        now = time.time()
        recent = [t for t in self.crashes.get(name, []) if now - t < CRASH_WINDOW_S] + [now]
        self.crashes[name] = recent
        h = self.health[name]
        h.update(state="down", restarts=h["restarts"] + 1, last_error=str(err))
        self.memory.record_incident(name, repr(err), now)
        delay = min(MAX_BACKOFF_S, 0.5 * 2 ** (len(recent) - 1))
        self.say("supervisor", f"{name} crashed ({err}); others unaffected, restarting in {delay:.1f}s")

        async def restart() -> None:
            await asyncio.sleep(delay)
            self._start(name)
            self.say("supervisor", f"{name} back up (restart #{h['restarts']}); queued work kept")

        asyncio.get_running_loop().create_task(restart())

    # --- Module 1: Listener (+ Module 3: Resume bookmark) ---
    async def _listen(self, until_idle: Optional[float]) -> None:
        cursor = self.memory.get_fact("cursor", 0)
        idle_since = time.monotonic()
        while not self.stopping.is_set():
            raw = b""
            if self.paths.inbox.exists():
                with self.paths.inbox.open("rb") as f:
                    f.seek(cursor)
                    raw = f.readline()
            if not raw.endswith(b"\n"):  # nothing new, or a half-written line
                if until_idle is not None and time.monotonic() - idle_since > until_idle:
                    return
                await asyncio.sleep(POLL_S)
                continue
            idle_since = time.monotonic()
            next_cursor = cursor + len(raw)
            await self._handle_line(raw.decode("utf-8", "replace").strip())
            self.memory.set_fact("cursor", next_cursor)
            cursor = next_cursor
            self._write_status()

    async def _handle_line(self, line: str) -> None:
        if not line:
            return
        try:
            event = json.loads(line)
            event.setdefault("ts", time.time())
            event_id, kind = event["id"], event["kind"]
        except (ValueError, KeyError, TypeError):
            self.say("listener", f"skipped unreadable input: {line[:60]}")
            return
        if self.memory.has_event(event_id):
            dupes = self.memory.get_fact("duplicates", 0) + 1
            self.memory.set_fact("duplicates", dupes)
            self.say("listener", f"{event_id} already handled — skipped duplicate")
            return
        self.say("listener", describe(event))
        self.publish(event)
        await asyncio.gather(*(q.join() for q in self.queues.values()))
        self.memory.record_event(event)  # marked done only once fully handled
        self.handled_this_boot += 1

    # --- status for the demo console ---
    def _write_status(self) -> None:
        inbox_size = self.paths.inbox.stat().st_size if self.paths.inbox.exists() else 0
        status = {
            "pid": os.getpid(),
            "boot": self.boot,
            "started": self.started,
            "heartbeat": time.time(),
            "cursor": self.memory.get_fact("cursor", 0),
            "inbox_bytes": inbox_size,
            "handled_this_boot": self.handled_this_boot,
            "events_total": self.memory.event_count(),
            "duplicates": self.memory.get_fact("duplicates", 0),
            "incidents": self.memory.incident_count(),
            "hunters": self.memory.hunter_count(),
            "helpers": self.health,
            "spend": self.guard.snapshot(Analyst.task_ref()),
        }
        tmp = self.paths.status.with_suffix(".tmp")
        tmp.write_text(json.dumps(status))
        os.replace(tmp, self.paths.status)

    async def _heartbeat(self) -> None:
        while not self.stopping.is_set():
            self.memory.set_fact("heartbeat", time.time())
            self._write_status()
            await asyncio.sleep(HEARTBEAT_S)

    async def run(self, until_idle: Optional[float] = None) -> None:
        self.stopping = asyncio.Event()
        self.boot = self.memory.get_fact("boots", 0) + 1
        self.memory.set_fact("boots", self.boot)
        for cls in ROSTER:
            helper = cls(self.memory, self.guard, self.say, self.publish)
            self.helpers[helper.name] = helper
            self.queues[helper.name] = asyncio.Queue()
            self.health[helper.name] = {"state": "starting", "restarts": 0, "handled": 0, "last_error": None}
            self._start(helper.name)
        cursor = self.memory.get_fact("cursor", 0)
        self.say("supervisor", f"boot #{self.boot} (pid {os.getpid()}): {self.memory.event_count()} events "
                               f"in memory, resuming at byte {cursor}; helpers: {', '.join(self.helpers)}; "
                               f"AI tier local → {self.guard.routes['local'].provider}")

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stopping.set)
            except (NotImplementedError, RuntimeError):
                pass

        beat = asyncio.create_task(self._heartbeat())
        listen = asyncio.create_task(self._listen(until_idle))
        stop = asyncio.create_task(self.stopping.wait())
        await asyncio.wait({listen, stop}, return_when=asyncio.FIRST_COMPLETED)
        if listen.done() and listen.exception():
            raise listen.exception()
        self.stopping.set()
        for t in (beat, listen, stop, *self.tasks.values()):
            t.cancel()
        for h in self.health.values():
            h["state"] = "stopped"
        self._write_status()
        self.say("supervisor", f"stopped cleanly after {self.handled_this_boot} events; "
                               "next boot resumes from the bookmark")
        self.memory.close()


def describe(event: dict) -> str:
    p, kind = event.get("payload", {}), event["kind"]
    if kind == "spawn":
        return f"{event['id']}: a wild pokémon appeared"
    if kind == "hunt":
        return f"{p.get('user')}: hunt {p.get('pokemon')}"
    if kind == "ask":
        return f"{p.get('user', 'someone')} asks: {p.get('text')}"
    if kind == "chaos":
        return f"(demo) inject a fault into {p.get('target')}"
    return f"{kind} {p}"


def main(until_idle: Optional[float] = None) -> None:
    bot = Bot(Paths(default_var()))
    try:
        asyncio.run(bot.run(until_idle))
    except KeyboardInterrupt:
        sys.exit(0)
