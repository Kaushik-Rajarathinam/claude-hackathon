# hatenna_lite/spend_guard.py — Module 4: Spend guard.
#
# A Python port of the Commerce Trust Labs gateway (autonomous-trust-gateway):
#   - helpers ask for a capability tier, never a vendor; the routing table
#     decides who serves it,
#   - every task has a token budget, and use is persisted so a restart does
#     not hand out a fresh budget,
#   - a breaker stops a helper that keeps asking the same thing (retry loop),
#   - each task+tier gets an Execution Tier Binding written to the ledger.
# When the guard refuses, the caller gets its deterministic draft back, so the
# bot keeps working without AI.
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import urllib.request
from collections import deque
from dataclasses import dataclass
from typing import Optional

from .ledger import CONTRACT_VERSION, Ledger, new_id, utc_now
from .memory import Memory

DEFAULT_BUDGET = int(os.getenv("HATENNA_LITE_BUDGET", "200"))
MAX_OUTPUT_TOKENS = 64
BREAKER_REPEATS = 3        # same prompt this many times...
BREAKER_WINDOW_S = 30.0    # ...within this window opens the breaker
BREAKER_COOLDOWN_S = 15.0
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class StubBackend:
    """Stands in for a local model: returns the helper's own draft. Token
    counts are estimates, and the ledger says so."""
    provider, model = "stub", "deterministic-draft"

    def generate(self, prompt: str, draft: str) -> tuple[str, int]:
        return draft, estimate_tokens(prompt) + estimate_tokens(draft)


class OllamaBackend:
    provider = "ollama"

    def __init__(self) -> None:
        self.model = OLLAMA_MODEL

    @staticmethod
    def available() -> bool:
        try:
            urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=0.5).close()
            return True
        except Exception:
            return False

    def generate(self, prompt: str, draft: str) -> tuple[str, int]:
        body = json.dumps({
            "model": self.model, "stream": False,
            "options": {"num_predict": MAX_OUTPUT_TOKENS},
            "prompt": f"{prompt}\n\nFacts (answer only from these, one short sentence):\n{draft}",
        }).encode()
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            out = json.load(resp)
        tokens = int(out.get("prompt_eval_count", 0)) + int(out.get("eval_count", 0))
        return out.get("response", "").strip() or draft, tokens


@dataclass
class Outcome:
    text: str
    allowed: bool
    reason: str
    tokens: int


class SpendGuard:
    def __init__(self, memory: Memory, ledger: Ledger, budget: int = DEFAULT_BUDGET,
                 use_ollama: Optional[bool] = None):
        self.memory = memory
        self.ledger = ledger
        self.budget = budget
        if use_ollama is None:
            use_ollama = os.getenv("HATENNA_LITE_OLLAMA", "auto") != "0" and OllamaBackend.available()
        # Routing table: capability tier -> whoever currently occupies it.
        self.routes = {"local": OllamaBackend() if use_ollama else StubBackend()}
        self.recent: dict[str, deque] = {}
        self.open_until: dict[str, float] = {}
        self.bound = {(b["taskRef"], b["tier"]) for b in ledger.read("execution-tier-binding")}

    def _bind(self, task: str, tier: str, helper: str) -> None:
        if (task, tier) in self.bound:
            return
        backend = self.routes[tier]
        self.ledger.write("execution-tier-binding", {
            "contractVersion": CONTRACT_VERSION,
            "bindingId": new_id("bind"),
            "taskRef": task,
            "tier": tier,
            "provider": backend.provider,
            "model": backend.model,
            "tokenBudget": {"total": self.budget},
            "escalation": {"allowed": False},
            "decidedAt": utc_now(),
            "decidedBy": {"type": "service", "id": "svc:spend-guard"},
        })
        self.bound.add((task, tier))

    def _refuse(self, task: str, draft: str, reason: str) -> Outcome:
        self.memory.add_spend(task, refused=1)
        self.ledger.write("refusal", {"taskRef": task, "reason": reason, "at": utc_now()})
        return Outcome(draft, False, reason, 0)

    async def call(self, task: str, helper: str, tier: str, prompt: str, draft: str) -> Outcome:
        now = time.monotonic()
        if self.open_until.get(task, 0) > now:
            return self._refuse(task, draft, "breaker open: repeated identical requests")

        key = hashlib.sha1(prompt.encode()).hexdigest()
        recent = self.recent.setdefault(task, deque())
        while recent and now - recent[0][0] > BREAKER_WINDOW_S:
            recent.popleft()
        if sum(1 for _, k in recent if k == key) >= BREAKER_REPEATS - 1:
            self.open_until[task] = now + BREAKER_COOLDOWN_S
            recent.clear()
            return self._refuse(task, draft, "breaker opened: retry loop detected")
        recent.append((now, key))

        used = self.memory.spend(task)["tokens"]
        if used + estimate_tokens(prompt) + MAX_OUTPUT_TOKENS > self.budget:
            return self._refuse(task, draft, f"budget exhausted ({used}/{self.budget} tokens)")

        self._bind(task, tier, helper)
        backend = self.routes[tier]
        try:
            text, tokens = await asyncio.to_thread(backend.generate, prompt, draft)
        except Exception as exc:  # a dead model is a fallback, never a crash
            return self._refuse(task, draft, f"{backend.provider} unavailable: {exc}")
        self.memory.add_spend(task, tokens=tokens, calls=1)
        self.ledger.write("usage", {"taskRef": task, "helper": helper, "tier": tier,
                                    "provider": backend.provider, "tokens": tokens,
                                    "estimated": backend.provider == "stub", "at": utc_now()})
        return Outcome(text, True, "ok", tokens)

    def snapshot(self, task: str) -> dict:
        s = self.memory.spend(task)
        left = self.open_until.get(task, 0) - time.monotonic()
        backend = self.routes["local"]
        return {"task": task, "budget": self.budget, "used": s["tokens"], "calls": s["calls"],
                "refused": s["refused"], "breaker_open_s": round(left) if left > 0 else 0,
                "tier": "local", "provider": backend.provider, "model": backend.model}
