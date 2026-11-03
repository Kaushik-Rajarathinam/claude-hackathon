# Hatenna Lite — Pillar 1: Always On

Draft spec for team review. Hackathon scope only.

## What Hatenna Lite is

- A slimmed-down HatennaBot for a hackathon demo.
- Keeps the core job: see a Pokémon spawn, name it, ping the people hunting it.
- Drops everything else: no Mongo/Redis, no auctions, no daycare; 4 helpers instead of ~55 cogs.
- Runs on one laptop from a scripted spawn feed, so the demo can't break on Discord or the network. Live Discord is optional.

This spec covers the first pillar, **Always on**, split into four modules.

## Module 1 — Listener (never stops listening)

- Waits for new events (spawns, hunt sign-ups, questions) and passes each one to the right helper.
- Reads a scripted feed by default; a live Discord connection can plug in later without changing anything else.
- Handles one event at a time so the demo is easy to follow.
- **Demo moment:** drop new events into the feed while the bot runs; it reacts within a second.
- **Done when:** it runs for hours untouched and picks up every new event.

## Module 2 — Supervisor (crash one, the rest keep going)

- Runs each helper on its own, so one failure can't take the others down.
- If a helper crashes, the supervisor notes it, waits a moment, and restarts it — waiting a little longer if it keeps crashing.
- Work queued for the crashed helper is kept, not dropped.
- Writes a heartbeat every few seconds so you can see it's alive.
- **Demo moment:** crash the Pinger on purpose; the Identifier keeps naming spawns, the Pinger is back in about a second, and the next ping goes out.
- **Done when:** a crashed helper always comes back and no other helper notices.

## Module 3 — Resume (stop, restart, carry on)

- Saves a bookmark of how far it got after every event.
- On restart it continues from the bookmark instead of starting over.
- Every event has an ID; if the same event arrives twice, it's recognised and skipped.
- Announces itself on restart, e.g. "boot #2, 14 events and 1 lesson in memory".
- **Demo moment:** kill the bot mid-demo, start it again; it carries on where it was, nothing missed, nothing repeated.
- **Done when:** a hard kill at any point loses nothing and repeats nothing.

## Module 4 — Spend guard (always on, never runaway)

- Every AI call goes through one checkpoint that picks the cheapest suitable kind of model (image model, local AI, or a bigger AI) and counts tokens.
- Each task has a token budget. When it's used up, the AI call is refused and the bot falls back to a fixed answer.
- Catches retry loops: a helper that keeps calling the AI without getting a better result is stopped.
- Every routing decision is logged in the standard Commerce Trust Labs record format.
- **Demo moment:** set a tiny budget, ask the Analyst questions until the cap hits; the bot keeps working without AI.
- **Done when:** spend can never pass the cap, and running out never crashes the bot.

## How they fit

Listener brings events in → Supervisor keeps helpers alive → Resume makes restarts safe → Spend guard keeps cost bounded.

Module 4 is ported from the Commerce Trust Labs gateway. Modules 1–3 are plain reliability engineering.

## Open questions for the team

1. Demo feed: scripted only, or also a live Discord channel?
2. AI budget: per task, per day, or per helper? (Suggest per task, matching the CTL gateway.)
