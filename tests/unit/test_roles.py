from __future__ import annotations

import pytest

from botc_ai.domain.artist import (
    ArtistStructuredQuestion,
    Comparator,
    QueryKind,
    evaluate_artist_query,
    parse_artist_question,
)
from botc_ai.domain.engine import GameEngine
from botc_ai.domain.models import AudienceScope, Phase, WakeEvent
from botc_ai.domain.roles import Alignment
from botc_ai.domain.rules import circular_distance, empath_count, nearest_living_neighbors
from tests.conftest import fixed_state


def test_circular_distance() -> None:
    assert circular_distance(6, 0, 1) == 1
    assert circular_distance(6, 0, 5) == 1
    assert circular_distance(6, 0, 3) == 3


def test_clockmaker_steps() -> None:
    state = fixed_state("clockmaker", "empath", "sage", "klutz", "scarlet_woman", "imp")
    result = GameEngine()._resolve_clockmaker(state, "human")
    assert result == 1


def test_empath_skips_dead_neighbors() -> None:
    state = fixed_state("empath", "clockmaker", "investigator", "klutz", "scarlet_woman", "imp")
    state.by_id("ai_1").alive = False
    left, right = nearest_living_neighbors(state, "human")
    assert {left.id, right.id} == {"ai_2", "ai_5"}
    assert empath_count(state, "human") == 1


def test_investigator_legal_info() -> None:
    state = fixed_state("investigator", "clockmaker", "empath", "klutz", "scarlet_woman", "imp")
    pair_a, pair_b, role = GameEngine()._resolve_investigator(state, "human")
    assert role == "scarlet_woman"
    assert "ai_4" in {pair_a, pair_b}
    assert any(event.type == "storyteller_policy" for event in state.events)


@pytest.mark.asyncio
async def test_chambermaid_wake_count() -> None:
    state = fixed_state("chambermaid", "clockmaker", "empath", "klutz", "scarlet_woman", "imp")
    engine = GameEngine()
    state.wake_events.clear()
    state.wake_events.extend(
        [
            WakeEvent(day=1, player_id="ai_1", role="clockmaker"),
            WakeEvent(day=1, player_id="ai_2", role="empath"),
        ]
    )
    state.night_action_choices[engine._choice_key(state, "human", "chambermaid_choice")] = [
        "ai_1",
        "ai_2",
    ]
    count = await engine._resolve_chambermaid(state, "human")
    assert count == 2


@pytest.mark.asyncio
async def test_human_chambermaid_gets_target_prompt_before_info(mock_engine) -> None:
    state = fixed_state("chambermaid", "clockmaker", "empath", "klutz", "scarlet_woman", "imp")
    state.phase = Phase.FIRST_NIGHT

    completed = await mock_engine._resolve_night_information(state, first_night=True)

    assert not completed
    prompt = state.pending_action_prompts["human"]
    assert prompt.action == "chambermaid_choice"
    assert prompt.target_count == 2
    assert "human" not in prompt.valid_target_ids
    assert not any(event.type == "chambermaid_info" for event in state.events)

    result = await mock_engine.submit_chambermaid_choice(state, "human", ["ai_1", "ai_2"])

    assert result.ok
    assert state.phase == Phase.DAWN
    assert "human" not in state.pending_action_prompts
    assert any(
        event.type == "chambermaid_info" and event.metadata["players"] == ["ai_1", "ai_2"]
        for event in state.events
    )


@pytest.mark.asyncio
async def test_human_imp_gets_target_prompt_before_night_kill(mock_engine) -> None:
    state = fixed_state("imp", "clockmaker", "empath", "sage", "scarlet_woman", "klutz")
    state.phase = Phase.NIGHT
    state.day = 2

    await mock_engine.run_night(state)

    prompt = state.pending_action_prompts["human"]
    assert prompt.action == "night_target"
    assert "ai_1" in prompt.valid_target_ids
    assert state.by_id("ai_1").alive

    result = await mock_engine.submit_night_target(state, "human", "ai_1")

    assert result.ok
    assert not state.by_id("ai_1").alive
    assert "ai_1" in state.last_night_deaths
    assert state.phase == Phase.DAWN


@pytest.mark.asyncio
async def test_human_target_prompt_rejects_invalid_target(mock_engine) -> None:
    state = fixed_state("imp", "clockmaker", "empath", "sage", "scarlet_woman", "klutz")
    state.phase = Phase.NIGHT
    state.day = 2
    await mock_engine.run_night(state)

    result = await mock_engine.submit_night_target(state, "human", "missing")

    assert not result.ok
    assert state.pending_action_prompts["human"].action == "night_target"
    assert state.by_id("ai_1").alive


def test_artist_dsl() -> None:
    state = fixed_state("artist", "clockmaker", "empath", "klutz", "scarlet_woman", "imp")
    query = ArtistStructuredQuestion(kind=QueryKind.IS_DEMON, player_id="ai_5")
    assert evaluate_artist_query(state, query) is True
    parsed = parse_artist_question("ai_5 是否為惡魔？", state)
    assert parsed.supported


@pytest.mark.asyncio
async def test_artist_unsupported_query_does_not_spend_ability(mock_engine) -> None:
    state = fixed_state("artist", "clockmaker", "empath", "klutz", "scarlet_woman", "imp")
    result = await mock_engine.artist_question(state, "human", "今晚我應該相信誰的夢？")
    assert not result.ok
    assert not any(event.type == "artist_used:human" for event in state.events)


@pytest.mark.asyncio
async def test_sage_only_triggers_on_demon_night_kill(mock_engine) -> None:
    state = fixed_state("sage", "clockmaker", "empath", "klutz", "scarlet_woman", "imp")
    await mock_engine.kill_player(state, "human", cause="execution", public=True)
    assert not any(event.type == "sage_info" for event in state.events)
    state = fixed_state("sage", "clockmaker", "empath", "klutz", "scarlet_woman", "imp")
    await mock_engine.kill_player(state, "human", cause="imp_kill", public=False, demon_attack=True)
    assert any(event.type == "sage_info" for event in state.events)


def test_drunk_ability_invalid_and_info_legal() -> None:
    state = fixed_state("drunk", "clockmaker", "investigator", "empath", "scarlet_woman", "imp")
    drunk = state.by_id("human")
    drunk.apparent_role = "empath"
    result = GameEngine()._resolve_empath(state, "human")
    assert result in {0, 1, 2}
    assert any(event.scope == AudienceScope.PLAYER_ONLY for event in state.events)
    assert any(event.type == "storyteller_policy" for event in state.events)


@pytest.mark.asyncio
async def test_klutz_selecting_evil_loses_immediately(mock_engine) -> None:
    state = fixed_state("klutz", "clockmaker", "empath", "sage", "scarlet_woman", "imp")
    await mock_engine.kill_player(state, "human", cause="execution", public=True, auto_klutz=False)
    await mock_engine.choose_klutz(state, "human", "ai_4")
    assert state.result is not None
    assert state.result.winner == Alignment.EVIL


@pytest.mark.asyncio
async def test_scarlet_woman_takes_over_at_threshold(mock_engine) -> None:
    state = fixed_state("clockmaker", "empath", "sage", "klutz", "scarlet_woman", "imp")
    await mock_engine.kill_player(state, "ai_5", cause="execution", public=True)
    assert state.result is None
    assert state.by_id("ai_4").true_role == "imp"
    assert state.current_demon_id == "ai_4"


@pytest.mark.asyncio
async def test_scarlet_woman_does_not_take_over_below_threshold(mock_engine) -> None:
    state = fixed_state("clockmaker", "empath", "sage", "klutz", "scarlet_woman", "imp")
    state.by_id("ai_1").alive = False
    state.by_id("ai_2").alive = False
    await mock_engine.kill_player(state, "ai_5", cause="execution", public=True)
    assert state.result is not None
    assert state.result.winner == Alignment.GOOD


@pytest.mark.asyncio
async def test_imp_normal_night_kill(mock_engine) -> None:
    state = fixed_state("clockmaker", "empath", "sage", "klutz", "scarlet_woman", "imp")
    await mock_engine.kill_player(state, "ai_1", cause="imp_kill", public=False, demon_attack=True)
    assert not state.by_id("ai_1").alive
    assert "ai_1" in state.last_night_deaths


@pytest.mark.asyncio
async def test_imp_self_kill_starpass(mock_engine) -> None:
    state = fixed_state("clockmaker", "empath", "sage", "klutz", "baron", "imp")
    await mock_engine.kill_player(
        state, "ai_5", cause="imp_kill", public=False, demon_attack=True, demon_self_kill=True
    )
    assert state.by_id("ai_4").true_role == "imp"
    assert state.current_demon_id == "ai_4"


@pytest.mark.asyncio
async def test_no_starpass_without_living_minion(mock_engine) -> None:
    state = fixed_state("clockmaker", "empath", "sage", "klutz", "baron", "imp")
    state.by_id("ai_4").alive = False
    await mock_engine.kill_player(
        state, "ai_5", cause="imp_kill", public=False, demon_attack=True, demon_self_kill=True
    )
    assert state.result is not None
    assert state.result.winner == Alignment.GOOD


def test_artist_alive_evil_count_compare() -> None:
    state = fixed_state("artist", "clockmaker", "empath", "klutz", "scarlet_woman", "imp")
    query = ArtistStructuredQuestion(
        kind=QueryKind.ALIVE_EVIL_COUNT_COMPARE,
        comparator=Comparator.GTE,
        count=2,
    )
    assert evaluate_artist_query(state, query) is True
