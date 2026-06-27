from __future__ import annotations

import random
from typing import Any

from botc_ai.domain.models import AudienceScope, TruthState
from botc_ai.domain.roles import Alignment


class AIStorytellerPolicy:
    """Constrained Storyteller discretion over legal engine-generated options.

    This is intentionally not an all-powerful LLM referee. The rules engine supplies
    legal options, and this policy only chooses among them, then records the choice as
    STORYTELLER_INTERNAL.
    """

    def choose_player(
        self,
        state: TruthState,
        *,
        actor_id: str,
        purpose: str,
        legal_player_ids: list[str],
        anchor_player_id: str | None = None,
    ) -> str:
        if not legal_player_ids:
            raise ValueError("AIStorytellerPolicy needs at least one legal player")
        rng = self._rng(state, f"{purpose}:{actor_id}:{anchor_player_id}")
        ordered = sorted(legal_player_ids)
        good_pressure = self._good_pressure(state)
        if anchor_player_id is not None and good_pressure > 0:
            anchor = state.by_id(anchor_player_id)
            ordered.sort(
                key=lambda player_id: (
                    abs(state.by_id(player_id).seat - anchor.seat),
                    player_id,
                )
            )
        elif good_pressure < 0:
            rng.shuffle(ordered)
        chosen = ordered[0]
        self._record(
            state,
            actor_id=actor_id,
            purpose=purpose,
            legal_options=legal_player_ids,
            chosen=chosen,
            pressure=good_pressure,
        )
        return chosen

    def choose_number(
        self,
        state: TruthState,
        *,
        actor_id: str,
        purpose: str,
        legal_values: list[int],
        truthful_value: int | None = None,
    ) -> int:
        if not legal_values:
            raise ValueError("AIStorytellerPolicy needs at least one legal number")
        values = sorted(set(legal_values))
        pressure = self._good_pressure(state)
        if truthful_value in values and abs(pressure) < 2:
            chosen = truthful_value
        elif pressure > 0:
            chosen = max(values)
        elif pressure < 0:
            chosen = min(values)
        else:
            chosen = self._rng(state, f"{purpose}:{actor_id}").choice(values)
        self._record(
            state,
            actor_id=actor_id,
            purpose=purpose,
            legal_options=values,
            chosen=chosen,
            truthful_value=truthful_value,
            pressure=pressure,
        )
        return chosen

    def choose_bool(
        self,
        state: TruthState,
        *,
        actor_id: str,
        purpose: str,
        truthful_value: bool | None = None,
    ) -> bool:
        pressure = self._good_pressure(state)
        if truthful_value is not None and abs(pressure) < 2:
            chosen = truthful_value
        elif pressure > 0:
            chosen = False
        elif pressure < 0:
            chosen = True
        else:
            chosen = self._rng(state, f"{purpose}:{actor_id}").choice([True, False])
        self._record(
            state,
            actor_id=actor_id,
            purpose=purpose,
            legal_options=[True, False],
            chosen=chosen,
            truthful_value=truthful_value,
            pressure=pressure,
        )
        return chosen

    def _good_pressure(self, state: TruthState) -> int:
        living_good = sum(
            player.alive and player.alignment == Alignment.GOOD for player in state.players
        )
        living_evil = sum(
            player.alive and player.alignment == Alignment.EVIL for player in state.players
        )
        info_events = sum(
            event.type
            in {
                "clockmaker_info",
                "investigator_info",
                "empath_info",
                "chambermaid_info",
                "sage_info",
                "artist_used:human",
            }
            for event in state.events
        )
        if living_evil <= 1 and living_good >= 4:
            return 2
        if info_events >= 4 and living_good > living_evil:
            return 1
        if living_good <= 3 or state.living_count() <= 3:
            return -2
        return 0

    def _record(
        self,
        state: TruthState,
        *,
        actor_id: str,
        purpose: str,
        legal_options: list[Any],
        chosen: Any,
        pressure: int,
        truthful_value: Any | None = None,
    ) -> None:
        state.add_event(
            f"AI 說書人策略選擇：{purpose}",
            scope=AudienceScope.STORYTELLER_INTERNAL,
            type="storyteller_policy",
            actor_id=actor_id,
            metadata={
                "purpose": purpose,
                "legal_options": legal_options,
                "chosen": chosen,
                "truthful_value": truthful_value,
                "good_pressure": pressure,
            },
        )

    def _rng(self, state: TruthState, salt: str) -> random.Random:
        return random.Random(f"{state.seed}:{state.day}:storyteller:{salt}:{len(state.events)}")
