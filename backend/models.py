import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base

class Seasons(Base):
    __tablename__ = "seasons"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), 
        primary_key=True, 
        server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    os: Mapped[str] = mapped_column(
        String(10), 
        nullable=False
    )
    sdk_version: Mapped[str] = mapped_column(
        String(10), 
        nullable=False
    )
    simulator_version: Mapped[str] = mapped_column(
        String(10), 
        nullable=False
    )
    track_id: Mapped[str] = mapped_column(
        String(100), 
        nullable=False
    )  # e.g. donkey-generated-track-v0
    car_config: Mapped[dict] = mapped_column(
        JSONB, 
        nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(10), 
        nullable=False
    )  # running / completed / crashed
    ended_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        nullable=False, 
        server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        nullable=False, 
        server_default=text("now()"),
        onupdate=text("now()")
    )

class Users(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
