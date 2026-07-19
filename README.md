# Routine Runner

An ambient pacing device for children's morning and bedtime routines. A
wall-mounted kiosk shows the current step and remaining time; parents verify and
configure from their phones. See [`docs/routine_runner_design_spec.md`](docs/routine_runner_design_spec.md)
for the full design and [`docs/routine_runner_technical_plan.md`](docs/routine_runner_technical_plan.md)
for the engineering plan.

## Status

**Phases 0–3 implemented.**

- **Foundations:** dual SQLite DBs with WAL + migrations, authoritative clock,
  in-process event bus + SSE, JWT device auth with instant revocation, test
  harness.
- **Walking skeleton:** SQL-seeded routines, kiosk idle → ready → active render,
  tile / NFC-token check-in, DONE advances with the no-untimed-gap invariant
  (§5.1), parent start / skip / pause / end.
- **Par engine:** bootstrap ramp (best → median handover at run 5), weekly
  Sunday recompute with ±10% cap and hard floor, par snapshot at check-in,
  freeze / reset, countdown rings with colour states (§5.5) and per-child
  display modes (§5.6), star scoring, records, streaks, run bonuses.
- **Gates:** blocking verification steps, waiting panel, approve with quality
  rating, reject-with-resume (per-attempt timing so the paused interval is never
  charged and the redo scores once against the same par), 5-minute auto-approve
  backstop, escalation ladder + stalled-segment nudges via ntfy, rate-limited
  "Ask again".

Not yet built (later phases): config UI (Phase 4), stats & delight (Phase 5),
NFC scan loop (Phase 6).

## Local development

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

### The `run` CLI

`run.py` is the single entry point; `run.sh` (POSIX) and `run.bat` (Windows)
wrap it and auto-activate `.venv` if present. It loads `.env` automatically.

```bash
./run.sh seed             # create ./data + two children + routines
./run.sh serve            # http://127.0.0.1:8000
./run.sh serve --reload   # dev mode with auto-reload
./run.sh serve --host 0.0.0.0 --port 9000
./run.sh migrate          # apply DB migrations only
./run.sh info             # show resolved config and paths
./run.sh test -q          # run the test suite (extra args pass to pytest)
```

On Windows use `run.bat serve --reload`, etc. Without the wrapper:
`python run.py serve`.

First boot with no devices serves `/setup`, which prints an enrolment QR. Enrol a
parent device, then add a kiosk device from **Devices**.

### Tests & lint

```bash
./run.sh test -q
ruff check app scripts tests run.py
```

## Deployment

LAN-only Docker with Caddy terminating TLS via Cloudflare DNS-01 (spec §9.1):

```bash
cp .env.example .env   # fill in RR_JWT_SECRET, CF_API_TOKEN, RR_NTFY_TOPIC
docker compose up -d --build
```

The kiosk must be pointed at the **hostname** (never the IP) so the TLS context
is secure — Web NFC and screen wake-lock require it.
