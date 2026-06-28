from __future__ import annotations

import random
from dataclasses import dataclass
from uuid import uuid4

from botc_ai.domain.models import (
    AIMemory,
    AudienceScope,
    DiscussionMode,
    Phase,
    PlayerTruth,
    TruthState,
)
from botc_ai.domain.roles import MINIONS, OUTSIDERS, ROLE_SPECS, TOWNSFOLK


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    style: str
    description: str
    speech_bias: str
    nomination_bias: str
    vote_bias: str


AI_PERSONAS: list[Persona] = [
    Persona(
        id="ai_1",
        name="林鏡",
        style="邏輯分析型",
        description="建立世界觀、比較配置、重視資訊一致性。",
        speech_bias="短而清楚地列出兩個可能世界。",
        nomination_bias="需要資訊矛盾或票型壓力才提名。",
        vote_bias="偏好投給與公開資訊矛盾的人。",
    ),
    Persona(
        id="ai_2",
        name="周棠",
        style="社交協調型",
        description="主動私聊、建立信任、協調公開說法。",
        speech_bias="語氣合作，常邀請交換資訊。",
        nomination_bias="較少早提名，除非能形成共識。",
        vote_bias="跟隨可信聯盟，但會保留轉票空間。",
    ),
    Persona(
        id="ai_3",
        name="沈炬",
        style="激進施壓型",
        description="願意早期提名，逼迫他人表態，風險容忍高。",
        speech_bias="直接施壓，要求明確角色或資訊。",
        nomination_bias="低門檻提名，喜歡測票。",
        vote_bias="願意用票壓出資訊。",
    ),
    Persona(
        id="ai_4",
        name="許霜",
        style="保守懷疑型",
        description="關注票型和矛盾，不輕易公開自己的角色。",
        speech_bias="謹慎，常提醒不要過早暴露核心資訊。",
        nomination_bias="通常等第二輪才提名。",
        vote_bias="不喜歡跟風，除非票型已清楚。",
    ),
    Persona(
        id="ai_5",
        name="祁風",
        style="直覺混沌型",
        description="容許較高 bluff，偶爾模糊宣稱，但仍遵守可見資訊。",
        speech_bias="模糊、跳躍，但會給出可檢驗的直覺。",
        nomination_bias="可能用直覺提名邊緣目標。",
        vote_bias="偏好打破僵局。",
    ),
]


def _pick_roles(
    rng: random.Random,
    *,
    force_minion: str | None = None,
    force_roles: list[str] | None = None,
) -> list[str]:
    if force_roles is not None:
        if len(force_roles) != 6:
            raise ValueError("force_roles must contain exactly six roles")
        if force_roles.count("imp") != 1:
            raise ValueError("setup must contain exactly one Imp")
        return list(force_roles)

    minion = force_minion or rng.choice(MINIONS)
    if minion == "baron":
        townsfolk = rng.sample(TOWNSFOLK, 2)
        outsiders = list(OUTSIDERS)
        return [*townsfolk, *outsiders, "baron", "imp"]
    if minion != "scarlet_woman":
        raise ValueError(f"Unsupported minion for No Greater Joy: {minion}")
    return [*rng.sample(TOWNSFOLK, 3), rng.choice(OUTSIDERS), "scarlet_woman", "imp"]


def _choose_drunk_fake(rng: random.Random, roles: list[str]) -> str | None:
    if "drunk" not in roles:
        return None
    unavailable = set(roles)
    candidates = [role for role in TOWNSFOLK if role not in unavailable]
    if not candidates:
        raise ValueError("No legal Townsfolk fake role available for Drunk")
    return rng.choice(candidates)


def generate_game(
    *,
    human_name: str = "旅人",
    human_count: int = 1,
    seed: int | None = None,
    force_minion: str | None = None,
    force_roles: list[str] | None = None,
    force_human_role: str | None = None,
    budget_usd: float = 1.0,
    mock_ai: bool = False,
    discussion_mode: DiscussionMode | str = DiscussionMode.FREE,
    shuffle_seats_on_start: bool = False,
    night_seconds: int = 90,
    day_discussion_seconds: int = 240,
    private_chat_seconds: int = 180,
    nominations_seconds: int = 180,
    voting_seconds: int = 60,
) -> TruthState:
    if human_count < 1 or human_count > 6:
        raise ValueError("human_count must be between 1 and 6")
    if seed is None:
        seed = random.SystemRandom().randrange(1, 2**31)
    rng = random.Random(seed)
    roles = _pick_roles(rng, force_minion=force_minion, force_roles=force_roles)
    drunk_fake = _choose_drunk_fake(rng, roles)

    if force_human_role is not None:
        if force_human_role not in roles:
            raise ValueError("force_human_role must be present in setup roles")
        roles.remove(force_human_role)
        rng.shuffle(roles)
        ordered_roles = [force_human_role, *roles]
    elif force_roles is not None:
        ordered_roles = roles
    else:
        rng.shuffle(roles)
        ordered_roles = roles

    ai_count = 6 - human_count
    human_ids = ["human", *[f"human_{index}" for index in range(2, human_count + 1)]]
    human_names = [human_name, *[f"玩家{index}" for index in range(2, human_count + 1)]]
    ai_personas = AI_PERSONAS[:ai_count]
    names = [*human_names, *[persona.name for persona in ai_personas]]
    ids = [*human_ids, *[persona.id for persona in ai_personas]]
    players: list[PlayerTruth] = []
    for seat, (player_id, name, role) in enumerate(zip(ids, names, ordered_roles, strict=True)):
        apparent = drunk_fake if role == "drunk" else None
        players.append(
            PlayerTruth(
                id=player_id,
                name=name,
                seat=seat,
                is_human=player_id in human_ids,
                true_role=role,
                apparent_role=apparent,
                role_history=[role],
            )
        )

    demon = next(player for player in players if player.true_role == "imp")
    state = TruthState(
        game_id=uuid4().hex,
        seed=seed,
        players=players,
        current_demon_id=demon.id,
        budget_usd=budget_usd,
        mock_ai=mock_ai,
        discussion_mode=DiscussionMode(discussion_mode),
        shuffle_seats_on_start=shuffle_seats_on_start,
        night_duration_seconds=night_seconds,
        day_discussion_duration_seconds=day_discussion_seconds,
        private_chat_duration_seconds=private_chat_seconds,
        nominations_duration_seconds=nominations_seconds,
        voting_duration_seconds=voting_seconds,
    )
    state.phase = Phase.SETUP

    for player in players:
        visible_role = ROLE_SPECS[player.visible_role]
        state.add_event(
            f"你的角色是 {visible_role.zh_name}（{visible_role.zh_type}）。{visible_role.ability}",
            scope=AudienceScope.PLAYER_ONLY,
            type="role_info",
            target_ids=[player.id],
            metadata={"visible_role": visible_role.slug},
        )
        if player.true_role == "drunk" and player.apparent_role is not None:
            state.add_event(
                f"{player.name} 是酒鬼，看到的假角色為 {ROLE_SPECS[player.apparent_role].zh_name}。",
                scope=AudienceScope.STORYTELLER_INTERNAL,
                type="drunk_assignment",
                target_ids=[player.id],
                metadata={"true_role": "drunk", "apparent_role": player.apparent_role},
            )
        if not player.is_human:
            persona = next(item for item in AI_PERSONAS if item.id == player.id)
            state.ai_memories[player.id] = AIMemory(
                player_id=player.id,
                suspicion={other.id: 0.5 for other in players if other.id != player.id},
                summary=f"{player.name} 採用 {persona.style}。尚未形成可靠世界觀。",
            )

    state.add_event(
        "No Greater Joy 房間已建立，等待所有真人玩家入座。",
        scope=AudienceScope.PUBLIC,
        type="lobby_created",
    )
    state.add_event(
        "Teensyville starting info skipped: evil players did not receive teammate identities.",
        scope=AudienceScope.STORYTELLER_INTERNAL,
        type="no_starting_info",
    )
    return state
