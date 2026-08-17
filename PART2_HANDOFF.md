# Part 2 Work — Handoff to Claude Code

**If you are Claude Code reading this: this document is your brief.** The person running
you does not know backend development and cannot review your code line-by-line — they
will review by *testing what you built*, and a separate reviewer (the project owner) will
read the diff on GitHub before merging. That means two things:

1. **Verify everything yourself before saying it's done.** Run the server, hit the actual
   endpoint with curl or a browser, show real output. Don't say "this should work."
2. **Stay inside the scope below.** If a task tempts you to touch something on the
   "do not touch" list, stop and explain why in the PR description instead of doing it.

Work one task at a time, one branch per task, one PR per task. Do not bundle multiple
tasks into one PR — the reviewer needs to approve them independently.

---

## 1. What this project is

ViTAinspire — a beginner-focused rehab/fitness app for adults 18+, India only, aimed at
~1,000 users initially. A user picks a health condition from a closed list (never free
text), and the app suggests exercises filtered through a safety table so nothing unsafe
for their condition gets recommended.

Full spec: `REQUIREMENTS.md` (numbered modules/submodules — this doc's tasks reference
those numbers). Technical/ops rationale: `STRATEGY_AND_OPS.md`. Read the specific section
for your task before starting; this handoff summarizes, it doesn't replace either doc.

---

## 2. Rules that override every task below

These are not suggestions. If a task's obvious implementation would violate one of these,
stop and flag it in the PR instead of building it anyway.

- **Never let a user submit free-text health data.** Conditions come from
  `data/conditions.csv` only. No task here should add a text box for symptoms, conditions,
  or diagnoses.
- **Never invent or edit exercise safety verdicts.** The files `data/safety_grid.csv`,
  `data/activity_taxonomy.csv` encode human-reviewed decisions (see the `reviewer` column
  — it honestly says "product-team (not clinically reviewed)", meaning a real clinician
  still needs to sign off before launch). None of the Part 2 tasks below require changing
  these files' *content*. If a task seems to need a new verdict or a new exercise, stop —
  that's a decision for the project owner, not something to infer.
- **Never log health conditions, auth tokens, or full request/response bodies.** Standard
  `print`/log statements for debugging errors are fine; logging what condition a user
  picked, or their JWT, is not.
- **No secrets in code or commits.** API keys, database URLs with passwords, etc. go in
  environment variables (`.env.dev` is already gitignored — never remove it from
  `.gitignore`, never commit a real `.env.dev`).
- **Adults only, 18+.** Don't touch the age gate in `app/profile/service.py`.

### Out of scope for this handoff — do not touch

Someone else is building these in parallel. Changes here will conflict with that work:

- `app/catalog/*` safety-grid/taxonomy **logic** (the CSV *content* is also off-limits, see
  above) — Modules 13, 14.8
- Anything resembling a chatbot, LLM call, or "agent" — Modules 5, 16
- Voice logging, speech-to-text, on-device parsing — Module 12
- Automatic progression / auto-increasing targets — Module 15

If you're not sure whether a file belongs to one of these, check whether it's imported by
`app/profile/service.py`'s `_tier_slice`, `_worst_verdict`, or `build_program` — those are
the safety-grid-adjacent core and are off-limits for edits (reading them for context is
fine and often necessary).

---

## 3. How the codebase is laid out

```
app/
  auth/        signup, login, tokens, password reset, account deletion
  gateway/     rate limiting, JWT verification (middleware, runs before every route)
  profile/     onboarding, conditions, program, activity logging, dashboard — routes.py
               is the HTTP layer, service.py is where the actual logic lives, models.py
               is the SQLAlchemy tables
  catalog/     the safety grid / taxonomy tables and the CSV loader — OFF LIMITS (§2)
  devconsole/  the web prototype SPA (templates/app.html) standing in for the real mobile
               app — single HTML file, vanilla JS, Tailwind via CDN, no build step
  platform/    config, db session handling, Redis clients, error classes
  static/      exercise demo GIFs served directly by Flask

data/          CSVs that seed the database — activity_taxonomy.csv, conditions.csv,
               safety_grid.csv (off-limits content, §2)
scripts/       one-off data-generation scripts (build_safety_grid.py, fetch_gifs.py, ...)
```

**Pattern for almost everything:** `routes.py` parses the request and calls a function in
`service.py` inside `session_scope()`; `service.py` does the actual work and raises
`ValidationError` / `NotFound` / `Forbidden` / `Conflict` (from
`app.platform.errors`) for anything that goes wrong — these are already translated to the
right HTTP status by the app, so just raise them, don't build your own error responses.

Look at `app/profile/service.py`'s `add_program_entry` / `remove_program_entry` /
`list_catalogue` (and their routes in `app/profile/routes.py`) as a working example of the
full pattern — model, service function, route, then the matching frontend fetch call in
`app.html`. New features should look like that, not introduce a new style.

**Frontend:** `app/devconsole/templates/app.html` is the whole app — one file. There's an
`api(method, path, body)` helper already wired for auth headers, a `toast(msg)` helper for
confirmation messages, and an existing modal pattern (search for `catalogue-modal` or
`demo-modal`) to copy for any new popup. Don't introduce a second frontend framework or
build tool — stay consistent with plain JS + Tailwind classes already in use.

---

## 4. Running and testing locally

```powershell
# one-time: copy .env.example to .env.dev and fill in a local Postgres URL
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m app.catalog.loader     # seeds conditions/taxonomy/safety-grid from data/*.csv
.\run.ps1                        # starts the dev server on :5057, kills stale port owners first
```

You will see warnings like `Redis unavailable ... using an IN-MEMORY fake` — that's
expected in dev, not a bug you need to fix.

**Test with curl**, not by eyeballing code — e.g.:

```bash
curl -s -X POST http://127.0.0.1:5057/v1/auth/signup -H 'Content-Type: application/json' \
  -H 'X-Device-Id: test' -d '{"email":"you@example.com","password":"testpass1234"}'
```

For anything with a UI component, actually open `http://127.0.0.1:5057/app` in a browser
and click through it before calling a task done.

---

## 5. Git / PR workflow

1. `git checkout -b task/<short-name>` (e.g. `task/profile-edit`)
2. Make the change, test it for real (§4)
3. Commit with a message describing *why*, not just what
4. Push the branch, open a PR against `main`
5. In the PR description: what you built, how you tested it (paste the actual curl
   output or a screenshot), and explicitly confirm you didn't touch anything in the §2
   off-limits list
6. Wait for review — don't merge your own PR

One task per PR. If you finish early, open the next task as a new branch, don't pile onto
an open PR.

---

## 6. The tasks

Each one names its REQUIREMENTS.md section for full detail. Sizes below are rough —
**Module 11 is enormous compared to the rest and probably deserves its own dedicated
stretch of time rather than being done "alongside" the others.**

### 3.4 — Quick profile edit (small)
Right now, changing your age/goal/unit-preference means resubmitting the entire onboarding
form. Add a `PUT /v1/profile` endpoint that updates just those fields without touching
conditions or rebuilding the program. Model it on how `/v1/profile/onboarding` already
works in `app/profile/service.py`, but it should be a much smaller function — no condition
handling, no `build_program` call.
**Done when:** a PUT with `{"age": 31}` changes only the age, and `GET /v1/profile`
reflects it immediately.

### 4.3 — Longer trend view (small–medium)
The dashboard chart (`weekly_volume` in `service.py`, `h-bars` in `app.html`) only covers 7
days. Add a monthly or 30-day view alongside it — same shape of data, longer window.
**Done when:** the dashboard can show a 30-day trend, and the existing 7-day view still
works unchanged.

### 4.4 — Custom exercises (medium)
REQUIREMENTS.md §4.4. Let a user log something outside the curated 12 activities (e.g.
"Swimming — 30 min") as free text, but — important — **never surfaced by Suggestions or
the Program**, only visible in their own log history. This is the one place free text is
allowed, precisely because it's inert: nothing reads it back as guidance.
**Done when:** a custom entry appears in Recent Logs but never appears as a suggestion,
never affects streaks' "outstanding" logic in a way that implies it was prescribed.

### 4.5 — Unmatched phrase log (small, depends on 4.4 or can be built standalone as an admin-visible table)
REQUIREMENTS.md §4.5. A place to record inputs the app couldn't match to a known
activity, purely for the team to review later — no user-facing behavior change required
beyond not silently dropping the phrase.

### 7.1 — Turn on video curation in production (small)
The endpoint `PUT /v1/admin/videos/<activity_type>` in `app/profile/routes.py` already
works, but is hard-blocked whenever `cfg.is_production` is true, pending staff auth (1.7).
**Do not remove that block.** This task is really "build 1.7 first" — see below.

### 1.7 — Staff authentication (medium)
REQUIREMENTS.md §1.7. A separate authenticated role for internal staff (not app users) so
7.1's guard has something real to check instead of "not production." Keep it simple: a
separate table/flag distinguishing staff accounts, checked by a decorator similar to
`require_auth` in `app/gateway/middleware.py`.
**Done when:** the video-curation endpoint works in production for a staff-flagged account
and still 403s for a normal user account.

### 7.3 — Internal admin console (medium, depends on 1.7)
A small internal-only screen (doesn't need to be pretty) for staff to see the video
catalogue and paste in links, instead of curling the API by hand.

### Module 8 — Notifications & reminders (large)
REQUIREMENTS.md §8. Four pieces: a scheduler that decides who's due a reminder, a
high-priority worker that actually sends the push, device-push-token lifecycle
(register/expire), and a low-priority batch worker for less time-sensitive notifications.
This is genuinely the second-largest task here after Module 11 — expect it to take a
while, and it's fine to split it into 4 separate PRs (one per submodule) rather than one
giant one.

### Module 9 — Real database/Redis setup (medium, ops-heavy not code-heavy)
REQUIREMENTS.md §9. Move from "one dev Postgres, Redis faked in memory" to real deployed
instances — two separate Redis instances (`redis-durable`, `redis-cache`, already
referenced by name in `app/platform/redis_clients.py` and `config.py`), a Postgres setup
that isn't just a laptop. This is mostly infrastructure/deployment work, not new
application code — coordinate with the project owner on where this actually gets hosted
before starting, since it involves real cost decisions.

### Module 10 — Observability (medium)
REQUIREMENTS.md §10. Centralized logging and basic alerting so a failure surfaces without
someone manually reading log files. Doesn't need to be fancy — a hosted logging service
plus an alert on error-rate spikes is enough to satisfy this.

### Module 11 — The real mobile app (very large — its own project)
REQUIREMENTS.md §11. Rebuilding what `app/devconsole/templates/app.html` currently
prototypes as a real Flutter or React Native app: auth screens, onboarding, dashboard,
logging, video links, push notifications (depends on Module 8), crash reporting, and
forced-update handling for old versions. **Recommend tackling this as a separate, later
initiative** rather than folding it into the same PR queue as everything else above — it's
bigger than all the other Part 2 tasks combined.

### 14.9 — Calibration (medium)
REQUIREMENTS.md §14.9. When someone adds their own exercise (via the catalogue — see
`add_program_entry` in `service.py`), it starts at a generic baseline
(`default_sets × default_amount`). After their first 3 logged sessions (or 7 days,
whichever comes first), replace that target with `median(logged volumes) × 0.9`, clamped
between 0.5× and 3× the original baseline. Skip calibration entirely if fewer than 2 logs
exist when the window closes — just keep the baseline. This is plain arithmetic on
existing log data, no model involved.
**Done when:** a newly added activity's target visibly changes after 3 logged sessions,
matching the formula, and does *not* change before that window closes.

---

## 7. If something in this document turns out to be wrong

The codebase may have moved since this was written. If what you find in the code
contradicts something stated here, trust the code and REQUIREMENTS.md over this handoff,
and say so explicitly in your PR rather than silently picking one.
