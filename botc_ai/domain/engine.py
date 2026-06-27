from __future__ import annotations

import random
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from botc_ai.ai.provider import AIProvider, BudgetExceeded, MockAIProvider
from botc_ai.domain.artist import evaluate_artist_query, parse_artist_question
from botc_ai.domain.models import (
    AIMemory,
    AudienceScope,
    GameResult,
    NominationRecord,
    Phase,
    PlayerTruth,
    TransformationEvent,
    TruthState,
    VoteRecord,
    WakeEvent,
)
from botc_ai.domain.roles import ROLE_SPECS, Alignment
from botc_ai.domain.rules import (
    circular_distance,
    empath_count,
    nearest_living_neighbors,
    seat_order,
    vote_threshold,
)
from botc_ai.domain.storyteller import AIStorytellerPolicy

FIRST_NIGHT_INFO_ROLES = {"clockmaker", "investigator", "empath", "chambermaid"}
RECURRING_INFO_ROLES = {"empath", "chambermaid"}
PUBLIC_SPEECH_PHASES = {Phase.DAY_DISCUSSION, Phase.NOMINATIONS}
PRIVATE_CHAT_PHASES = {Phase.DAY_DISCUSSION, Phase.PRIVATE_CHAT, Phase.NOMINATIONS}


@dataclass
class RuleResult:
    ok: bool
    message: str = ""


class GameEngine:
    def __init__(
        self,
        provider: AIProvider | None = None,
        storyteller: AIStorytellerPolicy | None = None,
    ) -> None:
        self.provider = provider or MockAIProvider()
        self.storyteller = storyteller or AIStorytellerPolicy()

    async def start_game(self, state: TruthState) -> TruthState:
        if state.phase != Phase.SETUP:
            return state
        state.phase = Phase.FIRST_NIGHT
        state.last_night_deaths = []
        await self._resolve_night_information(state, first_night=True)
        state.phase = Phase.DAWN
        state.add_event("第一夜結束，黎明到來。", scope=AudienceScope.PUBLIC, type="dawn")
        return state

    async def advance_phase(self, state: TruthState) -> TruthState:
        if state.result is not None:
            state.phase = Phase.GAME_OVER
            return state
        if state.phase == Phase.SETUP:
            return await self.start_game(state)
        if state.phase == Phase.DAWN:
            self._announce_dawn(state)
            state.phase = Phase.DAY_DISCUSSION
            await self.run_public_discussion(state, rounds=1, speaker_limit=1)
            return state
        if state.phase == Phase.DAY_DISCUSSION:
            state.phase = Phase.PRIVATE_CHAT
            await self.run_ai_private_chats(state, limit=2)
            return state
        if state.phase == Phase.PRIVATE_CHAT:
            state.phase = Phase.NOMINATIONS
            return state
        if state.phase == Phase.NOMINATIONS:
            created = await self.run_ai_nomination_once(state, wait_for_human_vote=True)
            if not created:
                await self.execute_top_candidate(state)
            return state
        if state.phase == Phase.EXECUTION:
            await self.end_day_or_game(state)
            return state
        if state.phase == Phase.NIGHT:
            await self.run_night(state)
            return state
        if state.phase == Phase.VOTING:
            return state
        return state

    async def ai_tick(self, state: TruthState) -> RuleResult:
        if state.result is not None:
            state.phase = Phase.GAME_OVER
            self._set_ai_status(state, "遊戲已結束。")
            return RuleResult(True, "遊戲已結束。")
        now = datetime.now(UTC)
        if state.last_ai_tick_at is not None:
            elapsed = (now - state.last_ai_tick_at).total_seconds()
            if elapsed < state.ai_cooldown_seconds:
                remaining = max(1, int(state.ai_cooldown_seconds - elapsed))
                self._set_ai_status(state, f"AI 冷卻中，約 {remaining} 秒後會再行動。")
                return RuleResult(True, "AI 冷卻中。")
        state.last_ai_tick_at = now

        if state.phase == Phase.SETUP:
            self._set_ai_status(state, "AI 說書人正在完成開局。")
            await self.start_game(state)
            return RuleResult(True, "AI 說書人完成開局。")
        if state.phase == Phase.DAWN:
            self._set_ai_status(state, "AI 正在聽黎明公布並整理第一輪說法。")
            self._announce_dawn(state)
            state.phase = Phase.DAY_DISCUSSION
            await self.run_public_discussion(state, rounds=1, speaker_limit=1)
            return RuleResult(True, "黎明後開始公開討論。")
        if state.phase == Phase.DAY_DISCUSSION:
            if state.discussion_rounds_today < 2:
                await self.run_public_discussion(state, rounds=1, speaker_limit=1)
                return RuleResult(True, "AI 完成一段公開討論。")
            state.phase = Phase.PRIVATE_CHAT
            state.add_event(
                "公開討論告一段落，進入私聊時間。", scope=AudienceScope.PUBLIC, type="phase"
            )
            await self.run_ai_private_chats(state, limit=2)
            return RuleResult(True, "AI 完成一段私聊行動。")
        if state.phase == Phase.PRIVATE_CHAT:
            await self.run_ai_private_chats(state, limit=2)
            ai_count = sum(not player.is_human for player in state.players)
            if len(set(state.ai_private_chat_initiated_today)) >= ai_count:
                state.phase = Phase.NOMINATIONS
                state.add_event(
                    "私聊時間結束，進入提名階段。",
                    scope=AudienceScope.PUBLIC,
                    type="phase",
                )
                self._set_ai_status(state, "AI 已完成私聊，桌面進入提名階段。")
                return RuleResult(True, "進入提名階段。")
            return RuleResult(True, "AI 完成一段私聊行動。")
        if state.phase == Phase.NOMINATIONS:
            self._set_ai_status(state, "AI 正在判斷是否提名。")
            created = await self.run_ai_nomination_once(state, wait_for_human_vote=True)
            if created:
                self._set_ai_status(state, "AI 已提出提名，等待真人投票。")
                return RuleResult(True, "AI 發起提名，等待真人投票。")
            await self.execute_top_candidate(state)
            return RuleResult(True, "提名階段結算。")
        if state.phase == Phase.EXECUTION:
            self._set_ai_status(state, "說書人正在結算處決與勝負。")
            await self.end_day_or_game(state)
            return RuleResult(True, "處決後進入下一階段。")
        if state.phase == Phase.NIGHT:
            self._set_ai_status(state, "夜晚行動處理中。")
            await self.run_night(state)
            return RuleResult(True, "夜晚已處理。")
        if state.phase == Phase.VOTING:
            self._set_ai_status(state, "等待真人玩家投票。")
            return RuleResult(True, "等待真人投票。")
        return RuleResult(True, "沒有可執行的 AI 行動。")

    async def run_until_human_decision(
        self, state: TruthState, *, max_steps: int = 18
    ) -> RuleResult:
        steps = 0
        while steps < max_steps and not self._needs_human_decision(state):
            before = self._progress_key(state)
            state.last_ai_tick_at = None
            await self.ai_tick(state)
            steps += 1
            if self._progress_key(state) == before:
                break
        if self._needs_human_decision(state):
            return RuleResult(True, f"AI 已推進 {steps} 步，現在需要真人決策。")
        return RuleResult(True, f"AI 已推進 {steps} 步。")

    async def auto_play(self, state: TruthState, *, max_steps: int = 200) -> TruthState:
        if state.phase == Phase.SETUP:
            await self.start_game(state)
        steps = 0
        while state.result is None and steps < max_steps:
            steps += 1
            if state.phase == Phase.DAWN:
                self._announce_dawn(state)
                state.phase = Phase.DAY_DISCUSSION
                await self.run_public_discussion(state)
            elif state.phase == Phase.DAY_DISCUSSION:
                state.phase = Phase.PRIVATE_CHAT
                await self.run_ai_private_chats(state)
            elif state.phase == Phase.PRIVATE_CHAT:
                state.phase = Phase.NOMINATIONS
            elif state.phase == Phase.NOMINATIONS:
                created = await self.run_ai_nomination_once(state, wait_for_human_vote=False)
                if not created:
                    await self.execute_top_candidate(state)
            elif state.phase == Phase.VOTING:
                pending = next(
                    (n for n in state.nominations if n.day == state.day and not n.resolved), None
                )
                if pending:
                    await self.resolve_vote(state, pending.id, human_vote=False)
                else:
                    state.phase = Phase.NOMINATIONS
            elif state.phase == Phase.EXECUTION:
                await self.end_day_or_game(state)
            elif state.phase == Phase.NIGHT:
                await self.run_night(state)
            elif state.phase == Phase.SETUP:
                await self.start_game(state)
            if state.day > state.max_days and state.result is None:
                await self._resolve_max_day_safety(state)
        if state.result is None:
            self._set_winner(state, Alignment.EVIL, "自動對局達到安全步數上限，邪惡陣營獲勝。")
        state.phase = Phase.GAME_OVER
        return state

    async def run_public_discussion(
        self, state: TruthState, rounds: int = 2, speaker_limit: int | None = None
    ) -> None:
        for _ in range(rounds):
            speakers = self._discussion_speakers(state, speaker_limit=speaker_limit)
            for player in speakers:
                spoken = await self._emit_ai_public_speech(state, player)
                if not spoken:
                    return
                if speaker_limit is not None and player.id not in state.discussion_speakers_today:
                    state.discussion_speakers_today.append(player.id)
            if speaker_limit is None or self._all_ai_spoke_this_round(state):
                state.discussion_rounds_today += 1
                state.discussion_speakers_today = []
        self._set_ai_status(state, "公開討論暫停，等待下一位玩家行動。")

    async def run_reactive_discussion(
        self, state: TruthState, *, trigger_player_id: str, speech: str, limit: int = 2
    ) -> None:
        responders = self._reactive_speakers(
            state, trigger_player_id=trigger_player_id, speech=speech, limit=limit
        )
        for player in responders:
            spoken = await self._emit_ai_public_speech(state, player)
            if not spoken:
                return
            if player.id not in state.discussion_speakers_today:
                state.discussion_speakers_today.append(player.id)
        self._set_ai_status(state, "AI 已回應你的發言，等待下一段桌面節奏。")

    async def run_ai_private_chats(self, state: TruthState, limit: int | None = None) -> None:
        ai_players = [player for player in seat_order(state) if not player.is_human]
        remaining = [
            player
            for player in ai_players
            if player.id not in state.ai_private_chat_initiated_today
        ]
        if limit is not None:
            remaining = remaining[:limit]
        if not remaining:
            self._set_ai_status(state, "今日 AI 私聊額度已用完或都選擇不主動私聊。")
            return
        for player in remaining:
            valid = [other.id for other in state.players if other.id != player.id]
            state.ai_private_chat_initiated_today.append(player.id)
            self._set_ai_status(state, f"{player.name} 正在考慮是否私聊。", player.id)
            try:
                action = await self.provider.private_message(state, player.id, valid)
            except BudgetExceeded:
                state.add_event("AI 預算已達上限，暫停新的自主私聊。", type="budget_paused")
                self._set_ai_status(state, "AI 預算已達上限，私聊暫停。")
                return
            if action is None or action.target_id not in valid:
                continue
            target = state.by_id(action.target_id)
            state.add_event(
                f"{_seat_label(player)} 私下對 {_seat_label(target)} 說：{action.message}",
                scope=AudienceScope.PRIVATE_CHAT_PARTICIPANTS,
                type="private_chat",
                actor_id=player.id,
                target_ids=[target.id],
                participants=[player.id, target.id],
            )
            self._remember_private(
                state, player.id, f"對 {_seat_label(target)} 說：{action.message}"
            )
            if not target.is_human:
                self._remember_private(
                    state, target.id, f"{_seat_label(player)} 對我說：{action.message}"
                )
        self._set_ai_status(state, "AI 私聊分段完成，等待下一段桌面節奏。")

    def _needs_human_decision(self, state: TruthState) -> bool:
        return (
            state.result is not None
            or state.phase == Phase.VOTING
            or (state.pending_klutz_id is not None and state.by_id(state.pending_klutz_id).is_human)
            or state.ai_budget_paused
        )

    def _progress_key(self, state: TruthState) -> tuple[object, ...]:
        return (
            state.phase,
            state.day,
            len(state.events),
            len(state.nominations),
            len(state.votes),
            state.pending_klutz_id,
            state.result is not None,
        )

    def _discussion_speakers(
        self, state: TruthState, *, speaker_limit: int | None
    ) -> list[PlayerTruth]:
        ai_players = [player for player in seat_order(state) if not player.is_human]
        if speaker_limit is None:
            return ai_players
        remaining = [
            player for player in ai_players if player.id not in state.discussion_speakers_today
        ]
        if not remaining:
            state.discussion_rounds_today += 1
            state.discussion_speakers_today = []
            remaining = ai_players
        return remaining[:speaker_limit]

    def _reactive_speakers(
        self, state: TruthState, *, trigger_player_id: str, speech: str, limit: int
    ) -> list[PlayerTruth]:
        del trigger_player_id
        lowered = speech.lower()
        ai_players = [player for player in seat_order(state) if not player.is_human]

        def was_mentioned(player: PlayerTruth) -> bool:
            seat_number = str(player.seat + 1)
            return (
                player.name.lower() in lowered
                or player.id.lower() in lowered
                or f"{seat_number}號" in lowered
                or f"{seat_number}号" in lowered
            )

        return sorted(
            ai_players,
            key=lambda player: (0 if was_mentioned(player) else 1, player.seat),
        )[:limit]

    def _all_ai_spoke_this_round(self, state: TruthState) -> bool:
        ai_ids = {player.id for player in state.players if not player.is_human}
        return ai_ids.issubset(set(state.discussion_speakers_today))

    async def _emit_ai_public_speech(self, state: TruthState, player: PlayerTruth) -> bool:
        self._set_ai_status(state, f"{player.name} 正在整理公開發言。", player.id)
        try:
            action = await self.provider.public_speech(state, player.id)
        except BudgetExceeded:
            state.add_event(
                "AI 預算已達上限，暫停新的自主公開發言。",
                scope=AudienceScope.PUBLIC,
                type="budget_paused",
            )
            self._set_ai_status(state, "AI 預算已達上限，公開發言暫停。")
            return False
        raw_speech = action.speech.strip()
        if raw_speech:
            speech = _table_public_speech(raw_speech)
            state.add_event(
                f"{_seat_label(player)}：{speech}",
                scope=AudienceScope.PUBLIC,
                type="public_speech",
                actor_id=player.id,
            )
            claimed_role = action.claimed_role or self._extract_claimed_role(raw_speech)
            self._apply_claim(state, player.id, claimed_role)
        return True

    def _speaker_priority(self, player_id: str) -> int:
        return {
            "ai_3": 5,  # aggressive pressure
            "ai_2": 4,  # social coordinator
            "ai_1": 3,  # logic analyst
            "ai_5": 2,  # chaotic intuition
            "ai_4": 1,  # conservative skeptic
        }.get(player_id, 0)

    def _set_ai_status(
        self, state: TruthState, message: str, active_player_id: str | None = None
    ) -> None:
        state.ai_last_status = message
        state.ai_active_player_id = active_player_id

    def add_human_public_speech(self, state: TruthState, player_id: str, speech: str) -> RuleResult:
        player = state.by_id(player_id)
        if not speech.strip():
            return RuleResult(False, "發言不可為空。")
        if state.phase not in PUBLIC_SPEECH_PHASES:
            return RuleResult(False, "現在不是公開討論時間，夜晚與處決流程不能在公頻發言。")
        state.add_event(
            f"{_seat_label(player)}：{speech.strip()}",
            scope=AudienceScope.PUBLIC,
            type="public_speech",
            actor_id=player.id,
        )
        if state.phase == Phase.DAY_DISCUSSION:
            state.discussion_rounds_today = min(state.discussion_rounds_today, 1)
        self._apply_claim(state, player.id, self._extract_claimed_role(speech))
        self._set_ai_status(state, "AI 正在消化你的公開發言。")
        return RuleResult(True, "已公開發言。")

    async def add_private_chat(
        self, state: TruthState, from_id: str, to_id: str, message: str
    ) -> RuleResult:
        sender = state.by_id(from_id)
        target = state.by_id(to_id)
        if state.phase not in PRIVATE_CHAT_PHASES:
            return RuleResult(False, "現在不是白天討論或私聊時間，不能私聊。")
        if sender.id == target.id:
            return RuleResult(False, "不能和自己私聊。")
        if not message.strip():
            return RuleResult(False, "私聊訊息不可為空。")
        state.add_event(
            f"{_seat_label(sender)} 私下對 {_seat_label(target)} 說：{message.strip()}",
            scope=AudienceScope.PRIVATE_CHAT_PARTICIPANTS,
            type="private_chat",
            actor_id=sender.id,
            target_ids=[target.id],
            participants=[sender.id, target.id],
        )
        if target.is_human:
            return RuleResult(True, "已送出私聊。")
        reply = await self.provider.private_message(state, target.id, [sender.id])
        reply_message = (
            reply.message
            if reply is not None
            else "我收到你的訊息了。我會先把這點記下來，等公開討論時再看怎麼配合。"
        )
        state.add_event(
            f"{_seat_label(target)} 私下對 {_seat_label(sender)} 說：{reply_message}",
            scope=AudienceScope.PRIVATE_CHAT_PARTICIPANTS,
            type="private_chat",
            actor_id=target.id,
            target_ids=[sender.id],
            participants=[sender.id, target.id],
        )
        return RuleResult(True, "已送出私聊。")

    async def create_nomination(
        self, state: TruthState, nominator_id: str, nominee_id: str, reason: str
    ) -> NominationRecord:
        nominator = state.by_id(nominator_id)
        nominee = state.by_id(nominee_id)
        self._validate_nomination(state, nominator, nominee)
        nomination = NominationRecord(
            day=state.day,
            nominator_id=nominator.id,
            nominee_id=nominee.id,
            reason=reason[:180] or "沒有提供理由。",
        )
        nominator.nominated_today = True
        nominee.was_nominated_today = True
        state.nominations.append(nomination)
        state.add_event(
            f"{_seat_label(nominator)} 提名 {_seat_label(nominee)}：{nomination.reason}",
            scope=AudienceScope.PUBLIC,
            type="nomination",
            actor_id=nominator.id,
            target_ids=[nominee.id],
        )
        self._remember_nomination_pressure(state, nominator.id, nominee.id)
        if nominee.is_human:
            nomination.defense = "真人玩家尚未輸入辯護。"
        else:
            defense = await self.provider.defense(state, nominee.id, nomination.reason)
            nomination.defense = defense.statement
            state.add_event(
                f"{_seat_label(nominee)} 辯護：{defense.statement}",
                scope=AudienceScope.PUBLIC,
                type="defense",
                actor_id=nominee.id,
            )
        state.phase = Phase.VOTING
        return nomination

    async def run_ai_nomination_once(self, state: TruthState, *, wait_for_human_vote: bool) -> bool:
        if state.execution_done_today:
            return False
        for player in seat_order(state):
            if player.is_human or not player.alive or player.nominated_today:
                continue
            valid = [
                other.id
                for other in state.living()
                if other.id != player.id and not other.was_nominated_today
            ]
            if not valid:
                continue
            action = await self.provider.nominate(state, player.id, valid)
            if not action.nominate or action.target_id not in valid:
                continue
            nomination = await self.create_nomination(
                state, player.id, action.target_id, action.reason
            )
            if wait_for_human_vote:
                return True
            await self.resolve_vote(state, nomination.id, human_vote=False)
            return True
        return False

    async def resolve_vote(
        self, state: TruthState, nomination_id: str, *, human_vote: bool
    ) -> NominationRecord:
        nomination = next(item for item in state.nominations if item.id == nomination_id)
        nominee = state.by_id(nomination.nominee_id)
        if nomination.resolved:
            return nomination
        threshold = vote_threshold(state.living_count())
        votes = 0
        for voter in seat_order(state):
            eligible = voter.alive or voter.ghost_vote_available
            if not eligible:
                continue
            if voter.is_human:
                decision = human_vote
                reason = "真人玩家投票" if decision else "真人玩家不投票"
            else:
                action = await self.provider.vote(
                    state,
                    voter.id,
                    nominee.id,
                    nomination.reason,
                    nomination.defense or "",
                )
                decision = action.vote
                reason = action.public_reason
            used_ghost = False
            if decision:
                votes += 1
                if not voter.alive:
                    voter.ghost_vote_available = False
                    used_ghost = True
            state.votes.append(
                VoteRecord(
                    nomination_id=nomination.id,
                    day=state.day,
                    voter_id=voter.id,
                    nominee_id=nominee.id,
                    vote=decision,
                    used_ghost_vote=used_ghost,
                    public_reason=reason,
                )
            )
            state.add_event(
                f"{_seat_label(voter)} {'投票' if decision else '不投票'}：{reason}",
                scope=AudienceScope.PUBLIC,
                type="vote",
                actor_id=voter.id,
                target_ids=[nominee.id],
            )
        nomination.votes = votes
        nomination.threshold = threshold
        nomination.eligible_for_execution = votes >= threshold
        nomination.resolved = True
        state.add_event(
            f"{_seat_label(nominee)} 得到 {votes} 票，處決門檻為 {threshold}。",
            scope=AudienceScope.PUBLIC,
            type="vote_result",
            target_ids=[nominee.id],
        )
        self._remember_vote_result(state, nomination)
        state.phase = Phase.NOMINATIONS
        return nomination

    async def cast_human_vote(
        self, state: TruthState, player_id: str, *, vote: bool
    ) -> NominationRecord:
        nomination = next(
            (item for item in state.nominations if item.day == state.day and not item.resolved),
            None,
        )
        if nomination is None or state.phase != Phase.VOTING:
            raise ValueError("目前沒有正在投票的提名。")
        voter = state.by_id(player_id)
        if not voter.is_human:
            raise ValueError("這個座位不是真人玩家。")
        if not (voter.alive or voter.ghost_vote_available):
            raise ValueError("你目前沒有可用的投票權。")
        if self._has_voted(state, nomination.id, voter.id):
            raise ValueError("你已經對這次提名投過票。")
        reason = "真人玩家投票" if vote else "真人玩家不投票"
        self._record_vote(state, nomination, voter, vote, reason)
        if self._all_required_human_votes_cast(state, nomination.id):
            await self._resolve_remaining_ai_votes(state, nomination)
        else:
            remaining = [
                _seat_label(player)
                for player in seat_order(state)
                if player.is_human
                and (player.alive or player.ghost_vote_available)
                and not self._has_voted(state, nomination.id, player.id)
            ]
            self._set_ai_status(state, f"等待真人投票：{'、'.join(remaining)}。")
        return nomination

    async def _resolve_remaining_ai_votes(
        self, state: TruthState, nomination: NominationRecord
    ) -> None:
        nominee = state.by_id(nomination.nominee_id)
        for voter in seat_order(state):
            if voter.is_human or not (voter.alive or voter.ghost_vote_available):
                continue
            if self._has_voted(state, nomination.id, voter.id):
                continue
            action = await self.provider.vote(
                state,
                voter.id,
                nominee.id,
                nomination.reason,
                nomination.defense or "",
            )
            self._record_vote(state, nomination, voter, action.vote, action.public_reason)
        self._finalize_nomination_vote(state, nomination)

    def _record_vote(
        self,
        state: TruthState,
        nomination: NominationRecord,
        voter: PlayerTruth,
        decision: bool,
        reason: str,
    ) -> None:
        nominee = state.by_id(nomination.nominee_id)
        used_ghost = False
        if decision and not voter.alive:
            voter.ghost_vote_available = False
            used_ghost = True
        state.votes.append(
            VoteRecord(
                nomination_id=nomination.id,
                day=state.day,
                voter_id=voter.id,
                nominee_id=nominee.id,
                vote=decision,
                used_ghost_vote=used_ghost,
                public_reason=reason,
            )
        )
        state.add_event(
            f"{_seat_label(voter)} {'投票' if decision else '不投票'}：{reason}",
            scope=AudienceScope.PUBLIC,
            type="vote",
            actor_id=voter.id,
            target_ids=[nominee.id],
        )

    def _finalize_nomination_vote(self, state: TruthState, nomination: NominationRecord) -> None:
        nominee = state.by_id(nomination.nominee_id)
        votes = sum(
            vote.vote
            for vote in state.votes
            if vote.day == state.day and vote.nomination_id == nomination.id
        )
        threshold = vote_threshold(state.living_count())
        nomination.votes = votes
        nomination.threshold = threshold
        nomination.eligible_for_execution = votes >= threshold
        nomination.resolved = True
        state.add_event(
            f"{_seat_label(nominee)} 得到 {votes} 票，處決門檻為 {threshold}。",
            scope=AudienceScope.PUBLIC,
            type="vote_result",
            target_ids=[nominee.id],
        )
        self._remember_vote_result(state, nomination)
        state.phase = Phase.NOMINATIONS

    def _all_required_human_votes_cast(self, state: TruthState, nomination_id: str) -> bool:
        return all(
            self._has_voted(state, nomination_id, player.id)
            for player in state.players
            if player.is_human and (player.alive or player.ghost_vote_available)
        )

    def _has_voted(self, state: TruthState, nomination_id: str, player_id: str) -> bool:
        return any(
            vote.nomination_id == nomination_id and vote.voter_id == player_id
            for vote in state.votes
        )

    async def execute_top_candidate(self, state: TruthState) -> None:
        if state.execution_done_today:
            state.phase = Phase.EXECUTION
            return
        valid = [n for n in state.nominations if n.day == state.day and n.eligible_for_execution]
        if not valid:
            state.add_event(
                "今日沒有玩家達到處決門檻。", scope=AudienceScope.PUBLIC, type="no_execution"
            )
            state.phase = Phase.EXECUTION
            return
        highest = max(n.votes for n in valid)
        leaders = [n for n in valid if n.votes == highest]
        if len(leaders) != 1:
            state.add_event(
                "最高票平手，今日無人被處決。", scope=AudienceScope.PUBLIC, type="tie_no_execution"
            )
            state.phase = Phase.EXECUTION
            return
        target = state.by_id(leaders[0].nominee_id)
        await self.kill_player(state, target.id, cause="execution", public=True, auto_klutz=True)
        state.execution_done_today = True
        state.phase = Phase.EXECUTION

    async def end_day_or_game(self, state: TruthState) -> None:
        if state.result is not None:
            state.phase = Phase.GAME_OVER
            return
        for player in state.players:
            player.nominated_today = False
            player.was_nominated_today = False
        state.execution_done_today = False
        state.discussion_rounds_today = 0
        state.discussion_speakers_today = []
        state.ai_private_chat_initiated_today = []
        self._set_ai_status(state, "新的一天即將開始。")
        state.day += 1
        if state.day > state.max_days:
            await self._resolve_max_day_safety(state)
            return
        state.phase = Phase.NIGHT

    async def run_night(self, state: TruthState) -> None:
        if state.result is not None:
            state.phase = Phase.GAME_OVER
            return
        state.last_night_deaths = []
        state.phase = Phase.NIGHT
        demon = state.demon()
        if demon is not None:
            valid = [player.id for player in state.living()]
            action = await self.provider.night_target(state, demon.id, valid)
            target_id = action.target_id if action.target_id in valid else demon.id
            await self.kill_player(
                state,
                target_id,
                cause="imp_kill",
                public=False,
                demon_attack=True,
                demon_self_kill=target_id == demon.id,
                auto_klutz=True,
            )
        if state.result is None:
            await self._resolve_night_information(state, first_night=False)
            state.phase = Phase.DAWN
            state.add_event(
                "夜晚結束，等待黎明公布。",
                scope=AudienceScope.STORYTELLER_INTERNAL,
                type="night_end",
            )

    async def artist_question(self, state: TruthState, player_id: str, question: str) -> RuleResult:
        player = state.by_id(player_id)
        if not player.alive:
            return RuleResult(False, "死亡玩家不能使用藝術家能力。")
        if player.visible_role != "artist":
            return RuleResult(False, "你目前沒有藝術家能力。")
        used_key = f"artist_used:{player_id}"
        if any(event.type == used_key for event in state.events):
            return RuleResult(False, "藝術家能力已使用過。")
        parsed = await self.provider.artist_question(state, player_id, question)
        if not parsed.supported or parsed.query is None:
            fallback = parse_artist_question(question, state)
            if not fallback.supported or fallback.query is None:
                return RuleResult(False, parsed.message or fallback.message)
            parsed = fallback
        query = parsed.query
        if query is None:
            return RuleResult(False, "無法解析，請重新表述。")
        if player.true_role == "drunk":
            truthful_answer = evaluate_artist_query(state, query)
            answer = self.storyteller.choose_bool(
                state,
                actor_id=player_id,
                purpose="drunk_artist_answer",
                truthful_value=truthful_answer,
            )
        else:
            answer = evaluate_artist_query(state, query)
        state.add_event(
            f"藝術家問題答案：{'是' if answer else '否'}",
            scope=AudienceScope.PLAYER_ONLY,
            type=used_key,
            target_ids=[player_id],
            metadata={"query": query.model_dump(mode="json")},
        )
        return RuleResult(True, "是" if answer else "否")

    async def choose_klutz(self, state: TruthState, player_id: str, target_id: str) -> RuleResult:
        if state.pending_klutz_id != player_id:
            return RuleResult(False, "目前沒有等待你的笨蛋選擇。")
        target = state.by_id(target_id)
        if not target.alive:
            return RuleResult(False, "笨蛋必須選一名存活玩家。")
        await self._finish_klutz_choice(state, player_id, target_id)
        return RuleResult(True, "笨蛋選擇已公開。")

    async def kill_player(
        self,
        state: TruthState,
        target_id: str,
        *,
        cause: str,
        public: bool,
        demon_attack: bool = False,
        demon_self_kill: bool = False,
        auto_klutz: bool = False,
    ) -> None:
        if state.result is not None:
            return
        target = state.by_id(target_id)
        if not target.alive:
            return
        pre_alive = state.living_count()
        target.alive = False
        target.death_cause = cause
        target.death_day = state.day
        message = f"{_seat_label(target)} 死亡（{self._cause_zh(cause)}）。"
        scope = AudienceScope.PUBLIC if public else AudienceScope.STORYTELLER_INTERNAL
        state.add_event(
            message, scope=scope, type="death", target_ids=[target.id], metadata={"cause": cause}
        )
        if not public:
            state.last_night_deaths.append(target.id)

        if target.id == state.current_demon_id:
            await self._handle_demon_death(
                state,
                target,
                pre_alive=pre_alive,
                demon_self_kill=demon_self_kill,
            )

        if demon_attack and target.true_role == "sage" and state.result is None:
            self._resolve_sage(state, target)

        if target.true_role == "klutz" and state.result is None:
            if auto_klutz or not target.is_human:
                valid = [player.id for player in state.living()]
                if valid:
                    if target.is_human:
                        choice = valid[0]
                    else:
                        action = await self.provider.klutz_choice(state, target.id, valid)
                        choice = action.target_id if action.target_id in valid else valid[0]
                    await self._finish_klutz_choice(state, target.id, choice)
            else:
                state.pending_klutz_id = target.id
                state.add_event(
                    f"{_seat_label(target)} 是笨蛋，必須公開選一名存活玩家。",
                    scope=AudienceScope.PUBLIC,
                    type="klutz_pending",
                    actor_id=target.id,
                )

        self._check_two_alive(state)

    async def _resolve_night_information(self, state: TruthState, *, first_night: bool) -> None:
        state.wake_events = [event for event in state.wake_events if event.day != state.day]
        for player in state.living():
            visible_role = player.visible_role
            info_roles = FIRST_NIGHT_INFO_ROLES if first_night else RECURRING_INFO_ROLES
            if visible_role in info_roles:
                state.wake_events.append(
                    WakeEvent(day=state.day, player_id=player.id, role=visible_role)
                )
        if not first_night:
            demon = state.demon()
            if demon is not None:
                state.wake_events.append(WakeEvent(day=state.day, player_id=demon.id, role="imp"))

        for player in state.living():
            if first_night and player.visible_role == "clockmaker":
                self._resolve_clockmaker(state, player.id)
            elif first_night and player.visible_role == "investigator":
                self._resolve_investigator(state, player.id)
            elif player.visible_role == "empath":
                self._resolve_empath(state, player.id)

        for player in state.living():
            if player.visible_role == "chambermaid":
                await self._resolve_chambermaid(state, player.id)

    def _resolve_clockmaker(self, state: TruthState, player_id: str) -> int:
        player = state.by_id(player_id)
        if player.true_role == "drunk":
            result = self.storyteller.choose_number(
                state,
                actor_id=player_id,
                purpose="drunk_clockmaker_info",
                legal_values=[1, 2, 3],
                truthful_value=None,
            )
        else:
            demon = state.demon()
            minions = state.minions()
            if demon is None or not minions:
                result = 0
            else:
                result = min(
                    circular_distance(len(state.players), demon.seat, minion.seat)
                    for minion in minions
                )
        state.add_event(
            f"鐘錶匠資訊：惡魔到最近爪牙的距離是 {result}。",
            scope=AudienceScope.PLAYER_ONLY,
            type="clockmaker_info",
            target_ids=[player_id],
            metadata={"value": result},
        )
        return result

    def _resolve_investigator(self, state: TruthState, player_id: str) -> tuple[str, str, str]:
        player = state.by_id(player_id)
        rng = self._rng(state, f"investigator:{player_id}:{state.day}")
        if player.true_role == "drunk":
            role = rng.choice(["scarlet_woman", "baron"])
            pair = rng.sample([p.id for p in state.players if p.id != player_id], 2)
        else:
            minion = rng.choice(state.minions())
            decoy_id = self.storyteller.choose_player(
                state,
                actor_id=player_id,
                purpose="investigator_decoy",
                legal_player_ids=[p.id for p in state.players if p.id != minion.id],
                anchor_player_id=minion.id,
            )
            role = minion.true_role
            pair = [minion.id, decoy_id]
            rng.shuffle(pair)
        names = [_seat_label(state.by_id(pid)) for pid in pair]
        state.add_event(
            f"調查員資訊：{names[0]} 與 {names[1]} 之中有一名是 {ROLE_SPECS[role].zh_name}。",
            scope=AudienceScope.PLAYER_ONLY,
            type="investigator_info",
            target_ids=[player_id],
            metadata={"players": pair, "minion_role": role},
        )
        return pair[0], pair[1], role

    def _resolve_empath(self, state: TruthState, player_id: str) -> int:
        player = state.by_id(player_id)
        if player.true_role == "drunk":
            result = self.storyteller.choose_number(
                state,
                actor_id=player_id,
                purpose="drunk_empath_info",
                legal_values=[0, 1, 2],
                truthful_value=empath_count(state, player_id),
            )
        else:
            result = empath_count(state, player_id)
        left, right = nearest_living_neighbors(state, player_id)
        state.add_event(
            f"共情者資訊：最近存活鄰居 {_seat_label(left)} 與 {_seat_label(right)} 中有 {result} 名邪惡玩家。",
            scope=AudienceScope.PLAYER_ONLY,
            type="empath_info",
            target_ids=[player_id],
            metadata={"value": result, "neighbors": [left.id, right.id]},
        )
        return result

    async def _resolve_chambermaid(self, state: TruthState, player_id: str) -> int:
        player = state.by_id(player_id)
        valid = [other.id for other in state.living() if other.id != player_id]
        if len(valid) < 2:
            return 0
        if player.is_human:
            chosen = valid[:2]
        else:
            action = await self.provider.chambermaid_choice(state, player_id, valid)
            chosen = [target_id for target_id in action.target_ids if target_id in valid][:2]
            if len(chosen) < 2:
                chosen.extend(
                    [target_id for target_id in valid if target_id not in chosen][: 2 - len(chosen)]
                )
        woke = {
            event.player_id
            for event in state.wake_events
            if event.day == state.day and event.reason == "own_ability"
        }
        truthful_count = sum(target_id in woke for target_id in chosen)
        if player.true_role == "drunk":
            count = self.storyteller.choose_number(
                state,
                actor_id=player_id,
                purpose="drunk_chambermaid_info",
                legal_values=[0, 1, 2],
                truthful_value=truthful_count,
            )
        else:
            count = truthful_count
        names = [_seat_label(state.by_id(target_id)) for target_id in chosen]
        state.add_event(
            f"侍女資訊：你選擇 {names[0]} 與 {names[1]}，其中 {count} 人因自己的能力醒來。",
            scope=AudienceScope.PLAYER_ONLY,
            type="chambermaid_info",
            target_ids=[player_id],
            metadata={"players": chosen, "value": count},
        )
        return count

    def _resolve_sage(self, state: TruthState, sage: PlayerTruth) -> tuple[str, str]:
        rng = self._rng(state, f"sage:{sage.id}:{state.day}")
        demon = state.demon()
        if demon is None and state.current_demon_id:
            demon = state.by_id(state.current_demon_id)
        if demon is None:
            return ("", "")
        decoy_id = self.storyteller.choose_player(
            state,
            actor_id=sage.id,
            purpose="sage_decoy",
            legal_player_ids=[player.id for player in state.players if player.id != demon.id],
            anchor_player_id=demon.id,
        )
        pair = [demon.id, decoy_id]
        rng.shuffle(pair)
        names = [_seat_label(state.by_id(pid)) for pid in pair]
        state.add_event(
            f"賢者資訊：{names[0]} 與 {names[1]} 之中有一名是惡魔。",
            scope=AudienceScope.PLAYER_ONLY,
            type="sage_info",
            target_ids=[sage.id],
            metadata={"players": pair},
        )
        return pair[0], pair[1]

    async def _handle_demon_death(
        self,
        state: TruthState,
        demon: PlayerTruth,
        *,
        pre_alive: int,
        demon_self_kill: bool,
    ) -> None:
        successor = None
        reason = ""
        if demon_self_kill:
            living_minions = state.living_minions()
            if living_minions:
                successor = sorted(living_minions, key=lambda player: player.seat)[0]
                reason = "imp_starpass"
        if successor is None and pre_alive >= 5:
            successor = next(
                (p for p in state.living_minions() if p.true_role == "scarlet_woman"), None
            )
            if successor is not None:
                reason = "scarlet_woman"
        if successor is None:
            state.current_demon_id = None
            self._set_winner(state, Alignment.GOOD, "惡魔死亡且沒有合法接任者。")
            return
        previous = successor.true_role
        successor.true_role = "imp"
        successor.apparent_role = None
        successor.role_history.append("imp")
        state.current_demon_id = successor.id
        state.transformations.append(
            TransformationEvent(
                day=state.day,
                player_id=successor.id,
                from_role=previous,
                to_role="imp",
                reason=reason,
            )
        )
        state.add_event(
            f"{_seat_label(successor)} 成為新的小惡魔。",
            scope=AudienceScope.PLAYER_ONLY,
            type="role_transform",
            target_ids=[successor.id],
            metadata={"from_role": previous, "to_role": "imp", "reason": reason},
        )
        state.add_event(
            f"惡魔死亡由 {_seat_label(successor)} 接任，原因：{reason}。",
            scope=AudienceScope.STORYTELLER_INTERNAL,
            type="demon_successor",
            target_ids=[successor.id],
        )

    async def _finish_klutz_choice(self, state: TruthState, klutz_id: str, target_id: str) -> None:
        klutz = state.by_id(klutz_id)
        target = state.by_id(target_id)
        state.pending_klutz_id = None
        state.add_event(
            f"{_seat_label(klutz)} 的笨蛋選擇：{_seat_label(target)}。",
            scope=AudienceScope.PUBLIC,
            type="klutz_choice",
            actor_id=klutz.id,
            target_ids=[target.id],
        )
        if target.alignment == Alignment.EVIL:
            self._set_winner(state, Alignment.EVIL, "笨蛋死亡後選中邪惡玩家。")

    def _validate_nomination(
        self, state: TruthState, nominator: PlayerTruth, nominee: PlayerTruth
    ) -> None:
        if state.execution_done_today:
            raise ValueError("今天已經處決過，不能再提名。")
        if not nominator.alive:
            raise ValueError("死亡玩家不能提名。")
        if nominator.nominated_today:
            raise ValueError("每名存活玩家每天最多提名一次。")
        if not nominee.alive:
            raise ValueError("不能提名死亡玩家。")
        if nominee.was_nominated_today:
            raise ValueError("每名玩家每天最多被提名一次。")

    def _check_two_alive(self, state: TruthState) -> None:
        if state.result is None and state.living_count() <= 2:
            self._set_winner(state, Alignment.EVIL, "存活玩家只剩兩人。")

    def _set_winner(self, state: TruthState, winner: Alignment, reason: str) -> None:
        if state.result is not None:
            return
        state.result = GameResult(winner=winner, reason=reason, day=state.day)
        state.phase = Phase.GAME_OVER
        state.add_event(
            f"遊戲結束：{'善良' if winner == Alignment.GOOD else '邪惡'}陣營獲勝。{reason}",
            scope=AudienceScope.PUBLIC,
            type="game_over",
            metadata={"winner": winner, "reason": reason},
        )

    async def _resolve_max_day_safety(self, state: TruthState) -> None:
        if state.result is not None:
            return
        if (state.seed or 0) % 2 == 0 and state.current_demon_id is not None:
            await self.kill_player(
                state,
                state.current_demon_id,
                cause="max_day_final_execution",
                public=True,
                auto_klutz=True,
            )
            if state.result is None:
                self._set_winner(state, Alignment.GOOD, "最大天數終局處決後沒有惡魔存活。")
        else:
            self._set_winner(state, Alignment.EVIL, "達到最大遊戲天數，邪惡陣營拖入終局。")

    def _announce_dawn(self, state: TruthState) -> None:
        if state.last_night_deaths:
            names = [_seat_label(state.by_id(player_id)) for player_id in state.last_night_deaths]
            state.add_event(
                f"黎明公布：昨夜死亡的是 {'、'.join(names)}。",
                scope=AudienceScope.PUBLIC,
                type="dawn_deaths",
                target_ids=state.last_night_deaths,
            )
        else:
            state.add_event(
                "黎明公布：昨夜沒有人死亡。", scope=AudienceScope.PUBLIC, type="dawn_deaths"
            )

    def _extract_claimed_role(self, speech: str) -> str | None:
        text = speech.strip()
        lowered = text.lower()
        compact = re.sub(r"\s+", "", lowered)
        markers = ("我是", "我跳", "自稱", "宣稱", "角色是", "偏向我是")
        english_markers = ("claim", "claimed", "role is", "i am")
        if not any(marker in compact for marker in markers) and not any(
            marker in lowered for marker in english_markers
        ):
            return None
        for slug, spec in sorted(
            ROLE_SPECS.items(),
            key=lambda item: max(len(item[0]), len(item[1].zh_name)),
            reverse=True,
        ):
            tokens = (slug.lower(), spec.zh_name.lower())
            for token in tokens:
                if not token:
                    continue
                token_compact = re.sub(r"\s+", "", token)
                index = compact.find(token_compact)
                if index < 0:
                    continue
                lead = compact[max(0, index - 6) : index]
                if "不是" in lead or "非" in lead or "not" in lead:
                    continue
                return slug
        return None

    def _apply_claim(self, state: TruthState, player_id: str, claimed_role: str | None) -> None:
        if not claimed_role or claimed_role not in ROLE_SPECS:
            return
        memory = state.ai_memories.get(player_id)
        if memory is not None:
            memory.public_claim = claimed_role
        for other_id, other_memory in state.ai_memories.items():
            if other_id != player_id:
                other_memory.known_claims[player_id] = claimed_role

    def _remember_nomination_pressure(
        self, state: TruthState, nominator_id: str, nominee_id: str
    ) -> None:
        for memory in state.ai_memories.values():
            self._bump_suspicion(memory, nominee_id, 0.05)
            self._append_memory_note(memory, f"public nomination: {nominator_id}->{nominee_id}")

    def _remember_vote_result(self, state: TruthState, nomination: NominationRecord) -> None:
        nominee_delta = 0.04 if nomination.eligible_for_execution else -0.02
        nominator_delta = 0.0 if nomination.eligible_for_execution else 0.015
        for memory in state.ai_memories.values():
            self._bump_suspicion(memory, nomination.nominee_id, nominee_delta)
            self._bump_suspicion(memory, nomination.nominator_id, nominator_delta)
            outcome = "eligible" if nomination.eligible_for_execution else "low"
            self._append_memory_note(
                memory,
                f"vote result: {nomination.nominee_id} {nomination.votes}/{nomination.threshold} {outcome}",
            )

    def _bump_suspicion(self, memory: AIMemory, target_id: str, delta: float) -> None:
        if target_id == memory.player_id or target_id not in memory.suspicion:
            return
        current = memory.suspicion[target_id]
        memory.suspicion[target_id] = round(min(1.0, max(0.0, current + delta)), 3)

    def _append_memory_note(self, memory: AIMemory, note: str) -> None:
        memory.summary = f"{memory.summary}\n{note}".strip()[-700:]
        memory.compact()

    def _remember_private(self, state: TruthState, player_id: str, note: str) -> None:
        memory = state.ai_memories.get(player_id)
        if memory is None:
            return
        memory.private_promises.append(note[:160])
        memory.compact()

    def _cause_zh(self, cause: str) -> str:
        return {
            "execution": "處決",
            "imp_kill": "小惡魔攻擊",
            "max_day_final_execution": "終局處決",
        }.get(cause, cause)

    def _rng(self, state: TruthState, salt: str) -> random.Random:
        return random.Random(f"{state.seed}:{salt}")


def _table_public_speech(text: str, *, max_chars: int = 170, max_sentences: int = 2) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return clean
    sentences = [part for part in re.split(r"(?<=[。！？!?])\s*", clean) if part]
    if len(sentences) > max_sentences:
        clean = "".join(sentences[:max_sentences]).strip()
    if len(clean) <= max_chars:
        return clean
    punctuation_cut = max(clean.rfind(mark, 0, max_chars) for mark in "。！？!?；;，,")
    if punctuation_cut >= 60:
        return clean[: punctuation_cut + 1].strip()
    return f"{clean[: max_chars - 1].rstrip()}…"


def _seat_label(player: PlayerTruth) -> str:
    return f"{player.seat + 1}號 {player.name}"


def evil_players(state: TruthState) -> Iterable[str]:
    return (player.id for player in state.players if player.alignment == Alignment.EVIL)
