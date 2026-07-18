# Routine Runner — Design Spec

**Status:** Draft v0.8
**Owner:** —
**Last updated:** 2026-07-18

---

## 1. Problem

Children lose momentum during morning and bedtime routines. The failure mode is rarely "refuses to brush teeth" — it's **drift**: 90 seconds of staring out a window between the bedroom and the bathroom, or a toothbrush abandoned mid-stroke because a sibling walked past.

Nagging is the current fix. It works, but it costs a parent's continuous attention and turns the routine adversarial.

### Goal

Replace the parent's voice with an **ambient, always-visible pacing device** in the hallway that:

- makes the *current* step and *remaining time* impossible to ignore,
- charges wandering time to the child's score, so transitions are part of the game,
- gives parents a lightweight verification and configuration surface from their own phone,
- accumulates history so kids compete against their own records rather than each other.

### Non-goals (v1)

- Not a chore-reward economy (no allowance, no point store).
- Not a behaviour tracker or discipline log.
- No internet exposure. LAN-only, self-hosted, single household.
- No punishment mechanics. Scores only ever add; a bad run scores zero, never negative.
- No cross-child comparison. Personal records only.

---

## 2. Scope (resolved)

| Decision | Value |
| --- | --- |
| Children | 2 — ages 8 and 5 |
| Kiosk layout | Fixed vertical split screen, one column each |
| Step lists | Identical for both children within a routine |
| Execution | Fully independent parallel tracks, no synchronisation |
| Par times | Derived from each child's own history (§5) |
| Sound | Out of scope for v1 — visual timer and stars are the motivator |
| Comparison | Personal records only; no sibling scoreboard |
| Finished child | Neutral "waiting" state; scorecards revealed at run close |
| Routines | `morning`, `bedtime` — bedtime is *getting ready for bed* only (no stories) |
| Deployment | LAN-only Docker container on existing home server |
| Hostname | `routines.philim.com`, LAN-resolved, Let's Encrypt via DNS-01 |
| Auth | QR device enrolment, long-lived device tokens |
| Notifications | ntfy (both parents on Android) |

---

## 3. Physical setup

| Element | Description |
| --- | --- |
| **Kiosk** | Retired Android phone, wifi-only, no SIM. Wall-mounted in the hallway between the bedrooms and the bathroom. Permanently powered, screen always on, Chrome fullscreen pointed at the kiosk URL. |
| **Server** | Docker container on the home server. Static IP + local DNS name, valid TLS cert (§9.1). |
| **Parent phones** | Two Android phones, normal browser, installed as a PWA. |
| **NFC tokens** | One per child — wristband or keyring fob. Tapped against the kiosk to identify who is checking in. |

The kiosk is deliberately *not* carried around. It is a fixed station the child must return to, which is what makes transition time measurable.

---

## 4. Core concepts

**Routine** — a named, ordered template. `morning`, `bedtime`.

**Step** — one unit of work. Applies to both children in v1. Two kinds:

- **`task`** — timed, has a par, completed by the child tapping DONE.
- **`gate`** — untimed, **blocking**. The child can go no further until a parent approves. On rejection the child is returned to an earlier task, whose clock resumes.

Verification is not an attribute of a task; it is a step in its own right. "Brush teeth" is a task; "Parent checks teeth" is the gate immediately after it.

**Run** — one execution of a routine on a given date, started by a parent (or by schedule). Contains one **track** per child who checks in.

**Segment** — one child's attempt at one step within a run. The timed unit.

**Par** — the per-child, per-step target duration, computed from that child's own history. Not hand-set. See §5.

**Gate rules** — a gate cannot be the first step of a routine, and its `on_reject_step_id` must point to an earlier task in the same routine (usually, but not necessarily, the one immediately before it).

---

## 5. Timing and par calibration

This is the heart of the design, so it gets stated precisely.

### 5.1 No untimed gaps

**A segment's clock starts the instant the previous segment ends.** There is no untimed transition. If a child finishes "Get dressed" at 07:04:10, the clock for "Brush teeth" starts at 07:04:10 — including the walk down the hallway.

This is the whole trick. Drift *between* tasks is the dominant failure mode; any model with untimed transitions would fail to capture it. It follows that pars measure *transit plus activity*, which is fine because pars are measured from reality, not guessed.

The **first** segment starts on kiosk check-in. Time-to-check-in after the routine opens is tracked but not scored in v1.

### 5.2 Calibration ramp

Par is bootstrapped over the first four runs of a routine, per child.

| Run | Par | Kiosk display | Stars |
| --- | --- | --- | --- |
| 1 | none | Plain elapsed timer, neutral grey | 1 participation star per completed step |
| 2–4 | `best_so_far + grace` | Full countdown ring | Normal scoring |
| 5+ | `median(last 10) + grace` | Full countdown ring | Normal scoring |

Run 1 is a measurement run with no target, which also lets both children learn the interface with nothing at stake. Because there is no clock pressure, run 1 times tend to be leisurely — so run 2's par (`run 1 + grace`) is an easy first win, which is exactly the right way to start.

Over runs 2–4, `best_so_far` falls as the children engage, so par tightens quickly. This is intentional: it gets to a realistic target in three days rather than three weeks.

**Note the handover discontinuity.** At run 5 the basis changes from *best* to *median*, and median-of-10 is always ≥ best-of-4, so par will typically **loosen** at that point. This is deliberate — `best + grace` requires roughly matching a personal best every single day, which is a fine three-day ramp but a punishing permanent state. Frame it positively on the kiosk rather than hiding it: *"New pars — settled in now."*

### 5.3 Grace

```
grace = max(30s, 0.20 × baseline)
```

A flat 30s is right at the 2-minute scale you'd expect for most steps, but it breaks at the extremes: 30s of grace on a 20-second "put your shoes on" is 150% headroom (unmissable, therefore meaningless), while on a 6-minute step it's 8% (unfairly tight). The `max()` keeps 30s as the floor for short steps and scales proportionally for long ones.

### 5.4 Steady state (run 5+)

Per `(child, step)`:

```
sample   = last 10 completed segments (excluding redos and abandoned)
baseline = median(sample)          # median resists the one disaster morning
par      = baseline + max(30s, 0.20 × baseline)
```

Rules:

1. **Recomputed weekly**, Sunday night — never mid-week, never mid-run. Par is stable and legible for seven days.
2. **Movement capped at ±10% per week.** No overnight cliff after one fast morning, and no sudden loosening after one bad one.
3. Par may move in **either direction**. A median over ten runs already absorbs a bad day; allowing par to loosen after a genuinely worse stretch — illness, a darker set of winter mornings — is more humane than forcing a manual reset.
4. **Hard floor:** `par >= max(step.floor_seconds, 60s)`, parent-settable per step. This is what prevents the four-second toothbrush.
5. **Parent freeze.** A parent can lock a par at any time — "this is good enough, stop moving it."
6. **Parent reset.** A parent can reset a step's par, which discards its par history and restarts the §5.2 ramp from run 1. For when the real world changed: new bathroom, new school time, a growth spurt.

Par is self-limiting without further machinery. As a child's median approaches the fastest they can physically complete a step, the median stops falling and par stops falling with it. At steady state `median + 20%` should mean clearing par on roughly three mornings in four — winnable, not guaranteed.

The one thing par does *not* capture is durable improvement during the transient, while it's still tightening. That is what **records** are for: absolute, permanent, and never recalculated.

### 5.5 In-run states

Visual only — sound is out of scope for v1.

| State | Threshold | Kiosk behaviour |
| --- | --- | --- |
| `on_pace` | `< 0.75 × par` | Calm colour |
| `closing` | `0.75–1.0 × par` | Amber, ring pulses gently at 10s remaining |
| `over` | `1.0–1.5 × par` | Red, ring inverts and counts up |
| `stalled` | `> 1.5 × par` | Slow pulse; parent notified via ntfy |

There is no auto-abandon. A stalled segment runs until the child completes it, or a parent skips the step or ends the run (§6.5).

Gates have no timer states — the child's clock is stopped entirely while a gate is open.

### 5.6 Per-child display

Age-appropriate, set per child:

- **Age 8** — countdown ring *and* numeric `mm:ss` remaining.
- **Age 5** — draining ring only, no numbers. A 5-year-old reading "0:12" and doing subtraction under pressure is a worse experience than a shrinking arc.

### 5.7 Pauses and clock authority

A child's clock is stopped entirely while a gate is open (§6.4). A parent can also globally pause a run ("breakfast is late"). Both are recorded as events so the audit trail stays honest.

The server is authoritative. On handshake the server sends its epoch; the kiosk measures skew and renders countdowns locally from `segment_started_at + par`. All state transitions are server-stamped. The kiosk displays elapsed time; it never decides it.

---

## 6. Key flows

### 6.1 Starting a routine

1. Parent opens the PWA, taps **Morning** → **Start**.
2. Server creates a `run` in state `open`, snapshots each child's step list and current pars, pushes to the kiosk over SSE.
3. Kiosk switches from idle clock face to **ready mode**: two large check-in tiles, name + avatar + colour.

Routines may also be **scheduled** (weekdays 07:00) so the kiosk opens itself. Scheduled runs auto-expire if nobody checks in within N minutes.

### 6.2 Check-in

Child taps their tile, or taps their NFC token against the marked spot on the bezel. NFC is preferred: it removes the "my brother pressed my button" failure mode, and a physical token is a nicer ritual than a rectangle. Tile-tap stays as fallback — tokens get lost.

On check-in the track goes `active` and segment 1 opens.

### 6.3 During the run

Vertical split, one column per child. Each column shows only:

- colour bar and name,
- current step icon + title, large,
- countdown ring (per §5.6),
- progress dots `●●●○○○`,
- one giant **DONE** button filling the bottom half.

No menus, no back button, no scroll. Everything a child can touch either completes a step or does nothing.

DONE closes the current segment and immediately opens the next. If the next step is a gate, the clock stops and the column switches to the waiting panel (§6.4).

### 6.4 Gates

A gate is a blocking checkpoint. It is an ordinary step in the routine, sitting immediately after the task it verifies.

```
Brush teeth          task   par 2:00
Parent checks teeth  gate   on_reject -> Brush teeth
Get dressed          task   par 4:00
```

**When the child taps DONE on the preceding task**, its segment closes and the gate opens. The child's clock **stops completely** — no timer runs during a gate. The kiosk column switches to a waiting panel: what's being checked, who's been asked, and a **"Ask again"** button that re-sends the notification. The child cannot advance past it.

The other child's track is unaffected. Tracks remain fully independent.

**On approve** — the parent may attach a 1–3 star quality rating and a preset note. The gate closes and the next step opens.

**On reject** — the child returns to the step named by `on_reject_step_id`, and **that segment's clock resumes from where it stopped**. The second attempt accumulates onto the first and is scored once against the same par.

```
Brush teeth   attempt 1   0:00 → 0:50    child taps done
Gate          0:50 → (clock stopped, parent takes 40s)  → rejected
Brush teeth   attempt 2   0:50 → 2:10    resumes; taps done
Gate                                      → approved
              segment total 2:10 vs par 2:00  →  ⭐ (within 1.25× par)
```

**No rejection grace.** The extra time, including the walk back to the bathroom, is the penalty for rushing a task to beat the clock without due care. This is the point of the mechanic.

Because the redo accumulates against the same par, no separate redo scoring rule is needed — stars fall out of the existing thresholds.

Rejected segments are **excluded from the par sample** (§5.4); a two-attempt time is atypical and would inflate the median for every subsequent day.

### 6.4.1 The parent is a blocking dependency

This is the cost of making gates blocking, and it needs a backstop. A parent in the shower must not strand a child in the hallway.

| Elapsed in gate | Behaviour |
| --- | --- |
| 0s | Notification to both parents |
| 60s | Re-notify, higher priority |
| Any time | Child may tap **Ask again** (rate-limited to once per 30s) |
| 5 min | Gate **auto-approves** with zero quality stars; logged as `auto_approved` |

Auto-approval is a reluctant compromise. A gate that is routinely auto-approved teaches the child that the check doesn't matter, so the dashboard surfaces an auto-approval rate per gate — if it climbs, either the parents need a better notification path (§15) or that gate shouldn't exist.

Gate wait time is recorded but **never scored against the child**. It is a metric about the parents, and a useful one: if gates are adding four minutes to every morning, that is worth knowing.

### 6.5 Parent run controls

From the live run view a parent can, at any time:

- **Pause / resume** the whole run ("breakfast is late").
- **Skip a step** for one child or both — the segment closes as `skipped`, scores no stars, and does not enter the par sample. For a sick child or a step that's irrelevant today.
- **End the run.** Any open segments close as `incomplete`, scoring zero. This is the only mechanism for terminating a run that has stalled; there is no automatic timeout.

### 6.6 Finishing

A track completes when its last segment closes. A track can never complete with a gate open, so no finished track is ever reopened. The column switches to a **neutral waiting state** — a calm "all done" panel with no score, no stars, no timer. Nothing to gloat over while a sibling is still working.

When the run closes (both tracks done, or parent force-close), both scorecards reveal together:

- stars earned this run out of possible,
- per step: time vs par, `▲ NEW RECORD` badges,
- current streak.

---

## 7. Scoring

Deliberately legible to a five-year-old.

**Per timed segment**

| Result | Stars |
| --- | --- |
| `≤ par` | ⭐⭐ |
| `≤ 1.25 × par` | ⭐ |
| over, completed | 0 |
| incomplete / abandoned | 0 |
| run 1 (no par yet) | ⭐ (participation) |
| skipped by parent | 0, excluded from par sample |
| rejected then redone | scored on cumulative time vs the same par; excluded from par sample |

**Per gate** — parent quality rating 0–3 on approve. Auto-approval at timeout gives 0.

**Run bonuses**
- All steps completed: +2
- All timed steps under par: +3
- New personal record on total routine time: +5

**Steady award** — awarded weekly for clearing par on every run that week, regardless of speed. This is the counterweight that gives the more careful child something winnable and takes pressure off raw pace.

Records and rolling 30-day averages are tracked per `(child, routine, step)` and per `(child, routine)` total.

> **Wellbeing guard.** Speed-only scoring incentivises sloppy work. Mitigations: floor times per step (§5.4), `parent` verification on quality-critical steps, and a minimum-time rule — a segment completed under `floor_seconds` earns no stars and flags for review.

---

## 8. Architecture

FastAPI + SQLite + Jinja2 + HTMX + DaisyUI. Thin routes, logic in services, dataclass models with mapping methods.

```
app/
  main.py                  # FastAPI app, middleware, SSE broker wiring
  config.py
  db/
    __init__.py            # connection factory, context managers
    main_db.py             # household/device/registry connections
    instance_db.py         # per-household connections
  models/
    device.py  child.py  routine.py  step.py  run.py  segment.py  par.py  award.py
  routes/
    enrol.py               # QR device enrolment
    parent.py              # dashboard, live run, review queue
    config_routes.py       # routine/step CRUD
    kiosk.py               # kiosk render + check-in/done
    events.py              # SSE
    stats.py
  services/
    device_service.py      # enrolment, tokens, revocation
    routine_service.py
    run_service.py         # run lifecycle, segment transitions   <- core
    par_service.py         # calibration + weekly recompute       <- core
    scoring_service.py
    verification_service.py
    stats_service.py
    notify_service.py      # ntfy
    event_bus.py           # in-process pub/sub feeding SSE
  jobs/
    weekly_par.py          # Sunday 22:00 recompute
    scheduler.py           # scheduled run opening
  migrations/
    main/001_init.sql ...
    instance/001_init.sql ...
  templates/
    base.html
    kiosk/idle.html  ready.html  active.html  waiting.html  scorecard.html
    parent/dashboard.html  run_live.html  review.html  routine_edit.html  devices.html
    partials/...
  static/  css/ js/ icons/
```

### 8.1 Databases

Dual-database pattern:

- **`main.db`** — households, devices, enrolment tokens, instance registry. Small, rarely written.
- **`household_<id>.db`** — children, routines, steps, pars, runs, segments, events, awards, records.

Single household in v1; the isolation costs nothing now and makes a second household (grandparents, co-parenting) a config change rather than a migration.

All access through context managers: open connection, `PRAGMA foreign_keys=ON`, `journal_mode=WAL`, transaction, commit or rollback. Parameterized queries only. WAL matters — the SSE broker and run service read and write concurrently during a run.

### 8.2 Real-time

SSE via `htmx-ext-sse`. Two channels:

- `kiosk:<device_id>` — run opened/closed, pause, config change, force-advance.
- `parent:<device_id>` — segment closed, review pending, run summary.

In-process `event_bus` publishes; SSE route subscribes. Single-worker Uvicorn keeps this simple — no Redis at two users.

Kiosk falls back to `hx-trigger="every 3s"` polling if SSE drops, and reconciles via a full-state endpoint on reconnect.

### 8.3 Kiosk client

HTMX page plus a small amount of vanilla JS for the countdown rings.

- `navigator.wakeLock`, re-acquired on visibility change; plus Android's "stay awake while charging" as belt-and-braces. **Requires HTTPS** (§9.1).
- Fullscreen via Fully Kiosk Browser (pragmatic) or Chrome PWA in fullscreen display mode.
- `localStorage` holds device token, last known run state, queued events.
- **Offline resilience:** if wifi drops mid-run, the kiosk keeps rendering local countdowns and queues DONE events with client timestamps. On reconnect it replays; the server accepts client timestamps within a sanity window and marks affected segments `reconciled`.

### 8.4 NFC

Web NFC (`NDEFReader`) is Chrome-on-Android only — exactly the kiosk environment. **Requires HTTPS.** Tags written once during setup with `routine-runner:child:<uuid>`.

Scan loop runs continuously in ready and active states; a scan resolves to a child and performs that child's contextual action. If Web NFC is unavailable or permission denied, the kiosk silently falls back to touch tiles. NFC is an enhancement, never a dependency.

> **Deferred:** location stickers (bathroom mirror, shoe rack) proving physical presence. Needs a device that travels with the child, which contradicts the fixed-kiosk model. Revisit only if a second device is added.

---

## 9. Auth and deployment

### 9.1 TLS — resolved

`NDEFReader` and `navigator.wakeLock` both require a **secure context**. A kiosk served over `http://192.168.1.x` would have no NFC, a screen that sleeps, and no PWA install. So TLS is a prerequisite, not a polish item.

**Decision: `routines.philim.com`, real Let's Encrypt cert via DNS-01.**

- Public `A` record for `routines.philim.com` → the server's **private** LAN IP (e.g. `192.168.1.50`).
- **No inbound ports opened.** DNS-01 proves domain control with a `_acme-challenge` TXT record, so Let's Encrypt never needs to reach the box. Only outbound access to the ACME endpoint and the DNS provider API is required.
- Publishing a private IP in public DNS is harmless — it discloses internal addressing, which is not a meaningful secret for a home LAN.
- Certs are fully trusted on all three devices with zero per-device setup. No CA installation on Android, which is the trap that makes the mkcert route painful.

**Implementation.** Caddy as reverse proxy in front of the FastAPI container, built with the Cloudflare DNS plugin via `xcaddy` (there is no official prebuilt image with DNS plugins, so a two-stage Dockerfile is needed). Caddy handles issuance and renewal unattended. Requires the domain's DNS to be hosted somewhere with an ACME-supported API — Cloudflare being the usual choice — and a scoped API token with edit rights on that zone only.

```
routines.philim.com {
    tls { dns cloudflare {env.CF_API_TOKEN} }
    reverse_proxy app:8000
}
```

### 9.1.1 WAN dependency — accepted, not mitigated

Name resolution is the only part of the system that leaves the house. Server, kiosk and both parent phones are metres apart, so a sustained ISP outage would break the routine despite every participating device being on the same switch.

**Decision: accepted risk, deferred.** WAN outages are rare enough not to design around, and a short blip is absorbed by DNS caching anyway.

If it ever becomes annoying, the fix is a local DNS override — Pi-hole, AdGuard, dnsmasq, or a single router host entry mapping `routines.philim.com` to the LAN IP. No code change, no cert change (validity depends on the *name*, not on how it was resolved). Recorded here purely so the option is on the shelf.

Cert renewal still needs outbound internet roughly every 60 days, which is a different and more forgiving timescale.

### 9.1.2 Known gotchas

| Symptom | Cause | Fix |
| --- | --- | --- |
| Kiosk gets NXDOMAIN for a name that resolves fine on mobile data | Router **DNS rebinding protection** dropping public names that resolve to private IPs (common on Fritz!Box, pfSense, OpenWRT). A day-one blocker, unrelated to outages | Whitelist the domain in the router's rebind settings |
| NFC silently unavailable | Kiosk pointed at the IP rather than the hostname, so the cert doesn't match and the context isn't secure | Kiosk URL must be the hostname. Never bookmark the IP |
| Cert renewal fails silently | Scoped DNS API token expired or rotated | Alert on cert expiry via ntfy at 21 days remaining |
| PWA won't install | Mixed content, or no valid manifest over HTTPS | Check all asset URLs are relative or https |

### 9.2 Device enrolment

No passwords, no email, no SMTP. Everything — both parent phones and the kiosk — is a **device**.

- **Bootstrap:** on first start with zero devices, the server serves `/setup` (reachable only while the device table is empty) and prints the claim URL + a QR to the container logs. First parent scans, names the device, receives a token.
- **Subsequent:** an enrolled parent device shows a QR under Settings → Add device. Single-use, 5-minute TTL, carries an intended role: `parent` or `kiosk`.
- **Token:** JWT with a `jti` bound to a row in `devices`, 1-year expiry, HttpOnly `SameSite=Lax` cookie. Every request validates `jti` against the table so revocation is instant. Renewed silently on use.
- **Device list** in settings: label, role, last-seen, revoke button.

WebAuthn passkeys would also work here and are arguably the "correct" answer, but for two users on a LAN they add a real chunk of complexity (RP ID handling, attestation, recovery when a phone dies) for little gain over a revocable device token behind a QR. Noted as an option, not recommended for v1.

### 9.3 Scopes

Kiosk tokens are the exposed surface — the device is a phone in a hallway that guests can pick up. Kiosk scope permits exactly: render kiosk state, check in, complete segment, reconcile, heartbeat, SSE. It cannot read stats, edit routines, enrol devices, or see anything about parent accounts.

Children never authenticate. Identity is the NFC token or the tile they tap. A child impersonating a sibling is a social problem, not a security one.

### 9.4 Notifications

**ntfy** for v1. Self-hostable or use ntfy.sh with a random topic; the Android app is a two-minute install and needs no code beyond an HTTP POST. Used for: review pending, stalled segment, run summary, kiosk offline.

Web push is a phase-4 upgrade — since HTTPS is already required for NFC, the main blocker is gone, leaving only a service worker and VAPID keys. Worth it only if ntfy's notification grouping proves annoying in daily use.

---

## 10. Data model

### main.db

```sql
households(id, name, created_at)
devices(id, household_id, label, role, jti, created_at, last_seen_at, revoked_at)
      -- role: parent | kiosk
enrolment_tokens(token_hash, household_id, role, expires_at, consumed_at, created_by)
instances(household_id, db_path, schema_version)
```

### household_<id>.db

```sql
children(id, name, colour, avatar, nfc_token, birth_year,
         display_mode,                                  -- ring | ring_numeric
         sort_order, active)

routines(id, name, kind, schedule_cron, active)
steps(id, routine_id, position, title, icon,
      kind,                                             -- task | gate
      floor_seconds,                                    -- task only
      on_reject_step_id,                                -- gate only, FK -> steps, must be earlier
      active)
      -- CHECK: a gate has no floor_seconds and must not be position 0

pars(id, child_id, step_id, par_seconds, grace_seconds, basis,
     effective_from, computed_from_n, frozen, superseded_at)
      -- basis: none | best | median   (see §5.2 ramp)
      -- append-only history; current par = row where superseded_at IS NULL
      -- a parent reset marks all rows superseded and restarts at basis='none' 

runs(id, routine_id, state, started_by, started_at, closed_at, paused_seconds)
      -- state: open|active|paused|closed|abandoned
run_children(run_id, child_id, state, checked_in_at, completed_at,
             total_seconds, stars)

segments(id, run_id, child_id, step_id, kind, position,
         first_started_at, ended_at,
         elapsed_seconds,                               -- task: SUM(attempts). gate: NULL
         gate_wait_seconds,                             -- gate only, never scored
         par_seconds,                                   -- task only, snapshot at run time
         attempt_count, rejection_count,
         state,                                         -- pending|active|gate_open|done
                                                        --   |skipped|incomplete
         stars, quality_stars,
         resolution,                                    -- gate: approved|rejected|auto_approved
         reviewed_by, reviewed_at, note, reconciled)

segment_attempts(id, segment_id, attempt_no, started_at, ended_at,
                 elapsed_seconds)
      -- task attempts only; segments.elapsed_seconds is the sum.
      -- attempt_no > 1 means the segment was rejected at a gate

events(id, run_id, child_id, type, payload_json, occurred_at, source)
      -- source: kiosk|parent|system ; append-only audit trail

records(child_id, routine_id, step_id NULL, best_seconds, achieved_at, run_id)
      -- step_id NULL = whole-routine record
streaks(child_id, routine_id, current, best, last_qualifying_date)
awards(id, child_id, kind, period_start, period_end, awarded_at)
      -- kind: steady | record | streak_milestone
```

`segments.par_seconds` is a **snapshot**. The weekly recompute must never retroactively rewrite last week's stars. Same reason `pars` is append-only rather than an updated row.

---

## 11. API surface

Parent (device cookie, role `parent`):

```
GET  /                            dashboard
POST /runs                        start a run {routine_id}
GET  /runs/{id}/live              live view (SSE-backed)
POST /runs/{id}/pause             pause / resume
POST /runs/{id}/close             end run; open segments -> incomplete
POST /segments/{id}/skip          parent skip, excluded from par sample
GET  /gates                       open gates awaiting approval
POST /gates/{segment_id}/approve  {quality_stars, note}
POST /gates/{segment_id}/reject   {note} -> child returns to on_reject_step, clock resumes
GET  /routines/{id}/edit
POST /routines/{id}/steps
PUT  /steps/{id}
POST /steps/reorder               {routine_id, ordered_ids}
GET  /pars                        current pars + history per child/step
POST /pars/{id}/freeze
POST /pars/reset                  {child_id, step_id} -> restart §5.2 ramp
GET  /children/{id}/stats
GET  /devices                     list / revoke
POST /devices/enrol               -> QR + single-use token
GET  /events/parent               SSE
```

Kiosk (device cookie, role `kiosk`):

```
GET  /kiosk                       full render for current state
POST /kiosk/checkin               {child_id | nfc_token}
POST /kiosk/complete              {child_id, segment_id, client_ts}
GET  /kiosk/state                 full-state reconcile after reconnect
GET  /events/kiosk                SSE
POST /kiosk/gate-nudge            {segment_id} "Ask again", rate-limited
POST /kiosk/heartbeat
```

Unauthenticated, only while device table is empty:

```
GET  /setup                       bootstrap enrolment
POST /setup/claim
```

---

## 12. Parent configuration UX

Routine editing gets used at 22:40 on a phone with one hand, so it must be fast:

- Drag-to-reorder step list, HTMX POST on drop.
- Duplicate step; disable step (soft, preserves history) rather than delete.
- Adding a gate prompts for which earlier task it returns to on rejection; defaults to the step immediately above it.
- Floor time set with `+/- 15s` steppers, not a text field.
- Par is **not** directly editable — it's derived. The parent can view par history, freeze a par, or reset it (restarting the §5.2 ramp) when something changed in the real world.
- Editing a routine mid-run does not affect the active run.

The par history view matters more than it sounds. When an 8-year-old says "this is impossible now", the parent needs to see the curve and be able to say "you're right" and freeze it.

---

## 13. Phasing

**Phase 1 — walking skeleton.** Routines seeded in SQL. Kiosk renders, tile check-in, run 1 behaviour only (plain elapsed timer, participation stars), DONE advances, run closes. Parent can start, skip, and end a run. No pars, no NFC, no verification, no config UI.

**Phase 2 — par engine.** `par_service` (ramp + weekly recompute), countdown rings, colour states, star scoring, records, freeze/reset. This is where it becomes a game.

**Phase 3 — gates.** Gate step kind, blocking wait panel, approve/reject with clock resume, escalation ladder and auto-approve backstop, notifications. Gates are load-bearing rather than a nicety — until they exist, nothing stops a child tapping DONE on a toothbrush they never picked up.

**Phase 4 — configuration.** Routine/step CRUD, floor times, child management, scheduling.

**Phase 5 — stats & delight.** Streaks, steady award, 30-day charts, scorecard animation.

**Phase 6 — NFC.** Token writing tool, scan loop, fallback handling.

Phase 1 is the risky one — not technically, but because it's where you find out whether an 8-year-old and a 5-year-old engage with it at all. It also collects the run-1 data Phase 2 needs. Ship it plain and watch a few real mornings before building the par engine.

---

## 14. Risks

| Risk | Mitigation |
| --- | --- |
| Par tightens faster than the child improves | ±10%/week cap, hard floor, median basis, parent freeze and reset. Records give durable credit during the transient (§5.4) |
| Novelty wears off in two weeks | Records, streaks, steady award. Treat as a live risk, not a solved one — schedule a week-3 review |
| 5-year-old finds the clock stressful | Ring-only display, no numbers; generous grace; no par at all on run 1 |
| Kids rush and do steps badly | Floor times, parent verification on quality-critical steps, under-floor completions flagged |
| WAN outage breaks name resolution | **Accepted, not mitigated** (§9.1.1). Rare event; local DNS override available later with no code change |
| Cert renewal fails unnoticed | ntfy alert at 21 days to expiry |
| Old Android dies / screen sleeps | Wake lock + charging-stay-awake + heartbeat with ntfy alert if the kiosk goes quiet |
| Run left open indefinitely after a child wanders off | Stalled-segment ntfy nudge; parent ends run. No auto-timeout by design |
| **Parent unavailable when a gate opens** — child stranded, routine halts | Escalating notifications, child-initiated "Ask again", 5-minute auto-approve backstop (§6.4.1). Auto-approval rate surfaced per gate |
| Gates routinely auto-approved, teaching the child checks don't matter | Per-gate auto-approval rate on the dashboard; if high, fix notifications or delete the gate |
| Wifi drops mid-run | Local countdown + queued events + reconcile on reconnect |
| Kiosk token theft | Narrow scope, `jti` checked per request, revocable from parent settings |
| Sibling rivalry turns sour | No cross-child scoring, neutral waiting state, simultaneous scorecard reveal. Side-by-side visible timers accepted as mild, healthy rivalry |

---

## 15. Optional integration: Home Assistant

Not yet decided. Recorded so the interfaces stay integration-shaped rather than needing retrofitting.

### 15.1 What it would buy

| Capability | Value | Phase |
| --- | --- | --- |
| **Actionable notifications** via the HA companion app | Parent approves/rejects a verification from the notification shade without opening the app. Directly targets the "distracted adult" failure mode the whole verification design works around. Replaces ntfy | 3 |
| **Motion sensors** (cheap Zigbee, bathroom + bedrooms) | Real location events. Distinguishes "stalled in the hallway" from "at the sink working". This solves the presence-proof problem that NFC location stickers could not, given a fixed kiosk | 6 |
| **TTS to any speaker** (`media_player.play_media`) | Audio pacing in the bathroom, where the kiosk is out of sight. Currently the clearest gap in the design. One REST call instead of hand-rolled `pychromecast` | 5 |
| **Fully Kiosk integration** | Screen off overnight, wake for the routine, remote reload, brightness. Saves burn-in and power | 4 |

The motion sensors are the only item compelling enough to justify standing HA up from scratch. Everything else is a convenience if it's already running.

### 15.2 Constraints on the integration

- **One-way only.** This app calls HA's REST API. HA never drives run state, never holds authoritative timing, and is never in the request path for check-in or segment completion.
- **Behind an adapter.** `services/notify_service.py` exposes `notify()` and `speak()`; HA is one implementation, ntfy another, no-op a third. Selected by config.
- **Degrades silently.** HA unreachable means no audio and plain notifications. The routine runs unaffected. A failed HA call is logged, never surfaced to the kiosk, and never retried into the critical path.
- **Auth:** long-lived HA access token in env, scoped to a dedicated HA user.
- Motion-sensor data, if adopted, is **advisory only** — it can trigger a nudge or annotate the audit trail, but must never auto-complete a step. Presence is not completion.

---

## 16. Remaining open questions

1. **Does run 1 need repeating per step, or per routine?** If a step is added to an existing routine, that one step has no history while the rest are at steady state. Assume: new steps get their own run-1 ramp independently, and the mixed display (one grey timer among several rings) is acceptable.
2. **Should the weekly recompute run on Sunday night or Friday night?** Sunday means a new par lands on the hardest morning of the week. Friday means it's first tested on a low-stakes weekend.
3. **Is the `steady` award legible enough to a 5-year-old?** "You cleared par every day this week" may be too abstract; a visible weekly dot-grid the child fills in might carry it better than a badge.
4. **Is 5 minutes the right gate auto-approve timeout?** Long enough to be rare, short enough that a child isn't abandoned. Tune from observed gate wait times after Phase 3.
5. **Should a second rejection of the same step escalate differently?** Two failed toothbrushing checks in one run probably means the child needs a hand, not another attempt. Possible: on second rejection the gate converts to "parent comes and does it with them", scoring the step as complete with 0 stars and no further timing.
6. **Can a gate return the child more than one step back?** The schema allows it (`on_reject_step_id` is any earlier step). Is there a real case, or should it be constrained to the immediately preceding task to keep it comprehensible to a 5-year-old?
