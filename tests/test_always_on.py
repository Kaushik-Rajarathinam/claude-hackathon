"""Pillar 1 checks: one test per module's "done when" line in the spec.

Run: python3 -m unittest discover tests   (install `jsonschema` to also check
ledger records against the CTL contract schemas)
"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from hatenna_lite.bot import Bot, Paths
from hatenna_lite.ledger import Ledger
from hatenna_lite.memory import Memory
from hatenna_lite.spend_guard import SpendGuard


def write_inbox(paths: Paths, *events: dict) -> None:
    paths.var.mkdir(parents=True, exist_ok=True)
    with paths.inbox.open("a") as f:
        for e in events:
            f.write((e if isinstance(e, str) else json.dumps(e)) + "\n")


def run_bot(paths: Paths, budget=None) -> Bot:
    bot = Bot(paths, budget=budget, use_ollama=False)
    asyncio.run(bot.run(until_idle=0.6))
    return bot


def feed(paths: Paths) -> list:
    return [json.loads(l) for l in paths.feed.read_text().splitlines()]


def said(paths: Paths, who: str) -> list:
    return [f["text"] for f in feed(paths) if f["who"] == who]


HUNT = {"id": "h-001", "kind": "hunt", "payload": {"user": "kaushik", "pokemon": "dratini"}}
SPAWN = {"id": "s-002", "kind": "spawn", "payload": {"model_guess": "Dratini", "confidence": 0.97}}


class AlwaysOnTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = Paths(Path(self.tmp.name) / "var")

    def tearDown(self):
        self.tmp.cleanup()

    def status(self) -> dict:
        return json.loads(self.paths.status.read_text())

    def test_listener_handles_every_event_and_survives_bad_input(self):
        write_inbox(self.paths, HUNT, "{not json", SPAWN)
        run_bot(self.paths)
        self.assertEqual(self.status()["events_total"], 2)
        self.assertTrue(any("unreadable" in t for t in said(self.paths, "listener")))
        self.assertIn("Dratini spawned! @kaushik", said(self.paths, "pinger"))

    def test_resume_continues_from_bookmark_without_repeats(self):
        write_inbox(self.paths, HUNT, SPAWN)
        run_bot(self.paths)
        write_inbox(self.paths, {**SPAWN, "id": "s-003"})   # arrives while the bot is down
        write_inbox(self.paths, SPAWN)                       # replay of an old event
        run_bot(self.paths)
        st = self.status()
        self.assertEqual(st["boot"], 2)
        self.assertEqual(st["handled_this_boot"], 1)
        self.assertEqual(st["events_total"], 3)
        self.assertEqual(st["duplicates"], 1)
        self.assertEqual(len(said(self.paths, "pinger")), 3)  # hunt ack + one ping per spawn

    def test_resume_reruns_an_event_interrupted_mid_handling(self):
        write_inbox(self.paths, HUNT)
        mem = Memory(self.paths.db)  # simulate kill -9 before the event was marked done
        mem.close()
        run_bot(self.paths)
        self.assertEqual(self.status()["events_total"], 1)

    def test_supervisor_restarts_a_crashed_helper_and_keeps_its_work(self):
        write_inbox(self.paths, HUNT,
                    {"id": "c-001", "kind": "chaos", "payload": {"target": "pinger"}},
                    SPAWN)
        run_bot(self.paths)
        st = self.status()
        self.assertEqual(st["incidents"], 1)
        self.assertEqual(st["helpers"]["pinger"]["restarts"], 1)
        self.assertEqual(st["helpers"]["identifier"]["restarts"], 0)
        self.assertIn("Dratini spawned! @kaushik", said(self.paths, "pinger"))
        self.assertTrue(any("back up" in t for t in said(self.paths, "supervisor")))


class SpeciesTest(unittest.TestCase):
    def test_every_dex_number_has_a_name_and_image(self):
        from hatenna_lite import species
        self.assertEqual(len(species.table()), species.MAX_DEX)
        self.assertEqual(species.name(234), "Stantler")
        self.assertEqual(species.lookup("#147"), species.lookup("dratini"))
        self.assertIsNone(species.lookup("1026"))
        self.assertEqual(species.image(234), "https://server.poketwo.io/image?species=234")


class SpendGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        var = Path(self.tmp.name)
        self.memory = Memory(var / "m.db")
        self.ledger = Ledger(var / "ledger.jsonl")

    def tearDown(self):
        self.memory.close()
        self.tmp.cleanup()

    def call(self, guard, prompt, task="task_demo-0001", draft="draft answer"):
        return asyncio.run(guard.call(task, "analyst", "local", prompt, draft))

    def test_budget_is_never_exceeded_and_refusal_falls_back(self):
        guard = SpendGuard(self.memory, self.ledger, budget=90, use_ollama=False)
        outcomes = [self.call(guard, f"question number {i}") for i in range(6)]
        self.assertTrue(outcomes[0].allowed)
        self.assertFalse(outcomes[-1].allowed)
        self.assertIn("budget exhausted", outcomes[-1].reason)
        self.assertEqual(outcomes[-1].text, "draft answer")
        self.assertLessEqual(self.memory.spend("task_demo-0001")["tokens"], 90)

    def test_budget_survives_restart(self):
        guard = SpendGuard(self.memory, self.ledger, budget=1000, use_ollama=False)
        self.call(guard, "hello there")
        used = self.memory.spend("task_demo-0001")["tokens"]
        again = SpendGuard(self.memory, self.ledger, budget=1000, use_ollama=False)
        self.assertEqual(again.snapshot("task_demo-0001")["used"], used)

    def test_breaker_opens_on_retry_loop(self):
        # Facts differ each time, so the cache can't absorb the repeats.
        guard = SpendGuard(self.memory, self.ledger, budget=10_000, use_ollama=False)
        results = [self.call(guard, "same question", draft=f"facts v{i}") for i in range(3)]
        self.assertEqual([r.allowed for r in results], [True, True, False])
        self.assertIn("retry loop", results[2].reason)
        self.assertFalse(self.call(guard, "a different question", draft="facts v9").allowed)  # still open

    def test_cache_answers_a_repeat_for_free(self):
        guard = SpendGuard(self.memory, self.ledger, budget=10_000, use_ollama=False)
        first = self.call(guard, "what spawns most?", draft="Stantler ×3")
        used = self.memory.spend("task_demo-0001")["tokens"]
        again = self.call(guard, "what spawns most?", draft="Stantler ×3")
        self.assertEqual((again.reason, again.tokens, again.text), ("cache", 0, first.text))
        self.assertEqual(self.memory.spend("task_demo-0001")["tokens"], used)
        self.assertEqual(guard.snapshot("task_demo-0001")["cache_hits"], 1)

    def test_cache_misses_when_facts_change(self):
        guard = SpendGuard(self.memory, self.ledger, budget=10_000, use_ollama=False)
        self.call(guard, "what spawns most?", draft="Stantler ×3")
        fresh = self.call(guard, "what spawns most?", draft="Stantler ×4")
        self.assertEqual((fresh.reason, fresh.text), ("ok", "Stantler ×4"))

    def test_cache_survives_restart(self):
        guard = SpendGuard(self.memory, self.ledger, budget=10_000, use_ollama=False)
        self.call(guard, "q", draft="facts")
        again = SpendGuard(self.memory, self.ledger, budget=10_000, use_ollama=False)
        self.assertEqual(self.call(again, "q", draft="facts").reason, "cache")

    def test_cache_is_skipped_for_refusals(self):
        guard = SpendGuard(self.memory, self.ledger, budget=10, use_ollama=False)
        self.assertFalse(self.call(guard, "q", draft="facts").allowed)
        self.assertIsNone(self.memory.cached_answer("anything"))
        self.assertEqual(self.memory.cache_stats()["hits"], 0)

    def test_one_tier_binding_per_task_and_tier(self):
        guard = SpendGuard(self.memory, self.ledger, budget=10_000, use_ollama=False)
        self.call(guard, "one")
        self.call(guard, "two")
        bindings = self.ledger.read("execution-tier-binding")
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0]["tier"], "local")  # a capability tier, never a vendor


if __name__ == "__main__":
    unittest.main()
