"""Onboarding, activity logging, suggestions.

The rule that shapes all of this: the safety grid decides what a user is shown.
Nothing here invents an exercise, and a condition the grid cannot cover produces
no suggestions rather than unfiltered ones.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.catalog.models import ActivityTaxonomy, Condition, SafetyGridCell, VideoCatalog
from app.platform.errors import Forbidden, NotFound, ValidationError
from app.profile.models import (
    ActivityLog, CustomExercise, OverrideConsent, UserCondition, UserProfile, UserProgram,
)

IST = timezone(timedelta(hours=5, minutes=30))  # single market (3.3)

REFERRAL_MESSAGE = (
    "Based on what you've told us, please speak to a doctor before starting an "
    "exercise programme. You can still log activities here, but we won't suggest "
    "exercises."
)

GOALS = {"health", "fitness", "flexibility", "habit"}
BANDS = {"beginner", "1m", "2m", "3m", "experienced"}

# How many activities a tier shows, sliced from the full priority-ordered,
# already-condition-filtered list. "experienced" is None: no cap, show
# everything that's safe. Counts are shown, not "default"; the default/optional
# split is just where within that count the line falls (see _tier_slice).
TIER_ORDER = ["beginner", "1m", "2m", "3m", "experienced"]
TIER_SHOWN = {"beginner": 4, "1m": 5, "2m": 7, "3m": 9, "experienced": None}


def today_ist() -> date:
    return datetime.now(IST).date()


# --- Reference data --------------------------------------------------------

def list_conditions(session: Session) -> list[dict]:
    rows = session.scalars(
        select(Condition).order_by(Condition.display_order)
    ).all()
    return [{"code": r.condition_code, "name": r.display_name,
             "bucket": r.bucket, "notes": r.notes} for r in rows]


def list_activities(session: Session) -> list[dict]:
    rows = session.scalars(
        select(ActivityTaxonomy).order_by(ActivityTaxonomy.display_name)
    ).all()
    return [{
        "activity_type": r.activity_type, "display_name": r.display_name,
        "prescription_type": r.prescription_type,
        "default_sets": r.default_sets, "default_amount": float(r.default_amount),
        "amount_unit": r.amount_unit, "per_side": r.per_side,
        "rest_seconds": r.rest_seconds, "synonyms": r.synonyms,
    } for r in rows]


# --- Onboarding ------------------------------------------------------------

def is_referred(session: Session, user_id: str) -> bool:
    """True if any selected condition is in the refer bucket (13.3b).

    Takes precedence over everything: no suggestions, no program, no videos.
    """
    n = session.scalar(
        select(func.count())
        .select_from(UserCondition)
        .join(Condition, Condition.condition_code == UserCondition.condition_code)
        .where(UserCondition.user_id == user_id, Condition.bucket == "refer")
    )
    return bool(n)


def complete_onboarding(session: Session, user_id: str, *, age: int, goal: str,
                        experience_band: str, condition_codes: list[str],
                        unit_preference: str = "km") -> dict:
    if not isinstance(age, int) or age < 18:
        raise ValidationError("You must be 18 or over to use this app.")
    if goal not in GOALS:
        raise ValidationError(f"Goal must be one of: {', '.join(sorted(GOALS))}")
    if experience_band not in BANDS:
        raise ValidationError(f"Experience band must be one of: {', '.join(sorted(BANDS))}")

    known = {c.condition_code for c in session.scalars(select(Condition)).all()}
    codes = [c for c in (condition_codes or []) if c]
    unknown = [c for c in codes if c not in known]
    if unknown:
        # Free text can never reach here — the client picks from /v1/conditions.
        raise ValidationError(f"Unknown condition(s): {', '.join(unknown)}")
    if not codes:
        codes = ["none"]

    session.merge(UserProfile(
        user_id=user_id, age=age, goal=goal,
        experience_band=experience_band, unit_preference=unit_preference,
    ))
    session.execute(delete(UserCondition).where(UserCondition.user_id == user_id))
    for code in codes:
        session.add(UserCondition(user_id=user_id, condition_code=code))
    session.flush()

    if is_referred(session, user_id):
        # Deliberately no program. A referred user gets no exercise content at all.
        session.execute(delete(UserProgram).where(UserProgram.user_id == user_id))
        return {"referred": True, "message": REFERRAL_MESSAGE, "program": []}

    program = build_program(session, user_id)
    return {"referred": False, "message": None, "program": program}


UNIT_PREFERENCES = {"km", "mi"}


def update_profile(session: Session, user_id: str, *, fields: dict) -> dict:
    """3.4 — edit one or more simple fields without touching conditions or the
    program. Only age / goal / experience_band / unit_preference are settable
    here; anything else in `fields` is ignored rather than silently accepted.
    """
    prof = session.get(UserProfile, user_id)
    if prof is None:
        raise NotFound("Complete onboarding before editing your profile.")

    if "age" in fields:
        age = fields["age"]
        if not isinstance(age, int) or age < 18:
            raise ValidationError("You must be 18 or over to use this app.")
        prof.age = age

    if "goal" in fields:
        goal = fields["goal"]
        if goal not in GOALS:
            raise ValidationError(f"Goal must be one of: {', '.join(sorted(GOALS))}")
        prof.goal = goal

    if "experience_band" in fields:
        band = fields["experience_band"]
        if band not in BANDS:
            raise ValidationError(f"Experience band must be one of: {', '.join(sorted(BANDS))}")
        prof.experience_band = band

    if "unit_preference" in fields:
        unit = fields["unit_preference"]
        if unit not in UNIT_PREFERENCES:
            raise ValidationError(f"Unit preference must be one of: {', '.join(sorted(UNIT_PREFERENCES))}")
        prof.unit_preference = unit

    session.flush()
    return profile_summary(session, user_id)


# --- Program ---------------------------------------------------------------

def _blocked_activities(session: Session, user_id: str) -> dict[str, str]:
    """activity_type -> note, for anything the grid marks `avoid`."""
    rows = session.execute(
        select(SafetyGridCell.activity_type, SafetyGridCell.note)
        .join(UserCondition,
              UserCondition.condition_code == SafetyGridCell.condition_code)
        .where(UserCondition.user_id == user_id, SafetyGridCell.verdict == "avoid")
    ).all()
    return {a: n for a, n in rows}


def _modified_activities(session: Session, user_id: str) -> dict[str, str]:
    rows = session.execute(
        select(SafetyGridCell.activity_type, SafetyGridCell.note)
        .join(UserCondition,
              UserCondition.condition_code == SafetyGridCell.condition_code)
        .where(UserCondition.user_id == user_id, SafetyGridCell.verdict == "modify")
    ).all()
    return {a: n for a, n in rows}


def build_program(session: Session, user_id: str) -> list[dict]:
    """Seed the program from the taxonomy baselines, minus anything `avoid`."""
    blocked = _blocked_activities(session, user_id)
    session.execute(delete(UserProgram).where(UserProgram.user_id == user_id))

    for row in session.scalars(select(ActivityTaxonomy)).all():
        if row.activity_type in blocked:
            continue
        session.add(UserProgram(
            user_id=user_id,
            activity_type=row.activity_type,
            target_sets=row.default_sets,
            target_amount=row.default_amount,
            unit=row.amount_unit,
            source="template",
        ))
    session.flush()
    return get_program(session, user_id)


def get_program(session: Session, user_id: str) -> list[dict]:
    if is_referred(session, user_id):
        return []
    modified = _modified_activities(session, user_id)
    rows = session.execute(
        select(UserProgram, ActivityTaxonomy)
        .join(ActivityTaxonomy,
              ActivityTaxonomy.activity_type == UserProgram.activity_type)
        .where(UserProgram.user_id == user_id)
        .order_by(ActivityTaxonomy.display_name)
    ).all()
    out = []
    for p, t in rows:
        amount = float(p.target_amount)
        est = None
        if t.prescription_type == "distance" and t.est_pace_min_per_km:
            # An honest time estimate without storing a second target.
            est = round(amount * float(t.est_pace_min_per_km))
        out.append({
            "activity_type": p.activity_type,
            "display_name": t.display_name,
            "prescription_type": t.prescription_type,
            "sets": p.target_sets,
            "amount": amount,
            "unit": p.unit,
            "per_side": t.per_side,
            "rest_seconds": t.rest_seconds,
            "prescription": t.describe(p.target_sets, amount),
            "total_volume": t.total_volume(p.target_sets, amount),
            "est_minutes": est,
            "status": p.status,
            "caution": modified.get(p.activity_type),
            "demo_gif": t.demo_gif,
            "demo_images": t.demo_images,
            "instructions": t.instructions,
            "priority_order": t.priority_order,
            "source": p.source,
        })
    return out


# --- Manual program control (14.7) ------------------------------------------

_VERDICT_RANK = {"safe": 0, "modify": 1, "avoid": 2}


def _worst_verdict(session: Session, user_id: str, activity_type: str) -> dict | None:
    """The most restrictive verdict across all of a user's conditions for one
    activity — the one shown in the override dialog. None if none of their
    conditions has a grid row for it at all (as safe as `none`)."""
    rows = session.execute(
        select(SafetyGridCell.verdict, SafetyGridCell.note, SafetyGridCell.reviewed_at,
               Condition.condition_code, Condition.display_name)
        .join(UserCondition, UserCondition.condition_code == SafetyGridCell.condition_code)
        .join(Condition, Condition.condition_code == SafetyGridCell.condition_code)
        .where(UserCondition.user_id == user_id,
               SafetyGridCell.activity_type == activity_type)
    ).all()
    if not rows:
        return None
    worst = max(rows, key=lambda r: _VERDICT_RANK[r.verdict])
    return {"condition_code": worst.condition_code, "condition_name": worst.display_name,
            "verdict": worst.verdict, "note": worst.note, "reviewed_at": worst.reviewed_at}


def _override_warning(condition_name: str) -> str:
    """Identical wording on every entry point that can write a user_override —
    manual add here, and any future one (16.1) — per 14.7.

    Names the condition explicitly so the reason is concrete, not generic. The
    bar stays doctor sign-off, not self-assessment: "I feel fine now" is
    exactly the judgement a reviewed grid exists to not depend on.
    """
    return (f"You've told us you have {condition_name}. This exercise isn't recommended "
            "for that condition, so it's blocked by default. Only continue if a doctor "
            "has examined you and specifically advised you to do it.")


def list_catalogue(session: Session, user_id: str) -> list[dict]:
    """14.7 / 13.3b — everything NOT already on the dashboard: tier-hidden
    activities the user hasn't grown into yet, and anything their condition
    blocks. Deliberately excludes by dashboard VISIBILITY, not raw program
    membership — most of the taxonomy is already silently in `user_program`
    from onboarding (13.3/14.2), so filtering on that would leave almost
    nothing to offer. Refer-bucket users cannot open this at all, the same
    hard gate as suggestions and videos.
    """
    if is_referred(session, user_id):
        raise Forbidden("Speak to a doctor before adding exercises to your program.")
    _program, visible, _band, _default, _optional = _dashboard_slice(session, user_id)
    out = []
    for t in session.scalars(
            select(ActivityTaxonomy).order_by(ActivityTaxonomy.priority_order)).all():
        if t.activity_type in visible:
            continue
        v = _worst_verdict(session, user_id, t.activity_type)
        out.append({
            "activity_type": t.activity_type,
            "display_name": t.display_name,
            "prescription_type": t.prescription_type,
            "default_sets": t.default_sets,
            "default_amount": float(t.default_amount),
            "amount_unit": t.amount_unit,
            "per_side": t.per_side,
            "verdict": v["verdict"] if v else "safe",
            "caution": v["note"] if v and v["verdict"] != "safe" else None,
        })
    return out


def add_program_entry(session: Session, user_id: str, activity_type: str,
                      override_confirmed: bool = False) -> dict:
    """14.7 — user-driven add, curated taxonomy only, never free text.

    An `avoid`-verdict activity is permitted, but only past a blocking
    confirmation naming the condition and verdict — a doctor who examined the
    user outranks the generic table, but the app does not adopt the choice as
    its own advice, and never progresses what it does not consider safe.
    """
    if is_referred(session, user_id):
        raise Forbidden("Speak to a doctor before adding exercises to your program.")
    t = session.get(ActivityTaxonomy, activity_type)
    if t is None:
        raise NotFound(f"Unknown activity: {activity_type}")

    worst = _worst_verdict(session, user_id, activity_type)
    if worst and worst["verdict"] == "avoid":
        if not override_confirmed:
            return {
                "added": False, "needs_confirmation": True,
                "condition_code": worst["condition_code"],
                "condition_name": worst["condition_name"],
                "verdict": "avoid",
                "warning_text": _override_warning(worst["condition_name"]),
            }
        session.add(OverrideConsent(
            user_id=user_id, condition_code=worst["condition_code"],
            activity_type=activity_type, grid_verdict="avoid",
            grid_reviewed_at=worst["reviewed_at"],
            warning_text=_override_warning(worst["condition_name"]),
        ))
        source = "user_override"
    else:
        source = "manual"

    existing = session.get(UserProgram, (user_id, activity_type))
    if existing is not None:
        # Re-adding restores rather than resets (14.9's principle) — whatever
        # target it already had stands, it just becomes active again. Source
        # updates to manual/user_override even if it was `template` before:
        # this was a deliberate catalogue pick, so it stays visible on the
        # dashboard regardless of tier from now on (_dashboard_slice).
        existing.status = "active"
        existing.suspended_reason = None
        existing.source = source
        # A re-add is a fresh start for "logged today" purposes (matches how
        # deleting+recreating a custom exercise gets a fresh id): any log from
        # before this moment must not count toward today's completion below.
        existing.added_at = datetime.now(timezone.utc)
    else:
        session.add(UserProgram(
            user_id=user_id, activity_type=activity_type,
            target_sets=t.default_sets, target_amount=t.default_amount,
            unit=t.amount_unit, source=source,
        ))
    session.flush()
    return {"added": True, "needs_confirmation": False, "program": get_program(session, user_id)}


def remove_program_entry(session: Session, user_id: str, activity_type: str) -> dict:
    """14.7 — always permitted, for any entry, even for a referred user.

    Marks the entry removed rather than deleting it, and _dashboard_slice
    trims the tier's visible count to match - a manual removal must stay
    removed, never silently backfilled by the next-priority template
    activity. Bringing it back is only ever a deliberate catalogue add.
    """
    row = session.get(UserProgram, (user_id, activity_type))
    if row is not None:
        row.status = "suspended"
        row.suspended_reason = "user_removed"
    session.flush()
    return {"removed": True, "program": get_program(session, user_id)}


def _tier_slice(items_by_priority: list[dict], band: str) -> tuple[set[str], set[str]]:
    """Split activities into (default, optional) for a dashboard experience tier.

    `items_by_priority` only ever contains activities already cleared by the
    safety grid for this user (anything `avoid` was removed before this runs) —
    so backfilling here can only ever promote an already-verified-safe activity,
    never a risky one. If a condition removes one, the next-most-important safe
    activity fills its spot, keeping the tier's usual count intact. Only when
    the safe pool itself runs out short does the count actually drop — never
    padded with anything unvetted.
    """
    shown = TIER_SHOWN.get(band)
    if shown is None:
        return {i["activity_type"] for i in items_by_priority}, set()
    idx = TIER_ORDER.index(band)
    prev_shown = TIER_SHOWN[TIER_ORDER[idx - 1]] if idx > 0 else shown
    ordered = sorted(items_by_priority, key=lambda i: i["priority_order"])
    default_n = min(prev_shown, len(ordered))
    optional_n = min(shown, len(ordered))
    default = {i["activity_type"] for i in ordered[:default_n]}
    optional = {i["activity_type"] for i in ordered[default_n:optional_n]}
    return default, optional


# --- Logging ---------------------------------------------------------------

def log_activity(session: Session, user_id: str, *, activity_type: str | None = None,
                 custom_exercise_id: str | None = None, amount: float, sets: int = 1,
                 unit: str | None = None, source: str = "manual",
                 client_entry_id: str | None = None) -> dict:
    if bool(activity_type) == bool(custom_exercise_id):
        raise ValidationError("Provide exactly one of activity_type or custom_exercise_id.")

    if custom_exercise_id:
        # 4.4 — same entry point, same validation shape as a taxonomy log;
        # the only different source is where the ceiling/unit come from.
        custom = session.get(CustomExercise, custom_exercise_id)
        if custom is None or custom.user_id != user_id or custom.deleted_at is not None:
            raise NotFound(f"Unknown custom exercise: {custom_exercise_id}")
        # unit->type is 1:1 (count/km/min never collide), so the unit on this
        # log tells us which of the exercise's selected types it belongs to -
        # no separate measurement-type field is needed on the log itself.
        allowed = {CUSTOM_MEASUREMENT_UNITS[t]: t for t in custom.measurement_types}
        if unit is None:
            if len(allowed) != 1:
                raise ValidationError(
                    f"{custom.name} tracks more than one thing — specify unit: "
                    f"{', '.join(sorted(allowed))}.")
            unit = next(iter(allowed))
        if unit not in allowed:
            raise ValidationError(
                f"{custom.name} is measured in {', '.join(sorted(allowed))}, not {unit}.")
        measurement_type = allowed[unit]
        if amount <= 0:
            raise ValidationError("Amount must be greater than zero.")
        if sets < 1:
            raise ValidationError("Sets must be at least 1.")
        if measurement_type in ("distance", "duration") and sets != 1:
            raise ValidationError(f"{custom.name} is a single effort, not sets.")
        total = amount * sets
        implausible = Decimal(str(total)) > Decimal(str(CUSTOM_MEASUREMENT_DEFAULT_MAX[measurement_type]))
        prescription = f"{sets} x {amount} {unit}" if sets > 1 else f"{amount} {unit}"
        row_activity_type = None
    else:
        row = session.get(ActivityTaxonomy, activity_type)
        if row is None:
            raise NotFound(f"Unknown activity: {activity_type}")

        unit = unit or row.amount_unit
        if unit != row.amount_unit:
            raise ValidationError(
                f"{row.display_name} is measured in {row.amount_unit}, not {unit}."
            )
        if amount <= 0:
            raise ValidationError("Amount must be greater than zero.")
        if sets < 1:
            raise ValidationError("Sets must be at least 1.")
        if row.prescription_type in ("distance", "duration") and sets != 1:
            # One continuous effort. Sets would be meaningless and would double-count
            # into total_volume.
            raise ValidationError(f"{row.display_name} is a single effort, not sets.")

        total = row.total_volume(sets, amount)
        # 4.6 — kept and flagged, never rejected. A user's entry is theirs.
        implausible = Decimal(str(total)) > row.plausible_max_total
        prescription = row.describe(sets, amount)
        row_activity_type = activity_type

    if client_entry_id:
        existing = session.scalar(
            select(ActivityLog).where(
                ActivityLog.user_id == user_id,
                ActivityLog.client_entry_id == client_entry_id)
        )
        if existing:  # offline replay (12.5) — not a second log
            return {"id": existing.id, "duplicate": True,
                    "implausible": existing.implausible,
                    "total_volume": float(existing.total_volume)}

    log = ActivityLog(
        user_id=user_id, activity_type=row_activity_type,
        custom_exercise_id=custom_exercise_id,
        sets_done=sets, amount_per_set=amount, unit=unit, total_volume=total,
        local_date=today_ist(), source=source, implausible=implausible,
        client_entry_id=client_entry_id,
    )
    session.add(log)
    session.flush()
    return {"id": log.id, "duplicate": False, "implausible": implausible,
            "total_volume": total, "prescription": prescription}


# --- Custom exercises (4.4) --------------------------------------------------
# Personal, tracking-only. Never read by suggestions_today, build_program, or
# list_catalogue — those stay taxonomy-only, on purpose (13.1).

CUSTOM_MEASUREMENT_UNITS = {"reps": "count", "distance": "km", "duration": "min"}
CUSTOM_MEASUREMENT_DEFAULT_MAX = {"reps": 500, "distance": 50, "duration": 300}
CUSTOM_EXERCISE_CAP = 20


def _normalise_exercise_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def _custom_exercise_out(row: CustomExercise, *, completed_today: bool = False) -> dict:
    return {"id": row.id, "name": row.name,
            "measurement_types": row.measurement_types,
            "units": [CUSTOM_MEASUREMENT_UNITS[t] for t in row.measurement_types],
            "completed_today": completed_today}


def list_custom_exercises(session: Session, user_id: str) -> list[dict]:
    rows = session.scalars(
        select(CustomExercise).where(CustomExercise.user_id == user_id,
                                      CustomExercise.deleted_at.is_(None))
        .order_by(CustomExercise.created_at)
    ).all()
    # Same "done today" concept the built-in cards already use (suggestions_today).
    done_today = set(session.scalars(
        select(ActivityLog.custom_exercise_id).where(
            ActivityLog.user_id == user_id,
            ActivityLog.local_date == today_ist(),
            ActivityLog.custom_exercise_id.is_not(None))
    ).all())
    return [_custom_exercise_out(r, completed_today=r.id in done_today) for r in rows]


def create_custom_exercise(session: Session, user_id: str, *, name: str,
                           measurement_types: list[str]) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValidationError("Name is required.")
    if len(name) > 64:
        raise ValidationError("Name must be 64 characters or fewer.")
    # De-dup while keeping the order the user picked them in.
    measurement_types = list(dict.fromkeys(measurement_types or []))
    if not measurement_types:
        raise ValidationError("Pick at least one way to count it.")
    if len(measurement_types) > 3:
        raise ValidationError("Pick at most 3 ways to count it.")
    unknown = [m for m in measurement_types if m not in CUSTOM_MEASUREMENT_UNITS]
    if unknown:
        raise ValidationError(
            f"measurement_types must be one of: {', '.join(sorted(CUSTOM_MEASUREMENT_UNITS))}")

    normalized = _normalise_exercise_name(name)

    # Dedup against the official taxonomy first (4.4) — "jogging" must resolve
    # to the real `run` activity, never spawn a personal duplicate of it.
    for t in session.scalars(select(ActivityTaxonomy)).all():
        synonyms = {s.lower() for s in (t.synonyms or [])}
        if normalized == t.display_name.lower() or normalized in synonyms:
            raise ValidationError(
                f'"{name}" matches the built-in activity "{t.display_name}" — log that instead.'
            )

    existing = session.scalar(
        select(CustomExercise).where(
            CustomExercise.user_id == user_id,
            CustomExercise.normalized_name == normalized,
            CustomExercise.deleted_at.is_(None))
    )
    if existing is not None:
        raise ValidationError(f'You already have a custom exercise called "{existing.name}".')

    count = session.scalar(
        select(func.count()).select_from(CustomExercise)
        .where(CustomExercise.user_id == user_id, CustomExercise.deleted_at.is_(None))
    )
    if count >= CUSTOM_EXERCISE_CAP:
        raise ValidationError(f"You can have at most {CUSTOM_EXERCISE_CAP} custom exercises.")

    row = CustomExercise(
        user_id=user_id, name=name, normalized_name=normalized,
        measurement_types=measurement_types,
    )
    session.add(row)
    session.flush()
    return _custom_exercise_out(row)


def rename_custom_exercise(session: Session, user_id: str, exercise_id: str,
                           new_name: str) -> dict:
    row = session.get(CustomExercise, exercise_id)
    if row is None or row.user_id != user_id or row.deleted_at is not None:
        raise NotFound("Custom exercise not found.")
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValidationError("Name is required.")
    if len(new_name) > 64:
        raise ValidationError("Name must be 64 characters or fewer.")
    normalized = _normalise_exercise_name(new_name)
    clash = session.scalar(
        select(CustomExercise).where(
            CustomExercise.user_id == user_id,
            CustomExercise.normalized_name == normalized,
            CustomExercise.deleted_at.is_(None),
            CustomExercise.id != exercise_id)
    )
    if clash is not None:
        raise ValidationError(f'You already have a custom exercise called "{clash.name}".')
    row.name = new_name
    row.normalized_name = normalized
    session.flush()
    return _custom_exercise_out(row)


def delete_custom_exercise(session: Session, user_id: str, exercise_id: str) -> dict:
    """Soft delete (4.4). Past logs keep referencing this row via the FK -
    recent_logs joins on it by name - so history keeps displaying exactly
    as it did before, it just drops off list_custom_exercises and can no
    longer be logged against (see log_activity above).
    """
    row = session.get(CustomExercise, exercise_id)
    if row is None or row.user_id != user_id or row.deleted_at is not None:
        raise NotFound("Custom exercise not found.")
    row.deleted_at = datetime.now(timezone.utc)
    session.flush()
    return {"deleted": True}


def recent_logs(session: Session, user_id: str, limit: int = 10) -> list[dict]:
    rows = session.execute(
        select(ActivityLog, ActivityTaxonomy, CustomExercise)
        .outerjoin(ActivityTaxonomy,
                   ActivityTaxonomy.activity_type == ActivityLog.activity_type)
        .outerjoin(CustomExercise,
                   CustomExercise.id == ActivityLog.custom_exercise_id)
        .where(ActivityLog.user_id == user_id)
        .order_by(ActivityLog.logged_at.desc())
        .limit(limit)
    ).all()
    out = []
    for l, t, c in rows:
        if t is not None:
            display_name = t.display_name
            prescription = t.describe(l.sets_done, float(l.amount_per_set))
        else:
            display_name = c.name
            prescription = (f"{l.sets_done} x {l.amount_per_set} {l.unit}"
                            if l.sets_done > 1 else f"{l.amount_per_set} {l.unit}")
        out.append({
            "display_name": display_name,
            "activity_type": l.activity_type,
            "custom": t is None,
            "sets": l.sets_done, "amount": float(l.amount_per_set), "unit": l.unit,
            "prescription": prescription,
            "logged_at": l.logged_at.isoformat(),
            "implausible": l.implausible, "source": l.source,
        })
    return out


def streak(session: Session, user_id: str) -> dict:
    """4.2 — computed from the logs, never cached as the authority."""
    days = session.scalars(
        select(ActivityLog.local_date).where(ActivityLog.user_id == user_id)
        .group_by(ActivityLog.local_date).order_by(ActivityLog.local_date.desc())
    ).all()
    if not days:
        return {"current": 0, "last_active": None, "active_days": 0}

    today = today_ist()
    # A streak survives "not yet today" — it breaks only after a full day missed.
    cursor = today if days[0] == today else today - timedelta(days=1)
    current = 0
    for d in days:
        if d == cursor:
            current += 1
            cursor -= timedelta(days=1)
        elif d < cursor:
            break
    return {"current": current, "last_active": days[0].isoformat(),
            "active_days": len(days)}


MAX_TREND_DAYS = 365


def trend(session: Session, user_id: str, *, days: int | None = None,
          start_date: date | None = None, end_date: date | None = None) -> list[dict]:
    """4.3 — logged-activity counts over an arbitrary window.

    Either pass `days` (counts back from today, inclusive) or an explicit
    start_date/end_date pair for a custom range. weekly_volume below is just
    this with days=7, so the original 7-day dashboard chart is unchanged.
    """
    today = today_ist()
    if start_date is not None or end_date is not None:
        if start_date is None or end_date is None:
            raise ValidationError("start_date and end_date must both be provided together.")
        if start_date > end_date:
            raise ValidationError("start_date must be on or before end_date.")
        if end_date > today:
            end_date = today
        if (end_date - start_date).days + 1 > MAX_TREND_DAYS:
            raise ValidationError(f"Custom range cannot exceed {MAX_TREND_DAYS} days.")
        start, end = start_date, end_date
    else:
        n = days if days is not None else 7
        if n < 1 or n > MAX_TREND_DAYS:
            raise ValidationError(f"days must be between 1 and {MAX_TREND_DAYS}.")
        start, end = today - timedelta(days=n - 1), today

    rows = session.execute(
        select(ActivityLog.local_date, func.count())
        .where(ActivityLog.user_id == user_id,
               ActivityLog.local_date >= start,
               ActivityLog.local_date <= end,
               ActivityLog.implausible.is_(False))
        .group_by(ActivityLog.local_date)
    ).all()
    counts = {d: n for d, n in rows}
    span = (end - start).days + 1
    return [{"date": (start + timedelta(days=i)).isoformat(),
             "label": (start + timedelta(days=i)).strftime("%a")[0],
             "count": counts.get(start + timedelta(days=i), 0)}
            for i in range(span)]


def weekly_volume(session: Session, user_id: str) -> list[dict]:
    """Seven days of logged volume for the dashboard chart (unchanged, 4.3)."""
    return trend(session, user_id, days=7)


# --- Suggestions + videos --------------------------------------------------

def _dashboard_slice(session: Session, user_id: str
                     ) -> tuple[list[dict], set[str], str, set[str], set[str]]:
    """The activities the dashboard actually shows, and the tier band used to
    decide it. Shared by suggestions_today (renders them) and list_catalogue
    (offers everything ELSE) so the two can never quietly disagree about what
    "already on your dashboard" means.

    Tier slicing governs `template` entries only. Anything the user put there
    themselves — via the catalogue (`manual`) or an override (`user_override`)
    — is always visible regardless of tier; a deliberate add should never be
    silently swallowed by a beginner-tier cap the user has already stepped
    past for that one activity.
    """
    full_program = get_program(session, user_id)
    program = [p for p in full_program if p["status"] == "active"]
    prof = session.get(UserProfile, user_id)
    band = prof.experience_band if prof else "beginner"

    # A manual removal (remove_program_entry) marks the row suspended rather
    # than deleting it, specifically so it can be excluded here WITHOUT ever
    # being backfilled. _tier_slice is run as if the removal never happened
    # (the removed row is temporarily added back into consideration just for
    # this calculation), then subtracted straight out of the result - this
    # is what actually cancels the backfill, whatever the tier's cap is.
    #
    # A plain "trim N off the tail" correction (tried first, wrong) over-
    # corrects the moment nothing was being backfilled in the first place -
    # e.g. the "experienced" band has no cap at all, or the activity pool
    # has simply run out - and ends up dropping one extra, unrelated
    # activity on top of the one actually removed.
    user_removed = set(session.scalars(
        select(UserProgram.activity_type).where(
            UserProgram.user_id == user_id, UserProgram.status == "suspended",
            UserProgram.suspended_reason == "user_removed")
    ).all())
    program_for_tier = [p for p in full_program
                        if p["status"] == "active" or p["activity_type"] in user_removed]
    default_codes, optional_codes = _tier_slice(program_for_tier, band)
    default_codes -= user_removed
    optional_codes -= user_removed

    self_added = {p["activity_type"] for p in program
                  if p["source"] in ("manual", "user_override")}
    return program, default_codes | optional_codes | self_added, band, default_codes, optional_codes


def suggestions_today(session: Session, user_id: str, limit: int = 3) -> dict:
    if is_referred(session, user_id):
        return {"referred": True, "message": REFERRAL_MESSAGE, "items": []}

    program, visible, band, default_codes, optional_codes = _dashboard_slice(session, user_id)
    # Latest log per activity today, not just "did it happen": a log from
    # before this program entry's most recent add/re-add (see add_program_entry)
    # must not count as completing today's fresh instance of it.
    last_logged = dict(session.execute(
        select(ActivityLog.activity_type, func.max(ActivityLog.logged_at)).where(
            ActivityLog.user_id == user_id,
            ActivityLog.local_date == today_ist(),
            ActivityLog.activity_type.is_not(None))
        .group_by(ActivityLog.activity_type)
    ).all())
    added_at_by_type = dict(session.execute(
        select(UserProgram.activity_type, UserProgram.added_at).where(
            UserProgram.user_id == user_id, UserProgram.status == "active")
    ).all())

    videos = {v.activity_type: v for v in session.scalars(select(VideoCatalog)).all()}

    def tier_of(activity_type: str) -> str:
        if activity_type in optional_codes:
            return "optional"
        if activity_type in default_codes:
            return "default"
        return "added"  # self-added, visible beyond what this tier normally shows

    items = []
    for p in program:
        if p["activity_type"] not in visible:
            continue
        v = videos.get(p["activity_type"])
        logged_at = last_logged.get(p["activity_type"])
        added_at = added_at_by_type.get(p["activity_type"])
        completed = (logged_at is not None and added_at is not None
                     and logged_at >= added_at)
        items.append({**p, "completed": completed,
                      "video_url": v.watch_url if v else None,
                      "tier": tier_of(p["activity_type"])})

    excluded_count = len(_blocked_activities(session, user_id))
    tier_note = (f"{excluded_count} exercise(s) left out based on your health conditions"
                 if excluded_count else None)

    # Surface what is still outstanding first; within that, most essential first
    # rather than alphabetical — the same priority_order the tier slice uses.
    items.sort(key=lambda i: (i["completed"], i["priority_order"]))
    return {"referred": False, "message": None, "items": items[:limit],
            "all_items": items, "tier": band, "tier_note": tier_note}


def video_library(session: Session, user_id: str) -> dict:
    if is_referred(session, user_id):
        # 6.3 — the referral gate applies on every exercise-content path.
        return {"referred": True, "message": REFERRAL_MESSAGE, "videos": []}
    blocked = _blocked_activities(session, user_id)
    rows = session.execute(
        select(VideoCatalog, ActivityTaxonomy)
        .join(ActivityTaxonomy,
              ActivityTaxonomy.activity_type == VideoCatalog.activity_type)
        .order_by(ActivityTaxonomy.display_name)
    ).all()
    return {"referred": False, "message": None, "videos": [{
        "activity_type": v.activity_type,
        "display_name": t.display_name,
        "title": v.title,
        "video_id": v.youtube_video_id or None,
        "curated": bool(v.youtube_video_id),
        "watch_url": (f"https://www.youtube.com/watch?v={v.youtube_video_id}"
                      if v.youtube_video_id else None),
        "prescription_type": t.prescription_type,
        "per_side": t.per_side,
        "demo_images": t.demo_images,
        "demo_gif": t.demo_gif,
        "demo_gif_source": t.demo_gif_source,
        "instructions": t.instructions,
    } for v, t in rows if v.activity_type not in blocked]}


def set_video(session: Session, activity_type: str, url_or_id: str,
              title: str | None = None) -> dict:
    """Admin curation (7.1). Accepts a full YouTube URL or a bare id."""
    row = session.scalar(
        select(VideoCatalog).where(VideoCatalog.activity_type == activity_type))
    if row is None:
        raise NotFound(f"No catalogue row for {activity_type}")

    vid = _extract_video_id(url_or_id)
    if not vid:
        raise ValidationError(
            "Could not read a YouTube video id from that. Paste a watch URL, a "
            "youtu.be link, or the 11-character id itself."
        )
    row.youtube_video_id = vid
    row.watch_url = f"https://www.youtube.com/watch?v={vid}"
    if title:
        row.title = title
    return {"activity_type": activity_type, "video_id": vid}


def _extract_video_id(value: str) -> str | None:
    """Pull the id out of any of the shapes a person is likely to paste."""
    import re
    value = (value or "").strip()
    if not value:
        return None
    patterns = [
        r"(?:youtube\.com/watch\?(?:.*&)?v=)([A-Za-z0-9_-]{11})",
        r"(?:youtu\.be/)([A-Za-z0-9_-]{11})",
        r"(?:youtube\.com/embed/)([A-Za-z0-9_-]{11})",
        r"(?:youtube\.com/shorts/)([A-Za-z0-9_-]{11})",
        r"^([A-Za-z0-9_-]{11})$",
    ]
    for pat in patterns:
        m = re.search(pat, value)
        if m:
            return m.group(1)
    return None


def profile_summary(session: Session, user_id: str) -> dict:
    prof = session.get(UserProfile, user_id)
    conds = session.execute(
        select(Condition.condition_code, Condition.display_name, Condition.bucket)
        .join(UserCondition, UserCondition.condition_code == Condition.condition_code)
        .where(UserCondition.user_id == user_id)
    ).all()
    return {
        "onboarded": prof is not None,
        "age": prof.age if prof else None,
        "goal": prof.goal if prof else None,
        "experience_band": prof.experience_band if prof else None,
        "unit_preference": prof.unit_preference if prof else "km",
        "conditions": [{"code": c, "name": n, "bucket": b} for c, n, b in conds],
        "referred": is_referred(session, user_id),
    }
