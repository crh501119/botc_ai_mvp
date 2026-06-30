from __future__ import annotations

import re

from botc_ai.domain.models import (
    AudienceScope,
    GameEvent,
    ParsedPublicClaim,
    PlayerTruth,
    TruthState,
)
from botc_ai.domain.role_knowledge import ROLE_KNOWLEDGE, role_from_alias, roles_mentioned
from botc_ai.domain.roles import ROLE_SPECS, RoleType

SELF_CLAIM_MARKERS = (
    "我是",
    "自己是",
    "我先半開",
    "我公開宣稱",
    "我公開立場",
    "這邊我",
    "我這邊",
)


def parse_public_claims(state: TruthState, limit: int = 24) -> list[ParsedPublicClaim]:
    claims: list[ParsedPublicClaim] = []
    for event in state.events:
        if event.scope != AudienceScope.PUBLIC or event.type != "public_speech":
            continue
        claim = parse_public_claim_event(state, event)
        if claim is not None:
            claims.append(claim)
    return claims[-limit:]


def public_role_claims_from_events(
    state: TruthState, parsed_claims: list[ParsedPublicClaim] | None = None
) -> dict[str, str]:
    claims = parsed_claims if parsed_claims is not None else parse_public_claims(state)
    role_claims: dict[str, str] = {}
    for claim in claims:
        if claim.speaker_id and claim.claimed_role in ROLE_SPECS:
            role_claims[claim.speaker_id] = claim.claimed_role
    return role_claims


def claim_warnings_for(claims: list[ParsedPublicClaim], limit: int = 10) -> list[str]:
    warnings: list[str] = []
    for claim in claims:
        if claim.claimed_role == "investigator" and claim.is_complete_format:
            warnings.append(
                f"{_claim_speaker_label(claim)} 的調查員宣稱是二選一完整資訊；不要追問唯一爪牙，改追問候選人的角色範圍。"
            )
        if claim.claimed_role == "empath" and claim.is_complete_format:
            warnings.append(
                f"{_claim_speaker_label(claim)} 的共情者數字只代表最近存活鄰居邪惡數，不是任選兩人查驗。"
            )
        if claim.claimed_role == "chambermaid" and claim.is_complete_format:
            warnings.append(f"{_claim_speaker_label(claim)} 的侍女數字是醒來人數，不是邪惡數。")
    return warnings[-limit:]


def parse_public_claim_event(state: TruthState, event: GameEvent) -> ParsedPublicClaim | None:
    text = _spoken_content(state, event.actor_id, event.message)
    claimed_role = _self_claimed_role(text)
    if claimed_role is None:
        return None

    if claimed_role == "clockmaker":
        number = _number_after(
            text,
            (
                r"數字\s*(?:是|為|=)?\s*([0-6])",
                r"拿到(?:的數字)?\s*(?:是|為|=)?\s*([0-6])",
            ),
        )
        if number is not None:
            return _claim(
                state,
                event,
                text,
                claimed_role,
                claim_type="clockmaker_distance",
                number=number,
                is_complete_format=True,
                confidence=0.92,
            )

    if claimed_role == "investigator":
        result_role = _result_role_of_type(text, RoleType.MINION, exclude_role=claimed_role)
        seats = _seat_numbers_before(text, ("有一個", "其中一個", "是", result_role or ""))
        if len(seats) >= 2 and result_role is not None:
            return _claim(
                state,
                event,
                text,
                claimed_role,
                claim_type="investigator_two_candidates_one_minion",
                target_seats=seats[:2],
                result_role=result_role,
                is_complete_format=True,
                confidence=0.95,
            )

    if claimed_role == "empath":
        number = _number_after(
            text,
            (
                r"有\s*([0-2])\s*(?:名|個)?邪惡",
                r"(?:是|為|=)\s*([0-2])\s*(?:名|個)?邪惡?",
                r"數字\s*(?:是|為|=)?\s*([0-2])",
                r"拿到(?:的數字)?\s*(?:是|為|=)?\s*([0-2])",
            ),
        )
        seats = _seat_numbers_before(text, ("有", "是", "為", "=", "數字"))
        if number is not None:
            return _claim(
                state,
                event,
                text,
                claimed_role,
                claim_type="empath_alive_neighbor_evil_count",
                target_seats=seats[:2],
                number=number,
                is_complete_format=len(seats) >= 2,
                confidence=0.86 if len(seats) >= 2 else 0.72,
            )

    if claimed_role == "chambermaid":
        number = _number_after(
            text,
            (
                r"得\s*([0-2])",
                r"結果\s*(?:是|為|=)?\s*([0-2])",
                r"數字\s*(?:是|為|=)?\s*([0-2])",
            ),
        )
        seats = _seat_numbers_before(text, ("得", "結果", "數字"))
        if len(seats) >= 2 and number is not None:
            return _claim(
                state,
                event,
                text,
                claimed_role,
                claim_type="chambermaid_two_targets_woke_count",
                target_seats=seats[:2],
                number=number,
                is_complete_format=True,
                confidence=0.9,
            )

    if claimed_role == "artist":
        answer = _yes_no_answer(text)
        return _claim(
            state,
            event,
            text,
            claimed_role,
            claim_type="artist_yes_no_answer" if answer is not None else "role_claim",
            answer=answer,
            is_complete_format=answer is not None or "還沒" in text or "未" in text,
            confidence=0.78 if answer is None else 0.88,
        )

    if claimed_role == "sage":
        seats = _seat_numbers_before(text, ("有", "是", "惡魔", "恶魔"))
        if len(seats) >= 2 and ("惡魔" in text or "恶魔" in text):
            return _claim(
                state,
                event,
                text,
                claimed_role,
                claim_type="sage_two_candidates_one_demon",
                target_seats=seats[:2],
                result_role="imp",
                is_complete_format=True,
                confidence=0.86,
            )

    return _claim(
        state,
        event,
        text,
        claimed_role,
        claim_type="role_claim",
        is_complete_format=False,
        confidence=0.68,
    )


def _claim(
    state: TruthState,
    event: GameEvent,
    text: str,
    claimed_role: str,
    *,
    claim_type: str,
    target_seats: list[int] | None = None,
    result_role: str | None = None,
    number: int | None = None,
    answer: bool | None = None,
    is_complete_format: bool,
    confidence: float,
) -> ParsedPublicClaim:
    speaker = state.by_id(event.actor_id) if event.actor_id else None
    targets = _players_from_seats(state, target_seats or [])
    knowledge = ROLE_KNOWLEDGE[claimed_role]
    result_spec = ROLE_SPECS.get(result_role or "")
    return ParsedPublicClaim(
        event_id=event.id,
        speaker_id=event.actor_id,
        speaker_name=speaker.name if speaker else None,
        speaker_seat=speaker.seat + 1 if speaker else None,
        raw_text=text,
        claim_type=claim_type,
        claimed_role=claimed_role,
        claimed_role_zh=ROLE_SPECS[claimed_role].zh_name,
        info_shape=knowledge.info_shape,
        target_ids=[player.id for player in targets],
        target_seats=[player.seat + 1 for player in targets],
        target_labels=[f"{player.seat + 1}號 {player.name}" for player in targets],
        result_role=result_role,
        result_role_zh=result_spec.zh_name if result_spec else None,
        number=number,
        answer=answer,
        is_complete_format=is_complete_format,
        invalid_followups=knowledge.invalid_followups,
        good_followups=knowledge.good_followups,
        deduction_limits=knowledge.deduction_limits,
        confidence=confidence,
    )


def _spoken_content(state: TruthState, actor_id: str | None, message: str) -> str:
    text = re.sub(r"^\s*\d+\s*號\s*[^:：]{0,16}[:：]\s*", "", message)
    if actor_id is None:
        return text.strip()
    try:
        actor_name = state.by_id(actor_id).name
    except KeyError:
        return text.strip()
    return re.sub(rf"^\s*{re.escape(actor_name)}\s*[:：]\s*", "", text).strip()


def _self_claimed_role(text: str) -> str | None:
    for marker in SELF_CLAIM_MARKERS:
        marker_index = text.find(marker)
        if marker_index < 0:
            continue
        window = text[marker_index : marker_index + 34]
        role = role_from_alias(window)
        if role is not None:
            return role

    compact = text.strip()
    if compact.startswith(("跳", "claim ")):
        return role_from_alias(compact[:24])
    return None


def _result_role_of_type(text: str, role_type: RoleType, *, exclude_role: str) -> str | None:
    for role in roles_mentioned(text):
        if role == exclude_role:
            continue
        if ROLE_SPECS[role].role_type == role_type:
            return role
    return None


def _seat_numbers_before(text: str, stop_tokens: tuple[str, ...]) -> list[int]:
    cut = text
    for token in stop_tokens:
        if token and token in cut:
            cut = cut.split(token, 1)[0]
            break
    numbers = [int(item) for item in re.findall(r"(?<!\d)([1-9])\s*號?", cut)]
    result: list[int] = []
    for number in numbers:
        if number not in result:
            result.append(number)
    return result


def _number_after(text: str, patterns: tuple[str, ...]) -> int | None:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return None


def _yes_no_answer(text: str) -> bool | None:
    if re.search(r"答案\s*(?:是|為|=)?\s*(?:是|對|yes|true)", text, re.IGNORECASE):
        return True
    if re.search(r"答案\s*(?:是|為|=)?\s*(?:否|不是|不對|no|false)", text, re.IGNORECASE):
        return False
    return None


def _players_from_seats(state: TruthState, seats: list[int]) -> list[PlayerTruth]:
    by_seat = {player.seat + 1: player for player in state.players}
    return [by_seat[seat] for seat in seats if seat in by_seat]


def _claim_speaker_label(claim: ParsedPublicClaim) -> str:
    if claim.speaker_seat is None or claim.speaker_name is None:
        return claim.speaker_id or "未知玩家"
    return f"{claim.speaker_seat}號 {claim.speaker_name}"
