from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from botc_ai.domain.artist import ArtistStructuredQuestion


class AIMemoryUpdate(BaseModel):
    suspicion_delta: dict[str, float] = Field(default_factory=dict)
    known_claims: dict[str, str] = Field(default_factory=dict)
    public_claim: str | None = None
    private_promises: list[str] = Field(default_factory=list)
    current_bluff: str | None = None
    next_intent: str = "觀察"
    summary: str = Field(default="", max_length=900)


class PublicSpeechAction(BaseModel):
    speech: str = Field(min_length=1, max_length=280)
    claimed_role: str | None = None
    concise_rationale: str = Field(default="", max_length=160)
    memory_update: AIMemoryUpdate = Field(default_factory=AIMemoryUpdate)


class PrivateMessageAction(BaseModel):
    target_id: str
    message: str = Field(min_length=1, max_length=240)
    concise_rationale: str = Field(default="", max_length=160)
    memory_update: AIMemoryUpdate = Field(default_factory=AIMemoryUpdate)


class NominationAction(BaseModel):
    nominate: bool = False
    target_id: str | None = None
    reason: str = Field(default="目前需要更多資訊。", max_length=180)
    concise_rationale: str = Field(default="", max_length=160)
    memory_update: AIMemoryUpdate = Field(default_factory=AIMemoryUpdate)

    @field_validator("target_id")
    @classmethod
    def empty_target_to_none(cls, value: str | None) -> str | None:
        return value or None


class DefenseAction(BaseModel):
    statement: str = Field(min_length=1, max_length=240)
    concise_rationale: str = Field(default="", max_length=160)
    memory_update: AIMemoryUpdate = Field(default_factory=AIMemoryUpdate)


class VoteAction(BaseModel):
    vote: bool
    public_reason: str = Field(default="依目前資訊判斷。", max_length=120)
    concise_rationale: str = Field(default="", max_length=160)
    memory_update: AIMemoryUpdate = Field(default_factory=AIMemoryUpdate)


class NightTargetAction(BaseModel):
    target_id: str
    concise_rationale: str = Field(default="", max_length=160)
    memory_update: AIMemoryUpdate = Field(default_factory=AIMemoryUpdate)


class ChambermaidChoice(BaseModel):
    target_ids: list[str] = Field(min_length=2, max_length=2)
    concise_rationale: str = Field(default="", max_length=160)
    memory_update: AIMemoryUpdate = Field(default_factory=AIMemoryUpdate)


class KlutzChoice(BaseModel):
    target_id: str
    concise_rationale: str = Field(default="", max_length=160)
    memory_update: AIMemoryUpdate = Field(default_factory=AIMemoryUpdate)


class AIActionBundle(BaseModel):
    speech: PublicSpeechAction | None = None
    private_message: PrivateMessageAction | None = None
    nomination: NominationAction | None = None
    defense: DefenseAction | None = None
    vote: VoteAction | None = None
    night_target: NightTargetAction | None = None
    chambermaid_choice: ChambermaidChoice | None = None
    klutz_choice: KlutzChoice | None = None
    artist_question: ArtistStructuredQuestion | None = None
    memory_update: AIMemoryUpdate = Field(default_factory=AIMemoryUpdate)
