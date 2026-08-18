"""Module 10 — a durable place issues get written the moment they happen.

Right now nothing survives past the terminal window a request happened to
print into. This table is deliberately narrow: it only ever gets a row for
a genuine unexpected server error, or one of the two named events in
REQUIREMENTS 10.2 (session theft, push delivery failure). An expected
ApiError (wrong password, a 404, a validation failure) is normal
application behaviour, not something staff need to review, and must
never end up in here.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.platform.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class AppIssue(Base):
    __tablename__ = "app_issues"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    issue_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str | None] = mapped_column(String(256))
    # Nulled rather than cascade-deleted on account deletion (1.10) - this
    # row is operational history about the system, not personal data that
    # needs to disappear with the account.
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"))
    # Ties one row back to the rest of that request's logs (10.1).
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)


def record_issue(session: Session, *, issue_type: str, message: str,
                  path: str | None = None, user_id: str | None = None,
                  request_id: str | None = None) -> None:
    session.add(AppIssue(
        issue_type=issue_type, message=message[:8000], path=path,
        user_id=user_id, request_id=request_id,
    ))
    session.flush()
