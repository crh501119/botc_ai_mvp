from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CreateGameRequest(BaseModel):
    human_name: str = Field(default="旅人", max_length=40)
    human_count: int = Field(default=1, ge=1, le=6)
    discussion_mode: Literal["free", "ordered"] = "free"
    shuffle_seats_on_start: bool = True
    night_seconds: int = Field(default=90, ge=15, le=1800)
    day_discussion_seconds: int = Field(default=240, ge=30, le=3600)
    private_chat_seconds: int = Field(default=180, ge=15, le=3600)
    nominations_seconds: int = Field(default=180, ge=30, le=3600)
    voting_seconds: int = Field(default=60, ge=15, le=600)
    seed: int | None = None
    budget_usd: float = Field(default=1.0, ge=0.0, le=100.0)
    mock_ai: bool | None = None
    force_minion: str | None = None


class JoinGameRequest(BaseModel):
    player_id: str
    player_name: str = Field(min_length=1, max_length=40)
    token: str | None = None


class PublicSpeechRequest(BaseModel):
    player_id: str = "human"
    speech: str = Field(min_length=1, max_length=300)


class PrivateChatRequest(BaseModel):
    from_id: str = "human"
    to_id: str
    message: str = Field(min_length=1, max_length=300)


class NominationRequest(BaseModel):
    nominator_id: str = "human"
    nominee_id: str
    reason: str = Field(default="我想測試這個說法。", max_length=180)


class VoteRequest(BaseModel):
    player_id: str = "human"
    vote: bool


class ArtistQuestionRequest(BaseModel):
    player_id: str = "human"
    question: str = Field(min_length=1, max_length=300)


class KlutzChoiceRequest(BaseModel):
    player_id: str = "human"
    target_id: str


class NightTargetRequest(BaseModel):
    player_id: str = "human"
    target_id: str


class ChambermaidChoiceRequest(BaseModel):
    player_id: str = "human"
    target_ids: list[str] = Field(min_length=2, max_length=2)


class PhaseReadyRequest(BaseModel):
    player_id: str = "human"


class BudgetUpdateRequest(BaseModel):
    budget_usd: float = Field(ge=0.0, le=100.0)
    mock_ai: bool | None = None


class ActionResponse(BaseModel):
    ok: bool
    message: str
