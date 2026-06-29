from __future__ import annotations

import ast
import asyncio
import json
import random
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, cast

from pydantic import BaseModel, ValidationError

from botc_ai.ai.schemas import (
    AIMemoryUpdate,
    ChambermaidChoice,
    DefenseAction,
    KlutzChoice,
    NightTargetAction,
    NominationAction,
    PrivateMessageAction,
    PublicSpeechAction,
    VoteAction,
)
from botc_ai.domain.ai_brain import refresh_ai_brain
from botc_ai.domain.artist import ArtistParseResult, ArtistStructuredQuestion, parse_artist_question
from botc_ai.domain.context import build_ai_context
from botc_ai.domain.models import (
    AIMemory,
    ApiUsageRecord,
    AudienceScope,
    CandidateScore,
    TruthState,
)
from botc_ai.domain.roles import OUTSIDERS, ROLE_SPECS, TOWNSFOLK, Alignment
from botc_ai.domain.setup import AI_PERSONAS
from botc_ai.domain.usage import record_usage, summarize_usage

T = TypeVar("T", bound=BaseModel)


class AIProviderError(RuntimeError):
    """Base error for AI provider failures."""


class BudgetExceeded(AIProviderError):
    """Raised when the configured game budget blocks a model call."""


class AIProvider(Protocol):
    async def public_speech(self, state: TruthState, player_id: str) -> PublicSpeechAction: ...

    async def private_message(
        self, state: TruthState, player_id: str, valid_targets: Sequence[str]
    ) -> PrivateMessageAction | None: ...

    async def nominate(
        self, state: TruthState, player_id: str, valid_targets: Sequence[str]
    ) -> NominationAction: ...

    async def defense(
        self, state: TruthState, player_id: str, accusation: str
    ) -> DefenseAction: ...

    async def vote(
        self, state: TruthState, player_id: str, nominee_id: str, accusation: str, defense: str
    ) -> VoteAction: ...

    async def night_target(
        self, state: TruthState, player_id: str, valid_targets: Sequence[str]
    ) -> NightTargetAction: ...

    async def chambermaid_choice(
        self, state: TruthState, player_id: str, valid_targets: Sequence[str]
    ) -> ChambermaidChoice: ...

    async def klutz_choice(
        self, state: TruthState, player_id: str, valid_targets: Sequence[str]
    ) -> KlutzChoice: ...

    async def artist_question(
        self, state: TruthState, player_id: str, text: str
    ) -> ArtistParseResult: ...


def _rng_for(state: TruthState, player_id: str, purpose: str) -> random.Random:
    seed = f"{state.seed}:{state.day}:{state.phase}:{player_id}:{purpose}:{len(state.events)}"
    return random.Random(seed)


def _safe_target(valid_targets: Sequence[str], fallback: str = "") -> str:
    if valid_targets:
        return valid_targets[0]
    return fallback


def _candidate_scores_for(
    state: TruthState, player_id: str, valid_targets: Sequence[str] | None = None
) -> list[CandidateScore]:
    includes_self = valid_targets is not None and player_id in valid_targets
    notebook = refresh_ai_brain(state, player_id, valid_targets if includes_self else None)
    allowed = set(valid_targets) if valid_targets is not None else None
    return [
        score
        for score in notebook.candidate_scores
        if allowed is None or score.player_id in allowed
    ]


def _best_candidate(
    state: TruthState,
    player_id: str,
    valid_targets: Sequence[str],
    field: str,
    *,
    purpose: str,
) -> CandidateScore | None:
    scores = _candidate_scores_for(state, player_id, valid_targets)
    if not scores:
        return None
    rng = _rng_for(state, player_id, f"brain:{purpose}")
    return max(scores, key=lambda score: getattr(score, field) + rng.random() * 0.025)


def _score_reason_text(score: CandidateScore | None) -> str:
    if score is None:
        return "目前沒有足夠資訊。"
    details = "；".join(score.reasons[:2])
    return details or f"{score.seat_number}號{score.name} 需要被追問。"


def _top_suspect(
    state: TruthState,
    player_id: str,
    valid_targets: Sequence[str],
    *,
    purpose: str = "suspect",
) -> str:
    if not valid_targets:
        return ""
    best = _best_candidate(state, player_id, valid_targets, "nomination_score", purpose=purpose)
    return best.player_id if best is not None else valid_targets[0]


def _mock_night_target_score(
    state: TruthState,
    player_id: str,
    target_id: str,
    *,
    memory: AIMemory | None,
    rng: random.Random,
) -> float:
    if target_id == player_id:
        # Self-kill remains possible for Imp starpass play, but it should not be the default.
        return 0.36 + rng.random() * 0.14
    suspicion = memory.suspicion.get(target_id, 0.5) if memory else 0.5
    claim = memory.known_claims.get(target_id) if memory else None
    role_threat = {
        "empath": 0.18,
        "investigator": 0.16,
        "clockmaker": 0.12,
        "artist": 0.14,
        "chambermaid": 0.1,
        "sage": -0.08,
        "klutz": -0.12,
    }.get(claim or "", 0.0)
    pressure_penalty = min(_recent_pressure_count(state, target_id), 4) * 0.025
    public_claim_bonus = 0.08 if claim else 0.0
    return suspicion + role_threat + public_claim_bonus - pressure_penalty + rng.random() * 0.08


def _alternate_suspect(
    state: TruthState, player_id: str, current_id: str, valid_targets: Sequence[str]
) -> str:
    if current_id != state.human_id or _recent_pressure_count(state, current_id) < 2:
        return current_id
    alternatives = [target_id for target_id in valid_targets if target_id != state.human_id]
    return _top_suspect(state, player_id, alternatives, purpose="public_speech_alt") or current_id


def _latest_vote_result_line(state: TruthState) -> str:
    for nomination in reversed(state.nominations):
        if nomination.day != state.day or not nomination.resolved:
            continue
        nominee = state.by_id(nomination.nominee_id).name
        if nomination.eligible_for_execution:
            return f"{nominee} 已經有 {nomination.votes} 票過門檻，後面的提名要有更強理由。"
        return f"{nominee} 剛才只有 {nomination.votes}/{nomination.threshold} 票，這個壓力暫時沒有成案。"
    return ""


def _resolved_nominations_today(state: TruthState) -> list[Any]:
    return [
        nomination
        for nomination in state.nominations
        if nomination.day == state.day and nomination.resolved
    ]


def _has_execution_candidate(state: TruthState) -> bool:
    return any(
        nomination.eligible_for_execution for nomination in _resolved_nominations_today(state)
    )


def _should_hold_nomination(state: TruthState, style: str, rng: random.Random) -> bool:
    resolved = _resolved_nominations_today(state)
    if not resolved:
        return False
    if len(resolved) >= 3 and rng.random() < 0.72:
        return True
    if _has_execution_candidate(state) and style not in {"激進施壓型", "直覺混沌型"}:
        return rng.random() < 0.62
    return False


def _mock_nomination_reason(
    state: TruthState, style: str, target_name: str, rng: random.Random
) -> str:
    vote_line = _latest_vote_result_line(state)
    options = {
        "邏輯分析型": [
            f"我提名 {target_name}，因為他的公開說法和剛才票型放在一起不夠順。",
            f"我要測 {target_name}：不是要立刻處決，而是看誰願意為這個世界觀投票。",
        ],
        "社交協調型": [
            f"我提名 {target_name}，想把私聊裡的模糊點放到公開場確認。",
            f"{target_name} 需要給一個更清楚的角色範圍，否則大家很難協調票。",
        ],
        "激進施壓型": [
            f"我提名 {target_name}。這個位置一直在躲明確判斷，我想現在就測。",
            f"我要把 {target_name} 推上來；如果他是好人，請拿出能讓人退票的資訊。",
        ],
        "保守懷疑型": [
            f"我不喜歡亂提，但 {target_name} 的票型位置需要被公開解釋。",
            f"我提名 {target_name}，理由很窄：只測他剛才的投票站位。",
        ],
        "直覺混沌型": [
            f"我提名 {target_name}，這不是結論，只是我想打斷現在太順的風向。",
            f"{target_name} 這格讓我不舒服，先上台聽辯護再說。",
        ],
    }
    reason = rng.choice(options.get(style, options["邏輯分析型"]))
    return f"{reason} {vote_line}".strip()


def _mock_defense_statement(state: TruthState, player_id: str, accusation: str) -> str:
    player = state.by_id(player_id)
    memory = state.ai_memories.get(player_id)
    own_claim = memory.public_claim if memory and memory.public_claim else player.visible_role
    role_name = ROLE_SPECS[own_claim].zh_name if own_claim in ROLE_SPECS else "不公開角色"
    pressure = _recent_pressure_count(state, player_id)
    if pressure >= 3:
        return f"我知道我被點很多次，但這裡有跟風成分。我目前公開範圍偏 {role_name}，請先比對票型再決定。"
    if "票型" in accusation:
        return "如果你們要用票型測我，至少請說清楚是哪一票有問題；單純上台不代表我是壞人。"
    return "我的說法目前可以對回公開資訊；我不想在壓力下全開，但我不是今天最該處決的位置。"


def _mock_vote_reason(
    state: TruthState,
    player_id: str,
    nominee_id: str,
    *,
    vote: bool,
    style: str,
    rng: random.Random,
) -> str:
    voter = state.by_id(player_id)
    nominee = state.by_id(nominee_id)
    ghost = "這是我的鬼票，" if not voter.alive else ""
    if player_id == nominee_id:
        return (
            f"{ghost}我願意投自己來讓票型清楚。"
            if vote
            else f"{ghost}我不投自己；要處決我請拿出比壓力更硬的理由。"
        )
    yes_options = {
        "邏輯分析型": [
            f"{ghost}我支持，因為這票能驗證剛才的提名邏輯。",
            f"{ghost}{nominee.name} 的說法和目前票型對不上，我投。",
        ],
        "社交協調型": [
            f"{ghost}我先上票，但如果後面有更清楚資訊我願意退。",
            f"{ghost}這票能逼出更多立場，我支持。",
        ],
        "激進施壓型": [
            f"{ghost}我投，今天需要有人承擔壓力。",
            f"{ghost}這個位置值得上台，不投就太軟了。",
        ],
        "保守懷疑型": [
            f"{ghost}我很少早票，但這次理由夠窄，我投。",
            f"{ghost}我投，主要看的是投票站位不是角色宣稱。",
        ],
        "直覺混沌型": [
            f"{ghost}我投，這格給我的直覺最不舒服。",
            f"{ghost}我想看這票會讓誰緊張，所以支持。",
        ],
    }
    no_options = {
        "邏輯分析型": [
            f"{ghost}我不投；目前證據還不能推出處決。",
            f"{ghost}這票資訊量不足，我想留給更明確的矛盾。",
        ],
        "社交協調型": [
            f"{ghost}我先不上票，想聽完其他人的範圍再決定。",
            f"{ghost}這樣票太快成形，我不想幫忙鎖死。",
        ],
        "激進施壓型": [
            f"{ghost}我暫時不投，不是保他，是這刀不夠準。",
            f"{ghost}壓力有了，但這輪還沒到我要落票的程度。",
        ],
        "保守懷疑型": [
            f"{ghost}我不投；今天最怕被帶成安全票。",
            f"{ghost}這個提名太順，我要先看誰急著湊票。",
        ],
        "直覺混沌型": [
            f"{ghost}我不投，風向太整齊我會反著看。",
            f"{ghost}我先保留，這票味道不對。",
        ],
    }
    table = yes_options if vote else no_options
    return rng.choice(table.get(style, table["邏輯分析型"]))


def _persona_style(player_id: str) -> str:
    persona = next((p for p in AI_PERSONAS if p.id == player_id), None)
    return persona.style if persona else "邏輯分析型"


def _claim_for_mock(state: TruthState, player_id: str, rng: random.Random) -> str:
    player = state.by_id(player_id)
    memory = state.ai_memories.get(player_id)
    if player.alignment == Alignment.GOOD:
        return player.visible_role
    if memory is not None and memory.current_bluff:
        return memory.current_bluff
    seen_claims = {memory.public_claim} if memory and memory.public_claim else set()
    seen_claims.update(memory.known_claims.values() if memory else [])
    candidates = [role for role in [*TOWNSFOLK, *OUTSIDERS] if role not in seen_claims]
    bluff = rng.choice(candidates or TOWNSFOLK)
    if memory is not None:
        memory.current_bluff = bluff
    return bluff


def _latest_public_speech(state: TruthState, player_id: str) -> tuple[str, str] | None:
    for event in reversed(state.events):
        actor_id = event.actor_id
        if event.type != "public_speech" or actor_id is None or actor_id == player_id:
            continue
        actor = state.by_id(actor_id)
        return actor.name, _spoken_content(event.message, actor.name)
    return None


def _recent_pressure_count(state: TruthState, target_id: str, limit: int = 10) -> int:
    target = state.by_id(target_id)
    return sum(
        target.name
        in _spoken_content(
            event.message,
            state.by_id(event.actor_id).name if event.actor_id is not None else "",
        )
        for event in [
            event
            for event in state.events
            if event.scope == AudienceScope.PUBLIC and event.type == "public_speech"
        ][-limit:]
    )


def _public_speech_count(state: TruthState, player_id: str) -> int:
    return sum(
        event.type == "public_speech" and event.actor_id == player_id for event in state.events
    )


def _public_claim_count(state: TruthState) -> int:
    return sum(memory.public_claim is not None for memory in state.ai_memories.values())


def _spoken_content(message: str, actor_name: str) -> str:
    if actor_name and message.startswith(actor_name):
        rest = message[len(actor_name) :]
        if rest[:1] in {"：", ":", "?", "？"}:
            return rest[1:]
    return message


def _mock_reaction_line(
    state: TruthState,
    *,
    style: str,
    latest_speech: tuple[str, str] | None,
    suspect_id: str,
    rng: random.Random,
) -> str:
    if latest_speech is None:
        return ""
    actor_name, message = latest_speech
    suspect_name = state.by_id(suspect_id).name if suspect_id else "那個位置"
    human_name = state.by_id(state.human_id).name
    if actor_name == human_name:
        style_options = {
            "邏輯分析型": [
                f"我先接 {actor_name} 的話：方向可以，但我要看到能被驗證的資訊點。",
                f"{actor_name} 剛剛的說法我記下來，現在先不要把它當結論。",
            ],
            "社交協調型": [
                f"{actor_name} 的問題可以私下補細節；公開場我先只收斂到角色範圍。",
                f"我願意跟 {actor_name} 對一下資訊，但不想逼他現在全公開。",
            ],
            "激進施壓型": [
                f"{actor_name} 既然開了這個方向，我想要求一個更明確的可驗證說法。",
                f"我不反對 {actor_name} 的方向，但模糊說法今天要被測。",
            ],
            "保守懷疑型": [
                f"{actor_name} 的話我先保留，現在跟票太快會讓壞人很好藏。",
                "我聽到了，但不想因為真人先發言就把桌面推歪。",
            ],
            "直覺混沌型": [
                f"{actor_name} 這句有味道，但我還分不清是好人的焦慮還是壞人的節奏。",
                f"我先把 {actor_name} 放進待觀察，不急著判死。",
            ],
        }
        return rng.choice(style_options.get(style, style_options["邏輯分析型"]))
    if suspect_name in message:
        reaction_options = [
            f"{actor_name} 也在看 {suspect_name}，但我想聽一個新的理由，不要只複讀名字。",
            f"{suspect_name} 被提到我有記，但目前還不等於可處決。",
            f"我注意到 {actor_name} 把焦點放在 {suspect_name}；下一步要看回應，不是直接定案。",
        ]
        return rng.choice(reaction_options)
    return ""


def _role_posture(state: TruthState, player_id: str, claim: str) -> str:
    visible_role = state.by_id(player_id).visible_role
    role_name = ROLE_SPECS[visible_role].zh_name
    if visible_role in {"clockmaker", "investigator", "empath", "chambermaid"}:
        return f"我有一點 {role_name} 方向的資訊，但第一輪先看誰急著要我全開。"
    if visible_role == "artist":
        return "如果桌面卡住，我的問題會留到能最大化資訊的時候再用。"
    if visible_role in {"sage", "klutz"}:
        return "我現在不急著把自己的位置講死，先看誰在塑造安全票。"
    return f"我的公開說法會先維持在 {ROLE_SPECS[claim].zh_name} 附近，不急著全開。"


def _followup_line(
    style: str, suspect_name: str, role_posture: str, reaction: str, rng: random.Random
) -> str:
    options = {
        "邏輯分析型": [
            f"{reaction or '我補一點。'}現在重點不是多喊一個名字，而是把 {suspect_name} 的說法跟票型放在一起驗。",
            f"第二輪我想少一點角色宣稱，多一點可交叉驗證的資訊。{role_posture}",
        ],
        "社交協調型": [
            f"{reaction or '我想把節奏拉回來。'}等私聊後再決定要不要推 {suspect_name}，現在先別全桌鎖死。",
            "如果有人願意給我兩格角色範圍，我可以幫忙整理公開版本。",
        ],
        "激進施壓型": [
            f"{reaction or '我還是要壓力。'}{suspect_name} 可以先給範圍，不給就值得被提名測票。",
            "空轉比錯提名更糟；我想看誰願意承擔第一個明確判斷。",
        ],
        "保守懷疑型": [
            f"{reaction or '我不同意無腦加速。'}如果大家都看 {suspect_name}，我反而要回頭看帶頭的人。",
            "現在最有價值的是票型，不是把自己的角色全部交出去。",
        ],
        "直覺混沌型": [
            f"{reaction or '我的直覺換了一點方向。'}{suspect_name} 不是唯一焦點，風向太順我會想拆它。",
            "我想留一個模糊的懷疑，不急著把刀落下去。",
        ],
    }
    return rng.choice(options.get(style, options["邏輯分析型"]))


def _clean_mock_speech(text: str) -> str:
    return re.sub(r"\s+", " ", text).replace(" 。", "。").strip()


def _player_label_for_mock(state: TruthState, player_id: str) -> str:
    player = state.by_id(player_id)
    return f"{player.seat + 1}號{player.name}"


def _private_info_brief_for_mock(state: TruthState, player_id: str) -> str:
    for event in reversed(state.events):
        if event.scope != AudienceScope.PLAYER_ONLY or player_id not in event.target_ids:
            continue
        if event.type == "clockmaker_info":
            return f"我是鐘錶匠，惡魔到最近爪牙的座位步數是 {event.metadata.get('value')}。"
        if event.type == "investigator_info":
            players = event.metadata.get("players", [])
            role = event.metadata.get("minion_role")
            if isinstance(players, list) and len(players) >= 2 and isinstance(role, str):
                return (
                    f"我是調查員，{_player_label_for_mock(state, str(players[0]))}"
                    f"和{_player_label_for_mock(state, str(players[1]))}裡有一個"
                    f"{ROLE_SPECS[role].zh_name}。"
                )
        if event.type == "empath_info":
            return f"我是共情者，兩側最近存活鄰居中的邪惡數是 {event.metadata.get('value')}。"
        if event.type == "chambermaid_info":
            players = event.metadata.get("players", [])
            if isinstance(players, list) and len(players) >= 2:
                return (
                    f"我是侍女，查了{_player_label_for_mock(state, str(players[0]))}"
                    f"和{_player_label_for_mock(state, str(players[1]))}，醒來人數是"
                    f"{event.metadata.get('value')}。"
                )
    return ""


def _asks_identity_for_mock(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return any(token in compact for token in ("身分", "身份", "角色", "資訊", "你是什麼"))


def _natural_mock_public_speech(state: TruthState, player_id: str) -> PublicSpeechAction:
    rng = _rng_for(state, player_id, "natural_public_speech")
    player = state.by_id(player_id)
    memory = state.ai_memories.get(player_id)
    style = _persona_style(player_id)
    valid_targets = [p.id for p in state.players if p.id != player_id]
    notebook = refresh_ai_brain(state, player_id, valid_targets)
    suspect_id = _top_suspect(state, player_id, valid_targets, purpose="natural_public_speech")
    suspect_id = _alternate_suspect(state, player_id, suspect_id, valid_targets)
    suspect_label = _player_label_for_mock(state, suspect_id) if suspect_id else "某個邊位"
    suspect_score = next(
        (score for score in notebook.candidate_scores if score.player_id == suspect_id), None
    )
    suspect_reason = _score_reason_text(suspect_score)
    world = notebook.worlds[0] if notebook.worlds else None
    speech_count = _public_speech_count(state, player_id)
    latest = _latest_public_speech(state, player_id)
    latest_text = latest[1] if latest else ""
    info = _private_info_brief_for_mock(state, player_id)
    claim = _claim_for_mock(state, player_id, rng)
    claim_name = ROLE_SPECS[claim].zh_name
    claim_used = False

    if not player.alive:
        speech = (
            f"我死了但還有鬼票，先不急著丟。我現在最想聽 {suspect_label} 把昨天那輪投票講清楚。"
        )
    elif latest and _asks_identity_for_mock(latest_text):
        if info:
            speech = f"我回一下，我可以先開：{info}所以我想先對 {suspect_label} 施壓，理由是：{suspect_reason}"
            claim_used = True
        else:
            speech = (
                f"我回一下，我目前偏向報 {claim_name}，但不想把細節一次交完。"
                f"{suspect_label} 先說你昨晚或第一夜拿到什麼；我目前卡的是：{suspect_reason}"
            )
            claim_used = True
    elif speech_count == 0 and info and rng.random() < 0.82:
        speech = f"我先給資訊：{info}這局先從座位和票型看，{suspect_label} 的反應我會特別記。"
        claim_used = True
    elif speech_count == 0:
        openings = {
            "邏輯分析型": f"我先給桌面讀法：{suspect_reason}所以我想先聽 {suspect_label} 的角色範圍。",
            "社交協調型": f"我想先把資訊排一下。{suspect_label} 你給一個能回頭檢查的範圍，我可以私聊對表。",
            "激進施壓型": f"我會先壓 {suspect_label}。不用長篇，直接說你是不是資訊位、拿到什麼。",
            "保守懷疑型": f"我先保守一點。{suspect_label} 目前是我的觀察位，理由是：{suspect_reason}",
            "直覺混沌型": f"我直覺先看 {suspect_label}，不是定狼，但你今天得丟一個真的可檢查的點。",
        }
        speech = openings.get(style, f"我先聽 {suspect_label} 的資訊，再決定票要不要動。")
    else:
        vote_line = _latest_vote_result_line(state)
        if vote_line:
            speech = f"{vote_line} 這票型我先記下來；下一個我想聽 {suspect_label} 怎麼解釋。"
        elif latest:
            actor_name, _ = latest
            speech = f"接 {actor_name} 那句，我不同意只打模糊仗。{suspect_label} 直接給角色範圍或夜晚資訊。"
        else:
            next_test = world.next_test if world else f"追問 {suspect_label}"
            speech = f"我目前不想散票。{next_test}"

    if memory is not None:
        memory.next_intent = (
            world.next_test[:180] if world else f"追問 {suspect_label} 的資訊與票型"
        )
        memory.summary = (
            f"{memory.summary}\nD{state.day}: public stance toward {suspect_id}".strip()[-1100:]
        )
        memory.public_facts.append(f"D{state.day}: 自己公開壓力給 {suspect_label}")
        memory.compact()
    _mock_usage(state, player_id, "public_speech", 220, 55)
    return PublicSpeechAction(
        speech=_clean_mock_speech(speech)[:220],
        claimed_role=claim if claim_used else None,
    )


class MockAIProvider:
    model = "mock"

    async def public_speech(self, state: TruthState, player_id: str) -> PublicSpeechAction:
        return _natural_mock_public_speech(state, player_id)
        rng = _rng_for(state, player_id, "public_speech")
        style = _persona_style(player_id)
        player = state.by_id(player_id)
        memory = state.ai_memories.get(player_id)
        valid_targets = [p.id for p in state.players if p.id != player_id]
        suspect_id = _top_suspect(state, player_id, valid_targets, purpose="public_speech")
        suspect_id = _alternate_suspect(state, player_id, suspect_id, valid_targets)
        suspect_name = state.by_id(suspect_id).name if suspect_id else "某人"
        claim = _claim_for_mock(state, player_id, rng)
        claim_name = ROLE_SPECS[claim].zh_name
        speech_count = _public_speech_count(state, player_id)
        pressure_count = _recent_pressure_count(state, suspect_id) if suspect_id else 0
        latest_death = (
            "昨晚沒死人"
            if not state.last_night_deaths
            else f"昨晚死亡是 {'、'.join(state.by_id(pid).name for pid in state.last_night_deaths)}"
        )
        latest_speech = _latest_public_speech(state, player_id)
        reaction = _mock_reaction_line(
            state,
            style=style,
            latest_speech=latest_speech,
            suspect_id=suspect_id,
            rng=rng,
        )
        role_posture = _role_posture(state, player_id, claim)
        already_claimed = memory.public_claim is not None if memory is not None else False
        should_claim = {
            "邏輯分析型": state.day >= 2 or rng.random() < 0.18,
            "社交協調型": rng.random() < 0.28,
            "激進施壓型": rng.random() < 0.34,
            "保守懷疑型": state.day >= 2 and rng.random() < 0.22,
            "直覺混沌型": rng.random() < 0.25,
        }.get(style, False)
        should_claim = should_claim and not already_claimed and _public_claim_count(state) < 3
        should_claim = should_claim and player.alive
        claim_text = f"我可以先放一個範圍，我偏向我是 {claim_name}。" if should_claim else ""
        vote_line = _latest_vote_result_line(state)
        if not player.alive:
            dead_options = [
                f"我已經死了，今天不能提名；我會把重點放在誰昨天推票、誰今天改口。{vote_line}",
                f"死人視角先少講角色，多看票型。{suspect_name} 可以解釋一下昨天那輪站位。",
                "我現在只剩資訊整理價值：請活人別只追同一個名字，先對昨晚死亡和票型。",
            ]
            core = rng.choice(dead_options).strip()
        elif vote_line and speech_count > 0 and rng.random() < 0.55:
            core = f"{vote_line} 我不想再重複同一個焦點，下一個發言請給新的可驗證資訊。"
        elif pressure_count >= 3 and style in {"邏輯分析型", "社交協調型", "保守懷疑型"}:
            core = f"{reaction or '我先踩一下煞車。'}{suspect_name} 被點太多次了，沒有新理由我不想跟著全桌一起壓。"
        elif speech_count > 0:
            core = _followup_line(style, suspect_name, role_posture, reaction, rng)
        else:
            openings = {
                "邏輯分析型": f"{reaction or latest_death} 我先看兩條線：角色說法能不能互相對上，以及誰在無理由推風向。{role_posture}",
                "社交協調型": f"{reaction or '我想先整理大家願意給的角色範圍。'}等一下我會找一兩個人私聊校準，不急著全票壓同一邊。{claim_text}",
                "激進施壓型": f"{reaction or '今天不要空轉。'}我會先壓 {suspect_name} 給一點說法，但票要看回應品質。{claim_text}",
                "保守懷疑型": f"{reaction or '我先不把底牌全開。'}第一天最怕大家跟同一個名字跑，我想看誰在硬帶方向。",
                "直覺混沌型": f"{reaction or '我現在有點不安。'}我會先盯 {suspect_name}，但如果風向太整齊我反而會懷疑帶頭的人。{claim_text}",
            }
            core = openings.get(style, openings["邏輯分析型"])
        if memory is not None:
            memory.next_intent = f"觀察 {suspect_name}，必要時推動提名"
            memory.summary = (
                f"第 {state.day} 天：{style}，目前觀察 {suspect_name}；避免無證據跟風。"
            )
            memory.compact()
        _mock_usage(state, player_id, "public_speech", 240, 70)
        speech = _clean_mock_speech(core)
        return PublicSpeechAction(
            speech=speech[:270],
            claimed_role=claim if should_claim else None,
        )

    async def private_message(
        self, state: TruthState, player_id: str, valid_targets: Sequence[str]
    ) -> PrivateMessageAction | None:
        rng = _rng_for(state, player_id, "private_message")
        style = _persona_style(player_id)
        chance = {
            "社交協調型": 0.85,
            "激進施壓型": 0.6,
            "直覺混沌型": 0.55,
            "邏輯分析型": 0.5,
            "保守懷疑型": 0.35,
        }.get(style, 0.5)
        if not valid_targets or rng.random() > chance:
            return None
        if style == "社交協調型" and "human" in valid_targets:
            target_id = "human"
        else:
            target_candidate = _best_candidate(
                state, player_id, valid_targets, "vote_score", purpose="private_message"
            )
            target_id = (
                target_candidate.player_id
                if target_candidate is not None
                else rng.choice(list(valid_targets))
            )
        target = state.by_id(target_id)
        target_score = next(
            (
                score
                for score in _candidate_scores_for(state, player_id, [target_id])
                if score.player_id == target_id
            ),
            None,
        )
        _mock_usage(state, player_id, "private_message", 220, 45)
        return PrivateMessageAction(
            target_id=target_id,
            message=(
                f"我想聽你對 {target.seat + 1}號{target.name} 的看法。"
                f"我這邊卡的是：{_score_reason_text(target_score)}"
            ),
        )

    async def nominate(
        self, state: TruthState, player_id: str, valid_targets: Sequence[str]
    ) -> NominationAction:
        rng = _rng_for(state, player_id, "nominate")
        player = state.by_id(player_id)
        style = _persona_style(player_id)
        best = _best_candidate(
            state, player_id, valid_targets, "nomination_score", purpose="nominate"
        )
        top_score = best.nomination_score if best is not None else 0.0
        nomination_chance = {
            "激進施壓型": 0.8,
            "直覺混沌型": 0.55,
            "邏輯分析型": 0.45,
            "社交協調型": 0.35,
            "保守懷疑型": 0.22,
        }.get(style, 0.4)
        should_hold = _should_hold_nomination(state, style, rng) and top_score < 0.78
        should_nominate = top_score >= 0.72 or rng.random() <= nomination_chance
        if (
            not player.alive
            or not valid_targets
            or should_hold
            or not should_nominate
            or top_score < 0.5
        ):
            _mock_usage(state, player_id, "nominate", 160, 20)
            return NominationAction(
                nominate=False,
                reason=rng.choice(
                    [
                        "目前已有足夠票型，我先不追加提名。",
                        "我想先讓桌面消化剛才的票，不急著再推一個人。",
                        "這輪沒有比現有候選人更強的理由。",
                    ]
                ),
            )
        target_id = best.player_id if best is not None else rng.choice(list(valid_targets))
        target = state.by_id(target_id)
        _mock_usage(state, player_id, "nominate", 180, 35)
        return NominationAction(
            nominate=True,
            target_id=target_id,
            reason=(
                f"我提名 {target.seat + 1}號{target.name}："
                f"{_score_reason_text(best)}。這一票主要是逼辯護和看票型。"
            )[:180],
        )

    async def defense(self, state: TruthState, player_id: str, accusation: str) -> DefenseAction:
        _mock_usage(state, player_id, "defense", 160, 35)
        return DefenseAction(statement=_mock_defense_statement(state, player_id, accusation))

    async def vote(
        self, state: TruthState, player_id: str, nominee_id: str, accusation: str, defense: str
    ) -> VoteAction:
        rng = _rng_for(state, player_id, f"vote:{nominee_id}")
        nominee = state.by_id(nominee_id)
        style = _persona_style(player_id)
        nominee_score = next(
            (
                score
                for score in _candidate_scores_for(state, player_id, [nominee_id])
                if score.player_id == nominee_id
            ),
            None,
        )
        vote_score = nominee_score.vote_score if nominee_score is not None else 0.5
        threshold = {
            "激進施壓型": 0.5,
            "直覺混沌型": 0.54,
            "邏輯分析型": 0.57,
            "社交協調型": 0.6,
            "保守懷疑型": 0.67,
        }.get(style, 0.58)
        if player_id == nominee_id:
            self_vote_chance = {"激進施壓型": 0.28, "直覺混沌型": 0.18}.get(style, 0.08)
            vote = nominee.alive and rng.random() < self_vote_chance
        else:
            margin = vote_score - threshold
            swing_chance = 0.18 + max(0.0, margin) * 1.8
            vote = nominee.alive and (
                margin >= 0.08 or (margin > -0.03 and rng.random() < min(0.55, swing_chance))
            )
        _mock_usage(state, player_id, "vote", 150, 20)
        reason = _score_reason_text(nominee_score)
        tone = {
            "激進施壓型": "我願意用票逼答案，",
            "保守懷疑型": "我不想輕易送人出局，",
            "社交協調型": "我看大家能不能跟上這個理由，",
            "邏輯分析型": "按目前桌面資訊，",
            "直覺混沌型": "我這票有一點直覺成分，",
        }.get(style, "")
        return VoteAction(
            vote=vote,
            public_reason=(
                f"{tone}我投，理由是：{reason}"
                if vote
                else f"{tone}我先不投，理由還沒過我的門檻：{reason}"
            )[:150],
        )

    async def night_target(
        self, state: TruthState, player_id: str, valid_targets: Sequence[str]
    ) -> NightTargetAction:
        candidates = list(valid_targets)
        if not candidates:
            target_id = player_id
        else:
            best = _best_candidate(
                state, player_id, candidates, "night_kill_score", purpose="night_target"
            )
            target_id = best.player_id if best is not None else candidates[0]
        _mock_usage(state, player_id, "night_target", 90, 8)
        return NightTargetAction(target_id=target_id)

    async def chambermaid_choice(
        self, state: TruthState, player_id: str, valid_targets: Sequence[str]
    ) -> ChambermaidChoice:
        rng = _rng_for(state, player_id, "chambermaid_choice")
        choices = list(valid_targets)
        rng.shuffle(choices)
        _mock_usage(state, player_id, "chambermaid_choice", 100, 12)
        return ChambermaidChoice(target_ids=choices[:2])

    async def klutz_choice(
        self, state: TruthState, player_id: str, valid_targets: Sequence[str]
    ) -> KlutzChoice:
        rng = _rng_for(state, player_id, "klutz_choice")
        target_id = rng.choice(list(valid_targets)) if valid_targets else player_id
        _mock_usage(state, player_id, "klutz_choice", 80, 8)
        return KlutzChoice(target_id=target_id)

    async def artist_question(
        self, state: TruthState, player_id: str, text: str
    ) -> ArtistParseResult:
        _mock_usage(state, player_id, "artist_parse", 140, 20)
        return parse_artist_question(text, state)


@dataclass
class OpenAIProvider:
    api_key: str
    dialogue_model: str
    decision_model: str
    store: bool = False
    timeout_seconds: float = 30.0
    max_retries: int = 2

    def __post_init__(self) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - exercised only without optional dependency
            raise AIProviderError("尚未安裝 OpenAI Python SDK。") from exc
        self.client: Any = AsyncOpenAI(api_key=self.api_key, timeout=self.timeout_seconds)

    async def public_speech(self, state: TruthState, player_id: str) -> PublicSpeechAction:
        return await self._call_structured(
            state, player_id, "public_speech", PublicSpeechAction, self.dialogue_model
        )

    async def private_message(
        self, state: TruthState, player_id: str, valid_targets: Sequence[str]
    ) -> PrivateMessageAction | None:
        action = await self._call_structured(
            state,
            player_id,
            f"private_message valid_targets={list(valid_targets)}",
            PrivateMessageAction,
            self.dialogue_model,
        )
        return action if action.target_id in valid_targets else None

    async def nominate(
        self, state: TruthState, player_id: str, valid_targets: Sequence[str]
    ) -> NominationAction:
        action = await self._call_structured(
            state,
            player_id,
            f"nominate valid_targets={list(valid_targets)}",
            NominationAction,
            self.decision_model,
        )
        if action.target_id not in valid_targets:
            action.target_id = None
            action.nominate = False
        return action

    async def defense(self, state: TruthState, player_id: str, accusation: str) -> DefenseAction:
        return await self._call_structured(
            state, player_id, f"defense accusation={accusation}", DefenseAction, self.dialogue_model
        )

    async def vote(
        self, state: TruthState, player_id: str, nominee_id: str, accusation: str, defense: str
    ) -> VoteAction:
        return await self._call_structured(
            state,
            player_id,
            f"vote nominee={nominee_id} accusation={accusation} defense={defense}",
            VoteAction,
            self.decision_model,
        )

    async def night_target(
        self, state: TruthState, player_id: str, valid_targets: Sequence[str]
    ) -> NightTargetAction:
        action = await self._call_structured(
            state,
            player_id,
            f"night_target valid_targets={list(valid_targets)}",
            NightTargetAction,
            self.decision_model,
        )
        if action.target_id not in valid_targets:
            action.target_id = _safe_target(valid_targets, player_id)
        return action

    async def chambermaid_choice(
        self, state: TruthState, player_id: str, valid_targets: Sequence[str]
    ) -> ChambermaidChoice:
        action = await self._call_structured(
            state,
            player_id,
            f"chambermaid_choice valid_targets={list(valid_targets)}",
            ChambermaidChoice,
            self.decision_model,
        )
        deduped = [target_id for target_id in action.target_ids if target_id in valid_targets]
        if len(deduped) < 2:
            deduped.extend(
                [target for target in valid_targets if target not in deduped][: 2 - len(deduped)]
            )
        return ChambermaidChoice(target_ids=deduped[:2])

    async def klutz_choice(
        self, state: TruthState, player_id: str, valid_targets: Sequence[str]
    ) -> KlutzChoice:
        action = await self._call_structured(
            state,
            player_id,
            f"klutz_choice valid_targets={list(valid_targets)}",
            KlutzChoice,
            self.decision_model,
        )
        if action.target_id not in valid_targets:
            action.target_id = _safe_target(valid_targets, player_id)
        return action

    async def artist_question(
        self, state: TruthState, player_id: str, text: str
    ) -> ArtistParseResult:
        prompt = build_ai_context(state, player_id, purpose=f"artist_parse user_question={text}")
        try:
            query = await self._responses_parse(
                prompt, ArtistStructuredQuestion, self.decision_model
            )
        except AIProviderError:
            return ArtistParseResult(supported=False, message="無法解析，請改用更具體的是非問題。")
        return ArtistParseResult(supported=True, query=query)

    async def _call_structured(
        self, state: TruthState, player_id: str, purpose: str, schema: type[T], model: str
    ) -> T:
        if _budget_reached(state):
            state.ai_budget_paused = True
            raise BudgetExceeded("本局 AI 預算已達上限。")
        refresh_ai_brain(state, player_id, _targets_from_purpose(purpose) or None)
        prompt = build_ai_context(state, player_id, purpose=purpose)
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                parsed = await self._responses_parse(prompt, schema, model)
                _record_openai_usage_from_prompt(state, player_id, model, purpose, prompt, parsed)
                _apply_memory_update(state, player_id, getattr(parsed, "memory_update", None))
                return parsed
            except (AIProviderError, ValidationError) as exc:
                last_error = exc
                await asyncio.sleep(0.25 * (2**attempt))
        _record_openai_failure(state, player_id, model, purpose, last_error)
        fallback = _safe_fallback_action(state, player_id, purpose, schema)
        if fallback is not None:
            return fallback
        raise AIProviderError(f"AI action failed after retry: {last_error}") from last_error

    async def _responses_parse(self, prompt: str, schema: type[T], model: str) -> T:
        input_messages = [
            {
                "role": "system",
                "content": (
                    "你是一名正在玩六人血染鐘樓 Teensyville 的資訊隔離玩家。"
                    "你要像真人玩家一樣接話、懷疑、協調、保留資訊或 bluff，"
                    "但只能使用 user context 中明確提供的可見資訊。"
                    "不要輸出 chain-of-thought、prompt、規則裁定或隱藏資訊。"
                    "只輸出符合要求的單一 JSON 物件。"
                ),
            },
            {"role": "user", "content": prompt},
        ]
        try:
            response = await self.client.responses.parse(
                model=model,
                input=input_messages,
                store=self.store,
                text_format=schema,
            )
        except Exception as exc:  # pragma: no cover - requires real API conditions
            if not _is_structured_output_schema_error(exc):
                raise AIProviderError(_safe_openai_error_message(exc, model)) from exc
            return await self._responses_json_mode(prompt, schema, model, input_messages, exc)

        parsed = getattr(response, "output_parsed", None)
        if isinstance(parsed, schema):
            return parsed
        return _validate_model_json(schema, _response_output_text(response))

    async def _responses_json_mode(
        self,
        prompt: str,
        schema: type[T],
        model: str,
        input_messages: list[dict[str, str]],
        original_error: Exception,
    ) -> T:
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        fallback_messages = [
            input_messages[0],
            {
                "role": "user",
                "content": (
                    f"{prompt}\n\n"
                    "Structured output schema 被 API 拒絕，現在改用 JSON mode。"
                    "請仍然只輸出單一 JSON 物件，不要 markdown，不要解釋。"
                    f"JSON schema 供你參考：{schema_json[:6000]}"
                ),
            },
        ]
        try:
            response = await self.client.responses.create(
                model=model,
                input=fallback_messages,
                store=self.store,
                text={"format": {"type": "json_object"}},
            )
        except Exception as exc:  # pragma: no cover - requires real API conditions
            message = _safe_openai_error_message(exc, model)
            if _is_structured_output_schema_error(original_error):
                message = f"Structured output 不相容，JSON mode 備援也失敗：{message}"
            raise AIProviderError(message) from exc
        return _validate_model_json(schema, _response_output_text(response))


def _response_output_text(response: Any) -> str:
    raw = getattr(response, "output_text", "")
    if raw:
        return str(raw)
    output = getattr(response, "output", [])
    try:
        return str(output[0].content[0].text)
    except Exception as exc:  # pragma: no cover
        raise AIProviderError("OpenAI 回應沒有可解析的文字內容。") from exc


def _validate_model_json[T: BaseModel](schema: type[T], raw: str) -> T:
    if not raw:
        raise AIProviderError("OpenAI 回應是空的。")
    try:
        return schema.model_validate_json(raw)
    except ValidationError:
        try:
            return schema.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise AIProviderError(f"OpenAI 回應不是合法的 {schema.__name__} JSON。") from exc


def _budget_reached(state: TruthState) -> bool:
    estimated = summarize_usage(state).estimated_usd
    return estimated is not None and estimated >= state.budget_usd


def _mock_usage(
    state: TruthState, player_id: str | None, purpose: str, input_tokens: int, output_tokens: int
) -> ApiUsageRecord:
    return record_usage(
        state,
        player_id=player_id,
        model="mock",
        purpose=purpose,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _record_openai_usage_from_prompt(
    state: TruthState, player_id: str, model: str, purpose: str, prompt: str, parsed: BaseModel
) -> None:
    # The Responses API usage shape may differ by model. We conservatively record approximate
    # counts when exact fields are unavailable, so the UI still reflects call volume offline.
    input_tokens = max(1, len(prompt) // 4)
    output_tokens = max(1, len(parsed.model_dump_json()) // 4)
    record_usage(
        state,
        player_id=player_id,
        model=model,
        purpose=purpose,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _record_openai_failure(
    state: TruthState,
    player_id: str,
    model: str,
    purpose: str,
    error: Exception | None,
) -> None:
    message = _safe_openai_error_message(error, model)
    record_usage(
        state,
        player_id=player_id,
        model=model,
        purpose=f"failed:{purpose[:80]}",
        input_tokens=0,
        output_tokens=0,
    )
    player_name = state.by_id(player_id).name
    state.ai_last_status = f"{player_name} 的 OpenAI 呼叫失敗，已使用安全 fallback：{message}"
    state.add_event(
        f"AI API 呼叫失敗，{player_name} 暫時使用安全 fallback：{message}",
        scope=AudienceScope.PUBLIC,
        type="ai_api_error",
        actor_id=player_id,
        metadata={"model": model, "purpose": purpose[:120], "error": message},
    )


def _is_structured_output_schema_error(error: Exception) -> bool:
    raw = str(error).lower()
    code = str(getattr(error, "code", "") or "").lower()
    combined = f"{raw} {code}"
    return (
        "json_schema" in combined
        or "structured output" in combined
        or "schema" in combined
        or "response_format" in combined
        or "text.format" in combined
    )


def _safe_openai_error_message(error: Exception | None, model: str) -> str:
    if error is None:
        return "未知錯誤。"
    raw = str(error)
    raw = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-***", raw)
    raw_lower = raw.lower()
    code = str(getattr(error, "code", "") or "").lower()
    status = str(getattr(error, "status_code", "") or "")
    combined = f"{raw_lower} {code} {status}"
    if "401" in combined or "authentication" in combined or "invalid_api_key" in combined:
        return "API key 驗證失敗，請確認 OPENAI_API_KEY。"
    if "429" in combined or "rate limit" in combined or "quota" in combined:
        return "OpenAI 限流或額度不足，請稍後重試或調整帳戶額度。"
    if (
        "model_not_found" in combined
        or "does not exist" in combined
        or "invalid model" in combined
        or "not found" in combined
        or "unsupported model" in combined
    ):
        return f"模型 {model} 不存在、未開通或此帳號無法使用。請改 AI_DIALOGUE_MODEL / AI_DECISION_MODEL。"
    if "json_schema" in combined or "schema" in combined:
        return "模型不支援目前的 structured output JSON schema，請換支援 Responses structured output 的模型。"
    if "timeout" in combined or "connection" in combined or "network" in combined:
        return "網路連線或逾時問題，請稍後重試。"
    return f"OpenAI API 呼叫失敗：{raw[:180]}"


def _apply_memory_update(state: TruthState, player_id: str, update: AIMemoryUpdate | None) -> None:
    if update is None:
        return
    memory = state.ai_memories.get(player_id)
    if memory is None:
        return
    valid_ids = {player.id for player in state.players if player.id != player_id}
    for target_id, delta in update.suspicion_delta.items():
        if target_id not in valid_ids:
            continue
        current = memory.suspicion.get(target_id, 0.5)
        memory.suspicion[target_id] = min(1.0, max(0.0, current + max(-0.35, min(0.35, delta))))
    for target_id, role in update.known_claims.items():
        if target_id in valid_ids and role in ROLE_SPECS:
            memory.known_claims[target_id] = role
    if update.public_claim in ROLE_SPECS:
        memory.public_claim = update.public_claim
    if update.current_bluff in ROLE_SPECS:
        memory.current_bluff = update.current_bluff
    if update.next_intent.strip():
        memory.next_intent = update.next_intent.strip()[:180]
    if update.summary.strip():
        memory.summary = f"{memory.summary}\n{update.summary.strip()}"[-1200:]
    for promise in update.private_promises[-3:]:
        if promise.strip():
            memory.private_promises.append(promise.strip()[:160])
    memory.compact()


def _safe_fallback_action[T: BaseModel](
    state: TruthState, player_id: str, purpose: str, schema: type[T]
) -> T | None:
    valid_targets = _targets_from_purpose(purpose)
    if not valid_targets:
        valid_targets = [player.id for player in state.players if player.id != player_id]
    first = valid_targets[0] if valid_targets else player_id
    if schema is PublicSpeechAction:
        return cast(
            T,
            PublicSpeechAction(
                speech="我先保留一下，等下一輪再把判斷講清楚。",
                concise_rationale="API fallback",
            ),
        )
    if schema is PrivateMessageAction and valid_targets:
        return cast(
            T,
            PrivateMessageAction(
                target_id=first,
                message="我這邊暫時先保守交換資訊，你目前最懷疑誰？",
                concise_rationale="API fallback",
            ),
        )
    if schema is NominationAction:
        return cast(T, NominationAction(nominate=False, reason="目前資訊不足，先不提名。"))
    if schema is DefenseAction:
        return cast(T, DefenseAction(statement="我先不過度辯解；請看我的公開說法與票型。"))
    if schema is VoteAction:
        return cast(T, VoteAction(vote=False, public_reason="理由不足，先不投票。"))
    if schema is NightTargetAction:
        return cast(T, NightTargetAction(target_id=first))
    if schema is ChambermaidChoice and len(valid_targets) >= 2:
        return cast(T, ChambermaidChoice(target_ids=valid_targets[:2]))
    if schema is KlutzChoice and valid_targets:
        return cast(T, KlutzChoice(target_id=first))
    return None


def _targets_from_purpose(purpose: str) -> list[str]:
    match = re.search(r"valid_targets=(\[[^\]]*\])", purpose)
    if not match:
        return []
    try:
        parsed = ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [target for target in parsed if isinstance(target, str)]
