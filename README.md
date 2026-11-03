# Hatenna Lite

A slimmed-down HatennaBot (Kaushik's Pokétwo helper bot for Discord) for a hackathon demo: see a Pokémon spawn, name it, ping the people hunting it. Built around three pillars:

1. **Always on**: built. Spec: [docs/always-on-spec.md](docs/always-on-spec.md)
2. **Institutional memory**: spec to come
3. **Specialist swarm**: spec to come

## Run the demo

Python 3.9+, standard library only. No Discord token, no network.

```bash
python3 -m hatenna_lite console
```

Open http://127.0.0.1:8765. The console starts the bot as a separate process, so you can stop it, kill -9 it, and watch what happens.

For the budget demo, start with a small budget so it runs out after a few questions:

```bash
HATENNA_LITE_BUDGET=150 python3 -m hatenna_lite console
```

## Five-minute walkthrough

| Step | Click | What the audience sees |
|---|---|---|
| 1. Listener | **Hunt** (kaushik / dratini), then **Spawn** and **Burst ×5** | Each spawn is named; Dratini pings @kaushik |
| 2. Supervisor | **crash** next to *pinger*, then **Spawn** | Pinger goes `down`, Identifier keeps working, Pinger is back in ~0.5 s and the ping still goes out |
| 3. Resume | **Kill -9**, then **Spawn** ×3 | Bot is DOWN; *Waiting in inbox* climbs to 3 |
|  | **Start bot** | Boot #2; the 3 waiting spawns drain; nothing repeated |
|  | **Replay last event** | "already handled — skipped duplicate" |
| 4. Spend guard | **Ask** a few times | Token meter fills; each AI call is logged in the Ledger |
|  | **Ask same thing ×3** | Third call refused (retry loop); breaker open 30 s; bot answers without AI |
|  | Keep asking | Budget runs out; refusals in the Ledger; bot keeps answering from its own data |

## How it's built

| Module | File | Comes from |
|---|---|---|
| Listener, Supervisor, Resume | `hatenna_lite/bot.py` | HatennaBot's reliability work, minus Mongo/Redis |
| Spend guard | `hatenna_lite/spend_guard.py` | Python port of the Commerce Trust Labs [gateway](https://github.com/commerce-trust-labs/autonomous-trust-gateway): tier routing, per-task budgets, Execution Tier Bindings |
| Helpers | `hatenna_lite/helpers.py` | Identifier (`cogs/prediction.py`), Pinger (`cogs/shinyhunt.py`), Analyst (`cogs/ai.py`) in HatennaBot |
| Memory | `hatenna_lite/memory.py` | one SQLite file instead of MongoDB + Redis |
| Demo console | `hatenna_lite/console.py`, `hatenna_lite/static/index.html` | new |

- The bot and the console only share files under `var/`: the console appends to `inbox.jsonl`; the bot writes `status.json`, `feed.jsonl` and `ledger.jsonl`.
- Delivery is at-least-once: an event killed mid-handling runs again on restart, so helper writes are idempotent.
- The Analyst uses a local Ollama model if one is running (`OLLAMA_MODEL`, default `llama3.2:3b`); otherwise a deterministic stub, with token counts marked `estimated`.
- Ledger records are validated against the vendored [Autonomous Trust contract](https://github.com/commerce-trust-labs/autonomous-trust-contracts) schemas (Apache-2.0) when `jsonschema` is installed.

## Tests

```bash
python3 -m unittest discover tests
```

One test per module's "done when" line in the spec.
