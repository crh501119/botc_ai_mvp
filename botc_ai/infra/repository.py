from __future__ import annotations

import json
from collections.abc import Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from botc_ai.domain.models import AudienceScope, TruthState
from botc_ai.infra.orm import (
    AIMemoryORM,
    ApiUsageORM,
    AssignmentORM,
    GameORM,
    GameResultORM,
    NominationORM,
    PlayerORM,
    PrivateEventORM,
    PublicEventORM,
    RoleStateORM,
    VoteORM,
)

CHILD_TABLES = (
    PlayerORM,
    AssignmentORM,
    RoleStateORM,
    PublicEventORM,
    PrivateEventORM,
    NominationORM,
    VoteORM,
    AIMemoryORM,
    ApiUsageORM,
    GameResultORM,
)


class GameRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_games(self) -> list[GameORM]:
        return list(self.session.scalars(select(GameORM).order_by(GameORM.updated_at.desc())).all())

    def get_state(self, game_id: str) -> TruthState:
        game = self.session.get(GameORM, game_id)
        if game is None:
            raise KeyError(game_id)
        return TruthState.model_validate_json(game.state_json)

    def delete_game(self, game_id: str) -> None:
        game = self.session.get(GameORM, game_id)
        if game is not None:
            self.session.delete(game)

    def save_state(self, state: TruthState) -> None:
        existing = self.session.get(GameORM, state.game_id)
        state_json = state.model_dump_json()
        if existing is None:
            existing = GameORM(
                id=state.game_id,
                version=state.version,
                game_version=state.version,
                seed=state.seed,
                phase=state.phase.value,
                day=state.day,
                budget_usd=state.budget_usd,
                mock_ai=state.mock_ai,
                state_json=state_json,
            )
            self.session.add(existing)
            self.session.flush()
        else:
            existing.version = state.version
            existing.game_version = state.version
            existing.seed = state.seed
            existing.phase = state.phase.value
            existing.day = state.day
            existing.budget_usd = state.budget_usd
            existing.mock_ai = state.mock_ai
            existing.state_json = state_json

        for table in CHILD_TABLES:
            self.session.execute(delete(table).where(table.game_id == state.game_id))
        self.session.flush()
        self._save_players(state)
        self._save_events(state)
        self._save_nominations_votes(state)
        self._save_memories_usage_results(state)

    def _save_players(self, state: TruthState) -> None:
        for player in state.players:
            self.session.add(
                PlayerORM(
                    game_id=state.game_id,
                    player_id=player.id,
                    name=player.name,
                    seat=player.seat,
                    is_human=player.is_human,
                    alive=player.alive,
                    ghost_vote_available=player.ghost_vote_available,
                )
            )
            self.session.add(
                AssignmentORM(
                    game_id=state.game_id,
                    player_id=player.id,
                    true_role=player.true_role,
                    apparent_role=player.apparent_role,
                    true_alignment=player.alignment.value,
                    postgame_only=True,
                )
            )
            self.session.add(
                RoleStateORM(
                    game_id=state.game_id,
                    player_id=player.id,
                    role=player.true_role,
                    state_json=json.dumps(
                        {
                            "alive": player.alive,
                            "death_cause": player.death_cause,
                            "death_day": player.death_day,
                            "role_history": player.role_history,
                        },
                        ensure_ascii=False,
                    ),
                )
            )

    def _save_events(self, state: TruthState) -> None:
        for event in state.events:
            payload = {
                "id": event.id,
                "game_id": state.game_id,
                "day": event.day,
                "phase": event.phase.value,
                "scope": event.scope.value,
                "actor_id": event.actor_id,
                "target_ids_json": json.dumps(event.target_ids, ensure_ascii=False),
                "participants_json": json.dumps(event.participants, ensure_ascii=False),
                "message": event.message,
                "metadata_json": json.dumps(event.metadata, ensure_ascii=False),
                "created_at": event.created_at,
            }
            if event.scope == AudienceScope.PUBLIC:
                self.session.add(PublicEventORM(type=event.type, **payload))
            else:
                self.session.add(PrivateEventORM(**payload))

    def _save_nominations_votes(self, state: TruthState) -> None:
        for nomination in state.nominations:
            self.session.add(
                NominationORM(
                    id=nomination.id,
                    game_id=state.game_id,
                    day=nomination.day,
                    nominator_id=nomination.nominator_id,
                    nominee_id=nomination.nominee_id,
                    reason=nomination.reason,
                    defense=nomination.defense,
                    votes=nomination.votes,
                    threshold=nomination.threshold,
                    eligible_for_execution=nomination.eligible_for_execution,
                    resolved=nomination.resolved,
                )
            )
        for vote in state.votes:
            self.session.add(
                VoteORM(
                    game_id=state.game_id,
                    nomination_id=vote.nomination_id,
                    day=vote.day,
                    voter_id=vote.voter_id,
                    nominee_id=vote.nominee_id,
                    vote=vote.vote,
                    used_ghost_vote=vote.used_ghost_vote,
                    public_reason=vote.public_reason,
                )
            )

    def _save_memories_usage_results(self, state: TruthState) -> None:
        for memory in state.ai_memories.values():
            self.session.add(
                AIMemoryORM(
                    game_id=state.game_id,
                    player_id=memory.player_id,
                    memory_json=memory.model_dump_json(),
                )
            )
        for usage in state.api_usage:
            self.session.add(
                ApiUsageORM(
                    id=usage.id,
                    game_id=state.game_id,
                    player_id=usage.player_id,
                    model=usage.model,
                    purpose=usage.purpose,
                    input_tokens=usage.input_tokens,
                    cached_input_tokens=usage.cached_input_tokens,
                    output_tokens=usage.output_tokens,
                    reasoning_tokens=usage.reasoning_tokens,
                    estimated_usd=usage.estimated_usd,
                    created_at=usage.created_at,
                )
            )
        if state.result is not None:
            self.session.add(
                GameResultORM(
                    game_id=state.game_id,
                    winner=state.result.winner.value,
                    reason=state.result.reason,
                    day=state.result.day,
                    created_at=state.result.created_at,
                )
            )


def filter_events_for_audience(
    state: TruthState, player_id: str, *, postgame: bool
) -> Iterable[str]:
    for event in state.events:
        if (
            event.scope == AudienceScope.PUBLIC
            or (event.scope == AudienceScope.PLAYER_ONLY and player_id in event.target_ids)
            or (
                event.scope == AudienceScope.PRIVATE_CHAT_PARTICIPANTS
                and player_id in event.participants
            )
            or (event.scope == AudienceScope.POSTGAME_ONLY and postgame)
        ):
            yield event.message
