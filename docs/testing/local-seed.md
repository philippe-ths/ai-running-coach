# Testing without a Strava link

There is no Strava OAuth app for dev/local, so the usual "connect Strava -> sync"
path cannot bootstrap data outside production. This is less limiting than it
looks, because Strava is only a port: once `Activity` / `ActivityStream` rows
exist locally, the whole analyze -> coach -> frontend chain runs with zero
Strava dependency. There are two real testing paths.

## Path 1: drive the deployed app (no setup)

The production Vercel app and per-branch preview deployments are publicly
reachable and render real synced data (the frontend injects the backend's HTTP
Basic credentials server-side). For UI, render, and read-path changes, verify
against the actual deployment — navigate, interact, screenshot, inspect console
and network. This is the highest-fidelity oracle and needs no local setup.

- Production: https://ai-running-coach-eta.vercel.app/
- Previews: one per active branch (Vercel dashboard -> Active Branches).

Caveat: previews point at the same single backend/DB as production, so they show
the same data and write to the same place. Treat them as read-mostly.

## Path 2: seed the local DB from a production snapshot

For backend pipeline work (analysis, coach generation, the API itself) and for
offline iteration, copy a real snapshot from production into the local DB:

```sh
make seed-local                          # full snapshot, Strava tokens redacted
make seed-local SEED_ARGS="--activities 20"   # only the 20 most recent activities
```

What it does (`backend/scripts/seed_from_prod.py`):

1. Pulls the source URL from Railway (`DATABASE_PUBLIC_URL`) using the project
   token at `~/.railway_token`.
2. Resets the local schema (`DROP SCHEMA public CASCADE` then
   `alembic upgrade head`), so the load is deterministic even if the local DB
   was stamped at a revision from another branch.
3. Copies `users`, profiles, the Strava account, activities and their streams,
   derived metrics, coach reports, chat messages, and check-ins, in FK order.

### Safety

- **Strava tokens are redacted by default.** The copied `StravaAccount` gets
  dummy tokens, so local cannot call real Strava and cannot rotate (and thereby
  invalidate) production's live refresh token. Pass
  `SEED_ARGS="--with-live-tokens"` only when you specifically need to exercise a
  real sync, and understand it shares the production token.
- The script **refuses any non-local target** (the URL must contain `localhost`
  or `127.0.0.1`); it only ever reads from the source.

### Verifying a change against seeded data

```sh
make seed-local SEED_ARGS="--activities 20"
docker compose up -d postgres redis        # if not already up
cd backend && .venv/bin/python -m uvicorn app.main:app --port 8000
# then: curl http://localhost:8000/api/activities, or run `next dev` and browse.
```
