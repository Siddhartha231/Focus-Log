"""Database tables for the server (SQLAlchemy). Works on Postgres and SQLite."""

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # Goes up by one for every change accepted for this user. Devices remember
    # the last value they saw and ask for everything after it.
    change_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Token(Base):
    """One login on one device. Deleting the row logs that device out."""
    __tablename__ = "tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_name: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StudySession(Base):
    __tablename__ = "sessions"

    # the uuid comes from the device; (user, uuid) is the primary key so two
    # users can never touch each other's rows
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    subject: Mapped[str] = mapped_column(String(200))
    started_at: Mapped[str] = mapped_column(String(32))   # local wall-clock time, ISO text
    duration_seconds: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    updated_at: Mapped[int] = mapped_column(BigInteger)   # ms since epoch, set by the device
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    seq: Mapped[int] = mapped_column(BigInteger)          # value of change_seq when last changed

    __table_args__ = (Index("ix_sessions_user_seq", "user_id", "seq"),)
