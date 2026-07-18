# Routine Runner

An ambient pacing device for children's morning and bedtime routines. A
wall-mounted kiosk shows the current step and remaining time; parents verify and
configure from their phones. See [`docs/routine_runner_design_spec.md`](docs/routine_runner_design_spec.md)
for the full design and [`docs/routine_runner_technical_plan.md`](docs/routine_runner_technical_plan.md)
for the engineering plan.

## Status

**Phase 0 (foundations) + Phase 1 (walking skeleton) implemented.**

- Foundations: dual SQLite DBs with WAL + migrations, authoritative clock,
  in-process event bus + SSE, JWT device auth with instant revocation, test
  harness.
- Walking skeleton: SQL-seeded routines, kiosk idle → ready → active render,
  tile / NFC-token check-in, DONE advances with the no-untimed-gap invariant
  (§5.1), run-1 participation stars, parent start / skip / pause / end.

Not yet built (later phases): par engine, countdown rings, gates, config UI,
stats, NFC scan loop.

## Local development

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# seed data and run
python -m scripts.seed_routines          # creates ./data + two kids + routines
uvicorn app.main:app --reload
```

First boot with no devices serves `/setup`, which prints an enrolment QR. Enrol a
parent device, then add a kiosk device from **Devices**.

### Tests & lint

```bash
pytest -q
ruff check app scripts tests
```

## Deployment

LAN-only Docker with Caddy terminating TLS via Cloudflare DNS-01 (spec §9.1):

```bash
cp .env.example .env   # fill in RR_JWT_SECRET, CF_API_TOKEN, RR_NTFY_TOPIC
docker compose up -d --build
```

The kiosk must be pointed at the **hostname** (never the IP) so the TLS context
is secure — Web NFC and screen wake-lock require it.
