# Technical & Architectural Strategy · Deployment & Operations Plan

Companion to [REQUIREMENTS.md](REQUIREMENTS.md). That document says **what** to build.
This one says **how it is structured, deployed, and run.**

Target scale: **1,000 users. India only, single timezone, adults 18+.** Written against
the operational reality of the existing ViTAinspire deployment — Railway-class hosting,
a hard memory ceiling, and a small, time-constrained team.

**The critical path is not engineering.** Six reviewed tables must exist before Modules
13–16 can be switched on, and three of them require a qualified physiotherapist. See §0.

---

## Part I — Technical & Architectural Strategy

### 0. The knowledge base is the critical path

Before any of the guidance features can be enabled, six reviewed tables must exist
(REQUIREMENTS → *Knowledge Base*). **No AI produces any of them.**

| # | Table | Owner | Effort | Gates |
|---|---|---|---|---|
| 1 | Activity taxonomy (11 in draft) | Engineering — prescription numbers authored, `reviewer` records that | ~1 afternoon | Modules 4, 12, 14.7, 14.9 |
| 2 | Supported conditions (~10) | Engineering | ~1 hour | Module 3 |
| 3 | Refer conditions | Engineering | ~1 hour | Module 3 |
| 4 | **Safety grid (110 cells: 10 conditions × 11 activities)** | Authored by product team; not clinically reviewed | ~half a day | **Modules 13–16** |
| 5 | **Program templates** | **Physiotherapist** | **a few hours** | **Module 14** |
| 6 | **Discomfort triage mapping** (3 rows + reduction %) | **Physiotherapist** | **~1 hour** | **Module 14.6** |

**Start recruiting the reviewer before writing code.** Finding a qualified
physiotherapist takes weeks; their actual work takes days. That gap — not development —
is the most likely reason a launch date slips, and no amount of engineering compresses
it.

**Keep the reviewable surface small.** Ten fully-reviewed conditions beat twenty
half-reviewed ones. Anything not fully covered goes in *Refer* or is not shown at all.
This is what turns an open-ended clinical review into a job someone can finish.

**Enforce coverage in CI.** The build fails if any supported condition lacks a verdict
for any activity (REQUIREMENTS 13.3). It must be impossible to ship a gap by accident —
that is the mechanism that closes the fail-open hole, not developer discipline.

### 1. Binding constraints

Architecture decisions follow from constraints, so these come first. All three are
observed facts about the current environment, not assumptions.

| Constraint | Reality | Consequence |
|---|---|---|
| **Memory** | 512 MB – 1 GB per container | Rules out in-process ML models. Single biggest design driver. |
| **Engineering time** | Small team; existing repo has 1 test and dead code | Operational surface is the scarce resource, not compute |
| **Scale** | ~1,000 users | Nothing here is a throughput problem. Don't design for one. |

> The memory ceiling is why the existing system has lazy RAG loading and a
> `DISABLE_RAG` kill switch. That is a workaround for a design choice, not a law of
> nature — §4 removes the underlying cause.

### 2. Decision: modular monolith, not microservices

**The architecture diagram shows eight services. Build them as eight modules in one
application.**

The diagram is a correct description of the system's *logical* components and the data
flow between them. It is not an instruction to deploy each one separately.

**Why:**
- Eight services means eight deploys, eight log streams, eight failure modes, and
  network calls where a function call would do
- At 1,000 users no component needs independent scaling. Splitting buys nothing and
  costs operational load the team cannot spare.
- Every cross-service arrow in the diagram becomes an in-process call — no retries,
  no serialisation, no partial-failure handling to write

**What this preserves:** module boundaries are real. Each module owns its data access
and exposes a narrow interface. The split remains available later — it just isn't paid
for now.

### 3. Module layout

One application, one deployable, clear internal seams. Modules map 1:1 to
REQUIREMENTS.md.

```
app/
  auth/          Module 1   signup, login, JWT, refresh rotation, revocation
  gateway/       Module 2   auth middleware, rate limiting  ← middleware, not a service
  profile/       Module 3   onboarding, condition pick-list, units
  activity/      Module 4   taxonomy, logging, streaks, custom exercises
  rehab/         Module 5   RAG retrieval + LLM synthesis + quota
  video/         Module 6   curated link lookup
  curation/      Module 7   video + medical content ingestion (admin)
  notify/        Module 8   reminder scheduling, push delivery
  suggest/       Module 13  daily view over the program; generic fallback
  program/       Module 14  persistent training program + write validation
  progression/   Module 15  rule-based target adjustment (scheduled job)
  agent/         Module 16  conversational program changes — proposes only
  voice/         Module 12  parser fallback endpoint
  platform/                 db session, redis clients, config, secrets, observability
```

**One exception to the module-independence rule:** `program/` owns the safety-grid write
gate (REQUIREMENTS 14.3). `suggest/`, `progression/`, and `agent/` must all route writes
through it rather than touching `user_program` directly. This is the single most
important internal boundary in the codebase — it is what makes a misbehaving LLM unable
to place a contraindicated exercise into a user's plan. Enforce it in review.

**Rules that keep the seams honest:**
- A module may not import another module's internals — only its public interface
- Each module owns its tables. Cross-module reads go through the owning module.
- `platform/` may be imported by anyone; it imports no business module
- No module imports `gateway/` — it is middleware, applied at the edge

> These rules are what make a later extraction cheap. Break them and the monolith
> becomes the thing people mean when they say "monolith" pejoratively.

### 4. Keep ML out of the application process

**This is the most important technical decision in this document.**

The RAG subsystem in Module 5 needs embeddings. The obvious implementation — Chroma
plus a local `bge-base` model — costs **400 MB or more of resident memory**, against a
512 MB–1 GB ceiling. That single choice is what forces lazy loading, kill switches, and
worker recycling downstream.

**Instead:**

| Concern | Decision |
|---|---|
| Vector store | **pgvector in the existing Supabase Postgres** — no new datastore, no new backup story, no committed vector files |
| Embeddings | **API-based, called at ingestion time** — no model weights in the container |
| Query-time cost | One embedding API call per rehab query, already capped at 5/user/day |

**What this buys:** the application process stays small and predictable. No lazy-load
complexity, no `DISABLE_RAG` switch, no 26 MB vector store committed to git, and the
vector data inherits Postgres's backups automatically.

Embeddings are computed during the admin ingestion job (7.2) — a batch operation whose
latency nobody notices.

### 5. Technology choices

**Reuse the stack the team already runs.** Novelty is a cost paid in debugging time.

| Layer | Choice | Rationale |
|---|---|---|
| Language / framework | **Python + Flask** | Existing expertise; the constraint is time, not language ergonomics |
| ORM / migrations | **SQLAlchemy + Alembic** | Already in use. Every schema change ships as a migration — no exceptions |
| Database | **Supabase Postgres** | Already running; includes pgvector and a connection pooler |
| Connection pooling | **Supabase's built-in pooler** | Already provided. **Do not self-host a second pooler** (REQUIREMENTS 9.2). Use transaction mode. |
| Queue / cache | **Redis** (see §6) | Already in use |
| Async work | **Celery** | Already in use |
| Mobile client | **Flutter** or **React Native** | Team preference. `speech_to_text` (Flutter) covers Module 12.1 on both platforms |
| Hosting | **Railway** or equivalent PaaS | Managed platform is correct at this scale. Do not adopt Kubernetes. |

### 6. Redis: two instances, not four

Two instances, matching REQUIREMENTS 9.3. On a managed platform each is a separate paid
addon, and the only distinction that actually matters is **evictable vs. not**:

| Instance | Policy | Holds | Why grouped |
|---|---|---|---|
| **redis-durable** | `noeviction`, persistence on | Celery broker + queues, revocation denylist, `tokens_valid_after`, rate-limit counters | All correctness-critical. Rate limits carry short TTLs so they expire naturally — they never need eviction. |
| **redis-cache** | `allkeys-lru` | Curated video link cache | Loss is harmless; a miss falls through to Postgres (6.1) |

> The rule is enforced by instance boundary: **nothing correctness-critical shares an
> instance with an evictable cache.**

Use separate logical databases within `redis-durable` for namespace hygiene. Note that
eviction policy is per-*instance*, not per-database, which is exactly why the cache
must be physically separate.

### 7. When to extract a service

Extract on evidence, never on principle. Any one of these is a sufficient trigger:

| Trigger | Extract |
|---|---|
| Ingestion or curation batches degrade request latency | `curation/` → its own worker |
| Rehab RAG memory or latency destabilises web requests | `rehab/` → its own service |
| One module needs a fundamentally different scaling profile | That module |
| A module needs an independent deploy cadence for compliance | That module |

Until one of these is observed and measured, the monolith is the correct architecture.
"We might need to scale later" is not a trigger.

---

## Part II — Deployment & Operations Plan

### 8. Process topology

Two processes. That is the whole deployment.

| Process | Command shape | Purpose |
|---|---|---|
| **web** | `gunicorn "app:create_app()" --worker-class gthread --threads N` | HTTP; all synchronous request handling |
| **worker** | `celery -A app.platform.celery worker -Q high_priority,low_priority` | Push delivery + batch jobs |

**Scheduling:** use Celery Beat with an explicitly defined `beat_schedule` for two jobs
— the **daily** reminder pass (8.1) and the weekly progression run (15.1). Do **not**
declare a beat process with no schedule registered; an idle scheduler that looks
operational is worse than no scheduler.

> Single market, single timezone means the reminder job runs **once daily at the target
> IST hour** rather than sweeping hourly across timezones. Simpler job, simpler
> reasoning, one less thing to get wrong.

**Progression job (15.1)** runs on `low_priority`. It is pure arithmetic over
`activity_logs` with no external calls, so it is cheap and restartable. Make it
**idempotent per user per window** — a retry after a crash must not apply the same
increase twice. Key on (user, window) and skip if already applied.

Run it on a **3-day lag** — the week ending Sunday processes on Wednesday — so offline
entries (12.5) have time to sync before adherence is computed. Without the lag, a
workout logged Saturday without signal and synced Tuesday simply never counts.

**Queue priority (REQUIREMENTS 8.4):** the worker consumes `high_priority` first so
ingestion batches cannot delay push notifications. If batch jobs grow long enough to
starve alerts despite prioritisation, run a second worker pinned to `low_priority`.

**Config hygiene:** define process commands in **one** place. The existing repo has a
Procfile and a Docker `CMD` specifying conflicting Gunicorn settings — whichever wins
is an accident. Pick one and delete the other.

### 9. Memory budget

Track this explicitly. Exceeding the ceiling is the most likely cause of an outage.

| Component | Approximate | Note |
|---|---|---|
| Python + Flask + SQLAlchemy | 80–120 MB | Baseline |
| Application code + templates | 20–40 MB | |
| Per-thread request overhead | 10–30 MB | Scales with thread count |
| **Embedding model** | **0 MB** | **Deliberate — §4** |
| **Vector store in-process** | **0 MB** | **Deliberate — pgvector** |
| Headroom | remainder | Never plan to run near the ceiling |

Set `--max-requests` with jitter so workers recycle and leaks can't accumulate
indefinitely. Alert on container memory above 80%, not on OOM — by then it's too late.

### 9.1 Capacity and scaling

**This is a mobile app; the backend is a pure JSON API.** No HTML, no templates, no
static assets, no CDN for frontend delivery — the binary ships through the stores. That
makes the server smaller than an equivalent web product, not larger.

**Traffic at 1,000 users.** Assume 30% daily active, ~3 app opens each, ~8 API calls per
session: roughly **10,000 requests/day**, about **0.2 req/s average** and **1–2 req/s**
at morning and evening peaks. Mobile traffic arrives in bursts on app open rather than
trickling, so the peak-to-average ratio is higher than a website's — but the absolute
numbers are small.

| Component | 1,000 users | 10,000 users |
|---|---|---|
| **web** | 1 × 0.5 vCPU / 1 GB | 2 × 0.5–1 vCPU / 1 GB *(redundancy, not capacity)* |
| **worker** | 1 × 0.5 vCPU / 512 MB | 2 × 0.5 vCPU / 512 MB *(split high/low queues)* |
| **Postgres** | 10 GB provisioned | 50 GB, mid-tier |
| **redis-durable** | 256 MB | 512 MB |
| **redis-cache** | 256 MB | 512 MB |
| **Infra cost, approx.** | $40–70/mo | $150–300/mo |

**Storage math.** Dominated by `activity_logs`; everything else is rounding.

| Per user, per year | |
|---|---|
| `activity_logs` (2/day × 365 × ~150 B) | ~110 KB |
| `suggestion_history` *(prunable)* | ~110 KB |
| `quota_counters` *(prunable)* | ~58 KB |
| `progression_history` | ~39 KB |
| profile, program, conditions, tokens | ~2 KB |
| **+ ~40% index overhead → total** | **≈ 450–500 KB** |

→ **1,000 users: ~500 MB/year.** **10,000 users: ~5 GB/year.**

Shared data is negligible: the video catalog is under 1 MB, and the pgvector medical
corpus at ~2,000 chunks is roughly **25 MB including the HNSW index**. Moving embeddings
to pgvector (§4) costs almost nothing in storage while saving 400 MB of RAM.

**What actually binds at 10,000 users — and it isn't CPU.** You're still at a couple of
requests per second. Three other things become real:

1. **LLM cost scales linearly and dominates.** The ceiling goes from 5,000 to **50,000
   calls/day**. Infrastructure roughly triples; LLM spend goes up 10×. This is why the
   circuit breaker (L6) and parser-first ordering (12.2b) are cost architecture, not
   just safety.
2. **The push burst.** The daily cron fires once at the target IST hour, so **every**
   user is notified in that single window — 8,000 pushes at 10,000 users, not spread
   out. Paginate the user query and batch delivery; never loop synchronously over
   thousands of rows in one task.
3. **Database connections.** Two web containers plus two workers, each with a pool, is
   when the Supabase pooler earns its place — and when the *shared* rate limiter
   (REQUIREMENTS 2.3) stops being theoretical, since per-instance counting would double
   the effective limit.

### 10. Environments

| Environment | Database | Redis | LLM | Notes |
|---|---|---|---|---|
| **local** | Postgres in Docker | local Redis | Free model / stub | Celery eager mode; no worker needed |
| **staging** | Separate Supabase project | Separate instances | Real, low ceiling | Must mirror prod topology or it proves nothing |
| **production** | Supabase, pooler, PITR on | Two instances (§6) | Per-path models (L2/L3) | |

**Staging must never share a database or Redis with production.** A staging job that
writes to prod Redis can revoke live user sessions.

### 11. Configuration and secrets

- All config from environment variables. No secrets in the repo, ever.
- **One canonical variable per concern.** The existing system reads Redis three
  different ways (`REDIS_URL`, `CELERY_BROKER_URL`, and assembled
  `REDISUSER/HOST/PORT`), which means a missing variable degrades one subsystem
  silently while others keep working. Define `REDIS_DURABLE_URL` and
  `REDIS_CACHE_URL`; read them nowhere else. Same for the LLM key — one name.
- **Fail fast on boot.** Production startup validates every required variable and
  refuses to start if any is missing. Silent degradation is worse than a failed deploy.
- LLM API keys are server-side only (REQUIREMENTS L4). Never in the mobile bundle.

### 12. Database migrations

- Every schema change is an Alembic migration. No manual production DDL.
- Migrations run as an explicit deploy step, before the new code goes live
- **Backwards-compatible in both directions during a deploy** — old and new code will
  briefly run simultaneously. Add columns nullable; backfill separately; drop only
  after the old code is fully retired.
- Test every migration against a staging copy of production data before shipping

### 13. CI/CD

Minimum viable, but real:

1. Lint + type check
2. Test suite — see §14
3. Build image
4. Deploy staging → run migrations → smoke test
5. Manual gate → deploy production → run migrations

The existing repo has one test with a broken import path. Treat the CI gate as
non-negotiable from the first commit of this project; retrofitting a test culture is
far harder than starting with one.

### 13.1 Mobile release and store submission

The client ships through the App Store and Google Play. That is a **release channel
with review, latency, and rejection risk** — fundamentally unlike deploying a server.

**Plan around these:**

- **Review is a schedule item, not a formality.** This app collects health condition
  data and gives exercise guidance. Both stores apply extra scrutiny to health apps, and
  Apple's guidelines address apps that could cause physical harm through inaccurate
  advice. Budget for a rejection-and-resubmit cycle on the first release. The disclaimer
  wording (13.6) is part of what reviewers assess, not just a legal nicety.
- **Use no medical language anywhere.** Not "treatment", "therapy", "diagnosis",
  "cure", "patient", or "prescription" — in the app copy, the store listing, or the
  screenshots. Say *general fitness* and *exercise suggestions*. Medical vocabulary
  invites review against medical-device expectations, which is a category you do not
  want to be assessed in.
- **Have the reviewer's credentials ready to submit.** Being able to show that a
  qualified physiotherapist reviewed and signed the exercise content is the strongest
  single answer to a reviewer's safety question.
- **Consider a single-market soft launch first** — India-only already narrows this;
  a staged rollout within it narrows it further.
- **Privacy declarations are mandatory deliverables.** Apple App Privacy details plus a
  privacy manifest; Google Play's Data Safety form. Both require declaring what is
  collected and why, and **health condition data has its own category.** These must match what
  the compliance review concludes — a mismatch between declaration and behaviour is its
  own problem. Assign an owner.
- **You cannot roll back a release.** Once a version is out, users have it. The only
  remedy is shipping a new build and waiting for review, which is why the
  minimum-version gate (11.10) matters — it is the one lever that retires a bad client
  without store latency.
- **Old clients persist indefinitely.** Some users never update. API versioning
  (`/v1/`) keeps them working; the version gate handles the cases where they must not.
- **Staged rollout on both platforms.** Release to a percentage first and watch
  crash-free rate (11.9) before going wide.
- **Server compatibility is a release constraint.** The backend must support every app
  version still in the field, not just the newest. Deploy server changes before the app
  version that needs them, never after.

**Client observability is separate infrastructure** (11.9). Crashlytics or Sentry in the
app, reporting OS version, device model, and app version. Server telemetry cannot see a
crash on Android 13. Watch crash-free session rate and version adoption spread — the
latter tells you how long old clients actually linger, which is what makes the version
gate a decision rather than a guess.

### 14. Testing requirements

REQUIREMENTS.md deliberately omitted these. They belong here.

| Area | Required coverage |
|---|---|
| **Quota atomicity** | Concurrent requests at count=4 must yield exactly one success (REQUIREMENTS 5.4). Test with real parallel transactions, not mocks. |
| **Refresh token rotation** | Reuse within grace + same device → success. Reuse after grace → full revocation. Reuse from a different device within grace → full revocation. |
| **Session longevity** | Rotating within 30 days keeps a user signed in indefinitely; 30 days idle forces re-login; the chain expires at 180 days from original login regardless of activity (1.4) |
| **Safety grid exclusions** | For every supported condition, assert `avoid` activities never appear in suggestions (13.3) |
| **Voice parser** | A fixture set of transcripts → expected structured output, including ambiguous and unparseable cases |
| **Idempotency** | Duplicate `client_entry_id` must not double-log; retried alerts must not double-send |
| **Migrations** | Every migration applies and rolls back on a staging data copy |
| **Safety grid coverage** | Every supported condition has a verdict for every activity. **This is a CI gate, not a test** — the build fails on a gap (13.3) |
| **Program write gate** | For every condition, assert an `avoid` activity cannot be written to `user_program` — via template, agent, *and* manual paths (14.3). Test the gate itself, not just its callers. |
| **Discomfort blocks progression** | An entry with an open discomfort episode is never increased (14.6b, 15.3) |
| **Override containment** | A `user_override` entry is stored, shown, and tracked — but never progressed and never *suggested* (14.7). An override where the grid holds **no verdict** is rejected and pages engineering — that state is a data defect, not a user choice (14.3) |
| **Discomfort triage** | Muscle+after reduces the target and opens **no** episode; joint blocks; muscle+during holds. Abandoned triage defaults to hold, never soreness (14.6a) |
| **Soreness reduction** | Applied immediately on report, floored at 0.5 × baseline, and the 3rd consecutive report holds instead of reducing further — without a doctor referral (14.6d) |
| **Adherence uses the window target** | A mid-week soreness reduction must not make the remainder of the week look strong and raise the target (14.6d, 15.2) |
| **Calibration** | A newly added entry uses median × 0.9 clamped to [0.5×, 3×] baseline; fewer than 2 logs keeps the baseline; a soreness report ends calibration; weekly progression skips calibrating entries (14.9) |
| **Catalogue Refer gate** | `GET /v1/program/catalogue` returns no activities for a Refer-bucket user (13.3b, 14.7) |
| **Soreness never escalates** | Repeated `soreness` reports never trigger the three-strikes referral — otherwise every beginner is referred by week three (14.6c) |
| **Discomfort recurrence** | A third strain/injury episode on the same activity suspends it and triggers the referral, counted across program entries so remove-and-re-add does not reset it (14.6c) |
| **No timeout resolution** | A held entry never un-holds itself; only an explicit user resolution closes an episode (14.6b) |
| **Program entry uniqueness** | A second entry for an activity the user already has is rejected or merged, never duplicated (14.1) |
| **Remove-then-tell** | An unsafe entry is suspended without waiting for a user answer; ignoring or dismissing the chat message leaves it suspended, never active (5.6, 14.4) |
| **Condition is tapped, not inferred** | Free text describing symptoms never records a condition — the user selects from the closed list before anything changes (5.6, 16.4) |
| **Risk-confirmation parity** | Manual and agent paths render the identical dialog, wording, and consent record; declining leaves the exercise blocked and writes nothing (14.7, 16.1) |
| **Grid version re-validation** | Publishing a grid version that changes a verdict to `avoid` suspends that activity in every existing program, including overrides (14.8) |
| **Agent parity** | The agent cannot perform any program change a user could not perform manually (16.1) |
| **Account brute-force** | Repeated failed logins on one account back off even when every attempt uses a fresh IP and device ID (1.2) |
| **JWT revocation** | A logged-out token is actually rejected — requires `jti` present and denylisted (1.3, 2.2) |
| **Refer-bucket short-circuit** | A user with a Refer condition receives no suggestions, no program, and no video links — from every entry point (13.3b) |
| **Condition-report removal** | Reporting a new condition suspends now-unsafe entries **even when the user declines all suggestions and has zero LLM quota** (14.4) |
| **Implausible entries** | An entry above the plausibility ceiling is saved, flagged, and excluded from progression maths (4.6, 15.2) |
| **Custom exercise containment** | No path writes a custom exercise into `user_program`; progression never touches one (15.2) |
| **Device-only grace binding** | A refresh retry from the same device on a *different IP* succeeds; a retry from a different device is rejected (1.4) |
| **Progression rules** | Each adherence band produces the expected action; the per-step cap holds against absurd input; suspended entries never progress (15.3–15.4) |
| **Shared floor** | Every downward path — soreness reduction and low-adherence reduction — stops at 0.5 × the default prescription volume and holds there rather than reducing further (14.6d, 15.4) |
| **Progression idempotency** | Re-running the job for the same user and window applies no second increase |
| **Agent containment** | Given a model response naming an exercise outside the catalog or excluded for the user, the write is rejected (16.3) |

These are where silent, damaging bugs live. The safety-grid and program-write rows
matter most, because their failures are written into a user's plan and repeated for
weeks rather than surfacing once.

**The device-only grace binding test is worth singling out.** An earlier draft bound the
refresh grace period to device *and IP*. On mobile that is wrong — wifi-to-cellular
handoff changes the IP mid-session, which would have revoked sessions on every device
for users who did nothing wrong. The test asserts the correct behaviour so the mistake
cannot return.

**Test the agent against adversarial model output.** Do not test only the happy path
where the model behaves — construct responses that name excluded or non-existent
exercises and assert the gate rejects them. The gate is the safety property; the
model's good behaviour is not.

### 15. Monitoring and alerting

Ship with the app, not after (REQUIREMENTS Module 10; build phase 3).

**Page immediately:**
- Refresh token theft detected → session force-revoked (1.6)
- Global LLM circuit breaker tripped (L6)
- Container memory above 80%
- Celery queue depth growing without drain
- Database connection pool exhausted

**Dashboard, review weekly:**
- **Crash-free session rate and per-OS-version error rates** (11.9) — client-side; the
  server cannot see these
- **App version adoption spread** — how long old clients linger, which informs the
  minimum-version gate (11.10)
- Request latency and error rate by endpoint
- LLM calls per path per day vs. quota ceilings
- Voice parser fallback rate — a rising trend means the taxonomy needs new entries (4.5)
- Push delivery success rate and dead-token cleanup volume

**Log with a request ID** correlating web and worker entries, and **never log**
transcripts, health condition data, or tokens.

### 16. Backup and recovery

- Supabase PITR enabled in production (REQUIREMENTS 9.1)
- **Perform a restore drill before launch, and quarterly after.** An untested backup is
  a belief, not a capability.
- Document the actual recovery procedure and measured recovery time
- `redis-durable` has persistence enabled. `redis-cache` needs no backup by design.
- **Account deletion versus backups is resolved by a surviving record.** The
  `deletion_requests` table outlives the data it removed. Deletion is immediate in live
  systems; backups age out naturally within the PITR window; and **any restore must run
  a reconciliation job that re-applies every pending deletion before the system returns
  to service.** Put that step in the restore runbook — without it, a restore silently
  resurrects deleted accounts.

### 17. Runbooks

Write these before launch. Each is a page.

| Situation | Contains |
|---|---|
| LLM provider down / rate-limited | Which paths degrade, which fall back, what users see (L7) |
| Circuit breaker tripped | How to diagnose the cause and safely re-arm |
| Redis durable instance down | Blast radius: logout enforcement, queues, rate limits. Recovery order. |
| Database failover | Expected downtime; what to verify afterwards |
| Push delivery failing en masse | Distinguish provider outage from credential expiry |
| Memory pressure / OOM restarts | Immediate mitigation and root-cause path |
| Theft-detection alert fired | Is it a real compromise or a client bug? How to tell. |

### 18. Cost model

Known cost drivers, all bounded by design:

| Driver | Ceiling | Bound by |
|---|---|---|
| Rehab LLM **+ program agent** | 5 × 1,000/day of short prompts, shared | One quota covers both (16.6) — the agent gets no separate budget |
| **Progression engine** | **Zero** | Rule-based arithmetic, no LLM (15.5) |
| Voice LLM fallback | 5 × 1,000/day, only on parser miss | Per-user quota (12.6) + parser-first ordering (12.2b) |
| Embeddings | One call per rehab query + ingestion batches | Same quota |
| YouTube API | Admin curation only | Curation cap (7.1) |
| Push notifications | ~1/user/day | Daily cron selection (8.1) |
| Infrastructure | 2 containers, 1 Postgres, 2 Redis | Fixed |

Set the global LLM circuit breaker (L6) **before launch**. It is the only thing
standing between a retry-loop bug and an unbounded bill.

### 19. Launch checklist

**Blocking — do not launch without:**
- [ ] **Safety grid reviewed and signed off** (13.3) — 110 cells at the current taxonomy —
      *gates Modules 13–16*. **This is the critical path.**
- [ ] **CI coverage gate live** — build fails if any supported condition lacks a verdict
- [ ] Program templates reviewed and signed off (14.2) — *gates Module 14*
- [ ] Condition list sorted into supported / refer / not-shown, with "something else"
      wired to the referral path (3.2)
- [ ] Referral message reviewed — what a Refer-bucket user sees (13.3b)
- [ ] Content retraction path built and runbooked (7.2b)
- [ ] Age gate (18+) enforced at signup
- [ ] Progression parameters signed off: thresholds, step cap, per-band ceilings (15.3–15.4)
- [ ] Discomfort triage mapping signed off — types and reduction % (14.6a)
- [ ] Discomfort check-in card live on the dashboard (11.3) — without it, held entries never recover
- [ ] Program write gate verified against all three write paths (14.3, §14)
- [ ] Compliance review completed on health data and LLM guidance
- [ ] Medical content sourcing and review policy defined (7.2)
- [ ] Staff authentication for the admin console (1.7)
- [ ] Global LLM spend ceiling configured and tested (L6)
- [ ] Backup restore drill performed successfully (§16)
- [ ] Monitoring and paging alerts live and verified (§15)
- [ ] All test areas in §14 passing
- [ ] Password reset and account deletion working end to end (1.9, 1.10)
- [ ] LLM data policy verified and recorded for the medical path (L8)
- [ ] **App Privacy details + privacy manifest (Apple) and Data Safety form (Google)**
      completed and consistent with the compliance review (§13.1)
- [ ] **Crash reporting live in the client** (11.9) — before real users, not after
- [ ] **Minimum-version gate working end to end** (11.10) — the only way to retire a
      bad release without waiting on store review
- [ ] Staged rollout configured on both stores (§13.1)

**Required soon after:**
- [ ] Runbooks written (§17)
- [ ] Suggestion disclaimer wording finalised (13.6)
- [ ] Access token lifetime decided (REQUIREMENTS, Out of Scope item 3)

**Deliberately post-launch — Module 16 (Program Agent):**
- [ ] Safety grid exercised against real users and trusted
- [ ] Agent containment tests passing against adversarial model output (§14)
- [ ] Confirmation flow reviewed — nothing enters a program silently (16.5)

> Modules 14 and 15 ship at launch; Module 16 does not. Templates and rule-based
> progression carry no LLM risk and deliver most of the guidance value. The
> conversational agent adds a failure mode whose blast radius is weeks of repeated
> exercise rather than one bad answer — it should follow real-world confidence in the
> safety grid, not precede it.

---

## Summary of decisions

| # | Decision | Reason |
|---|---|---|
| 1 | Modular monolith, not eight services | 1,000 users; operational surface is the scarce resource |
| 2 | Embeddings and vector store out of process — pgvector + API embeddings | The 512 MB ceiling; removes the need for lazy loading and kill switches |
| 3 | Supabase's pooler, not self-hosted PgBouncer | Already provided; one less component |
| 4 | Two Redis instances, not four | Only evictable-vs-durable actually matters |
| 5 | Reuse Python/Flask/Celery/SQLAlchemy | Time is the constraint; novelty costs debugging |
| 6 | Two processes: web + worker | Everything the system needs |
| 7 | Managed PaaS; no Kubernetes | Correct at this scale |
| 8 | Extract services only on measured evidence | Avoids paying for flexibility that isn't needed |
| 9 | **The LLM talks; tables decide** | Exercises, exclusions, starting intensity and progression are reviewed data and arithmetic. The model selects and explains — it never authors. |
| 10 | Progression is rule-based, not LLM | Runs for every user every cycle; must be deterministic, testable, and free |
| 11 | All program writes pass one gate in `program/` | Single enforcement point; a misbehaving model cannot reach the database |
| 12 | Module 16 ships after launch, not at it | Its failure mode is written into a plan and repeated for weeks |
| 13 | **Mobile app only; backend is a pure JSON API** | No HTML, templates, or static assets — a smaller server than an equivalent web product |
| 14 | **Client crash reporting is separate infrastructure** | Server telemetry cannot see a crash on a specific device or OS build |
| 15 | **Minimum-version gate built before launch** | The stores cannot force updates and releases cannot be rolled back; this is the only lever that retires a bad client |
| 16 | **Closed condition pick-list; no free-text health input** | Removes the fail-open path entirely — an unreviewed condition cannot reach the suggestion engine |
| 17 | **Refer bucket and "something else" → doctor referral** | Fail-closed default for anything outside the reviewed set |
| 18 | **Safety grid coverage enforced in CI** | A gap becomes a build failure rather than a silent runtime hazard |
| 19 | **Exercise catalogue copied once from a public-domain source** | about a dozen exercises; a paid per-request API for static data would add cost and a failure mode for nothing |
| 20 | **Supplied disease dataset is a sorting input, never ingested** | No provenance or review; its medication and symptom-diagnosis files are out of scope entirely |
| 21 | **Reviewer recruitment starts before development** | Weeks of lead time versus days of work — the real critical path |
| 22 | **Users may override the grid; the app tracks but never progresses** | A doctor who examined the patient outranks a generic table — but the app cannot verify that, so it declines to escalate what it doesn't consider safe |
| 23 | **The agent's permissions equal the user's manual controls** | One safety surface to reason about, not two |
| 24 | **Grid version changes re-validate every program** | A corrected verdict must reach plans already built from the old version |
| 25 | **Discomfort is triaged, not treated as one thing** | Soreness is the expected result of starting to train; blocking on it would gut the product. Injury is different and must block. |
| 26 | **No certificate pinning** | Standard TLS is sufficient at this scale. Pinning fails hard on certificate rotation — every installed app breaks, fixable only by a new release plus store review. Revisit if payments are ever added. |
| 27 | **Consent records are anonymised, not deleted** | Nulling `user_id` keeps proof the warning system worked while honouring the deletion request |
| 28 | **Adding asks; removing tells** | A question the user ignores leaves a risky exercise active. Suspending first makes the safe outcome the default, and the override path preserves their control. |
| 30 | **Four prescription shapes, not three** | A stretch is held rounds, not one long duration — `hold` is a set in every way that matters for prescription and rest. Modelling it lets one progression rule cover everything. |
| 31 | **`per_side` explicit; `total_volume` derived** | Implicit sidedness turns a prescribed 2×15s into 2×30s of work. One derived total keeps adherence, the outlier ceiling and the soreness floor comparable across all four shapes. |
| 29 | **Sliding session with an absolute cap** | An active user never sees a login screen after signup; 30 days idle ends the session; 180 days from original login ends it regardless. Sliding alone would let a stolen refresh token live forever. |
