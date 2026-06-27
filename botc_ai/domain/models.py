from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from botc_ai.domain.roles import ROLE_SPECS, Alignment, RoleType, role_alignment


class Phase(StrEnum):
    SETUP = "SETUP"
    FIRST_NIGHT = "FIRST_NIGHT"
    DAWN = "DAWN"
    DAY_DISCUSSION = "DAY_DISCUSSION"
    PRIVATE_CHAT = "PRIVATE_CHAT"
    NOMINATIONS = "NOMINATIONS"
    VOTING = "VOTING"
    EXECUTION = "EXECUTION"
    NIGHT = "NIGHT"
    GAME_OVER = "GAME_OVER"


class AudienceScope(StrEnum):
    PUBLIC = "PUBLIC"
    PLAYER_ONLY = "PLAYER_ONLY"
    PRIVATE_CHAT_PARTICIPANTS = "PRIVATE_CHAT_PARTICIPANTS"
    STORYTELLER_INTERNAL = "STORYTELLER_INTERNAL"
    POSTGAME_ONLY = "POSTGAME_ONLY"


class GameEvent(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    day: int
    phase: Phase
    scope: AudienceScope
    message: str
    type: str = "generic"
    actor_id: str | None = None
    target_ids: list[str] = Field(default_factory=list)
    participants: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlayerTruth(BaseModel):
    id: str
    name: str
    seat: int
    is_human: bool = False
    true_role: str
    apparent_role: str | None = None
    alive: bool = True
    ghost_vote_available: bool = True
    nominated_today: bool = False
    was_nominated_today: bool = False
    death_cause: str | None = None
    death_day: int | None = None
    role_history: list[str] = Field(default_factory=list)

    @property
    def visible_role(self) -> str:
        return self.apparent_role or self.true_role

    @property
    def alignment(self) -> Alignment:
        return role_alignment(self.true_role)

    @property
    def visible_alignment(self) -> Alignment:
        return role_alignment(self.visible_role)


class PlayerSession(BaseModel):
    player_id: str
    token: str
    claimed_name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WakeEvent(BaseModel):
    day: int
    player_id: str
    role: str
    reason: str = "own_ability"


class TransformationEvent(BaseModel):
    day: int
    player_id: str
    from_role: str
    to_role: str
    reason: str


class NominationRecord(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    day: int
    nominator_id: str
    nominee_id: str
    reason: str
    defense: str | None = None
    votes: int = 0
    threshold: int = 0
    eligible_for_execution: bool = False
    resolved: bool = False


class VoteRecord(BaseModel):
    nomination_id: str
    day: int
    voter_id: str
    nominee_id: str
    vote: bool
    used_ghost_vote: bool = False
    public_reason: str = ""


class AIMemory(BaseModel):
    player_id: str
    suspicion: dict[str, float] = Field(default_factory=dict)
    known_claims: dict[str, str] = Field(default_factory=dict)
    public_claim: str | None = None
    private_promises: list[str] = Field(default_factory=list)
    current_bluff: str | None = None
    next_intent: str = "觀察局勢"
    summary: str = ""

    def compact(self, max_chars: int = 700) -> None:
        if len(self.summary) > max_chars:
            self.summary = self.summary[-max_chars:]
        if len(self.private_promises) > 8:
            self.private_promises = self.private_promises[-8:]


class ApiUsageRecord(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    game_id: str
    player_id: str | None
    model: str
    purpose: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    estimated_usd: float | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GameResult(BaseModel):
    winner: Alignment
    reason: str
    day: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TruthState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: str
    version: str = "0.1.0"
    seed: int | None = None
    human_id: str = "human"
    day: int = 1
    phase: Phase = Phase.SETUP
    players: list[PlayerTruth]
    current_demon_id: str | None = None
    events: list[GameEvent] = Field(default_factory=list)
    nominations: list[NominationRecord] = Field(default_factory=list)
    votes: list[VoteRecord] = Field(default_factory=list)
    wake_events: list[WakeEvent] = Field(default_factory=list)
    transformations: list[TransformationEvent] = Field(default_factory=list)
    ai_memories: dict[str, AIMemory] = Field(default_factory=dict)
    api_usage: list[ApiUsageRecord] = Field(default_factory=list)
    result: GameResult | None = None
    last_night_deaths: list[str] = Field(default_factory=list)
    pending_klutz_id: str | None = None
    execution_done_today: bool = False
    max_days: int = 5
    budget_usd: float = 1.0
    ai_budget_paused: bool = False
    mock_ai: bool = False
    ai_cooldown_seconds: int = 10
    last_ai_tick_at: datetime | None = None
    discussion_rounds_today: int = 0
    discussion_speakers_today: list[str] = Field(default_factory=list)
    ai_private_chat_initiated_today: list[str] = Field(default_factory=list)
    player_sessions: dict[str, PlayerSession] = Field(default_factory=dict)
    ai_last_status: str = "AI 正在等待桌面節奏。"
    ai_active_player_id: str | None = None

    def by_id(self, player_id: str) -> PlayerTruth:
        for player in self.players:
            if player.id == player_id:
                return player
        raise KeyError(f"Unknown player id: {player_id}")

    def living(self) -> list[PlayerTruth]:
        return [player for player in self.players if player.alive]

    def living_count(self) -> int:
        return len(self.living())

    def minions(self) -> list[PlayerTruth]:
        return [
            player
            for player in self.players
            if ROLE_SPECS[player.true_role].role_type == RoleType.MINION
        ]

    def living_minions(self) -> list[PlayerTruth]:
        return [player for player in self.minions() if player.alive]

    def demon(self) -> PlayerTruth | None:
        if self.current_demon_id is None:
            return None
        try:
            demon = self.by_id(self.current_demon_id)
        except KeyError:
            return None
        return demon if demon.alive and demon.true_role == "imp" else None

    def add_event(
        self,
        message: str,
        *,
        scope: AudienceScope = AudienceScope.PUBLIC,
        type: str = "generic",
        actor_id: str | None = None,
        target_ids: list[str] | None = None,
        participants: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GameEvent:
        event = GameEvent(
            day=self.day,
            phase=self.phase,
            scope=scope,
            message=message,
            type=type,
            actor_id=actor_id,
            target_ids=target_ids or [],
            participants=participants or [],
            metadata=metadata or {},
        )
        self.events.append(event)
        return event


class PublicPlayer(BaseModel):
    id: str
    name: str
    seat: int
    is_human: bool
    alive: bool
    ghost_vote_available: bool
    nominated_today: bool
    was_nominated_today: bool
    claimed: bool = False


class ScriptRoleView(BaseModel):
    slug: str
    zh_name: str
    role_type: str
    ability: str


class UsageSummary(BaseModel):
    calls: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    estimated_usd: float | None = 0.0
    budget_usd: float = 1.0
    remaining_usd: float | None = 1.0
    by_player: dict[str, dict[str, float | int | None]] = Field(default_factory=dict)
    by_purpose: dict[str, dict[str, float | int | None]] = Field(default_factory=dict)
    estimate_note: str = "費用為本機估算；缺少價格資料時只顯示 token。"


class PublicState(BaseModel):
    game_id: str
    day: int
    phase: Phase
    mock_ai: bool
    players: list[PublicPlayer]
    public_events: list[GameEvent]
    nominations: list[NominationRecord]
    votes: list[VoteRecord]
    last_night_deaths: list[str]
    current_on_the_block: str | None
    current_high_votes: int
    result: GameResult | None
    usage: UsageSummary
    ai_status: str
    ai_active_player_id: str | None
    ai_cooldown_seconds: int
    discussion_rounds_today: int


class PlayerPrivateView(BaseModel):
    player_id: str
    name: str
    seat: int
    alive: bool
    ghost_vote_available: bool
    role: ScriptRoleView
    apparent_alignment: Alignment
    private_events: list[GameEvent]
    private_chats: list[GameEvent]
    memory: AIMemory | None = None
    legal_actions: list[str] = Field(default_factory=list)


class PostgameReveal(BaseModel):
    players: list[dict[str, Any]]
    transformations: list[TransformationEvent]
    all_events: list[GameEvent]
    ai_memories: dict[str, AIMemory]


class GameView(BaseModel):
    public: PublicState
    private: PlayerPrivateView
    script: list[ScriptRoleView]
    postgame: PostgameReveal | None = None
    dev_reveal: PostgameReveal | None = None
    session_token: str | None = None


def script_view() -> list[ScriptRoleView]:
    return [
        ScriptRoleView(
            slug=role.slug,
            zh_name=role.zh_name,
            role_type=role.zh_type,
            ability=role.ability,
        )
        for role in ROLE_SPECS.values()
    ]
