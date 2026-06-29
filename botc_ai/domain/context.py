from __future__ import annotations

import ast
import json
import re
from datetime import UTC, datetime
from typing import Any

from botc_ai.domain.ai_brain import refresh_ai_brain
from botc_ai.domain.models import (
    AudienceScope,
    GameEvent,
    GameView,
    PlayerPrivateView,
    PlayerTruth,
    PostgameReveal,
    PublicPlayer,
    PublicState,
    ScriptRoleView,
    TruthState,
    script_view,
)
from botc_ai.domain.roles import ROLE_SPECS, Alignment
from botc_ai.domain.sessions import all_human_seats_claimed, human_seat_claimed
from botc_ai.domain.setup import AI_PERSONAS
from botc_ai.domain.usage import summarize_usage


def _event_visible_to_player(event: GameEvent, player_id: str, *, game_over: bool) -> bool:
    if event.scope == AudienceScope.PUBLIC:
        return True
    if event.scope == AudienceScope.PLAYER_ONLY:
        return player_id in event.target_ids
    if event.scope == AudienceScope.PRIVATE_CHAT_PARTICIPANTS:
        return player_id in event.participants
    if event.scope == AudienceScope.POSTGAME_ONLY:
        return game_over
    return False


def build_public_state(state: TruthState) -> PublicState:
    public_events = [event for event in state.events if event.scope == AudienceScope.PUBLIC]
    current_on_the_block = None
    current_high_votes = 0
    valid = [n for n in state.nominations if n.day == state.day and n.eligible_for_execution]
    if valid:
        max_votes = max(n.votes for n in valid)
        leaders = [n for n in valid if n.votes == max_votes]
        if len(leaders) == 1:
            current_on_the_block = leaders[0].nominee_id
            current_high_votes = max_votes
    return PublicState(
        game_id=state.game_id,
        day=state.day,
        phase=state.phase,
        mock_ai=state.mock_ai,
        players=[
            PublicPlayer(
                id=player.id,
                name=player.name,
                seat=player.seat,
                is_human=player.is_human,
                alive=player.alive,
                ghost_vote_available=player.ghost_vote_available,
                nominated_today=player.nominated_today,
                was_nominated_today=player.was_nominated_today,
                claimed=human_seat_claimed(state, player.id) if player.is_human else True,
            )
            for player in sorted(state.players, key=lambda p: p.seat)
        ],
        public_events=public_events,
        nominations=state.nominations,
        votes=state.votes,
        last_night_deaths=state.last_night_deaths,
        current_on_the_block=current_on_the_block,
        current_high_votes=current_high_votes,
        result=state.result,
        usage=summarize_usage(state),
        ai_status=state.ai_last_status,
        ai_active_player_id=state.ai_active_player_id,
        ai_cooldown_seconds=state.ai_cooldown_seconds,
        phase_started_at=state.phase_started_at,
        phase_deadline_at=state.phase_deadline_at,
        phase_remaining_seconds=_phase_remaining_seconds(state),
        host_player_id=state.host_player_id,
        discussion_mode=state.discussion_mode,
        discussion_rounds_today=state.discussion_rounds_today,
        current_speaker_id=state.ordered_speaker_id,
        human_seats_ready=all_human_seats_claimed(state),
    )


def legal_actions_for(state: TruthState, player_id: str) -> list[str]:
    player = state.by_id(player_id)
    actions: list[str] = ["save"]
    pending_prompt = state.pending_action_prompts.get(player_id)
    if pending_prompt is not None:
        actions.append(pending_prompt.action)
    if (
        state.phase == "SETUP"
        and player.id == state.host_player_id
        and all_human_seats_claimed(state)
    ):
        actions.append("start_game")
    if _player_can_public_speak(state, player_id):
        actions.append("public_speech")
    if state.phase == "DAY_DISCUSSION" and state.ordered_speaker_id == player_id:
        actions.append("skip_speech")
    if state.phase in {"DAY_DISCUSSION", "PRIVATE_CHAT", "NOMINATIONS"}:
        actions.append("private_chat")
    if state.phase in {"DAWN", "DAY_DISCUSSION", "PRIVATE_CHAT"}:
        actions.append("advance")
    if player.alive and state.phase in {"DAY_DISCUSSION", "PRIVATE_CHAT", "NOMINATIONS"}:
        actions.append("nominate")
    if state.phase == "VOTING" and _player_can_vote_pending_nomination(state, player_id):
        actions.extend(["vote_yes", "vote_no"])
    if (
        player.alive
        and player.visible_role == "artist"
        and state.phase in {"DAY_DISCUSSION", "PRIVATE_CHAT"}
    ):
        actions.append("artist_question")
    if state.pending_klutz_id == player_id:
        actions.append("klutz_choose")
    if state.phase in {"DAY_DISCUSSION", "PRIVATE_CHAT", "NOMINATIONS"}:
        actions.append("phase_ready")
    return actions


def _player_can_public_speak(state: TruthState, player_id: str) -> bool:
    if state.phase == "NOMINATIONS":
        return True
    if state.phase != "DAY_DISCUSSION":
        return False
    if state.discussion_mode == "ordered":
        return state.ordered_speaker_id == player_id
    return True


def _player_can_vote_pending_nomination(state: TruthState, player_id: str) -> bool:
    player = state.by_id(player_id)
    if not (player.alive or player.ghost_vote_available):
        return False
    nomination = next(
        (item for item in state.nominations if item.day == state.day and not item.resolved),
        None,
    )
    if nomination is None:
        return False
    return not any(
        vote.nomination_id == nomination.id and vote.voter_id == player_id for vote in state.votes
    )


def build_private_view(state: TruthState, player_id: str) -> PlayerPrivateView:
    player = state.by_id(player_id)
    pregame = state.phase == "SETUP"
    role = ROLE_SPECS[player.visible_role] if not pregame else None
    game_over = state.result is not None
    visible_events = [
        event
        for event in state.events
        if _event_visible_to_player(event, player_id, game_over=game_over)
    ]
    private_events = [
        event
        for event in visible_events
        if event.scope in {AudienceScope.PLAYER_ONLY, AudienceScope.POSTGAME_ONLY}
        and not (pregame and event.type == "role_info")
    ]
    private_chats = [
        event for event in visible_events if event.scope == AudienceScope.PRIVATE_CHAT_PARTICIPANTS
    ]
    return PlayerPrivateView(
        player_id=player.id,
        name=player.name,
        seat=player.seat,
        alive=player.alive,
        ghost_vote_available=player.ghost_vote_available,
        role=ScriptRoleView(
            slug=role.slug if role else "pending",
            zh_name=role.zh_name if role else "尚未發身分",
            role_type=role.zh_type if role else "等待",
            ability=role.ability if role else "所有真人入座並由房主開始後才會揭露。",
        ),
        apparent_alignment=player.visible_alignment if role else Alignment.GOOD,
        private_events=private_events,
        private_chats=private_chats,
        memory=state.ai_memories.get(player_id),
        legal_actions=legal_actions_for(state, player_id),
        pending_actions=[
            prompt
            for owner_id, prompt in state.pending_action_prompts.items()
            if owner_id == player_id
        ],
    )


def _phase_remaining_seconds(state: TruthState) -> int | None:
    if state.phase_deadline_at is None:
        return None
    return max(0, int((state.phase_deadline_at - datetime.now(UTC)).total_seconds()))


def build_postgame_reveal(state: TruthState) -> PostgameReveal:
    return PostgameReveal(
        players=[
            {
                "id": player.id,
                "name": player.name,
                "seat": player.seat,
                "true_role": player.true_role,
                "true_role_zh": ROLE_SPECS[player.true_role].zh_name,
                "apparent_role": player.apparent_role,
                "apparent_role_zh": ROLE_SPECS[player.apparent_role].zh_name
                if player.apparent_role
                else None,
                "alignment": player.alignment,
                "alive": player.alive,
                "death_cause": player.death_cause,
            }
            for player in sorted(state.players, key=lambda p: p.seat)
        ],
        transformations=state.transformations,
        all_events=state.events,
        ai_memories=state.ai_memories,
    )


def build_game_view(
    state: TruthState, player_id: str, *, dev_reveal: bool = False, session_token: str | None = None
) -> GameView:
    postgame = build_postgame_reveal(state) if state.result is not None else None
    return GameView(
        public=build_public_state(state),
        private=build_private_view(state, player_id),
        script=script_view(),
        postgame=postgame,
        dev_reveal=build_postgame_reveal(state) if dev_reveal else None,
        session_token=session_token,
    )


def build_ai_context(
    state: TruthState, player_id: str, *, purpose: str, max_chars: int = 11000
) -> str:
    notebook = refresh_ai_brain(state, player_id)
    public = build_public_state(state).model_dump(mode="json")
    private = build_private_view(state, player_id).model_dump(mode="json")
    memory = state.ai_memories.get(player_id)
    persona = next((item for item in AI_PERSONAS if item.id == player_id), None)
    payload: dict[str, Any] = {
        "language": "zh-TW",
        "purpose": purpose,
        "persona": persona.__dict__ if persona else None,
        "table_cadence": {
            "ai_status": state.ai_last_status,
            "active_player_id": state.ai_active_player_id,
            "cooldown_seconds": state.ai_cooldown_seconds,
            "discussion_rounds_today": state.discussion_rounds_today,
            "max_discussion_rounds_before_nominations": 2,
            "mode": "線上即時桌遊；一次只做一小段像真人的行動。",
        },
        "action_contract": _action_contract(purpose, state, player_id),
        "conversation_directive": _conversation_directive(state, player_id, purpose),
        "real_player_speech_protocol": [
            "先回應上一位玩家或目前提名，不要像摘要機器一樣重複全桌狀態。",
            "每次公開發言只做一到兩件事：給自己的資訊/立場、問一個具體座位、或推一個明確行動。",
            "常用座位號，例如『3號』、『我左邊』、『5號剛才那票』；不要只用抽象詞。",
            "如果你是資訊角色且已收到私人資訊，白天要考慮主動透露全部或部分資訊；不要整天只說再觀察。",
            "避免模板句：『可驗證的點』、『先看票型』、『把話收窄』、『需要被追問』。除非你接著講出具體座位與理由。",
            "如果真人直接問身份或資訊，請正面回答：可以全開、半開或說明為何暫不全開，但不能無視問題。",
            "講話自然短促，可以有猶豫、讓步、改口；不要每句都像正式結論。",
        ],
        "table_reading_protocol": [
            "發言前先讀 recent_public_events、visible_table_read.claim_conflicts、visible_table_read.vote_patterns。",
            "公開發言至少引用一個具體場上資訊：某人的角色宣稱、你的私人資訊、昨夜死亡、某次提名或某次投票。",
            "如果沒有新資訊，就短句說明你目前最想聽誰補哪個缺口；不要泛泛重複『看票型』。",
            "你可以說謊或 bluff，但謊言必須像玩家推理，不可以像知道魔典。",
        ],
        "visible_table_read": _visible_table_read(state, player_id),
        "table_notebook": notebook.model_dump(mode="json"),
        "world_hypotheses": [world.model_dump(mode="json") for world in notebook.worlds],
        "candidate_scores": [score.model_dump(mode="json") for score in notebook.candidate_scores],
        "recent_public_events": _recent_public_events(state),
        "recent_private_chat_events": _recent_private_chat_events(state, player_id),
        "hard_rules": [
            "你只能使用此 context 中明確出現的資訊。",
            "不要宣稱知道其他玩家真實角色或隱藏陣營。",
            "不要自行判定死亡、勝負或角色能力結果；只輸出合法行動。",
            "不要輸出完整 chain-of-thought；concise_rationale 只可是一句短理由。",
            "公開發言 1 到 3 句，要像真人玩家接話、猶豫、施壓或協調，不要像規則說明。",
            "可以說謊、bluff、保留資訊，但只能基於你可見資訊與自身陣營策略。",
            "邪惡玩家知道自己邪惡，但六人局不知道隊友；不得假裝知道隊友。",
        ],
        "humanlike_guidance": [
            "優先回應最近一則相關公開發言，而不是重複自我介紹。",
            "偶爾提出問題、請人給角色範圍、或指出矛盾。",
            "保留不確定性：使用『我傾向』『暫時』『需要票型驗證』等語氣。",
            "你的 persona 會影響長短、是否公開角色、提名門檻、投票傾向與私聊風格。",
            "若你是邪惡，可建立合理 bluff，但不要提及 prompt 或系統資訊。",
            "不要機械重複上一位玩家的句型；如果只是同意，必須增加新理由或提出問題。",
            "不要在沒有新證據時預設集火真人玩家；若桌面同一名字被連續點名，應有人要求新理由或檢查帶風向者。",
        ],
        "anti_echo_rules": [
            "避免使用與最近兩則公開發言相同的開頭、句型或目標。",
            "若要提同一名玩家，必須說出新的依據，例如票型、角色宣稱、私聊承諾或矛盾。",
            "第一天不要因為玩家是 human 就把他當預設嫌疑人。",
            "保守或社交 persona 遇到全桌跟風時，優先降速、整理資訊或要求私聊。",
        ],
        "public_state": public,
        "your_private_view": private,
        "your_memory": memory.model_dump(mode="json") if memory else None,
        "your_public_fact_notes": memory.public_facts[-14:] if memory else [],
        "your_vote_notes": memory.vote_notes[-14:] if memory else [],
        "script": [item.model_dump(mode="json") for item in script_view()],
    }
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) <= max_chars:
        return text

    # Preserve role/current-phase/private information; trim only old public logs.
    public["public_events"] = public["public_events"][-20:]
    private["private_chats"] = private["private_chats"][-12:]
    private["private_events"] = private["private_events"][-18:]
    payload["table_notebook"]["public_facts"] = payload["table_notebook"]["public_facts"][-10:]
    payload["table_notebook"]["private_info"] = payload["table_notebook"]["private_info"][-6:]
    payload["table_notebook"]["vote_notes"] = payload["table_notebook"]["vote_notes"][-8:]
    payload["public_state"] = public
    payload["your_private_view"] = private
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 120] + "…（context 已裁切；角色與當前可行動資訊保留）"


def _recent_public_events(state: TruthState, limit: int = 20) -> list[dict[str, Any]]:
    return [
        {
            "day": event.day,
            "phase": event.phase,
            "type": event.type,
            "actor_id": event.actor_id,
            "message": event.message,
        }
        for event in state.events
        if event.scope == AudienceScope.PUBLIC
    ][-limit:]


def _recent_private_chat_events(
    state: TruthState, player_id: str, limit: int = 8
) -> list[dict[str, Any]]:
    return [
        {
            "day": event.day,
            "phase": event.phase,
            "actor_id": event.actor_id,
            "participants": event.participants,
            "message": event.message,
        }
        for event in state.events
        if event.scope == AudienceScope.PRIVATE_CHAT_PARTICIPANTS
        and player_id in event.participants
    ][-limit:]


def _conversation_directive(state: TruthState, player_id: str, purpose: str) -> dict[str, Any]:
    player = state.by_id(player_id)
    role = ROLE_SPECS[player.visible_role]
    private_info = [
        event.message
        for event in state.events
        if event.scope == AudienceScope.PLAYER_ONLY and player_id in event.target_ids
    ][-5:]
    latest_human = _latest_human_public_speech(state)
    asks_identity = _asks_identity_or_info(latest_human or "")
    first_night_info_roles = {"clockmaker", "investigator", "empath", "chambermaid"}
    role_pressure = "正常桌上需要你給出可回頭檢查的內容，不要只說看票型。"
    if player.visible_role in first_night_info_roles and private_info:
        role_pressure = (
            "你是資訊角色或看起來像資訊角色；第一天通常要公開或半公開你的資訊，"
            "例如數字、兩人組、鄰座判讀或你查過誰。可保留一點，但不能整段都空泛。"
        )
    elif player.visible_alignment == Alignment.EVIL:
        role_pressure = (
            "你知道自己是邪惡，但此局不認隊友也沒有惡魔 bluff；請自行選一個可信好人角色或資訊角說法，"
            "說得像真人 bluff，但不要聲稱知道隊友或隱藏魔典。"
        )
    if not player.alive:
        role_pressure = (
            "你已死亡；可以繼續說話，但要像死亡玩家一樣交代遺言、資訊與懷疑，"
            "不要假裝自己仍有存活能力或提名權。"
        )
    return {
        "your_visible_role_slug": player.visible_role,
        "your_visible_role_zh": role.zh_name,
        "you_are_alive": player.alive,
        "private_info_you_may_discuss": private_info,
        "latest_human_public_speech": latest_human,
        "human_is_asking_identity_or_info": asks_identity,
        "role_pressure": role_pressure,
        "public_speech_style": [
            "討論玩家時優先使用座位號與名字，例如「3號 林鏡」，避免只用名字造成桌面追蹤困難。",
            "公開發言請像真人桌邊說話：1 到 2 句，通常不超過 120 個中文字。",
            "如果真人直接問身分或資訊，請直接回答你的角色宣稱、二選一範圍或你拿到的資訊，不要把問題丟回全桌。",
            "每次至少包含一個具體內容：角色宣稱、數字、兩人組、昨夜死亡判讀、提名/投票對象或明確懷疑理由。",
            "避免連續使用『可驗證』『票型』『卡點』『空轉』這些抽象詞；同一次發言最多使用其中一個。",
            "不要重複上一位玩家的句型；可以口語、短句、有猶豫，但要推進局面。",
        ],
        "nomination_style": (
            "提名前先確認是否真的有新理由；不要只因為流程輪到你就提名。"
            if purpose.startswith("nominate")
            else ""
        ),
    }


def _visible_table_read(state: TruthState, player_id: str) -> dict[str, Any]:
    memory = state.ai_memories.get(player_id)
    suspicion = memory.suspicion if memory else {}
    claims = memory.known_claims if memory else {}
    return {
        "players": [
            {
                "id": player.id,
                "name": player.name,
                "seat": player.seat,
                "seat_number": player.seat + 1,
                "seat_label": _seat_label(player),
                "alive": player.alive,
                "is_you": player.id == player_id,
                "ghost_vote_available": player.ghost_vote_available,
                "public_claim_known_to_you": claims.get(player.id),
                "your_suspicion": suspicion.get(player.id),
                "recent_public_pressure_count": _recent_pressure_count(state, player.id),
            }
            for player in sorted(state.players, key=lambda item: item.seat)
        ],
        "open_nominations": [
            nomination.model_dump(mode="json")
            for nomination in state.nominations
            if nomination.day == state.day and not nomination.resolved
        ],
        "today_vote_count": len([vote for vote in state.votes if vote.day == state.day]),
        "last_night_deaths": state.last_night_deaths,
        "claim_conflicts": _claim_conflicts(state, player_id),
        "pressure_summary": _pressure_summary(state),
        "vote_patterns": _vote_patterns(state),
    }


def _claim_conflicts(state: TruthState, player_id: str) -> list[dict[str, Any]]:
    memory = state.ai_memories.get(player_id)
    if memory is None:
        return []
    claims = dict(memory.known_claims)
    if memory.public_claim:
        claims[player_id] = memory.public_claim
    grouped: dict[str, list[str]] = {}
    for claimant_id, role in claims.items():
        if role in ROLE_SPECS:
            grouped.setdefault(role, []).append(claimant_id)
    conflicts = []
    for role, claimant_ids in grouped.items():
        if len(claimant_ids) < 2:
            continue
        conflicts.append(
            {
                "role": role,
                "role_zh": ROLE_SPECS[role].zh_name,
                "claimants": [
                    {
                        "id": claimant_id,
                        "name": state.by_id(claimant_id).name,
                        "seat_number": state.by_id(claimant_id).seat + 1,
                        "seat_label": _seat_label(state.by_id(claimant_id)),
                    }
                    for claimant_id in sorted(claimant_ids)
                ],
            }
        )
    return conflicts


def _pressure_summary(state: TruthState) -> list[dict[str, Any]]:
    return [
        {
            "player_id": player.id,
            "name": player.name,
            "seat_number": player.seat + 1,
            "seat_label": _seat_label(player),
            "recent_public_pressure_count": _recent_pressure_count(state, player.id),
            "nominated_today": player.was_nominated_today,
        }
        for player in sorted(state.players, key=lambda item: item.seat)
    ]


def _vote_patterns(state: TruthState) -> dict[str, Any]:
    return {
        "recent_votes": [vote.model_dump(mode="json") for vote in state.votes[-18:]],
        "today_nomination_results": [
            nomination.model_dump(mode="json")
            for nomination in state.nominations
            if nomination.day == state.day and nomination.resolved
        ],
    }


def _seat_label(player: PlayerTruth) -> str:
    return f"{player.seat + 1}號 {player.name}"


def _action_contract(purpose: str, state: TruthState, player_id: str) -> dict[str, Any]:
    valid_targets = _extract_valid_targets(purpose)
    if not valid_targets:
        valid_targets = [player.id for player in state.players if player.id != player_id]
    names = {
        player.id: _seat_label(player) for player in state.players if player.id in valid_targets
    }
    base = {
        "valid_target_ids": valid_targets,
        "valid_target_names": names,
        "required_memory_update": "填寫短 summary、next_intent，必要時更新 suspicion_delta 或 current_bluff。",
        "speech_shape": {
            "public": "1 到 2 句，像真人桌邊發言。至少包含一個具體座位、角色資訊、票型事件或問題。",
            "private": "直接交換資訊或承諾，不要寫成公開演講。",
            "nomination": "只有在已經有具體理由時才提名，理由要能讓其他玩家投票。",
        },
    }
    if purpose.startswith("public_speech"):
        base["instruction"] = (
            "輸出一段像真人玩家的公開發言。接最近話題；可提問題、施壓、協調或有限角色宣稱。"
            "不要複讀上一句；不要無新理由跟風點同一人。"
        )
    elif purpose.startswith("private_message"):
        base["instruction"] = "選一名合法目標私聊，建立信任、測謊、交換範圍或設局。"
    elif purpose.startswith("nominate"):
        base["instruction"] = "只在值得測票或施壓時 nominate=true；理由要公開可接受。"
    elif purpose.startswith("defense"):
        base["instruction"] = "像被提名的玩家一樣辯護；不要承認隱藏資訊來源。"
    elif purpose.startswith("vote"):
        base["instruction"] = "用你的懷疑度、票型與 persona 決定投票；public_reason 簡短。"
    elif purpose.startswith("night_target"):
        base["instruction"] = "選合法夜間目標；若邪惡，可考慮威脅、保護 bluff、或 starpass。"
    elif purpose.startswith("chambermaid_choice"):
        base["instruction"] = "選兩名合法存活玩家，像真人一樣測資訊價值。"
    elif purpose.startswith("klutz_choice"):
        base["instruction"] = "笨蛋死亡選擇；根據可見資訊避開最可疑邪惡目標。"
    elif purpose.startswith("artist_parse"):
        base["instruction"] = "把自然語言問題轉成受限 ArtistStructuredQuestion；不能回答真偽。"
    else:
        base["instruction"] = "輸出 schema 要求的單一合法行動。"
    return base


def _extract_valid_targets(purpose: str) -> list[str]:
    match = re.search(r"valid_targets=(\[[^\]]*\])", purpose)
    if not match:
        return []
    try:
        parsed = ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str)]


def _recent_pressure_count(state: TruthState, player_id: str, limit: int = 10) -> int:
    player = state.by_id(player_id)
    public_speeches = [
        event
        for event in state.events
        if event.scope == AudienceScope.PUBLIC and event.type == "public_speech"
    ]
    return sum(
        player.name in _spoken_content(state, event.actor_id, event.message)
        for event in public_speeches[-limit:]
    )


def _latest_human_public_speech(state: TruthState) -> str | None:
    for event in reversed(state.events):
        if (
            event.scope == AudienceScope.PUBLIC
            and event.type == "public_speech"
            and event.actor_id == state.human_id
        ):
            return _spoken_content(state, event.actor_id, event.message)
    return None


def _asks_identity_or_info(text: str) -> bool:
    compact = re.sub(r"\s+", "", text.lower())
    return any(
        token in compact
        for token in (
            "身分",
            "身份",
            "角色",
            "資訊",
            "你是什麼",
            "你們是什麼",
            "拿到什麼",
            "查到什麼",
            "報資訊",
            "claim",
            "role",
            "info",
        )
    )


def _spoken_content(state: TruthState, actor_id: str | None, message: str) -> str:
    if actor_id is None:
        return message
    try:
        actor_name = state.by_id(actor_id).name
    except KeyError:
        return message
    if message.startswith(actor_name):
        rest = message[len(actor_name) :]
        if rest[:1] in {"：", ":", "?", "？"}:
            return rest[1:]
    return message
