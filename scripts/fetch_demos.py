#!/usr/bin/env python3
"""Pull demonstration frames + steps from free-exercise-db into data/demos.json.

Why this instead of curated YouTube links: the upstream images and instructions are
public domain (Unlicense), already mapped to our `source_exercise_id`, and need no
per-exercise human choice. Every user sees them the moment they sign up.

Two frames per exercise — start and end position — alternated in the UI gives a
usable demonstration of the movement without any video hosting.

    python scripts/fetch_demos.py

Re-run to refresh. Output is committed so the app does not depend on GitHub at
runtime.
"""
from __future__ import annotations

import csv
import json
import sys
import urllib.request
from pathlib import Path

SRC = "https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/dist/exercises.json"
IMG_BASE = "https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/exercises/"
DATA = Path(__file__).resolve().parents[1] / "data"

# Upstream instruction text that is wrong for a home rehab context. The source
# audit flagged these; rewriting them here keeps the fix next to the evidence.
# `None` means "drop this step entirely".
INSTRUCTION_FIXES: dict[str, dict[int, str | None]] = {
    "Ankle_Circles": {
        0: "Sit or stand and hold a chair or wall for balance if you need it.",
    },
    "Wrist_Circles": {
        0: "Sit comfortably with your arms relaxed by your sides or resting on your lap.",
    },
}


def main() -> int:
    rows = list(csv.DictReader((DATA / "activity_taxonomy.csv").open(encoding="utf-8")))
    upstream = {x["id"]: x for x in json.loads(urllib.request.urlopen(SRC, timeout=60).read())}

    out: dict[str, dict] = {}
    missing: list[str] = []

    for r in rows:
        code = r["activity_type"].strip()
        src = (r.get("source_exercise_id") or "").strip()
        if not src:
            # e.g. walking — no upstream entry, and none is needed.
            out[code] = {"images": [], "instructions": [], "source": None}
            continue
        ex = upstream.get(src)
        if ex is None:
            missing.append(f"{code} -> {src}")
            continue

        steps = list(ex.get("instructions") or [])
        for idx, replacement in INSTRUCTION_FIXES.get(src, {}).items():
            if idx < len(steps):
                if replacement is None:
                    steps[idx] = None
                else:
                    steps[idx] = replacement
        steps = [s for s in steps if s]

        out[code] = {
            "images": [IMG_BASE + p for p in ex.get("images", [])],
            "instructions": steps,
            "source": src,
            "primary_muscles": ex.get("primaryMuscles", []),
        }

    if missing:
        print("FATAL: source ids not found upstream:", ", ".join(missing), file=sys.stderr)
        return 1

    path = DATA / "demos.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    have = sum(1 for v in out.values() if v["images"])
    print(f"{path}: {len(out)} activities, {have} with demonstration frames")
    for code, v in out.items():
        mark = f"{len(v['images'])} frames, {len(v['instructions'])} steps" if v["images"] else "no upstream source"
        print(f"  {code:<26} {mark}")
    if INSTRUCTION_FIXES:
        print("\nrewritten steps (upstream text was wrong for a home setting):")
        for src, fixes in INSTRUCTION_FIXES.items():
            for i in fixes:
                print(f"  {src} step {i + 1}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
