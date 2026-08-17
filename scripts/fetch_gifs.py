#!/usr/bin/env python3
"""Fetch animated GIF demonstrations from ExerciseDB and SELF-HOST them.

Confirmed directly against the live API (2026-08-14), not from their docs pages,
which are JS-rendered and were incomplete when checked:

  * /exercises/name/{term}       -> JSON array of candidates. No gifUrl field.
  * /exercises/exercise/{id}     -> JSON detail for one exercise. Also no gifUrl.
  * /image?exerciseId=X&resolution=180  -> streams the actual GIF bytes
    (Content-Type: image/gif). The free tier silently returns 180x180 no matter
    what resolution you ask for -- fine for a mobile card thumbnail.

Two-step on purpose:

    python scripts/fetch_gifs.py propose    # search the source, write candidates
    <edit data/gif_mapping.csv, set confirmed=yes on the rows you approve>
    python scripts/fetch_gifs.py fetch      # download approved GIFs to disk

Why not one step: exercise names do not map cleanly. "neck" matches a dozen
different neck movements upstream, and the wrong one shown to someone with a
neck condition is a safety problem, not a cosmetic one. So nothing is accepted
automatically -- a human confirms each pairing once, and the record of that
decision is committed alongside the code.

Why download instead of hotlink:
  * Every user's phone would otherwise hit ExerciseDB's proxy directly on every
    view: that is our request budget spent on image loads, for no benefit.
  * 11 GIFs at ~90KB each is under 1MB total. No reason to depend on anyone
    else's uptime for that.

Licence: ExerciseDB's FAQ (exercisedb.io/faq) explicitly permits self-hosting
and storing their GIF/JSON files in your own database and displaying them
commercially inside your product. The one thing it forbids is republishing the
raw files as a downloadable database/API/competing dataset -- not what this
does. Their separate open-source server (github.com/ExerciseDB/exercisedb-api)
is AGPL-3.0, but that only binds you if you self-host and modify *their server
code*; consuming the hosted RapidAPI product and caching its output, as this
script does, is outside that license entirely.

Needs RAPIDAPI_KEY in the environment. Never write it to a file or commit it --
pass it as an environment variable each time:
    $env:RAPIDAPI_KEY = '...'          (PowerShell)
    RAPIDAPI_KEY=... python scripts/fetch_gifs.py propose   (bash)
"""
from __future__ import annotations

import csv
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
GIF_DIR = ROOT / "app" / "static" / "demos"
MAPPING = DATA / "gif_mapping.csv"

API = "https://exercisedb.p.rapidapi.com"
HOST = "exercisedb.p.rapidapi.com"
RESOLUTION = 180  # the free tier returns this regardless of what's requested

# Search terms per activity. Deliberately broad -- `propose` shows what came
# back and a human picks; a narrow term that returns one wrong result is worse
# than a broad one that returns ten and makes the choice visible.
SEARCH_TERMS = {
    "neck_isometric": "neck",
    "shoulder_circles": "shoulder circle",
    "lower_back_side_stretch": "side stretch",
    "glute_bridge": "glute bridge",
    "dead_bug": "dead bug",
    "hamstring_stretch": "hamstring stretch",
    "quad_stretch": "quadriceps stretch",
    "calf_stretch": "calf stretch",
    "ankle_circles": "ankle circle",
    "wrist_circles": "wrist circle",
    # walk is intentionally absent: no demonstration needed, and inventing one
    # would imply there is a technique to learn.
}


def _key() -> str:
    k = os.environ.get("RAPIDAPI_KEY", "").strip()
    if not k:
        sys.exit(
            "RAPIDAPI_KEY is not set.\n"
            "  Subscribe to ExerciseDB's free tier on RapidAPI, then:\n"
            "    $env:RAPIDAPI_KEY='...'   (PowerShell)\n"
            "  The app runs without this -- it falls back to the public-domain\n"
            "  two-frame animation, which needs no key and no subscription."
        )
    return k


_HEADERS = {"x-rapidapi-host": HOST, "User-Agent": "curl/8.4.0"}


def _get(path: str) -> list | dict:
    req = urllib.request.Request(
        API + path, headers={**_HEADERS, "x-rapidapi-key": _key()})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        sys.exit(f"{path} -> HTTP {e.code}: {body}")


def _activities() -> list[dict]:
    return list(csv.DictReader((DATA / "activity_taxonomy.csv").open(encoding="utf-8")))


def propose() -> int:
    """Search upstream and write candidate pairings for a human to confirm."""
    existing = {}
    if MAPPING.exists():
        existing = {r["activity_type"]: r for r in
                    csv.DictReader(MAPPING.open(encoding="utf-8"))}

    out = []
    for row in _activities():
        code = row["activity_type"].strip()
        term = SEARCH_TERMS.get(code)
        if term is None:
            print(f"{code:<26} skipped (no demonstration needed)")
            continue
        if existing.get(code, {}).get("confirmed", "").lower() == "yes":
            out.append(existing[code])
            print(f"{code:<26} already confirmed -> {existing[code]['source_name']}"
                  f" (id={existing[code]['source_id']})")
            continue

        q = urllib.parse.quote(term)
        hits = _get(f"/exercises/name/{q}?limit=10&offset=0")
        print(f"\n{code}  ({row['display_name']})  search={term!r}  {len(hits)} hit(s)")
        for i, h in enumerate(hits[:10]):
            print(f"    [{i}] {h['name']:<44} target={h.get('target',''):<14} "
                  f"body={h.get('bodyPart',''):<12} id={h['id']}")
        if not hits:
            print("    NO MATCH -- keeps the two-frame animation")
            continue

        best = hits[0]
        out.append({
            "activity_type": code,
            "display_name": row["display_name"],
            "source_id": best["id"],
            "source_name": best["name"],
            "confirmed": "no",
        })

    MAPPING.parent.mkdir(parents=True, exist_ok=True)
    with MAPPING.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "activity_type", "display_name", "source_id", "source_name", "confirmed"])
        w.writeheader()
        w.writerows(out)

    pending = sum(1 for r in out if r["confirmed"] != "yes")
    print(f"\n{MAPPING}: {len(out)} candidate(s), {pending} awaiting confirmation.")
    print("Open it, check the [index] chosen above is really the movement we")
    print("prescribe (look at the printed target/bodyPart, not just the name),")
    print("set confirmed=yes, then: python scripts/fetch_gifs.py fetch")
    return 0


def fetch() -> int:
    """Verify each confirmed id, download its GIF, and record it in demos.json."""
    if not MAPPING.exists():
        sys.exit(f"{MAPPING} not found. Run `propose` first.")

    rows = [r for r in csv.DictReader(MAPPING.open(encoding="utf-8"))
            if r["confirmed"].strip().lower() == "yes"]
    if not rows:
        sys.exit("Nothing confirmed yet -- set confirmed=yes in "
                 f"{MAPPING.name} for the rows you have checked.")

    GIF_DIR.mkdir(parents=True, exist_ok=True)
    demos = json.loads((DATA / "demos.json").read_text(encoding="utf-8"))

    saved = 0
    for r in rows:
        code, source_id = r["activity_type"], r["source_id"].strip()

        # Re-verify id -> name right before download. Catches a hand-edited or
        # stale source_id in the CSV instead of silently shipping the wrong
        # movement to someone who selected a health condition.
        detail = _get(f"/exercises/exercise/{source_id}")
        if detail.get("name") != r["source_name"]:
            print(f"  {code:<26} MISMATCH: id {source_id} is now "
                  f"{detail.get('name')!r}, expected {r['source_name']!r} -- "
                  "skipped. Re-run `propose` and reconfirm.")
            continue

        req = urllib.request.Request(
            f"{API}/image?exerciseId={source_id}&resolution={RESOLUTION}",
            headers={**_HEADERS, "x-rapidapi-key": _key()})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                content_type = resp.headers.get("Content-Type", "")
                blob = resp.read()
        except urllib.error.HTTPError as e:
            print(f"  {code:<26} FAILED: HTTP {e.code}")
            continue

        if "gif" not in content_type or not blob.startswith(b"GIF8"):
            print(f"  {code:<26} REJECTED: response was not a GIF "
                  f"(Content-Type={content_type!r}, {len(blob)} bytes)")
            continue

        dest = GIF_DIR / f"{code}.gif"
        dest.write_bytes(blob)
        demos.setdefault(code, {"images": [], "instructions": []})
        demos[code]["gif"] = f"/static/demos/{code}.gif"
        demos[code]["gif_source"] = r["source_name"]
        saved += 1
        print(f"  {code:<26} {len(blob) // 1024} KB -> {dest.relative_to(ROOT)}")

    (DATA / "demos.json").write_text(json.dumps(demos, indent=2), encoding="utf-8")
    print(f"\n{saved} GIF(s) saved locally. Now: python -m app.catalog.loader")
    print("Activities without a confirmed GIF keep the two-frame animation.")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "propose":
        raise SystemExit(propose())
    if cmd == "fetch":
        raise SystemExit(fetch())
    raise SystemExit(__doc__)
