# hatenna_lite/helpers.py — the helpers the supervisor keeps alive.
#
# Each helper owns one job, declares the event kinds it handles, and talks to
# the others only by publishing events. Full-bot equivalents are noted.
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

if TYPE_CHECKING:
    from .memory import Memory
    from .spend_guard import SpendGuard

Say = Callable[[str, str], None]
Publish = Callable[[dict], None]


class Helper:
    name = "helper"
    handles: tuple[str, ...] = ()

    def __init__(self, memory: "Memory", guard: "SpendGuard", say: Say, publish: Publish):
        self.memory = memory
        self.guard = guard
        self._say = say
        self.publish = publish

    def say(self, text: str) -> None:
        self._say(self.name, text)

    def handle(self, event: dict) -> Optional[Awaitable[None]]:
        raise NotImplementedError


class Identifier(Helper):
    """Names a spawn. Full bot: cogs/prediction.py + model.py (TFLite)."""
    name = "identifier"
    handles = ("spawn",)

    def handle(self, event: dict) -> None:
        p = event["payload"]
        # Plug point for the real image model; the demo feed carries its answer.
        name, conf = p["model_guess"], float(p.get("confidence", 0.9))
        self.memory.record_sighting(event["id"], p.get("dex"), p.get("image"), name, conf, time.time())
        dex = f" #{p['dex']}" if p.get("dex") else ""
        self.say(f"{name}{dex} · {conf:.0%} sure")
        self.publish({"kind": "identified", "payload": {"spawn_id": event["id"], "name": name}})


class Pinger(Helper):
    """Pings hunters. Full bot: cogs/shinyhunt.py, typepings.py, questpings.py."""
    name = "pinger"
    handles = ("identified", "hunt")

    def handle(self, event: dict) -> None:
        p = event["payload"]
        if event["kind"] == "hunt":
            self.memory.add_hunter(p["user"], p["pokemon"])
            self.say(f"Got it, {p['user']}. I'll ping you when {p['pokemon']} spawns.")
            return
        users = self.memory.hunters_for(p["name"])
        if users:
            self.say(f"{p['name']} spawned! {' '.join('@' + u for u in users)}")


class Analyst(Helper):
    """Answers questions about the bot. Full bot: cogs/ai.py (local Ollama).
    Every model call goes through the spend guard on the `local` tier."""
    name = "analyst"
    handles = ("ask",)

    @staticmethod
    def task_ref() -> str:
        return "task_analyst-" + time.strftime("%Y%m%d", time.gmtime())

    def _draft(self, question: str) -> str:
        q = question.lower()
        if "common" in q or "top" in q or "most" in q:
            top = ", ".join(f"{n} ×{c}" for n, c in self.memory.top_sightings()) or "nothing yet"
            return f"Most seen so far: {top}."
        if "hunt" in q:
            return f"{self.memory.hunter_count()} hunt sign-ups on record."
        return (f"Up on boot #{self.memory.get_fact('boots', 0)}, "
                f"{self.memory.event_count()} events handled, "
                f"{self.memory.incident_count()} helper crashes recovered.")

    async def handle(self, event: dict) -> None:
        question = event["payload"]["text"]
        draft = self._draft(question)
        out = await self.guard.call(self.task_ref(), self.name, "local",
                                    f"Question from a Discord user: {question}", draft)
        if out.allowed:
            self.say(f"{out.text}  [{out.tokens} tokens]")
        else:
            self.say(f"{out.text}  [no AI: {out.reason}]")


ROSTER = (Identifier, Pinger, Analyst)
