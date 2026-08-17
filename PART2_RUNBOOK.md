# Part 2 Runbook

**ViTAinspire — Engineering Handoff**

The non-agent half of what's left to build — the parts that don't need any AI/RAG design,
just steady implementation.

You don't need backend experience for this. **Claude Code writes the code** — your job is
to hand it the brief, watch what it builds, test it yourself, and manage the pull request.
Here's exactly how the loop works, start to finish.

## How it works

1. **Clone the repo** — Ayush shares the GitHub link
2. **Open Claude Code** in the project folder
3. **Point it at the brief** — give it `PART2_HANDOFF.md` and one task
4. **Test it yourself** — click through it, don't just trust it
5. **Push a branch, open a PR** — one task, one PR
6. **Ayush reviews** — merges if it's good

```bash
git clone <the repo url Ayush sends you>
cd fitness_app
# open this folder in Claude Code, then say:
# "Read PART2_HANDOFF.md and implement the 3.4 task"
```

## Before touching anything

- Never let a user type in their own health condition as free text — it always comes
  from the existing fixed list.
- Never change the exercise/safety data itself (the CSV files under `data/`) — those
  numbers were reviewed on purpose and aren't yours or Claude's to edit.
- Never log or print a user's health condition, password, or login token anywhere.
- No real passwords or database URLs in any file that gets committed.
- Stay out of anything related to the chatbot, the "agent," voice logging, or
  auto-progression — that's the other half of the work, being built separately, and
  touching it will cause conflicts.

## Your task list (12 tasks)

### 3.4 — Quick profile edit `Small`
Right now, changing your age or goal means redoing the entire signup form. This adds a
simple "edit and save" instead.
Spec: `REQUIREMENTS.md §3.4`

### 4.3 — Longer trend view `Small–Medium`
The dashboard only shows the last 7 days. Add a 30-day view alongside it.
Spec: `REQUIREMENTS.md §4.3`

### 4.4 — Custom exercises `Medium`
Let someone log something outside the built-in 12 exercises — like "Swimming, 30 min" —
purely for their own history. It should never be suggested back to them or anyone else.
Spec: `REQUIREMENTS.md §4.4`

### 4.5 — Unmatched phrase log `Small`
When the app can't understand something someone logged, save it somewhere the team can
review later — instead of just silently dropping it.
Spec: `REQUIREMENTS.md §4.5`

### 1.7 — Staff login `Medium`
A separate login just for the internal team, not regular users — needed before the next
task can go live for real.
Spec: `REQUIREMENTS.md §1.7`

### 7.1 — Turn on video curation `Small`
The "add a YouTube link to this exercise" feature already works in testing — it just
needs staff login (above) before it's allowed to run for real users.
Spec: `REQUIREMENTS.md §7.1`

### 7.3 — Internal admin page `Medium`
A simple internal screen so the team can manage video links by clicking, instead of
typing raw commands.
Spec: `REQUIREMENTS.md §7.3`

### 8 — Notifications & reminders `Large`
Actual push notifications — deciding who's due a reminder, sending it, and keeping track
of each phone. The biggest piece here besides the mobile app itself; fine to split into
several smaller PRs.
Spec: `REQUIREMENTS.md §8`

### 9 — Real database setup `Medium · Ops`
Moving off "one laptop's database" onto something real that survives a restart. Mostly a
hosting/setup decision — check with Ayush before spending money on it.
Spec: `REQUIREMENTS.md §9`

### 10 — Monitoring `Medium`
A way to find out something broke without someone having to notice it manually — logs in
one place, an alert if errors spike.
Spec: `REQUIREMENTS.md §10`

### 11 — The real mobile app `Its own project`
What you've been testing is a website dressed up like an app. This is rebuilding it as a
real, installable app — auth, dashboard, logging, push notifications, all of it. Bigger
than everything else on this page combined — treat it as a separate project, not one more
task in the queue.
Spec: `REQUIREMENTS.md §11`

### 14.9 — Calibration `Medium`
When someone adds their own exercise, it starts at a generic guess. After their first few
real sessions, quietly adjust the target to match what they actually do — plain math, no
AI involved.
Spec: `REQUIREMENTS.md §14.9`

---

Full technical detail — file paths, exact conventions, "done when" criteria — lives in
`PART2_HANDOFF.md` in the repo. That's the file you actually hand to Claude Code; this page
is just your map of it.
