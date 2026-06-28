from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from botc_ai.domain.models import Phase
from tests.conftest import fixed_state


@pytest.mark.asyncio
async def test_phase_ready_advances_when_all_humans_are_ready(mock_engine) -> None:
    state = fixed_state()
    state.phase = Phase.DAY_DISCUSSION

    result = await mock_engine.mark_phase_ready(state, "human")

    assert result.ok
    assert state.phase == Phase.PRIVATE_CHAT


@pytest.mark.asyncio
async def test_voting_timeout_records_missing_human_no_vote(mock_engine) -> None:
    state = fixed_state()
    nomination = await mock_engine.create_nomination(state, "human", "ai_1", "pressure test")
    state.phase_deadline_at = datetime.now(UTC) - timedelta(seconds=1)

    result = await mock_engine.ai_tick(state)

    assert result.ok
    assert nomination.resolved
    assert any(
        vote.nomination_id == nomination.id and vote.voter_id == "human" and not vote.vote
        for vote in state.votes
    )
