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
        and not _artist_used(state, player_id)
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


def _artist_used(state: TruthState, player_id: str) -> bool:
    return any(event.type == f"artist_used:{player_id}" for event in state.events)


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
    state: TruthState, player_id: str, *, purpose: str, max_chars: int = 14000
) -> str:
    notebook = refresh_ai_brain(state, player_id)
    public = build_public_state(state).model_dump(mode="json")
    private = build_private_view(state, player_id).model_dump(mode="json")
    memory = state.ai_memories.get(player_id)
    private["memory"] = _memory_context(memory)
    persona = next((item for item in AI_PERSONAS if item.id == player_id), None)
    payload: dict[str, Any] = {
        "language": "zh-TW",
        "purpose": purpose,
        "persona": persona.__dict__ if persona else None,
        "self_identity": _self_identity(state, player_id, memory),
        "self_reference_rules": [
            "凡 actor_id 等於 self_identity.player_id 的 public_speech、nomination、vote 或 private_chat，都是你自己做過的事。",
            "your_public_history 是你自己先前公開說過的話；延續或修正它，不要把它當成別人的發言。",
            "如果你之前已經宣稱角色或資訊，後續發言要承認那是你的說法，除非你打算明確改口或 bluff。",
        ],
        "your_public_history": _own_public_history(state, player_id),
        "table_cadence": {
            "ai_status": state.ai_last_status,
            "active_player_id": state.ai_active_player_id,
            "cooldown_seconds": state.ai_cooldown_seconds,
            "discussion_rounds_today": state.discussion_rounds_today,
            "max_discussion_rounds_before_nominations": 2,
            "mode": "線上即時桌遊；一次只做一小段像真人的行動。",
        },
        "action_contract": _action_contract(purpose, state, player_id),
        "rules_reference": _rules_reference_context(state, player_id),
        "conversation_directive": _conversation_directive(state, player_id, purpose),
        "real_player_speech_protocol": [
            "先回應上一位玩家或目前提名，不要像摘要機器一樣重複全桌狀態。",
            "每次公開發言只做一到兩件事：給自己的資訊/立場、問一個具體座位、或推一個明確行動。",
            "常用座位號，例如『3號』、『我左邊』、『5號剛才那票』；不要只用抽象詞。",
            "如果你是資訊角色且已收到私人資訊，白天要考慮主動透露全部或部分資訊；不要整天只說再觀察。",
            "如果某玩家今天還沒有公開發言，不要說他的資訊怪、前後矛盾或說法站不住；只能說『先讓他發言』。",
            "第一輪真人桌通常會收角色與夜間資訊；除非你有明確 bluff 策略，否則不要多數時間藏身分。",
            "避免模板句：『可驗證的點』、『先看票型』、『把話收窄』、『需要被追問』。除非你接著講出具體座位與理由。",
            "如果真人直接問身份或資訊，請正面回答：可以全開、半開或說明為何暫不全開，但不能無視問題。",
            "講話自然短促，可以有猶豫、讓步、改口；不要每句都像正式結論。",
        ],
        "table_reading_protocol": [
            "發言前先讀 recent_public_events、visible_table_read.claim_conflicts、visible_table_read.vote_patterns。",
            "再讀 claim_engine.parsed_public_claims；若 claim_engine 已標示某資訊是完整格式，不要追問角色規則上不可能知道的細節。",
            "公開發言至少引用一個具體場上資訊：某人的角色宣稱、你的私人資訊、昨夜死亡、某次提名或某次投票。",
            "使用 candidate_scores.spoke_today 和 last_public_statement 判斷發言內容；spoke_today=false 時不得批評該玩家的資訊內容。",
            "如果沒有新資訊，就短句說明你目前最想聽誰補哪個缺口；不要泛泛重複『看票型』。",
            "你可以說謊或 bluff，但謊言必須像玩家推理，不可以像知道魔典。",
        ],
        "visible_table_read": _visible_table_read(state, player_id),
        "table_notebook": notebook.model_dump(mode="json"),
        "claim_engine": {
            "parsed_public_claims": [
                claim.model_dump(mode="json") for claim in notebook.parsed_claims[-10:]
            ],
            "claim_warnings": notebook.claim_warnings,
            "unparsed_policy": "若玩家宣稱無法解析，不要自行補完；請對方用座位號、角色名、數字或兩人組重講。",
        },
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
            "第一天請推動全桌依序給資訊：資訊角色給數字/兩人組/查驗，非資訊角色至少給角色範圍。",
        ],
        "anti_echo_rules": [
            "避免使用與最近兩則公開發言相同的開頭、句型或目標。",
            "若要提同一名玩家，必須說出新的依據，例如票型、角色宣稱、私聊承諾或矛盾。",
            "不要臆造還沒發言者的資訊或矛盾；先請他發言，等他說完再評價。",
            "第一天不要因為玩家是 human 就把他當預設嫌疑人。",
            "保守或社交 persona 遇到全桌跟風時，優先降速、整理資訊或要求私聊。",
        ],
        "public_state": public,
        "your_private_view": private,
        "your_memory": _memory_context(memory),
        "your_public_fact_notes": memory.public_facts[-14:] if memory else [],
        "your_vote_notes": memory.vote_notes[-14:] if memory else [],
    }
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) <= max_chars:
        return text

    # Preserve role/current-phase/private information; trim only old public logs.
    public["public_events"] = public["public_events"][-20:]
    private["private_chats"] = private["private_chats"][-12:]
    private["private_events"] = private["private_events"][-18:]
    payload["your_public_history"] = payload["your_public_history"][-8:]
    payload["table_notebook"]["public_facts"] = payload["table_notebook"]["public_facts"][-10:]
    payload["table_notebook"]["private_info"] = payload["table_notebook"]["private_info"][-6:]
    payload["table_notebook"]["vote_notes"] = payload["table_notebook"]["vote_notes"][-8:]
    payload["table_notebook"]["parsed_claims"] = payload["table_notebook"]["parsed_claims"][-6:]
    payload["table_notebook"]["claim_warnings"] = payload["table_notebook"]["claim_warnings"][-6:]
    payload["claim_engine"]["parsed_public_claims"] = payload["claim_engine"][
        "parsed_public_claims"
    ][-6:]
    payload["claim_engine"]["claim_warnings"] = payload["claim_engine"]["claim_warnings"][-6:]
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


def _memory_context(memory: Any | None) -> dict[str, Any] | None:
    if memory is None:
        return None
    return {
        "player_id": memory.player_id,
        "suspicion": memory.suspicion,
        "known_claims": memory.known_claims,
        "public_claim": memory.public_claim,
        "private_promises": memory.private_promises[-6:],
        "current_bluff": memory.current_bluff,
        "next_intent": memory.next_intent,
        "summary": memory.summary[-900:],
        "public_facts": memory.public_facts[-10:],
        "vote_notes": memory.vote_notes[-10:],
        "worlds": [world.model_dump(mode="json") for world in memory.worlds[:3]],
    }


def _rules_reference_context(state: TruthState, player_id: str) -> dict[str, Any]:
    player = state.by_id(player_id)
    return {
        "game": [
            "這是六人 No Greater Joy Teensyville：3 鎮民、1 外來者、1 爪牙、1 小惡魔；若男爵在場則是 2 鎮民、2 外來者、男爵、小惡魔。",
            "六人局惡魔與爪牙不互認，惡魔沒有 bluff。邪惡玩家只能從公開互動推理隊友。",
            "善良目標是處決或殺死惡魔且沒有合法接任；邪惡目標是讓存活玩家剩兩人、笨蛋選中邪惡，或拖到安全終局。",
            "說書人/規則引擎判定死亡、能力、投票與勝負；你只做玩家可做的策略與發言。",
        ],
        "phase_playbook": {
            "FIRST_NIGHT": "接收自己的角色與合法夜間資訊；不要公開說話。",
            "DAWN": "聽昨夜死亡公告；準備白天資訊順序。",
            "DAY_DISCUSSION": "依序或自由發言。第一輪通常收角色範圍與夜間資訊；未發言者只能被請出來發言，不可被說成資訊矛盾。",
            "PRIVATE_CHAT": "交換角色範圍、資訊、承諾與懷疑；只能使用你參與的私聊。",
            "NOMINATIONS": "只有有具體公開理由時才提名；不要因為流程輪到你就亂提。",
            "VOTING": "每個提名依座位順序投票；活人每次可投，死人整局只有一張 ghost vote。",
            "EXECUTION": "最高且達門檻者處決；平手無處決。",
            "NIGHT": "只有被喚醒或有 pending action 時選目標；不要在公頻發言。",
        },
        "voting": [
            "處決門檻是存活玩家數的一半向上取整。",
            "當天最高有效票者進入處決；最高票平手時不處決。",
            "死人可發言與私聊，但不能提名；ghost vote 用掉後不能再投。",
        ],
        "claim_semantics": _claim_semantics_reference(),
        "common_logic_pitfalls": _common_logic_pitfalls_reference(),
        "roles": _script_rules_reference(),
        "your_role_playbook": _role_playbook_for(player.visible_role),
    }


def _claim_semantics_reference() -> dict[str, list[str]]:
    return {
        "clockmaker": [
            "鐘錶匠只得到惡魔到最近爪牙的最短座位步數。",
            "數字 1 代表相鄰；數字 2 代表隔一個座位或圓桌最短距離為 2。",
            "鐘錶匠數字不能直接指出誰是惡魔或爪牙，只能限制可能世界。",
        ],
        "investigator": [
            "調查員得到兩名玩家和一個在場爪牙角色；意思是兩人其中一名可能是該爪牙。",
            "調查員不知道兩人中到底哪一位是爪牙，所以『2、6 有一個紅唇女郎』已經是完整資訊。",
            "不要追問調查員『2、6 到底誰是紅唇女郎』；正確追問是請 2 號與 6 號給角色範圍、比較票型與其他資訊。",
        ],
        "empath": [
            "共情者每晚得到兩名最近存活鄰居中的邪惡人數，不是自己任選兩名玩家。",
            "若 4 號共情者說看到 3 號與 5 號有 2 名邪惡，在 3 號與 5 號是最近存活鄰居時，這個資訊格式是合法的。",
            "質疑共情者時要分清楚：格式非法、資訊可能被酒鬼污染、或玩家可能在說謊，三者不是同一件事。",
        ],
        "chambermaid": [
            "侍女選兩名存活且不是自己的玩家，得到其中幾人當夜因自己的能力醒來。",
            "侍女數字不是邪惡數，也不是角色陣營查驗。",
        ],
        "artist": [
            "藝術家白天一次問是非題，答案由說書人/引擎回覆。",
            "藝術家尚未使用能力時，通常只能給角色範圍或說明準備問什麼問題。",
        ],
        "sage": [
            "賢者只有被惡魔夜殺時才得到兩人，其中一名是惡魔。",
            "非惡魔造成的死亡不會觸發賢者資訊。",
        ],
        "klutz": [
            "笨蛋死亡時必須選一名存活玩家；若選中邪惡，善良立刻輸。",
            "笨蛋不能靠『出局自證』安全證明自己，因為選錯會直接輸。",
        ],
    }


def _common_logic_pitfalls_reference() -> list[str]:
    return [
        "先判斷一段宣稱是『規則不可能』、『可能為假』還是『需要交叉驗證』；不要把可疑等同於違規。",
        "不要要求玩家提供自己角色規則上不可能知道的細節，例如要求調查員指出二選一中的唯一爪牙。",
        "若真人糾正规則且符合 rules_reference，先承認規則點，再回到桌面推理。",
        "資訊衝突時，同時考慮說謊、酒鬼、邪惡 bluff、死亡造成鄰居改變與提名票型，不要只用單一口號推人。",
        "提出提名或投票前，至少說出一個可驗證來源：角色宣稱、夜間資訊、座位關係、死亡、私聊承諾或票型。",
    ]


def _script_rules_reference() -> list[dict[str, str]]:
    return [
        {
            "slug": role.slug,
            "name": role.zh_name,
            "type": role.zh_type,
            "rule": role.ability,
        }
        for role in ROLE_SPECS.values()
    ]


def _role_playbook_for(role: str) -> list[str]:
    playbooks = {
        "clockmaker": [
            "第一天通常要公開或半公開你的數字；數字能幫桌面縮小惡魔與爪牙距離。",
            "如果你藏資訊，要明確說稍後公布的理由，否則會像空轉。",
        ],
        "investigator": [
            "第一天通常公開兩人組與爪牙角色，或先私聊其中一人測反應。",
            "你只能說引擎給你的兩人組；不要自己改查驗結果。",
        ],
        "empath": [
            "每晚數字依最近存活鄰居計算；死亡玩家會被跳過。",
            "公開時要說清楚你的鄰居與數字，方便桌面重建死亡後變化。",
        ],
        "chambermaid": [
            "夜晚選兩名存活非自己玩家；結果是其中幾人因自身能力醒來。",
            "公開時說你查了誰與數字，避免只說『我有資訊』。",
        ],
        "artist": [
            "白天每局一次私下問說書人是非題；問題必須能被結構化解析。",
            "能力沒用前可以先收資訊；使用後可公開答案與問題，也可保留以測謊。",
        ],
        "sage": [
            "只有被惡魔夜殺時才得知兩人其中一名是惡魔。",
            "活著時通常可給角色範圍；死後若觸發資訊要優先公開。",
        ],
        "drunk": [
            "你不知道自己是酒鬼；照你看到的假角色正常行動。",
            "不要自己推斷自己是酒鬼，除非公開資訊強烈指向能力失效。",
        ],
        "klutz": [
            "死亡時必須公開選一名存活玩家；選中邪惡會讓善良立刻輸。",
            "死前要盡量建立可信名單，避免死後亂選。",
        ],
        "scarlet_woman": [
            "惡魔死亡且死亡前至少 5 人存活、你仍存活時，你會成為小惡魔。",
            "六人局你不認惡魔；用公開資訊找可能隊友，並準備可信好人 bluff。",
        ],
        "baron": [
            "你的能力只影響設置；六人局會有 2 鎮民、2 外來者、男爵、小惡魔。",
            "你不認惡魔；可 bluff 成資訊角色或外來者，但不要聲稱知道隊友。",
        ],
        "imp": [
            "第一夜不殺人；之後每夜選一名存活玩家死亡，可以選自己。",
            "自殺時若有存活爪牙會 starpass；六人局你不認爪牙，所以自殺是高風險策略。",
        ],
    }
    return playbooks.get(role, ["依照你的可見角色與公開資訊行動，不要自行判定規則結果。"])


def _self_identity(state: TruthState, player_id: str, memory: Any | None) -> dict[str, Any]:
    player = state.by_id(player_id)
    role = ROLE_SPECS[player.visible_role]
    return {
        "player_id": player.id,
        "name": player.name,
        "seat": player.seat,
        "seat_number": player.seat + 1,
        "seat_label": _seat_label(player),
        "alive": player.alive,
        "visible_role_slug": player.visible_role,
        "visible_role_zh": role.zh_name,
        "visible_alignment": player.visible_alignment,
        "public_claim_you_have_made": memory.public_claim if memory else None,
        "current_bluff_you_are_tracking": memory.current_bluff if memory else None,
        "next_intent": memory.next_intent if memory else "",
    }


def _own_public_history(state: TruthState, player_id: str, limit: int = 10) -> list[dict[str, Any]]:
    player = state.by_id(player_id)
    history: list[dict[str, Any]] = []
    for event in state.events:
        if event.scope != AudienceScope.PUBLIC or event.actor_id != player_id:
            continue
        if event.type not in {"public_speech", "nomination", "defense", "vote"}:
            continue
        history.append(
            {
                "day": event.day,
                "phase": event.phase,
                "type": event.type,
                "actor_id": event.actor_id,
                "actor_label": _seat_label(player),
                "this_was_you": True,
                "message": event.message,
                "spoken_content": _spoken_content(state, player_id, event.message),
            }
        )
    return history[-limit:]


def _players_yet_to_speak_today(state: TruthState) -> list[dict[str, Any]]:
    spoken = {
        event.actor_id
        for event in state.events
        if event.scope == AudienceScope.PUBLIC
        and event.type == "public_speech"
        and event.day == state.day
        and event.actor_id is not None
    }
    return [
        {
            "id": player.id,
            "name": player.name,
            "seat_number": player.seat + 1,
            "seat_label": _seat_label(player),
            "alive": player.alive,
        }
        for player in sorted(state.players, key=lambda item: item.seat)
        if player.id not in spoken
    ]


def _disclosure_expectation(state: TruthState, player: PlayerTruth, private_info: list[str]) -> str:
    spoke_today = any(
        event.scope == AudienceScope.PUBLIC
        and event.type == "public_speech"
        and event.actor_id == player.id
        and event.day == state.day
        for event in state.events
    )
    if spoke_today:
        return "你今天已發言；延續自己的宣稱與資訊，不要假裝第一次開口。"
    if private_info:
        return "你今天第一次發言時應公開或半公開私人資訊，例如數字、兩人組或查驗結果。"
    if player.visible_alignment == Alignment.EVIL:
        return "你今天第一次發言時應給可信角色範圍或 bluff；不要只說觀察。"
    if player.visible_role in {"artist", "sage", "klutz"}:
        return "你今天第一次發言時至少給角色範圍，並說明你打算如何使用或保留能力。"
    return "你今天第一次發言時至少給角色範圍或一個明確座位觀察。"


def _conversation_directive(state: TruthState, player_id: str, purpose: str) -> dict[str, Any]:
    player = state.by_id(player_id)
    role = ROLE_SPECS[player.visible_role]
    private_info = [
        event.message
        for event in state.events
        if event.scope == AudienceScope.PLAYER_ONLY and player_id in event.target_ids
    ][-5:]
    latest_human = _latest_human_public_speech(state)
    latest_human_analysis = _latest_human_public_speech_analysis(state, player_id)
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
        "players_yet_to_speak_today": _players_yet_to_speak_today(state),
        "latest_human_public_speech": latest_human,
        "latest_human_speech_analysis": latest_human_analysis,
        "human_is_asking_identity_or_info": asks_identity,
        "role_pressure": role_pressure,
        "disclosure_expectation": _disclosure_expectation(state, player, private_info),
        "public_speech_style": [
            "討論玩家時優先使用座位號與名字，例如「3號 林鏡」，避免只用名字造成桌面追蹤困難。",
            "公開發言請像真人桌邊說話：1 到 2 句，通常不超過 120 個中文字。",
            "回應真人前先看 latest_human_speech_analysis：若真人主要指向別人，不要把那句當成在指控你自己。",
            "若 latest_human_speech_analysis.directly_targets_you=false 且 primary_targets 有其他玩家，只能評論該指控、請被點名者回答，或補充你的資訊。",
            "質疑角色宣稱前先查 rules_reference.claim_semantics；不要要求對方提供該角色規則上不會知道的答案。",
            "調查員的『兩人中有一個某爪牙』是完整資訊；不要問他到底哪一位是爪牙，改問兩名候選人的角色範圍與交叉驗證。",
            "如果真人直接問身分或資訊，請直接回答你的角色宣稱、二選一範圍或你拿到的資訊，不要把問題丟回全桌。",
            "輪到你第一輪發言時，預設至少給角色範圍；資訊角色通常給完整或半完整資訊。",
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
    claims = dict(memory.known_claims) if memory else {}
    if memory and memory.public_claim:
        claims[player_id] = memory.public_claim
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
                "spoke_today": _spoke_today(state, player.id),
                "public_speech_count": _public_speech_count_for_context(state, player.id),
                "last_public_statement": _last_public_statement(state, player.id),
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


def _spoke_today(state: TruthState, player_id: str) -> bool:
    return any(
        event.scope == AudienceScope.PUBLIC
        and event.type == "public_speech"
        and event.actor_id == player_id
        and event.day == state.day
        for event in state.events
    )


def _public_speech_count_for_context(state: TruthState, player_id: str) -> int:
    return sum(
        event.scope == AudienceScope.PUBLIC
        and event.type == "public_speech"
        and event.actor_id == player_id
        for event in state.events
    )


def _last_public_statement(state: TruthState, player_id: str) -> str | None:
    for event in reversed(state.events):
        if (
            event.scope == AudienceScope.PUBLIC
            and event.type == "public_speech"
            and event.actor_id == player_id
        ):
            return _spoken_content(state, player_id, event.message)
    return None


def _latest_human_public_speech(state: TruthState) -> str | None:
    event = _latest_human_public_speech_event(state)
    if event is None:
        return None
    return _spoken_content(state, event.actor_id, event.message)


def _latest_human_public_speech_event(state: TruthState) -> GameEvent | None:
    for event in reversed(state.events):
        if (
            event.scope == AudienceScope.PUBLIC
            and event.type == "public_speech"
            and event.actor_id == state.human_id
        ):
            return event
    return None


def _latest_human_public_speech_analysis(state: TruthState, viewer_id: str) -> dict[str, Any]:
    event = _latest_human_public_speech_event(state)
    if event is None:
        return {
            "exists": False,
            "spoken_content": None,
            "mentioned_players": [],
            "primary_targets": [],
            "directly_mentions_you": False,
            "directly_targets_you": False,
            "accuses_you": False,
            "response_instruction": "目前沒有真人公開發言需要回應。",
        }

    text = _spoken_content(state, event.actor_id, event.message)
    mentioned = _mentioned_players_in_text(state, text, viewer_id=viewer_id)
    primary_targets = [
        item for item in mentioned if event.actor_id is None or item["id"] != event.actor_id
    ]
    primary_target_ids = {item["id"] for item in primary_targets}
    directly_mentions_you = any(item["id"] == viewer_id for item in mentioned)
    directly_targets_you = viewer_id in primary_target_ids
    accusation_language = _contains_accusation_language(text)
    accuses_you = directly_targets_you and accusation_language
    return {
        "exists": True,
        "speaker_id": event.actor_id,
        "speaker_label": _seat_label(state.by_id(event.actor_id)) if event.actor_id else None,
        "spoken_content": text,
        "mentioned_players": mentioned,
        "primary_targets": primary_targets,
        "primary_target_ids": sorted(primary_target_ids),
        "directly_mentions_you": directly_mentions_you,
        "directly_targets_you": directly_targets_you,
        "accusation_language_detected": accusation_language,
        "accuses_you": accuses_you,
        "response_instruction": _human_speech_response_instruction(
            primary_targets=primary_targets,
            directly_targets_you=directly_targets_you,
            accuses_you=accuses_you,
            asks_identity_or_info=_asks_identity_or_info(text),
        ),
    }


def _mentioned_players_in_text(
    state: TruthState, text: str, *, viewer_id: str
) -> list[dict[str, Any]]:
    mentioned: list[dict[str, Any]] = []
    for player in sorted(state.players, key=lambda item: item.seat):
        seat_number = player.seat + 1
        seat_pattern = rf"(?<!\d){seat_number}\s*號"
        if re.search(seat_pattern, text) or player.name in text:
            mentioned.append(
                {
                    "id": player.id,
                    "name": player.name,
                    "seat_number": seat_number,
                    "seat_label": _seat_label(player),
                    "is_you": player.id == viewer_id,
                }
            )
    return mentioned


def _contains_accusation_language(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return any(
        token in compact
        for token in (
            "邪惡",
            "壞人",
            "惡魔",
            "小惡魔",
            "爪牙",
            "可疑",
            "狼",
            "弄出去",
            "推出去",
            "出局",
            "提名",
            "處決",
        )
    )


def _human_speech_response_instruction(
    *,
    primary_targets: list[dict[str, Any]],
    directly_targets_you: bool,
    accuses_you: bool,
    asks_identity_or_info: bool,
) -> str:
    target_labels = "、".join(str(item["seat_label"]) for item in primary_targets)
    if accuses_you:
        return (
            "真人正在指控或壓力你；請用你的公開宣稱、可見資訊或票型正面回應，不要轉移成無關問題。"
        )
    if directly_targets_you:
        return "真人正在直接問你或點你；請正面回答，必要時給角色範圍、夜間資訊或明確立場。"
    if primary_targets:
        return (
            f"真人這句主要指向 {target_labels}，不是在指控你；不要替自己辯護，"
            "可以評論這個指控是否合理、請被點名者回答，或補充你手上的可見資訊。"
        )
    if asks_identity_or_info:
        return "真人在問全桌身份或資訊；請回答自己的角色範圍、夜間資訊或說明為何暫時保留。"
    return "真人沒有直接點名你；回應時先接住他的問題，再補一個具體座位或資訊點。"


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
