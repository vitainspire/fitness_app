"""Module 3 (profile) + Module 4 (activity) + Module 14 (program) tables."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer,
    Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class UserProfile(Base):
    """3.1 — age (18+), goal, experience band, unit preference."""
    __tablename__ = "user_profiles"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    goal: Mapped[str] = mapped_column(String(32), nullable=False)
    experience_band: Mapped[str] = mapped_column(String(16), nullable=False)
    unit_preference: Mapped[str] = mapped_column(String(8), nullable=False, default="km")
    onboarded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        # 18+ only. Accepting minors brings parental consent and different
        # guidance entirely — out of scope.
        CheckConstraint("age >= 18", name="prof_adults_only"),
        CheckConstraint(
            "experience_band IN ('beginner','1m','2m','3m','experienced')",
            name="prof_band_known"),
        CheckConstraint("unit_preference IN ('km','mi')", name="prof_unit_known"),
    )


class UserCondition(Base):
    """3.2 — conditions selected from the closed list. Never free text."""
    __tablename__ = "user_conditions"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    condition_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("conditions.condition_code"), primary_key=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)


class CustomExercise(Base):
    """4.4 — a user's own free-named exercise.

    Personal (owned by exactly one user), tracking-only: never suggested,
    never programmed (13.1). Exists purely so activity_logs has something
    to point at when a user logs something outside the reviewed taxonomy.
    """
    __tablename__ = "custom_exercises"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # Lowercased/trimmed, so "Swimming" and "swimming " collide as one entry.
    normalized_name: Mapped[str] = mapped_column(String(64), nullable=False)
    # 1 to 3 of 'reps'/'distance'/'duration' — a user can track an exercise by
    # more than one measure at once (e.g. Swimming by both Distance and Time).
    # unit and plausible_max_total aren't stored: both are fixed per type (see
    # CUSTOM_MEASUREMENT_UNITS / CUSTOM_MEASUREMENT_DEFAULT_MAX in service.py),
    # so keeping a copy on the row would just be a second place to drift.
    measurement_types: Mapped[list[str]] = mapped_column(ARRAY(String(16)), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Soft delete (mirrors User.deleted_at): the row stays so past logs that
    # reference it via the FK keep displaying correctly (recent_logs joins
    # on this table by name) - it just drops out of list_custom_exercises
    # and can no longer be logged against. This is what actually lets a
    # user delete an exercise with history, instead of refusing outright.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # No DB-level uniqueness here on purpose: a name must stay free to
        # reuse once its old row is soft-deleted, and the service layer
        # already enforces "no two ACTIVE exercises share a name" itself.
        CheckConstraint(
            "measurement_types <@ ARRAY['reps','distance','duration']::varchar[] "
            "AND array_length(measurement_types, 1) BETWEEN 1 AND 3",
            name="custom_measurement_known"),
    )


class ActivityLog(Base):
    """4.1 — one entry point for manual and voice input alike."""
    __tablename__ = "activity_logs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # Exactly one of these two is set (see log_exactly_one_source below).
    # Official taxonomy activity:
    activity_type: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("activity_taxonomy.activity_type"), nullable=True)
    # 4.4 — or the user's own custom exercise. No ON DELETE CASCADE here on
    # purpose: deleting a custom exercise must never silently erase the
    # history logged against it (see delete_custom_exercise in service.py,
    # which refuses the delete instead while logs still reference it).
    custom_exercise_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("custom_exercises.id"), nullable=True)

    # What was actually performed, in the shape it was prescribed.
    sets_done: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    amount_per_set: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    # sets x amount x sides. Adherence, the implausibility ceiling and the
    # soreness reduction all need ONE comparable number whatever the shape.
    total_volume: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    local_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")

    # 4.6 — kept, flagged, and excluded from progression. Never silently dropped:
    # rejecting what a user entered is worse than storing it with a marker.
    implausible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Idempotency for offline replay (12.5). A retry must not double-log.
    client_entry_id: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("user_id", "client_entry_id", name="uq_log_client_entry"),
        Index("ix_activity_logs_user_date", "user_id", "local_date"),
        CheckConstraint("source IN ('manual','voice')", name="log_source_known"),
        CheckConstraint("amount_per_set > 0", name="log_amount_positive"),
        CheckConstraint("sets_done >= 1", name="log_sets_positive"),
        CheckConstraint("num_nonnulls(activity_type, custom_exercise_id) = 1",
                        name="log_exactly_one_source"),
    )


class UserProgram(Base):
    """14.1 — the persistent program.

    One entry per activity per user, enforced by the primary key. Without that a
    logged activity has no unambiguous target to score adherence against.
    """
    __tablename__ = "user_program"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    activity_type: Mapped[str] = mapped_column(
        String(64), ForeignKey("activity_taxonomy.activity_type"), primary_key=True)

    target_sets: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    target_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="template")
    suspended_reason: Mapped[str | None] = mapped_column(String(256))
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('active','suspended')", name="prog_status_known"),
        CheckConstraint("source IN ('template','agent','manual','user_override')",
                        name="prog_source_known"),
        CheckConstraint("target_amount > 0", name="prog_amount_positive"),
        CheckConstraint("target_sets >= 1", name="prog_sets_positive"),
    )


class OverrideConsent(Base):
    """14.7 — the record of every 'my doctor advised this' override.

    Answers "what was this user told, and when?" after the fact. Not the app's
    protection — the reviewed safety grid is that. This is supporting evidence
    that the warning was actually shown, worded exactly as shown, at the time
    of the decision.

    On account deletion (1.10), `user_id` is nulled and the row otherwise kept:
    proof the warning system worked, without holding health data tied to an
    identifiable person once they're gone.
    """
    __tablename__ = "override_consents"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"))
    condition_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("conditions.condition_code"), nullable=False)
    activity_type: Mapped[str] = mapped_column(
        String(64), ForeignKey("activity_taxonomy.activity_type"), nullable=False)
    grid_verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    # No global grid version counter exists yet (14.8 isn't built either) — the
    # safety cell's own reviewed_at is the closest honest stand-in for "which
    # version of the guidance this was."
    grid_reviewed_at: Mapped[date | None] = mapped_column(Date)
    warning_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("grid_verdict IN ('safe','modify','avoid')",
                        name="oc_verdict_known"),
    )
