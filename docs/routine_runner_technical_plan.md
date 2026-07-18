# Routine Runner — Technical Plan

**Status:** Draft v1.0
**Companion to:** `routine_runner_design_spec.md` (Draft v0.8)
**Last updated:** 2026-07-18

---

## 0. Purpose of this document

The design spec settles *what* to build and *why*. This plan settles *how*: the
concrete stack, the module boundaries, the schema DDL, the order of work, and the
tests that prove each phase. It is written to be executed phase by phase (§13 of
the spec), with each phase independently shippable and observable on a real
morning before the next begins.

Where the spec is authoritative on behaviour, this plan defers to it and cites the
section (e.g. §5.4). Where the spec leaves an engineering choice open, this plan
makes it and flags the decision.

---

## 1. Technology stack (locked)

| Concern | Choice | Notes |
| --- | --- | --- |
| Language | Python 3.12 | Pattern matching, `datetime.UTC`, faster interpreter |
| Web framework | FastAPI | Per spec §8 |
| Server | Uvicorn, single worker | Single worker is required — the in-process `event_bus` (§8.2) has no cross-process transport |
| Templating | Jinja2 | Server-rendered; HTMX swaps partials |
| Frontend | HTMX + `htmx-ext-sse` + DaisyUI (Tailwind) | Plus vanilla JS for countdown rings only |
| Persistence | SQLite (WAL) | Dual-DB pattern §8.1 |
| DB access | `sqlite3` stdlib + thin context-manager layer | No ORM — dataclass models with `from_row`/`to_params` mapping methods per §8 |
| Migrations | Plain numbered `.sql` files + a tiny runner | `schema_version` tracked in `instances` |
| Auth | JWT device tokens, HttpOnly cookie | §9.2; `PyJWT` |
| Background jobs | APScheduler (in-process) | Weekly par recompute + scheduled run opening (§8 `jobs/`) |
| Notifications | `httpx` POST to ntfy | Behind `notify_service` adapter (§15.2) |
| Reverse proxy / TLS | Caddy + Cloudflare DNS plugin via `xcaddy` | §9.1, two-stage Dockerfile |
| Packaging | Docker Compose (`app` + `caddy`) | §3 |
| Testing | pytest + `httpx.ASGITransport` + `freezegun` | Time control is essential for par/timer logic |
| Lint/format | ruff + ruff-format | |
| Dependency mgmt | `uv` + `pyproject.toml` | Fast, lockfile-based |

**Decisions made here (not in spec):**
- **APScheduler** over a bare thread for jobs — it survives restarts cleanly and
  gives cron semantics matching `routines.schedule_cron`.
- **`uv`** for dependency management — fastest path for a solo maintainer; `pip`
  fallback documented.
- **`freezegun`** in tests — the entire par/scoring/gate system is time-driven and
  must be tested deterministically without real sleeps.

---

## 2. Repository layout

Extends the spec's §8 tree with the engineering scaffolding it implies:

```
routine-runner/
  pyproject.toml            # deps, ruff, pytest config
  uv.lock
  Dockerfile                # two-stage: xcaddy build + python app
  docker-compose.yml        # app + caddy services
  Caddyfile
  .env.example              # CF_API_TOKEN, NTFY_TOPIC, HOUSEHOLD_ID, ...
  app/
    main.py  config.py
    db/        __init__.py  main_db.py  instance_db.py  migrate.py
    models/    device.py child.py routine.py step.py run.py segment.py par.py award.py
    routes/    enrol.py parent.py config_routes.py kiosk.py events.py stats.py deps.py
    services/  device_service.py routine_service.py run_service.py par_service.py
               scoring_service.py verification_service.py stats_service.py
               notify_service.py event_bus.py clock.py
    jobs/      weekly_par.py scheduler.py
    migrations/  main/001_init.sql   instance/001_init.sql
    templates/  (per spec §8)
    static/    css/ js/ icons/
  tests/
    conftest.py             # in-memory DB fixtures, frozen clock, test client
    unit/                   # par math, scoring thresholds, grace, state machine
    integration/            # full run flows via ASGI client
  scripts/
    seed_routines.py        # Phase 1 SQL-seeded routines
    write_nfc.py            # Phase 6 tag-writing tool
```

**Additions beyond spec §8:**
- `db/migrate.py` — the migration runner the spec's `migrations/` folder implies.
- `services/clock.py` — a single injectable time source (`now_utc()`), so tests
  freeze time in one place and all server timestamps flow through it. This is the
  backbone of testable timing (§5.7: "the server is authoritative").
- `routes/deps.py` — FastAPI dependencies for auth (`require_parent`,
  `require_kiosk`), DB connections, and current-household resolution.

---

## 3. Cross-cutting foundations (build first, before Phase 1)

These are not user-visible but every phase depends on them.

### 3.1 Clock authority (`services/clock.py`)
A module-level `now_utc()` returning tz-aware UTC. **All** timestamps —
`segment.first_started_at`, `ended_at`, event `occurred_at` — go through it.
The kiosk never authors authoritative time; it sends `client_ts` only for offline
reconcile (§8.3), which the server validates against a sanity window and stamps
`reconciled`. Storing UTC throughout; presentation-layer localisation only.

### 3.2 DB layer (`db/`)
- `main_db.py` / `instance_db.py` expose context managers that open a connection,
  set `PRAGMA foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=5000`, run a
  transaction, commit/rollback (§8.1).
- `migrate.py` — applies numbered `.sql` files in order, records applied version in
  `instances.schema_version` (instance) and a `schema_migrations` table (main).
  Idempotent: safe to run on every boot.
- Household resolution: v1 has one household; `HOUSEHOLD_ID` from env, its
  `db_path` looked up in `instances`. Bootstrap creates both on first run.

### 3.3 Event bus + SSE (`services/event_bus.py`, `routes/events.py`)
In-process async pub/sub. Publishers (`run_service`, `verification_service`) call
`bus.publish(channel, event)`; SSE routes hold per-connection `asyncio.Queue`s
subscribed to `kiosk:<device_id>` or `parent:<device_id>` (§8.2). Kiosk polling
fallback (`hx-trigger="every 3s"`) and the `/kiosk/state` reconcile endpoint are
built alongside so an SSE drop never wedges a run.

### 3.4 Auth (`services/device_service.py`, `routes/deps.py`)
JWT with `jti` bound to a `devices` row, validated per request against the table
(instant revocation, §9.2). HttpOnly `SameSite=Lax` cookie, 1-year expiry, silent
renewal. Kiosk scope strictly limited to the six kiosk endpoints (§9.3).
`require_parent` / `require_kiosk` dependencies enforce role.

### 3.5 Test harness (`tests/conftest.py`)
- In-memory or tmpfile SQLite per test, migrations applied fresh.
- Frozen clock fixture wrapping `clock.now_utc`, advanceable in tests.
- ASGI `httpx` client with helpers to mint parent/kiosk tokens.
- Factory helpers: `make_child`, `make_routine`, `make_run`, `advance(seconds)`.

**Exit criteria for foundations:** a test can bootstrap a household, enrol a parent
and kiosk device, open a run, and receive an SSE event — all with a frozen clock.

---

## 4. Data model — DDL notes

The spec §10 gives the shape. Engineering specifics to lock down:

- **Timestamps:** stored as ISO-8601 UTC text (`TEXT`), or Unix epoch `INTEGER`.
  **Decision: epoch-milliseconds `INTEGER`** — arithmetic for elapsed/par is
  trivial and unambiguous, and skew handling (§5.7) is integer math. Presentation
  converts at the edge.
- **CHECK constraints** enforced in DDL where SQLite allows:
  - `steps`: a `gate` has `floor_seconds IS NULL` and `position > 0`;
    `on_reject_step_id IS NULL` for tasks and NOT NULL for gates (§4 gate rules).
  - `segments.state` and `runs.state` constrained to their enums.
- **`pars` append-only** (§10): current par = row with `superseded_at IS NULL`.
  A unique partial index `(child_id, step_id) WHERE superseded_at IS NULL`
  guarantees exactly one live par per (child, step).
- **`segments.par_seconds` is a snapshot** taken at run open (§5.7, §10) — the
  weekly recompute must never touch historical segments.
- **`segment_attempts`** carries per-attempt timing; `segments.elapsed_seconds` is
  `SUM(attempts.elapsed_seconds)` for tasks, `NULL` for gates (§10).
- **FK integrity:** `on_reject_step_id` FK to `steps`; application-level check that
  it points to an *earlier* task in the *same* routine (SQLite can't express that
  in a CHECK). Enforced in `routine_service` on step create/edit.
- **Migrations** ship as `instance/001_init.sql` (all household tables) and
  `main/001_init.sql` (households/devices/tokens/instances). Later phases add
  numbered migrations rather than editing `001`.

---

## 5. Core service designs

### 5.1 `run_service` — run & segment state machine (the core, §8)
Owns the lifecycle. Explicit state machine:

```
run:      open → active → (paused ⇄ active) → closed | abandoned
track:    (run_child) checked_in → active → completed
segment:  pending → active → (gate_open) → done | skipped | incomplete
```

Key operations, each a single DB transaction that stamps server time and publishes
an event:
- `open_run(routine_id, parent)` — snapshot step list + current pars into the run
  (§6.1); create `run_children` lazily on check-in.
- `check_in(run, child)` — track→active, open segment 1, stamp `first_started_at`
  = check-in time (§5.1, §6.2).
- `complete_segment(run, child, segment, client_ts?)` — **the critical
  transition**: close current segment, compute elapsed, immediately open the next
  with `first_started_at == prev.ended_at` (§5.1 no untimed gaps). If next is a
  gate, open it and stop the clock (§6.4).
- `pause_run` / `resume_run` — accrue `paused_seconds` (§5.7).
- `skip_segment`, `close_run` — §6.5 terminal transitions.

**No-untimed-gap invariant** is enforced structurally: opening segment N+1 uses
segment N's `ended_at` as its `first_started_at`, never a fresh `now()`. A unit
test asserts `seg[n+1].first_started_at == seg[n].ended_at` across a full run.

### 5.2 `par_service` — calibration & recompute (the core, §5)
- `current_par(child, step)` → the live `pars` row, or `basis='none'` during run 1.
- `snapshot_pars(run)` — called at run open; copies live par into each segment.
- `bootstrap_ramp` — implements the §5.2 table: run 1 none; runs 2–4
  `best_so_far + grace`; run 5+ `median(last 10) + grace`. Run-count is per
  `(child, routine)`, and per-step for steps added later (§16 Q1: new steps ramp
  independently).
- `recompute_weekly()` — the §5.4 steady-state formula, invoked by the Sunday
  22:00 job. Applies: median of last 10 (excluding rejected/skipped/abandoned),
  `grace = max(30s, 0.20×baseline)`, ±10%/week movement cap, hard floor
  `max(step.floor_seconds, 60s)`, respects `frozen`. Writes a new append-only
  `pars` row, supersedes the old.
- `freeze(par)` / `reset(child, step)` — §5.4 rules 5–6; reset supersedes all rows
  and restarts at `basis='none'`.

Grace, median, cap, and floor are **pure functions** in a `par_math` module, unit
tested exhaustively against the worked examples in §5.2–5.4.

### 5.3 `scoring_service` — stars (§7)
Pure threshold function per segment: `≤par → ⭐⭐`, `≤1.25×par → ⭐`, else 0;
run-1 participation star; skipped/incomplete → 0; under-`floor_seconds` → 0 +
review flag (§7 wellbeing guard). Run bonuses (+2 all complete, +3 all under par,
+5 new total record) and the weekly steady award computed at run close / weekly
job. Records & streaks updated transactionally at run close (§6.6).

### 5.4 `verification_service` — gates (§6.4, Phase 3)
`open_gate`, `approve(quality_stars, note)`, `reject(note)` → returns child to
`on_reject_step_id` with **clock resume** (new attempt on the same segment,
accumulating elapsed, §6.4). Runs the escalation ladder (§6.4.1) via APScheduler
one-shots: notify at 0s, re-notify at 60s, auto-approve at 5min. `Ask again`
nudge rate-limited to once/30s. Gate wait time recorded, never scored.

### 5.5 `notify_service` — adapter (§15.2)
`notify(...)` and `speak(...)` interface; implementations `ntfy`, `noop`, (later)
`home_assistant`. Selected by config. Failures logged, never surfaced to kiosk,
never retried into the critical path.

---

## 6. Phase-by-phase execution

Mirrors spec §13. Each phase lists deliverables, the tests that gate it, and the
real-world observation that closes it.

### Phase 0 — Foundations (this plan §3)
Scaffolding, DB layer, migrations, clock, event bus/SSE, auth, test harness,
Docker + Caddy + TLS so the kiosk runs in a secure context from day one (§9.1 —
NFC and wake-lock need HTTPS, so TLS cannot be deferred even though NFC is Phase 6).
**Gate:** foundations exit criteria (§3.5) green; kiosk loads over HTTPS on the
real phone with wake-lock acquired.

### Phase 1 — Walking skeleton (spec §13)
SQL-seeded routines, kiosk idle→ready→active render, tile check-in, run-1
behaviour (plain elapsed timer, participation stars), DONE advances, run closes,
parent start/skip/end. No pars, NFC, gates, or config UI.
**Tests:** full run via ASGI client — open, two check-ins, complete all segments,
close, participation stars awarded; no-untimed-gap invariant holds.
**Real-world gate:** run it on genuine mornings; collect run-1 timing data; confirm
the kids engage before building the par engine (spec §13 explicitly calls this the
risky phase).

### Phase 2 — Par engine (spec §13)
`par_service` ramp + weekly recompute, countdown rings (§5.6 per-child display),
colour states (§5.5), star scoring (§7), records, freeze/reset, the Sunday job.
**Tests:** par_math unit suite against §5.2–5.4 worked examples; ramp progression
runs 1→5 including the best→median handover discontinuity (§5.2); ±10% cap; floor;
`freezegun`-driven state thresholds (`on_pace`/`closing`/`over`/`stalled`).

### Phase 3 — Gates (spec §13)
Gate step kind, blocking wait panel, approve/reject with clock resume, escalation
ladder + 5-min auto-approve, ntfy notifications.
**Tests:** reject→resume accumulates onto same par and scores once (§6.4 worked
example: 2:10 vs 2:00 → ⭐); rejected segment excluded from par sample; auto-approve
at 5min gives 0 quality stars and logs `auto_approved`; nudge rate-limit.

### Phase 4 — Configuration (spec §13)
Routine/step CRUD, drag-reorder, floor-time steppers, child management, scheduling.
Gate-creation prompts for `on_reject_step_id` (§12). Mid-run edits don't affect the
active run.
**Tests:** gate-rule validation (not position 0, on_reject points earlier/same
routine); reorder; soft-disable preserves history.

### Phase 5 — Stats & delight (spec §13)
Streaks, steady award, 30-day charts, scorecard reveal animation, par-history view
(§12).
**Tests:** streak increment/break; steady award (clear par every run in a week);
30-day aggregates.

### Phase 6 — NFC (spec §13)
`scripts/write_nfc.py`, `NDEFReader` scan loop in ready/active, silent fallback to
tiles when unavailable (§8.4).
**Tests:** manual on-device (Web NFC can't be unit-tested); check-in-by-token path
covered server-side via `/kiosk/checkin {nfc_token}`.

---

## 7. API implementation notes

Build the exact surface in spec §11. Cross-cutting:
- Kiosk endpoints return **rendered HTML partials** (HTMX swaps), not JSON —
  the kiosk is a dumb renderer (§5.7).
- `/kiosk/complete` accepts `client_ts` and is **idempotent** on `segment_id` so a
  replayed offline event doesn't double-advance (§8.3). Idempotency key =
  `(segment_id, transition)`.
- `/kiosk/state` returns the full current render for reconcile after reconnect.
- Setup routes (`/setup`, `/setup/claim`) are mounted only while the `devices`
  table is empty — a middleware guard returns 404 once any device exists (§9.2).
- All mutating routes are thin: validate → call service → return partial (§8).

---

## 8. Testing strategy (summary)

| Layer | What | Tooling |
| --- | --- | --- |
| Unit | par_math, scoring thresholds, grace, state-machine transitions | pytest, plain functions |
| Time | ramp, weekly recompute, in-run states, gate escalation | `freezegun` + injectable clock |
| Integration | full run flows, gate reject/resume, offline reconcile, auth/scope | `httpx.ASGITransport` |
| Migration | fresh DB applies cleanly; version recorded | tmpfile SQLite |
| Manual/on-device | NFC, wake-lock, fullscreen, real SSE over wifi | the actual kiosk phone |

The two highest-risk correctness areas — **no-untimed-gaps** (§5.1) and
**reject-then-resume scoring** (§6.4) — each get a dedicated integration test with
the spec's worked numbers as assertions.

---

## 9. Deployment

`docker-compose.yml`: `app` (Uvicorn single worker) + `caddy` (reverse proxy, TLS).
Two-stage Dockerfile for the app; a separately-built `xcaddy` image with the
Cloudflare DNS plugin (§9.1). `.env` supplies `CF_API_TOKEN` (zone-scoped),
`NTFY_TOPIC`, `HOUSEHOLD_ID`. Volumes persist `main.db`, `household_<id>.db`, and
Caddy's cert store. First boot: migrations run, bootstrap `/setup` prints claim
URL + QR to container logs (§9.2). ntfy alert on cert expiry at 21 days (§9.1.2).

**Operational gotchas to bake into the runbook** (§9.1.2): kiosk must use the
hostname never the IP; whitelist the domain against router DNS-rebinding
protection; verify PWA manifest served over HTTPS with relative/https assets.

---

## 10. Open questions carried from the spec (§16)

These are behavioural decisions the design owner must resolve; the plan defaults so
implementation isn't blocked, and isolates each so a later change is cheap:

1. **Run-1 per step vs per routine** — default: **per step** (new steps ramp
   independently). Isolated in `par_service.bootstrap_ramp`.
2. **Weekly recompute Sunday vs Friday** — default: **Sunday 22:00** (spec §5.4).
   One cron string in `jobs/weekly_par.py`.
3. **Steady award legibility** — presentation-only; ship the badge, revisit a
   dot-grid in Phase 5.
4. **5-min auto-approve timeout** — config constant `GATE_AUTO_APPROVE_SECONDS`,
   tunable from observed data (§6.4.1).
5. **Second-rejection escalation** — deferred; `verification_service` leaves a hook
   but v1 treats every rejection identically.
6. **Multi-step-back gate** — schema allows it; UI defaults to the immediately
   preceding task (§12). No code constraint added, keeping §16 Q6 open.

---

## 11. Suggested milestone ordering for a solo maintainer

1. Phase 0 foundations + Docker/TLS so the kiosk is real from the start.
2. Phase 1, then **stop and run real mornings** — this is the engagement bet.
3. Phase 2 once run-1 data exists to calibrate against.
4. Phase 3 gates (load-bearing — nothing enforces quality until they exist).
5. Phases 4–6 as convenience/polish, ordered by whatever the daily use surfaces.

Ship each phase behind the real mornings that validate it. The spec's own framing —
"ship it plain and watch a few real mornings before building the par engine" — is
the governing principle of this plan.
