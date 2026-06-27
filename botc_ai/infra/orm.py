from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from botc_ai.infra.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class GameORM(Base):
    __tablename__ = "games"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    game_version: Mapped[str] = mapped_column(String(32), nullable=False, default="0.1.0")
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    day: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    budget_usd: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    mock_ai: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    players: Mapped[list[PlayerORM]] = relationship(cascade="all, delete-orphan")


class PlayerORM(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    player_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    seat: Mapped[int] = mapped_column(Integer, nullable=False)
    is_human: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    alive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ghost_vote_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (UniqueConstraint("game_id", "player_id", name="uq_players_game_player"),)


class AssignmentORM(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    player_id: Mapped[str] = mapped_column(String(32), index=True)
    true_role: Mapped[str] = mapped_column(String(64), nullable=False)
    apparent_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    true_alignment: Mapped[str] = mapped_column(String(16), nullable=False)
    postgame_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class RoleStateORM(Base):
    __tablename__ = "role_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    player_id: Mapped[str] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)


class PublicEventORM(Base):
    __tablename__ = "public_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    day: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    participants_json: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PrivateEventORM(Base):
    __tablename__ = "private_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    day: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    participants_json: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NominationORM(Base):
    __tablename__ = "nominations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    day: Mapped[int] = mapped_column(Integer, nullable=False)
    nominator_id: Mapped[str] = mapped_column(String(32), nullable=False)
    nominee_id: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    defense: Mapped[str | None] = mapped_column(Text, nullable=True)
    votes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    eligible_for_execution: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class VoteORM(Base):
    __tablename__ = "votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    nomination_id: Mapped[str] = mapped_column(String(64), index=True)
    day: Mapped[int] = mapped_column(Integer, nullable=False)
    voter_id: Mapped[str] = mapped_column(String(32), nullable=False)
    nominee_id: Mapped[str] = mapped_column(String(32), nullable=False)
    vote: Mapped[bool] = mapped_column(Boolean, nullable=False)
    used_ghost_vote: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    public_reason: Mapped[str] = mapped_column(Text, nullable=False)


class AIMemoryORM(Base):
    __tablename__ = "ai_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    player_id: Mapped[str] = mapped_column(String(32), index=True)
    memory_json: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (UniqueConstraint("game_id", "player_id", name="uq_ai_mem_game_player"),)


class ApiUsageORM(Base):
    __tablename__ = "api_usage"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    player_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(128), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GameResultORM(Base):
    __tablename__ = "game_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    winner: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    day: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
