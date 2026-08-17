"""Load the reviewed CSVs into PostgreSQL, and gate on coverage.

Run:  python -m app.catalog.loader

The coverage check is the important part. A supported condition missing a verdict
for any activity is not "probably fine" — it is the failure mode the closed list
exists to prevent, where an absent row silently reads as "nothing to exclude,
therefore everything is safe". The loader refuses to finish rather than leave that
state in the database.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import delete, select

from app.catalog.models import ActivityTaxonomy, Condition, SafetyGridCell, VideoCatalog
from app.platform.config import load_config
from app.platform.db import init_engine, session_scope

DATA = Path(__file__).resolve().parents[2] / "data"


class CoverageError(RuntimeError):
    """Raised when the safety grid has a gap. Never downgraded to a warning."""


def _split(value: str) -> list[str]:
    return [p.strip() for p in value.split("|") if p.strip()]


def _date_or_none(value: str):
    value = (value or "").strip()
    return date.fromisoformat(value) if value else None


def load_conditions(session) -> int:
    """Upsert. Never delete-and-recreate.

    `user_conditions` and `safety_grid` both reference these rows, so a wipe fails
    with a foreign-key violation the moment a single user has onboarded — i.e. every
    reload in production. Rows that disappear from the CSV are reported, not
    silently removed, because deleting a condition someone has selected is a data
    decision, not a reload side effect.
    """
    rows = list(csv.DictReader((DATA / "conditions.csv").open(encoding="utf-8")))
    incoming = {r["condition_code"].strip(): r for r in rows}

    for code, r in incoming.items():
        existing = session.get(Condition, code)
        if existing is None:
            session.add(Condition(
                condition_code=code,
                display_name=r["display_name"].strip(),
                bucket=r["bucket"].strip(),
                display_order=int(r["display_order"]),
                notes=(r.get("notes") or "").strip() or None,
            ))
        else:
            existing.display_name = r["display_name"].strip()
            existing.bucket = r["bucket"].strip()
            existing.display_order = int(r["display_order"])
            existing.notes = (r.get("notes") or "").strip() or None
    session.flush()

    stale = [c.condition_code for c in session.scalars(select(Condition)).all()
             if c.condition_code not in incoming]
    if stale:
        print(f"  note: {len(stale)} condition(s) in the database but not in the CSV: "
              f"{', '.join(stale)} — left in place (users may have selected them)")

    for structural in ("none", "other_unlisted"):
        if structural not in incoming:
            raise CoverageError(
                f"conditions.csv is missing the structural row '{structural}'. "
                "'none' lets a healthy user proceed; 'other_unlisted' is the "
                "fail-closed default for anything not listed."
            )
    return len(rows)


def load_taxonomy(session) -> int:
    rows = list(csv.DictReader((DATA / "activity_taxonomy.csv").open(encoding="utf-8")))
    demos_path = DATA / "demos.json"
    demos = json.loads(demos_path.read_text(encoding="utf-8")) if demos_path.exists() else {}
    # Upsert for the same reason as conditions: activity_logs and user_program
    # both hold foreign keys into this table.
    for r in rows:
        code = r["activity_type"].strip()
        session.merge(ActivityTaxonomy(
            activity_type=code,
            display_name=r["display_name"].strip(),
            prescription_type=r["prescription_type"].strip(),
            default_sets=int(r["default_sets"]),
            default_amount=float(r["default_amount"]),
            amount_unit=r["amount_unit"].strip(),
            rest_seconds=int(r["rest_seconds"] or 0),
            per_side=r["per_side"].strip().upper() == "TRUE",
            progression_axis=r["progression_axis"].strip(),
            progression_step=float(r["progression_step"]),
            max_sets=int(r["max_sets"]),
            max_amount=float(r["max_amount"]),
            plausible_max_total=float(r["plausible_max_total"]),
            est_pace_min_per_km=float(r["est_pace_min_per_km"])
                if (r.get("est_pace_min_per_km") or "").strip() else None,
            synonyms=_split(r["synonyms"]),
            source_exercise_id=(r.get("source_exercise_id") or "").strip() or None,
            priority_order=int(r["priority_order"]),
            youtube_url=(r.get("youtube_url") or "").strip() or None,
            demo_images=demos.get(r["activity_type"].strip(), {}).get("images", []),
            instructions=demos.get(r["activity_type"].strip(), {}).get("instructions", []),
            demo_gif=demos.get(r["activity_type"].strip(), {}).get("gif"),
            demo_gif_source=demos.get(r["activity_type"].strip(), {}).get("gif_source"),
            reviewer=r["reviewer"].strip(),
            reviewed_at=_date_or_none(r.get("reviewed_at", "")),
        ))
    session.flush()

    # A spoken phrase must resolve to exactly one activity (12.2). Two activities
    # claiming "stretch" would make the parser pick one at random.
    seen: dict[str, str] = {}
    for r in rows:
        for syn in _split(r["synonyms"]):
            key = syn.lower()
            if key in seen:
                raise CoverageError(
                    f"synonym {syn!r} claimed by both {seen[key]!r} and "
                    f"{r['activity_type']!r} — the voice parser could not disambiguate."
                )
            seen[key] = r["activity_type"]
    return len(rows)


def load_safety_grid(session) -> int:
    rows = list(csv.DictReader((DATA / "safety_grid.csv").open(encoding="utf-8")))
    # Nothing holds a foreign key into the grid, so a clean replace is safe
    # and is what makes a re-review land in full.
    session.execute(delete(SafetyGridCell))
    for r in rows:
        verdict = r["verdict"].strip()
        if not verdict:
            raise CoverageError(
                f"safety_grid.csv has an empty verdict for "
                f"{r['condition_code']} x {r['activity_type']}. A blank is not "
                "'probably safe' — fill it or the condition cannot be supported."
            )
        session.add(SafetyGridCell(
            condition_code=r["condition_code"].strip(),
            activity_type=r["activity_type"].strip(),
            verdict=verdict,
            note=(r.get("note") or "").strip() or None,
            reviewer=r["reviewer"].strip(),
            reviewed_at=_date_or_none(r.get("reviewed_at", "")),
        ))
    session.flush()
    return len(rows)


def assert_full_coverage(session) -> None:
    """Every supported condition needs a verdict for every activity."""
    supported = session.scalars(
        select(Condition.condition_code)
        .where(Condition.bucket == "supported", Condition.condition_code != "none")
    ).all()
    activities = session.scalars(select(ActivityTaxonomy.activity_type)).all()
    have = {
        (c, a) for c, a in session.execute(
            select(SafetyGridCell.condition_code, SafetyGridCell.activity_type)
        ).all()
    }

    missing = [(c, a) for c in supported for a in activities if (c, a) not in have]
    if missing:
        shown = ", ".join(f"{c}x{a}" for c, a in missing[:8])
        raise CoverageError(
            f"safety grid is missing {len(missing)} cell(s): {shown}"
            f"{' …' if len(missing) > 8 else ''}. "
            "Suggestions would fail open for these conditions."
        )


def seed_videos(session) -> int:
    """Create one catalogue row per activity, awaiting curation.

    Deliberately NOT a search URL. A search page is the manual YouTube experience
    the app exists to replace — it returns a wall of results the user has to judge,
    which is exactly the judgement a curated library is supposed to have already
    made. A row with no `youtube_video_id` renders as "not curated yet" rather than
    pretending to be content.

    Curation is an admin job (7.1): paste one watched, approved URL per exercise.
    Existing curated rows are preserved on reload.
    """
    existing = {
        v.activity_type: v
        for v in session.scalars(select(VideoCatalog)).all()
    }
    n = 0
    for row in session.scalars(select(ActivityTaxonomy)).all():
        cur = existing.get(row.activity_type)
        if cur is not None:
            continue  # never clobber a curated pick
        session.add(VideoCatalog(
            activity_type=row.activity_type,
            title=f"{row.display_name} — demonstration",
            youtube_video_id="",
            watch_url="",
        ))
        n += 1
    return n


def main() -> int:
    cfg = load_config()
    init_engine(cfg.database_url)
    try:
        with session_scope() as s:
            c = load_conditions(s)
            t = load_taxonomy(s)
            g = load_safety_grid(s)
            assert_full_coverage(s)
            v = seed_videos(s)
    except CoverageError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1

    print(f"conditions   {c:4}")
    print(f"activities   {t:4}")
    print(f"grid cells   {g:4}  (coverage complete)")
    print(f"video links  {v:4}  (placeholder search links)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
