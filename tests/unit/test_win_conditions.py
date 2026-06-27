from __future__ import annotations

import pytest

from botc_ai.domain.roles import Alignment
from tests.conftest import fixed_state


@pytest.mark.asyncio
async def test_demon_death_good_wins(mock_engine) -> None:
    state = fixed_state("clockmaker", "empath", "sage", "klutz", "baron", "imp")
    state.by_id("ai_4").alive = False
    await mock_engine.kill_player(state, "ai_5", cause="execution", public=True)
    assert state.result is not None
    assert state.result.winner == Alignment.GOOD


@pytest.mark.asyncio
async def test_scarlet_woman_successor_prevents_immediate_good_win(mock_engine) -> None:
    state = fixed_state("clockmaker", "empath", "sage", "klutz", "scarlet_woman", "imp")
    await mock_engine.kill_player(state, "ai_5", cause="execution", public=True)
    assert state.result is None
    assert state.current_demon_id == "ai_4"


def test_two_alive_evil_wins(mock_engine) -> None:
    state = fixed_state()
    for player_id in ["human", "ai_1", "ai_2", "ai_3"]:
        state.by_id(player_id).alive = False
    mock_engine._check_two_alive(state)
    assert state.result is not None
    assert state.result.winner == Alignment.EVIL


@pytest.mark.asyncio
async def test_klutz_loss(mock_engine) -> None:
    state = fixed_state("klutz", "clockmaker", "empath", "sage", "scarlet_woman", "imp")
    await mock_engine.kill_player(state, "human", cause="execution", public=True, auto_klutz=False)
    await mock_engine.choose_klutz(state, "human", "ai_5")
    assert state.result is not None
    assert state.result.winner == Alignment.EVIL


@pytest.mark.asyncio
async def test_win_condition_only_once(mock_engine) -> None:
    state = fixed_state("clockmaker", "empath", "sage", "klutz", "baron", "imp")
    state.by_id("ai_4").alive = False
    await mock_engine.kill_player(state, "ai_5", cause="execution", public=True)
    reason = state.result.reason if state.result else ""
    mock_engine._set_winner(state, Alignment.EVIL, "should not replace")
    assert state.result is not None
    assert state.result.reason == reason
