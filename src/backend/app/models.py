from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_id() -> str:
    """Random 48-bit hex id. For groups it doubles as the invite token."""
    return secrets.token_hex(6)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    travel_mode: Mapped[str] = mapped_column(String(16))
    meet_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    venue_types: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    members: Mapped[list[Member]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Member.created_at",
    )
    recommendations: Mapped[list[Recommendation]] = relationship(back_populates="group", cascade="all, delete-orphan", passive_deletes=True)

    def touch(self) -> None:
        self.updated_at = utcnow()


class Member(Base):
    __tablename__ = "members"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    travel_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    group: Mapped[Group] = relationship(back_populates="members")


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    group: Mapped[Group] = relationship(back_populates="recommendations")
