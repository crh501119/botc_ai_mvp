from __future__ import annotations

import pytest

from botc_ai.domain.context import legal_actions_for
from botc_ai.domain.models import Phase
from botc_ai.domain.sessions import claim_human_seat
from botc_ai.domain.setup import generate_game
from tests.conftest import fixed_state


def test_human_public_claim_is_recorded_for_ai_memories(mock_engine) -> None:
    state = fixed_state()
    state.phase = Phase.DAY_DISCUSSION

    result = mock_engine.add_human_public_speech(state, "human", "我偏向我是 clockmaker")

    assert result.ok
    assert all(
        memory.known_claims.get("human") == "clockmaker" for memory in state.ai_memories.values()
    )


def test_natural_public_claim_phrasing_is_recorded(mock_engine) -> None:
    state = fixed_state()
    state.phase = Phase.DAY_DISCUSSION

    result = mock_engine.add_human_public_speech(state, "human", "我可能是 clockmaker，先不全開")

    assert result.ok
    assert all(
        memory.known_claims.get("human") == "clockmaker" for memory in state.ai_memories.values()
    )


def test_public_speech_rejected_at_night(mock_engine) -> None:
    state = fixed_state()
    state.phase = Phase.NIGHT

    result = mock_engine.add_human_public_speech(state, "human", "半夜偷講一句")

    assert not result.ok
    assert not any(event.type == "public_speech" for event in state.events)


def test_public_speech_not_legal_at_night() -> None:
    state = fixed_state()
    state.phase = Phase.NIGHT

    assert "public_speech" not in legal_actions_for(state, "human")


@pytest.mark.asyncio
async def test_multiplayer_waits_for_all_humans_before_start(mock_engine) -> None:
    state = generate_game(human_count=2, seed=203, mock_ai=True)
    claim_human_seat(state, "human", "A")

    await mock_engine.start_game(state)

    assert state.phase == Phase.SETUP

    claim_human_seat(state, "human_2", "B")
    await mock_engine.start_game(state)

    assert state.phase == Phase.DAWN


@pytest.mark.asyncio
async def test_ordered_discussion_requires_current_speaker(mock_engine) -> None:
    state = generate_game(
        human_count=2,
        seed=204,
        mock_ai=True,
        discussion_mode="ordered",
        shuffle_seats_on_start=False,
    )
    claim_human_seat(state, "human", "A")
    claim_human_seat(state, "human_2", "B")

    await mock_engine.start_game(state)
    await mock_engine.advance_phase(state)

    assert state.phase == Phase.DAY_DISCUSSION
    assert state.ordered_speaker_id == "human"
    assert "public_speech" in legal_actions_for(state, "human")
    assert "public_speech" not in legal_actions_for(state, "human_2")
    assert not mock_engine.add_human_public_speech(state, "human_2", "插話").ok

    assert mock_engine.add_human_public_speech(state, "human", "我先講我的資訊").ok

    assert state.ordered_speaker_id == "human_2"
    assert "public_speech" in legal_actions_for(state, "human_2")


@pytest.mark.asyncio
async def test_ordered_discussion_starts_with_last_night_death(mock_engine) -> None:
    state = generate_game(
        seed=205,
        mock_ai=True,
        discussion_mode="ordered",
        shuffle_seats_on_start=False,
    )

    await mock_engine.start_game(state)
    state.last_night_deaths = ["ai_3"]
    state.by_id("ai_3").alive = False
    await mock_engine.advance_phase(state)

    assert state.phase == Phase.DAY_DISCUSSION
    assert state.ordered_speaker_id == "ai_3"


@pytest.mark.asyncio
async def test_private_chat_rejected_at_night(mock_engine) -> None:
    state = fixed_state()
    state.phase = Phase.NIGHT

    result = await mock_engine.add_private_chat(state, "human", "ai_1", "半夜私聊")

    assert not result.ok
    assert not any(event.type == "private_chat" for event in state.events)


@pytest.mark.asyncio
async def test_nomination_updates_ai_suspicion_without_truth(mock_engine) -> None:
    state = fixed_state()
    before = state.ai_memories["ai_2"].suspicion["ai_1"]

    await mock_engine.create_nomination(state, "human", "ai_1", "公開壓力測試")

    assert state.ai_memories["ai_2"].suspicion["ai_1"] > before


@pytest.mark.asyncio
async def test_low_vote_result_softens_nominee_suspicion(mock_engine) -> None:
    state = fixed_state()
    nomination = await mock_engine.create_nomination(state, "human", "ai_1", "公開壓力測試")
    after_nomination = state.ai_memories["ai_2"].suspicion["ai_1"]

    await mock_engine.resolve_vote(state, nomination.id, human_vote=False)

    assert state.ai_memories["ai_2"].suspicion["ai_1"] < after_nomination


@pytest.mark.asyncio
async def test_nomination_limit(mock_engine) -> None:
    state = fixed_state()
    await mock_engine.create_nomination(state, "human", "ai_1", "測試")
    state.phase = Phase.NOMINATIONS
    with pytest.raises(ValueError):
        await mock_engine.create_nomination(state, "human", "ai_2", "第二次")


@pytest.mark.asyncio
async def test_nominee_limit(mock_engine) -> None:
    state = fixed_state()
    await mock_engine.create_nomination(state, "human", "ai_1", "測試")
    state.phase = Phase.NOMINATIONS
    with pytest.raises(ValueError):
        await mock_engine.create_nomination(state, "ai_2", "ai_1", "重複被提名")


@pytest.mark.asyncio
async def test_open_nomination_blocks_collision(mock_engine) -> None:
    state = fixed_state()
    await mock_engine.create_nomination(state, "human", "ai_1", "第一個提名")

    with pytest.raises(ValueError, match="已有提名"):
        await mock_engine.create_nomination(state, "ai_2", "ai_3", "撞車提名")


@pytest.mark.asyncio
async def test_dead_cannot_nominate(mock_engine) -> None:
    state = fixed_state()
    state.by_id("human").alive = False
    with pytest.raises(ValueError):
        await mock_engine.create_nomination(state, "human", "ai_1", "死亡提名")


@pytest.mark.asyncio
async def test_living_players_vote(mock_engine) -> None:
    state = fixed_state()
    nomination = await mock_engine.create_nomination(state, "human", "ai_1", "測試")
    await mock_engine.resolve_vote(state, nomination.id, human_vote=True)
    assert any(vote.voter_id == "human" and vote.vote for vote in state.votes)


@pytest.mark.asyncio
async def test_multiple_humans_vote_independently_before_ai_votes(mock_engine) -> None:
    state = generate_game(human_count=2, seed=202, mock_ai=True)
    state.phase = Phase.NOMINATIONS
    nomination = await mock_engine.create_nomination(state, "human", "ai_1", "測試票型")

    await mock_engine.cast_human_vote(state, "human", vote=True)

    assert state.phase == Phase.VOTING
    assert not nomination.resolved
    assert any(vote.voter_id == "human" and vote.vote for vote in state.votes)
    assert not any(vote.voter_id == "human_2" for vote in state.votes)
    assert "vote_yes" in legal_actions_for(state, "human_2")
    assert "vote_yes" not in legal_actions_for(state, "human")

    await mock_engine.cast_human_vote(state, "human_2", vote=False)

    assert nomination.resolved
    assert state.phase == Phase.NOMINATIONS
    assert any(vote.voter_id == "human_2" and not vote.vote for vote in state.votes)
    assert any(vote.voter_id.startswith("ai_") for vote in state.votes)


@pytest.mark.asyncio
async def test_ghost_vote_only_once(mock_engine) -> None:
    state = fixed_state()
    state.by_id("human").alive = False
    state.by_id("human").ghost_vote_available = True
    nomination = await mock_engine.create_nomination(state, "ai_2", "ai_1", "第一輪")
    await mock_engine.resolve_vote(state, nomination.id, human_vote=True)
    assert not state.by_id("human").ghost_vote_available
    state.phase = Phase.NOMINATIONS
    nomination2 = await mock_engine.create_nomination(state, "ai_3", "ai_2", "第二輪")
    await mock_engine.resolve_vote(state, nomination2.id, human_vote=True)
    human_votes = [vote for vote in state.votes if vote.voter_id == "human"]
    assert len(human_votes) == 1


@pytest.mark.asyncio
async def test_majority_threshold(mock_engine) -> None:
    state = fixed_state()
    nomination = await mock_engine.create_nomination(state, "human", "ai_1", "測試")
    await mock_engine.resolve_vote(state, nomination.id, human_vote=False)
    assert nomination.threshold == 3


@pytest.mark.asyncio
async def test_highest_votes_executed(mock_engine) -> None:
    state = fixed_state()
    first = await mock_engine.create_nomination(state, "human", "ai_1", "低票")
    await mock_engine.resolve_vote(state, first.id, human_vote=False)
    first.votes = 3
    first.threshold = 3
    first.eligible_for_execution = True
    state.phase = Phase.NOMINATIONS
    second = await mock_engine.create_nomination(state, "ai_2", "ai_3", "高票")
    await mock_engine.resolve_vote(state, second.id, human_vote=True)
    second.votes = 4
    second.threshold = 3
    second.eligible_for_execution = True
    await mock_engine.execute_top_candidate(state)
    assert not state.by_id("ai_3").alive


@pytest.mark.asyncio
async def test_tie_no_execution(mock_engine) -> None:
    state = fixed_state()
    first = await mock_engine.create_nomination(state, "human", "ai_1", "測試")
    await mock_engine.resolve_vote(state, first.id, human_vote=False)
    first.votes = 3
    first.threshold = 3
    first.eligible_for_execution = True
    state.phase = Phase.NOMINATIONS
    second = await mock_engine.create_nomination(state, "ai_2", "ai_3", "測試")
    await mock_engine.resolve_vote(state, second.id, human_vote=False)
    second.votes = 3
    second.threshold = 3
    second.eligible_for_execution = True
    await mock_engine.execute_top_candidate(state)
    assert state.by_id("ai_1").alive
    assert state.by_id("ai_3").alive


@pytest.mark.asyncio
async def test_one_execution_per_day(mock_engine) -> None:
    state = fixed_state()
    nomination = await mock_engine.create_nomination(state, "human", "ai_1", "測試")
    await mock_engine.resolve_vote(state, nomination.id, human_vote=True)
    nomination.votes = 4
    nomination.threshold = 3
    nomination.eligible_for_execution = True
    await mock_engine.execute_top_candidate(state)
    state.phase = Phase.NOMINATIONS
    with pytest.raises(ValueError):
        await mock_engine.create_nomination(state, "ai_2", "ai_3", "不應允許")
