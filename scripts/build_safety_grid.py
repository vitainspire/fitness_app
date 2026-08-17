#!/usr/bin/env python3
"""Generate the condition x activity safety grid.

Authored by the product team, not a clinician. The `reviewer` column records that
truthfully rather than implying a review that did not happen — if anyone later asks
"who decided this?", the answer is in the data.

Two principles applied throughout:

  1. Bias to caution. Where a movement is plausibly implicated in a condition it is
     `modify`, not `safe`. Where it is a recognised contraindication it is `avoid`.
     A missing suggestion costs nothing; a harmful one does not.

  2. Every verdict carries a `note` saying WHY. A future reviewer can then correct a
     specific judgement instead of re-deriving all 110 from scratch.

Re-run to regenerate after editing RULES.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

AUTHOR = "product-team (not clinically reviewed)"
TODAY = date.today().isoformat()

ACTIVITIES = [
    "walk", "neck_isometric", "shoulder_circles", "lower_back_side_stretch",
    "glute_bridge", "dead_bug", "hamstring_stretch", "quad_stretch",
    "calf_stretch", "ankle_circles", "wrist_circles", "running",
]

CONDITIONS = [
    "chronic_back_pain", "herniated_disk", "degenerative_disc", "spinal_stenosis",
    "spondylosis", "arthritis_hip", "bursitis", "carpal_tunnel",
    "sprain_strain", "gout",
]

# (condition, activity) -> (verdict, note)
# Anything not listed defaults to `safe` — see DEFAULT_NOTE. Only departures from
# "this is a gentle movement with no specific concern" are recorded here.
RULES: dict[tuple[str, str], tuple[str, str]] = {}


def rule(conditions, activities, verdict, note):
    for c in conditions:
        for a in activities:
            RULES[(c, a)] = (verdict, note)


SPINE = ["chronic_back_pain", "herniated_disk", "degenerative_disc",
         "spinal_stenosis", "spondylosis"]
DISC = ["herniated_disk", "degenerative_disc"]

# --- Spine -----------------------------------------------------------------
# The seated floor hamstring stretch is a loaded flexed-spine position. This is the
# single clearest contraindication in the set, and it is exactly the kind that looks
# harmless: the upstream source describes reaching for the ankle with the leg
# extended. Flagged in the source audit as needing a chair substitute.
rule(DISC + ["spinal_stenosis"], ["hamstring_stretch"], "avoid",
     "Seated floor version is a loaded flexed-spine position. Substitute a supine or "
     "chair variant before offering this for a disc or stenosis diagnosis.")
rule(["chronic_back_pain", "spondylosis"], ["hamstring_stretch"], "modify",
     "Use a supine (lying) or standing variant rather than the seated floor reach.")

rule(SPINE, ["lower_back_side_stretch"], "modify",
     "Seated and gentle, but it is direct lateral spine flexion. Keep the range small "
     "and stop at any symptom change.")
rule(SPINE, ["dead_bug"], "modify",
     "Commonly used for spinal stability, but requires a neutral spine held throughout. "
     "Reduce range if the lower back lifts off the floor.")
rule(["spinal_stenosis"], ["glute_bridge"], "modify",
     "Bridging extends the lumbar spine, which is the direction typically less tolerated "
     "in stenosis. Keep the lift low.")

# --- Hip / gluteal ---------------------------------------------------------
rule(["arthritis_hip"], ["glute_bridge"], "modify",
     "Loads the hip joint directly. Small range, stop before end-of-range discomfort.")
rule(["arthritis_hip"], ["quad_stretch"], "modify",
     "The side-lying position presses the hip forward into extension. Reduce range.")
rule(["bursitis"], ["glute_bridge"], "modify",
     "If the bursitis is gluteal or trochanteric this compresses the affected area.")
rule(["bursitis"], ["shoulder_circles"], "modify",
     "If the bursitis is in the shoulder, circling is direct impingement. Small range only.")
rule(["bursitis"], ["quad_stretch"], "modify",
     "Side-lying places body weight on the outer hip — the usual trochanteric site.")

# --- Wrist / hand ----------------------------------------------------------
rule(["carpal_tunnel"], ["wrist_circles"], "modify",
     "Gentle mobility is usually tolerated, but the upstream position holds both arms "
     "out at shoulder height. Perform seated with arms down.")

# --- Gout ------------------------------------------------------------------
# Gout is episodic; the flare is the constraint, not the diagnosis.
rule(["gout"], ["walk", "ankle_circles", "calf_stretch", "running"], "modify",
     "Weight-bearing and foot/ankle movement should be reduced or paused during an "
     "acute flare. Fine between episodes.")

# --- Sprain / strain -------------------------------------------------------
# The site is unknown, so nothing can be assumed safe for it.
rule(["sprain_strain"],
     ["glute_bridge", "dead_bug", "hamstring_stretch", "quad_stretch",
      "calf_stretch", "ankle_circles", "wrist_circles", "shoulder_circles",
      "neck_isometric", "lower_back_side_stretch", "running"],
     "modify",
     "Injury site is not captured, so no movement can be assumed clear of it. "
     "Avoid anything that loads the injured area.")

# --- Running -----------------------------------------------------------
# Everything else in the taxonomy so far is gentle: isometrics, stretches, or
# walking. Running is the first genuinely high-impact activity, so it gets its
# own pass rather than inheriting "gentle, low-load" by default.
rule(SPINE, ["running"], "modify",
     "Running adds repeated impact loading to the spine that walking does not. "
     "Start with a run/walk mix and stop if any symptom travels below the knee.")
rule(["arthritis_hip"], ["running"], "modify",
     "Running loads the hip joint with repeated impact. Prefer walking, or reduce "
     "distance and frequency, especially during a flare.")
rule(["bursitis"], ["running"], "modify",
     "If the bursitis is hip/trochanteric, the repeated impact of running is more "
     "provocative than walking. Reduce or pause during a flare.")

DEFAULT_NOTE = "Gentle, low-load movement with no specific concern for this condition."


def main() -> None:
    out = Path("data/safety_grid.csv")
    out.parent.mkdir(parents=True, exist_ok=True)

    counts = {"safe": 0, "modify": 0, "avoid": 0}
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["condition_code", "activity_type", "verdict", "note",
                    "reviewer", "reviewed_at"])
        for c in CONDITIONS:
            for a in ACTIVITIES:
                verdict, note = RULES.get((c, a), ("safe", DEFAULT_NOTE))
                counts[verdict] += 1
                w.writerow([c, a, verdict, note, AUTHOR, TODAY])

    total = len(CONDITIONS) * len(ACTIVITIES)
    print(f"{out}: {total} cells "
          f"({len(CONDITIONS)} conditions x {len(ACTIVITIES)} activities)")
    for k, v in counts.items():
        print(f"  {k:7} {v:3}  ({v / total:.0%})")
    print(f"\nauthored by: {AUTHOR}")
    print("Every cell filled — no gaps, so the coverage gate passes.")


if __name__ == "__main__":
    main()
