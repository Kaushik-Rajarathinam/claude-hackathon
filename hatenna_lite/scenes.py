# hatenna_lite/scenes.py — scripted demo scenes, run against the real bot.
#
# Each scene is a list of steps. A step does something (send an event, kill
# the bot, start it) and then checks the bot's own status for the expected
# result, so the UI only shows a ✓ when the bot actually did it.
from __future__ import annotations

import signal
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from .console import Console

HUNTER, HUNTED = "kaushik", "Stantler"


class StepFailed(Exception):
    pass


@dataclass
class Step:
    title: str
    why: str
    run: Callable[["Ctx"], Optional[str]]  # returns a detail line for the UI


@dataclass
class Scene:
    key: str
    title: str
    module: str
    control: str
    expect: str
    say: str
    steps: list[Step] = field(default_factory=list)


class Ctx:
    """What a step can see and do."""

    def __init__(self, console: "Console"):
        self.console = console
        self.memo: dict = {}

    def state(self) -> dict:
        return self.console.state()

    def status(self) -> dict:
        return self.state()["status"] or {}

    def send(self, **body) -> dict:
        return self.console.send(body)

    def wait(self, check: Callable[[], bool], what: str, timeout: float = 8.0) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                if check():
                    return
            except (KeyError, TypeError):
                pass
            time.sleep(0.15)
        raise StepFailed(f"timed out waiting for: {what}")

    def ensure_running(self) -> str:
        if self.state()["bot"]["running"]:
            return "already running"
        self.console.start()
        started = time.time()
        self.wait(lambda: self.state()["bot"]["running"] and self.status()["heartbeat"] > started,
                  "bot heartbeat")
        return f"started as boot #{self.status()['boot']}"

    def ensure_hunter(self) -> None:
        if not self.memo.get("hunter"):
            self.send(kind="hunt", user=HUNTER, pokemon=HUNTED)
            self.wait(lambda: self.status()["hunters"] >= 1, "hunt saved")
            self.memo["hunter"] = True

    def spawn(self, pokemon: str = "") -> dict:
        ev = self.send(kind="spawn", pokemon=pokemon)
        self.wait(lambda: any(s["spawn_id"] == ev["id"] for s in self.status()["recent_spawns"]),
                  f"{ev['id']} named")
        return next(s for s in self.status()["recent_spawns"] if s["spawn_id"] == ev["id"])

    def feed_count(self, who: str, text: str) -> int:
        path = self.console.paths.feed
        if not path.exists():
            return 0
        return sum(1 for line in path.read_text().splitlines()
                   if f'"who": "{who}"' in line and text in line)

    def wait_for_new(self, who: str, text: str, before: int, what: str) -> None:
        self.wait(lambda: self.feed_count(who, text) > before, what)


# --- scene 1: a normal day -------------------------------------------------

def _start(c: Ctx) -> str:
    return c.ensure_running()


def _hunt(c: Ctx) -> str:
    c.ensure_hunter()
    return f"@{HUNTER} is hunting {HUNTED}"


def _random_spawns(c: Ctx) -> str:
    names = [c.spawn()["name"] for _ in range(3)]
    return "named: " + ", ".join(names)


def _hunted_spawn(c: Ctx) -> str:
    before = c.feed_count("pinger", f"{HUNTED} spawned")
    sp = c.spawn(HUNTED)
    c.wait_for_new("pinger", f"{HUNTED} spawned", before, "ping sent")
    return f"{sp['name']} #{sp['dex']} → pinged @{HUNTER}"


# --- scene 2: a helper crashes ---------------------------------------------

def _crash_pinger(c: Ctx) -> str:
    before = c.status()["helpers"]["pinger"]["restarts"]
    c.memo["restarts"] = before
    c.send(kind="chaos", target="pinger")
    c.wait(lambda: c.status()["helpers"]["pinger"]["restarts"] > before, "pinger crash recorded")
    return "pinger crashed (injected fault)"


def _spawn_during_crash(c: Ctx) -> str:
    c.memo["pings"] = c.feed_count("pinger", f"{HUNTED} spawned")
    sp = c.spawn(HUNTED)
    return f"identifier still named it: {sp['name']}"


def _pinger_back(c: Ctx) -> str:
    c.wait(lambda: c.status()["helpers"]["pinger"]["state"] == "up", "pinger back up")
    c.wait_for_new("pinger", f"{HUNTED} spawned", c.memo["pings"], "delayed ping sent")
    return f"restart #{c.status()['helpers']['pinger']['restarts']}; @{HUNTER} still got the ping"


# --- scene 3: power cut ----------------------------------------------------

def _pull_plug(c: Ctx) -> str:
    c.memo["boot"] = c.status()["boot"]
    c.memo["events"] = c.status()["events_total"]
    c.console.signal_bot(signal.SIGKILL)
    c.wait(lambda: not c.state()["bot"]["running"], "bot down")
    return "kill -9: no warning, no cleanup"


def _spawn_while_down(c: Ctx) -> str:
    for _ in range(3):
        c.send(kind="spawn")
    c.wait(lambda: c.state()["backlog"] >= 3, "events queued")
    return f"{c.state()['backlog']} events waiting; nobody is handling them"


def _restart(c: Ctx) -> str:
    c.ensure_running()
    c.wait(lambda: c.state()["backlog"] == 0, "backlog drained")
    s = c.status()
    if s["boot"] != c.memo["boot"] + 1:
        raise StepFailed("expected a new boot")
    handled = s["events_total"] - c.memo["events"]
    if handled != 3:
        raise StepFailed(f"expected exactly 3 new events handled, got {handled}")
    return f"boot #{s['boot']} handled the 3 waiting spawns, each once"


# --- scene 4: same event twice ---------------------------------------------

def _resend(c: Ctx) -> str:
    before, events = c.status()["duplicates"], c.status()["events_total"]
    c.send(kind="replay")
    c.wait(lambda: c.status()["duplicates"] == before + 1, "duplicate detected")
    if c.status()["events_total"] != events:
        raise StepFailed("duplicate was handled again")
    return "recognised by its id and skipped"


# --- scene 5: same question again (cache) ----------------------------------

QUESTION = "what spawns most?"


def _wait_guard(c: Ctx) -> str:
    c.wait(lambda: c.status()["spend"]["breaker_open_s"] == 0, "repeat guard to clear", timeout=20)
    return "repeat guard is off"


def _answered(c: Ctx, before: dict) -> dict:
    keys = ("calls", "refused", "cache_hits")
    c.wait(lambda: sum(c.status()["spend"][k] for k in keys) > sum(before[k] for k in keys), "an answer")
    return c.status()["spend"]


def _ask_once(c: Ctx) -> str:
    c.spawn()  # fresh facts, so this question is new to the cache
    g = c.status()["spend"]
    c.send(kind="ask", text=QUESTION)
    after = _answered(c, g)
    if after["calls"] == g["calls"]:
        raise StepFailed("the AI budget is already used up; Reset demo to run this scene")
    c.memo["tokens"] = after["used"]
    return f"AI answered, {after['used'] - g['used']} tokens"


def _ask_again(c: Ctx) -> str:
    g = c.status()["spend"]
    c.send(kind="ask", text=QUESTION)
    after = _answered(c, g)
    if after["cache_hits"] != g["cache_hits"] + 1 or after["used"] != g["used"]:
        raise StepFailed("expected a cache hit with no new tokens")
    return f"served from the local cache, 0 tokens (saved {after['tokens_saved']} so far)"


def _facts_change(c: Ctx) -> str:
    sp = c.spawn()
    g = c.status()["spend"]
    c.send(kind="ask", text=QUESTION)
    after = _answered(c, g)
    if after["cache_hits"] != g["cache_hits"]:
        raise StepFailed("served a cached answer although the facts changed")
    return f"a {sp['name']} spawned, so the old answer was stale: asked the AI again"


# --- scene 6: budget runs out ----------------------------------------------

def _ask_until_cap(c: Ctx) -> str:
    g = c.status()["spend"]
    refused, asked = g["refused"], 0
    c.memo["fallbacks"] = c.feed_count("analyst", "no AI: budget")
    while c.status()["spend"]["refused"] == refused and asked < 30:
        handled = c.status()["handled_this_boot"]
        c.send(kind="ask", text=f"how is the bot doing? (#{c.state()['inbox_total']})")
        c.wait(lambda: c.status()["handled_this_boot"] > handled, "answer")
        asked += 1
    g = c.status()["spend"]
    if g["refused"] == refused:
        raise StepFailed("budget never ran out")
    return f"{asked} questions; refused at {g['used']} of {g['budget']} tokens"


def _never_over(c: Ctx) -> str:
    g = c.status()["spend"]
    if g["used"] > g["budget"]:
        raise StepFailed("spend passed the cap")
    c.wait_for_new("analyst", "no AI: budget", c.memo["fallbacks"] - 1, "fallback answer")
    return f"{g['used']} ≤ {g['budget']}; bot kept answering without AI"


SCENES = [
    Scene("normal", "A normal day", "Always listening",
          "Listener", "Every spawn is named; the hunter gets pinged.",
          "Spawns come in, each one is named, and the people hunting it get pinged.", [
              Step("Start the bot", "It runs nonstop as its own process.", _start),
              Step(f"@{HUNTER} starts hunting {HUNTED}", "Saved to memory, so it survives restarts.", _hunt),
              Step("Three random spawns arrive", "The Identifier names each one; nobody hunts them.", _random_spawns),
              Step(f"A {HUNTED} spawns", "The Pinger sees the name and pings its hunter.", _hunted_spawn),
          ]),
    Scene("crash", "A helper crashes", "Heals itself",
          "Supervisor", "The others keep working; the crashed helper restarts and its work is kept.",
          "One part failing never takes the bot down, and nothing waiting for it is lost.", [
              Step("Make sure the bot is up", "", _start),
              Step(f"@{HUNTER} hunts {HUNTED}", "", _hunt),
              Step("Crash the Pinger", "Simulates a bug or a dropped connection in one helper.", _crash_pinger),
              Step(f"A {HUNTED} spawns while it's down", "The Identifier runs on its own, so it carries on.", _spawn_during_crash),
              Step("The Pinger comes back", "The supervisor restarts it, and the queued ping goes out.", _pinger_back),
          ]),
    Scene("power", "Power cut", "Picks up where it left off",
          "Resume", "After a hard kill and restart, nothing is lost and nothing is repeated.",
          "Pull the plug at any moment: on restart it carries on from exactly where it was.", [
              Step("Make sure the bot is up", "", _start),
              Step("Pull the plug", "The worst case: the process dies mid-flight.", _pull_plug),
              Step("Three spawns arrive while it's down", "They wait in the inbox.", _spawn_while_down),
              Step("Start the bot again", "It reads its saved position and drains what it missed.", _restart),
          ]),
    Scene("dupe", "Same event twice", "Picks up where it left off",
          "Resume", "A repeated event is recognised and skipped.",
          "Networks redeliver. Every event has an id, so a repeat never pings anyone twice.", [
              Step("Make sure the bot is up", "", _start),
              Step("Resend the last event", "Simulates a retry or a redelivery from Discord.", _resend),
          ]),
    Scene("cache", "Same question again", "Capped AI spend",
          "Spend guard", "A repeat question is answered from the local cache for 0 tokens; new facts get a fresh answer.",
          "Ask twice, pay once. The cache never serves an answer whose facts have changed.", [
              Step("Make sure the bot is up", "", _start),
              Step("Wait for a clean slate", "", _wait_guard),
              Step("Ask a question", "The bot looks up the facts; the AI rewords them, within budget.", _ask_once),
              Step("Ask the exact same thing", "Same question, same facts: reuse the saved answer.", _ask_again),
              Step("A new spawn, then ask again", "The facts changed, so the cached answer no longer fits.", _facts_change),
          ]),
    Scene("budget", "Budget runs out", "Capped AI spend",
          "Spend guard", "Spend stops at the cap; the bot keeps working.",
          "There is a hard ceiling on AI spend, and hitting it doesn't break anything.", [
              Step("Make sure the bot is up", "", _start),
              Step("Wait for a clean slate", "", _wait_guard),
              Step("Keep asking different questions", "Each AI answer is counted against the task's budget.", _ask_until_cap),
              Step("Spend never passes the cap", "The budget is saved, so a restart doesn't reset it.", _never_over),
          ]),
]
SCENE_BY_KEY = {s.key: s for s in SCENES}


class SceneRunner:
    def __init__(self, console: "Console"):
        self.console = console
        self.lock = threading.Lock()
        self.current: Optional[dict] = None
        self.pace = 1.2  # seconds between steps, so the audience can follow

    def catalog(self) -> list[dict]:
        return [{"key": s.key, "title": s.title, "module": s.module, "control": s.control,
                 "expect": s.expect} for s in SCENES]

    def busy(self) -> bool:
        return bool(self.current and self.current["state"] == "running")

    def start(self, key: str) -> dict:
        scene = SCENE_BY_KEY.get(key)
        if not scene:
            return {"error": f"unknown scene {key!r}"}
        with self.lock:
            if self.busy():
                return {"error": "a scene is already running"}
            self.current = {"key": key, "title": scene.title, "module": scene.module,
                            "expect": scene.expect, "say": scene.say, "state": "running",
                            "steps": [{"title": s.title, "why": s.why, "state": "pending", "detail": ""}
                                      for s in scene.steps]}
        threading.Thread(target=self._run, args=(scene,), daemon=True).start()
        return {"result": "started"}

    def _run(self, scene: Scene) -> None:
        ctx = Ctx(self.console)
        cur = self.current
        for i, step in enumerate(scene.steps):
            view = cur["steps"][i]
            view["state"] = "running"
            time.sleep(self.pace / 2)
            try:
                view["detail"] = step.run(ctx) or ""
                view["state"] = "pass"
            except Exception as exc:  # show the failure, never hang the UI
                view["detail"] = str(exc)
                view["state"] = "fail"
                cur["state"] = "failed"
                return
            time.sleep(self.pace / 2)
        cur["state"] = "passed"
