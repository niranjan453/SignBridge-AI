from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    token_version: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    settings: Mapped["UserSettings"] = relationship(cascade="all, delete-orphan", back_populates="user")


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    preferred_language: Mapped[str] = mapped_column(String(35), default="en-IN")
    save_history: Mapped[bool] = mapped_column(default=True)
    user: Mapped[User] = relationship(back_populates="settings")


class Translation(Base):
    __tablename__ = "translations"
    __table_args__ = (
        Index("ix_translations_user_created_id", "user_id", "created_at", "id"),
        CheckConstraint("status IN ('completed', 'failed', 'timed_out')", name="ck_translation_status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    mode: Mapped[str] = mapped_column(String(32))
    input_text: Mapped[str | None] = mapped_column(Text)
    output_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    model_version: Mapped[str] = mapped_column(String(100))
    is_mock: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Feedback(Base):
    __tablename__ = "translation_feedback"
    __table_args__ = (CheckConstraint("rating BETWEEN 1 AND 5", name="ck_feedback_rating"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    translation_id: Mapped[UUID] = mapped_column(
        ForeignKey("translations.id", ondelete="CASCADE"), unique=True
    )
    rating: Mapped[int]
    comment: Mapped[str | None] = mapped_column(String(500))
