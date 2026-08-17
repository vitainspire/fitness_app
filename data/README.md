# Reviewed data tables

Four CSVs. **No AI produces any of them.** The app reads these at startup; two of
them require a qualified physiotherapist's sign-off before the app may run in
production.

Kept as CSV rather than JSON or a seed script on purpose:

- a physiotherapist can open them in Excel or Sheets
- `git log` shows who changed a safety verdict and when — that *is* the provenance
  the requirements ask for
- a reviewer can diff two versions and see exactly what moved

---

## Who fills what

| File | Filled by | Blocks |
|---|---|---|
| `conditions.csv` | Engineering + product | Module 3 |
| `activity_taxonomy.csv` | Engineering, **except two columns** | Modules 4, 12 |
| `safety_grid.csv` | **Physiotherapist** — 60 cells | Modules 13–16 |
| `program_templates.csv` | **Physiotherapist** | Module 14 |

---

## `activity_taxonomy.csv`

Engineering fills everything except the two columns below, which are clinical
judgements and are the reviewer's alone:

| Column | Meaning |
|---|---|
| `beginner_baseline` | The starting target for someone with no training history. In `default_unit`. Walking might be `1.0` (km); push-ups might be `5` (reps). |
| `plausible_max` | The largest value a single entry could sensibly hold. Anything above is kept but excluded from progression, because it is almost always a voice mishearing — "20 push-ups" heard as "80". |

`activity_type` is a **stable code**. It is written into every activity log and
program row; changing it orphans data. `display_name` can be edited freely.

`checkin` needs no baseline or max — it is a boolean daily event.

---

## `safety_grid.csv` — the safety-critical table

One row per condition × activity. **60 cells.** Every cell needs a `verdict`:

| Verdict | Meaning |
|---|---|
| `safe` | Suggest and program normally |
| `modify` | Suggest, but the note explains the adjustment |
| `avoid` | Never suggested, never programmed. A user may still override with a doctor's advice, and it is then never progressed. |

**Every cell must be filled.** A blank is not "probably fine" — CI fails the build
if any cell is empty, and the app suppresses suggestions entirely for a condition
with incomplete coverage. That gate is what prevents the failure where a missing
row silently reads as "nothing to exclude, therefore everything is safe".

`reviewer` and `reviewed_at` are what make this defensible to an app store, a
lawyer, or a user. Fill them.

---

## `conditions.csv`

Three buckets:

- **`supported`** — a program is built, filtered by the safety grid
- **`refer`** — the app shows "please consult a doctor" and provides **no** exercise
  content at all. Manual logging still works.
- Anything not in this file is not shown in the app

Two rows are structural and must not be removed:

- `none` — "None of these", so a healthy user can proceed
- `other_unlisted` — **the fail-closed default.** Anything the user can't find maps
  here and is treated as `refer`. Deleting it reopens the hole the closed list
  exists to close.

---

## Placeholder safety

Until a real reviewer name appears in `reviewer`, seeded rows carry
`UNREVIEWED-PLACEHOLDER`. A startup check **refuses to boot in production** while any
placeholder remains, so invented numbers cannot ship by accident.

Development runs fine with placeholders — that is the point. They unblock building
without ever becoming shippable.
