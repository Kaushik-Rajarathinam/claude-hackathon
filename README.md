# Who's That Pokémon?

Hackathon build of Hatenna Lite, a slimmed-down HatennaBot (Kaushik's Pokétwo helper bot for Discord): see a Pokémon spawn, name it, ping the people hunting it. Built around three pillars:

1. **Always on**: built. Spec: [docs/always-on-spec.md](docs/always-on-spec.md)
2. **Institutional memory**: spec to come
3. **Specialist swarm**: spec to come

## Run the demo

Python 3.9+, standard library only. No Discord token needed; spawn images load from Pokétwo's image server.

```bash
python3 -m hatenna_lite console
```

Open http://127.0.0.1:8765 (best on a wide screen). Three columns:

- **Left: the bot and the demo scenes.** Start, Stop, Pull the plug (kill -9), Reset; six scenes with what each one should prove.
- **Middle: the game, simulated.** A `#pokétwo-spawns` channel where Pokétwo posts wild spawns with their real images and Hatenna replies: names the pokémon, pings its hunters, answers questions. Type in it yourself: `h!sh stantler`, `h!spawn 234` (any dex number 1–1025 or name), `@hatenna what spawns most?`. Above the channel, the running scene ticks each step only when the bot really did it.
- **Right: the proof.** The four always-on checks live, the AI spend log (click a line for the full CTL record), and the bot's internal log.

## Demo scenes

Run them in order. Each one drives the real bot, then checks the bot's own status before ticking a step.

| # | Scene | Shows | Expected |
|---|---|---|---|
| 1 | A normal day | Always listening | Every spawn is named; the hunter gets pinged |
| 2 | A helper crashes | Heals itself | The others keep working; the crashed helper restarts and its work is kept |
| 3 | Power cut | Picks up where it left off | After kill -9 and restart, nothing is lost and nothing is repeated |
| 4 | Same event twice | Picks up where it left off | A repeated event is recognised and skipped |
| 5 | Retry loop | Capped AI spend | The third identical question is refused; the bot still answers without AI |
| 6 | Budget runs out | Capped AI spend | Spend stops at the cap; the bot keeps working |

Each passed scene shows a one-line talk track. Use **Slow / Normal / Fast** to set the pace, and **Reset** to start over (scene 6 uses up the AI budget for the day).

## How it's built

| Module | File | Comes from |
|---|---|---|
| Listener, Supervisor, Resume | `hatenna_lite/bot.py` | HatennaBot's reliability work, minus Mongo/Redis |
| Spend guard | `hatenna_lite/spend_guard.py` | Python port of the Commerce Trust Labs [gateway](https://github.com/commerce-trust-labs/autonomous-trust-gateway): tier routing, per-task budgets, Execution Tier Bindings |
| Helpers | `hatenna_lite/helpers.py` | Identifier (`cogs/prediction.py`), Pinger (`cogs/shinyhunt.py`), Analyst (`cogs/ai.py`) in HatennaBot |
| Memory | `hatenna_lite/memory.py` | one SQLite file instead of MongoDB + Redis |
| Demo console + scenes | `hatenna_lite/console.py`, `hatenna_lite/scenes.py`, `hatenna_lite/static/index.html` | new |
| Species 1–1025 | `hatenna_lite/species.py`, `hatenna_lite/data/species.json` | generated from HatennaBot's `data/pokemon.csv` |

- The bot and the console only share files under `var/`: the console appends to `inbox.jsonl`; the bot writes `status.json`, `feed.jsonl` and `ledger.jsonl`.
- Delivery is at-least-once: an event killed mid-handling runs again on restart, so helper writes are idempotent.
- The Analyst uses a local Ollama model if one is running (`OLLAMA_MODEL`, default `llama3.2:3b`); otherwise a deterministic stub, with token counts marked `estimated`.
- Ledger records are validated against the vendored [Autonomous Trust contract](https://github.com/commerce-trust-labs/autonomous-trust-contracts) schemas (Apache-2.0) when `jsonschema` is installed.

## Tests

```bash
python3 -m unittest discover tests
```

One test per module's "done when" line in the spec.
