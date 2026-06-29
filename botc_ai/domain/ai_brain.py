from __future__ import annotations

import re
from collections.abc import Sequence

from botc_ai.domain.models import (
    AIMemory,
    AudienceScope,
    CandidateScore,
    GameEvent,
    TableNotebook,
    TruthState,
    WorldHypothesis,
)
from botc_ai.domain.roles import ROLE_SPECS, Alignment

INFO_ROLE_THREAT = {
    "clockmaker": 0.11,
    "investigator": 0.16,
    "empath": 0.18,
    "chambermaid": 0.12,
    "artist": 0.15,
    "sage": -0.08,
    "klutz": -0.1,
}


def refresh_ai_brain(
    state: TruthState, player_id: str, valid_targets: Sequence[str] | None = None
) -> TableNotebook:
    """Refresh one AI player's public-safe table read.

    This function intentionally reads only public events, the player's own private events,
    and that player's isolated memory. It must not inspect other players' true roles or
    alignments.
    """

    memory = state.ai_memories.get(player_id)
    if memory is None:
        return build_table_notebook(state, player_id, valid_targets)

    scores = score_candidates(state, player_id, valid_targets)
    worlds = build_world_hypotheses(state, player_id, scores)
    notebook = build_table_notebook(state, player_id, valid_targets, scores=scores, worlds=worlds)
    memory.notebook = notebook
    memory.worlds = worlds
    memory.public_facts = _merge_tail(memory.public_facts, notebook.public_facts, limit=14)
    memory.vote_notes = _merge_tail(memory.vote_notes, notebook.vote_notes, limit=14)
    if worlds and worlds[0].next_test:
        memory.next_intent = worlds[0].next_test[:180]
    memory.compact()
    return notebook


def build_table_notebook(
    state: TruthState,
    player_id: str,
    valid_targets: Sequence[str] | None = None,
    *,
    scores: list[CandidateScore] | None = None,
    worlds: list[WorldHypothesis] | None = None,
) -> TableNotebook:
    memory = state.ai_memories.get(player_id)
    claims = _visible_claims(memory, player_id)
    claim_conflicts = _claim_conflicts(state, claims)
    candidate_scores = (
        scores if scores is not None else score_candidates(state, player_id, valid_targets)
    )
    hypotheses = (
        worlds if worlds is not None else build_world_hypotheses(state, player_id, candidate_scores)
    )
    return TableNotebook(
        day=state.day,
        phase=state.phase,
        claims=claims,
        public_facts=_public_facts_from_events(state),
        vote_notes=_vote_notes(state),
        private_info=_private_info_for_player(state, player_id),
        contradictions=claim_conflicts,
        candidate_scores=candidate_scores[:6],
        worlds=hypotheses,
        endgame_warning=_endgame_warning(state),
    )


def score_candidates(
    state: TruthState, player_id: str, valid_targets: Sequence[str] | None = None
) -> list[CandidateScore]:
    memory = state.ai_memories.get(player_id)
    valid_ids = set(valid_targets) if valid_targets is not None else None
    claims = _visible_claims(memory, player_id)
    conflicts = _claim_conflict_roles(claims)
    scores: list[CandidateScore] = []
    for player in sorted(state.players, key=lambda item: item.seat):
        if player.id == player_id:
            if valid_ids is None or player.id not in valid_ids:
                continue
        elif valid_ids is not None and player.id not in valid_ids:
            continue

        claim = claims.get(player.id)
        suspicion = memory.suspicion.get(player.id, 0.5) if memory else 0.5
        pressure_count = _recent_pressure_count(state, player.id)
        vote_pressure = _vote_pressure(state, player.id)
        claim_modifier = _claim_nomination_modifier(claim, conflicts)
        night_claim_threat = INFO_ROLE_THREAT.get(claim or "", 0.0)
        alive_penalty = 0.0 if player.alive else -0.22
        self_penalty = -0.18 if player.id == player_id else 0.0

        nomination_score = _clamp01(
            suspicion
            + vote_pressure
            + min(pressure_count, 5) * 0.035
            + claim_modifier
            + alive_penalty
            + self_penalty
        )
        vote_score = _clamp01(
            suspicion
            + vote_pressure
            + min(pressure_count, 5) * 0.025
            + claim_modifier * 0.55
            + alive_penalty * 0.7
            + self_penalty
        )
        night_kill_score = _clamp01(
            0.48
            + (suspicion - 0.5) * 0.18
            + night_claim_threat
            - min(pressure_count, 5) * 0.025
            + (0.08 if claim else 0.0)
            + (-0.3 if not player.alive else 0.0)
            + (0.1 if player.id == player_id else 0.0)
        )
        scores.append(
            CandidateScore(
                player_id=player.id,
                seat_number=player.seat + 1,
                name=player.name,
                alive=player.alive,
                public_claim=claim,
                suspicion=round(suspicion, 3),
                pressure_count=pressure_count,
                vote_pressure=round(vote_pressure, 3),
                nomination_score=round(nomination_score, 3),
                vote_score=round(vote_score, 3),
                night_kill_score=round(night_kill_score, 3),
                reasons=_score_reasons(
                    state,
                    player.id,
                    claim=claim,
                    suspicion=suspicion,
                    pressure_count=pressure_count,
                    vote_pressure=vote_pressure,
                    conflict=claim in conflicts if claim else False,
                    alive=player.alive,
                ),
            )
        )
    return sorted(scores, key=lambda item: (item.nomination_score, item.vote_score), reverse=True)


def build_world_hypotheses(
    state: TruthState, player_id: str, scores: Sequence[CandidateScore]
) -> list[WorldHypothesis]:
    own_player = state.by_id(player_id)
    alive_scores = [score for score in scores if score.alive and score.player_id != player_id]
    if not alive_scores:
        return []
    ranked = sorted(alive_scores, key=lambda item: item.vote_score, reverse=True)
    trusted = sorted(alive_scores, key=lambda item: item.vote_score)[:2]
    top = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    confidence = _clamp01(0.48 + (top.vote_score - (second.vote_score if second else 0.45)) * 0.6)
    worlds: list[WorldHypothesis] = [
        WorldHypothesis(
            summary=(
                f"世界A：{_score_label(top)} 最需要被追問或驗票；"
                f"{_score_label(second) if second else '暫無第二人'} 是次要壓力點。"
            ),
            demon_candidates=[top.player_id],
            minion_candidates=[second.player_id] if second else [],
            trusted_candidates=[item.player_id for item in trusted],
            confidence=round(confidence, 3),
            next_test=f"請 {_score_label(top)} 給出可回頭檢查的角色範圍或夜間資訊。",
        )
    ]
    if len(ranked) >= 3:
        alternate = ranked[1]
        worlds.append(
            WorldHypothesis(
                summary=(
                    f"世界B：如果 {_score_label(top)} 是被帶風向，"
                    f"{_score_label(alternate)} 的票型或發言需要重新比對。"
                ),
                demon_candidates=[alternate.player_id],
                minion_candidates=[top.player_id, ranked[2].player_id],
                trusted_candidates=[item.player_id for item in trusted],
                confidence=round(max(0.2, confidence - 0.12), 3),
                next_test=f"比對 {_score_label(top)} 與 {_score_label(alternate)} 的提名和投票理由。",
            )
        )
    if own_player.visible_alignment == Alignment.EVIL:
        pressure = top if top.player_id != player_id else (second or top)
        worlds.insert(
            0,
            WorldHypothesis(
                summary=(
                    f"邪惡視角：目前可把公開壓力維持在 {_score_label(pressure)}，"
                    "但不要暴露未被公開的隊友資訊。"
                ),
                demon_candidates=[],
                minion_candidates=[],
                trusted_candidates=[item.player_id for item in trusted],
                confidence=0.55,
                next_test=f"推動桌面追問 {_score_label(pressure)} 的前後矛盾。",
            ),
        )
    return worlds[:3]


def _visible_claims(memory: AIMemory | None, player_id: str) -> dict[str, str]:
    if memory is None:
        return {}
    claims = {
        claimed_player_id: role
        for claimed_player_id, role in memory.known_claims.items()
        if role in ROLE_SPECS
    }
    if memory.public_claim in ROLE_SPECS:
        claims[player_id] = memory.public_claim
    return claims


def _public_facts_from_events(state: TruthState, limit: int = 16) -> list[str]:
    facts: list[str] = []
    for event in state.events:
        if event.scope != AudienceScope.PUBLIC:
            continue
        if event.type in {"public_speech", "nomination", "vote", "execution", "dawn", "game_start"}:
            facts.append(_compact_event_line(event))
    return facts[-limit:]


def _vote_notes(state: TruthState, limit: int = 14) -> list[str]:
    notes: list[str] = []
    for nomination in state.nominations:
        if not nomination.resolved:
            continue
        nominee = state.by_id(nomination.nominee_id)
        outcome = "達標" if nomination.eligible_for_execution else "未達標"
        notes.append(
            f"D{nomination.day}: {nominee.seat + 1}號{nominee.name} "
            f"{nomination.votes}/{nomination.threshold} {outcome}"
        )
    return notes[-limit:]


def _private_info_for_player(state: TruthState, player_id: str, limit: int = 8) -> list[str]:
    private: list[str] = []
    for event in state.events:
        if (event.scope == AudienceScope.PLAYER_ONLY and player_id in event.target_ids) or (
            event.scope == AudienceScope.PRIVATE_CHAT_PARTICIPANTS
            and player_id in event.participants
        ):
            private.append(_compact_event_line(event))
    return private[-limit:]


def _claim_conflicts(state: TruthState, claims: dict[str, str]) -> list[str]:
    conflicts: list[str] = []
    grouped: dict[str, list[str]] = {}
    for claimed_player_id, role in claims.items():
        if role in ROLE_SPECS:
            grouped.setdefault(role, []).append(claimed_player_id)
    for role, player_ids in grouped.items():
        if len(player_ids) < 2:
            continue
        labels = ", ".join(_player_label(state, player_id) for player_id in player_ids)
        conflicts.append(f"多人宣稱 {ROLE_SPECS[role].zh_name}: {labels}")
    return conflicts


def _claim_conflict_roles(claims: dict[str, str]) -> set[str]:
    counts: dict[str, int] = {}
    for role in claims.values():
        counts[role] = counts.get(role, 0) + 1
    return {role for role, count in counts.items() if count > 1}


def _claim_nomination_modifier(claim: str | None, conflict_roles: set[str]) -> float:
    if not claim:
        return 0.04
    if claim in conflict_roles:
        return 0.18
    if claim in INFO_ROLE_THREAT:
        return -0.03
    return 0.02


def _recent_pressure_count(state: TruthState, target_id: str, limit: int = 16) -> int:
    target = state.by_id(target_id)
    patterns = [
        target.id,
        target.name,
        f"{target.seat + 1}號",
        f"{target.seat + 1}号",
        f"{target.seat + 1}.",
    ]
    count = 0
    recent = [event for event in state.events if event.scope == AudienceScope.PUBLIC][-limit:]
    for event in recent:
        if event.actor_id == target_id:
            continue
        text = event.message
        if any(pattern and pattern in text for pattern in patterns):
            count += 1
    return count


def _vote_pressure(state: TruthState, target_id: str) -> float:
    score = 0.0
    for nomination in state.nominations:
        if nomination.nominee_id == target_id:
            if nomination.resolved:
                score += 0.08 if nomination.eligible_for_execution else -0.02
            else:
                score += 0.06
        if nomination.nominator_id == target_id and nomination.resolved:
            score += 0.02 if not nomination.eligible_for_execution else 0.0
    yes_votes = sum(1 for vote in state.votes if vote.nominee_id == target_id and vote.vote)
    score += min(yes_votes, 5) * 0.012
    return _clamp(score, -0.12, 0.25)


def _score_reasons(
    state: TruthState,
    player_id: str,
    *,
    claim: str | None,
    suspicion: float,
    pressure_count: int,
    vote_pressure: float,
    conflict: bool,
    alive: bool,
) -> list[str]:
    reasons: list[str] = []
    player = state.by_id(player_id)
    reasons.append(f"{player.seat + 1}號{player.name} 記憶懷疑值 {suspicion:.2f}")
    if claim:
        reasons.append(f"公開宣稱 {ROLE_SPECS[claim].zh_name}")
    else:
        reasons.append("尚未有清楚公開宣稱")
    if pressure_count:
        reasons.append(f"最近公開提到 {pressure_count} 次")
    if abs(vote_pressure) >= 0.03:
        reasons.append(f"票型壓力 {vote_pressure:+.2f}")
    if conflict:
        reasons.append("角色宣稱與他人撞車")
    if not alive:
        reasons.append("已死亡，白天提名價值降低")
    return reasons[:5]


def _compact_event_line(event: GameEvent) -> str:
    clean = re.sub(r"\s+", " ", event.message).strip()
    if len(clean) > 170:
        clean = f"{clean[:169].rstrip()}…"
    return f"D{event.day}/{event.type}: {clean}"


def _player_label(state: TruthState, player_id: str) -> str:
    player = state.by_id(player_id)
    return f"{player.seat + 1}號{player.name}"


def _score_label(score: CandidateScore | None) -> str:
    if score is None:
        return "暫無對象"
    return f"{score.seat_number}號{score.name}"


def _endgame_warning(state: TruthState) -> str | None:
    living = state.living_count()
    if living <= 3:
        return f"只剩 {living} 名存活玩家，下一次處決或夜晚可能直接決定勝負。"
    if state.day >= state.max_days:
        return "已到最大天數保護，今天需要明確收斂處決方向。"
    return None


def _merge_tail(existing: list[str], incoming: Sequence[str], *, limit: int) -> list[str]:
    merged = list(existing)
    seen = set(merged)
    for item in incoming:
        if item and item not in seen:
            merged.append(item)
            seen.add(item)
    return merged[-limit:]


def _clamp01(value: float) -> float:
    return _clamp(value, 0.0, 1.0)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))
