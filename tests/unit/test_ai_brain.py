from __future__ import annotations

from botc_ai.domain.ai_brain import (
    build_world_hypotheses,
    refresh_ai_brain,
    score_candidates,
)
from botc_ai.domain.models import AudienceScope
from tests.conftest import fixed_state


def test_table_notebook_contains_public_claims_without_truth() -> None:
    state = fixed_state("clockmaker", "investigator", "empath", "klutz", "scarlet_woman", "imp")
    state.ai_memories["ai_1"].known_claims["human"] = "artist"
    state.add_event(
        "1號旅人：我偏向是藝術家。",
        scope=AudienceScope.PUBLIC,
        type="public_speech",
        actor_id="human",
    )

    notebook = refresh_ai_brain(state, "ai_1")
    payload = notebook.model_dump_json()

    assert notebook.claims["human"] == "artist"
    assert "true_role" not in payload
    assert "current_demon_id" not in payload
    assert "ai_5" not in notebook.claims


def test_candidate_score_uses_public_pressure_and_memory() -> None:
    state = fixed_state()
    memory = state.ai_memories["ai_1"]
    memory.suspicion["ai_3"] = 0.93
    memory.suspicion["human"] = 0.05
    memory.known_claims["ai_3"] = "artist"
    state.add_event(
        "3號祁風今天需要被追問。",
        scope=AudienceScope.PUBLIC,
        type="public_speech",
        actor_id="human",
    )

    scores = score_candidates(state, "ai_1")

    assert scores[0].player_id == "ai_3"
    assert scores[0].nomination_score > 0.7
    assert any("懷疑值" in reason for reason in scores[0].reasons)


def test_world_hypothesis_uses_candidate_scores() -> None:
    state = fixed_state()
    state.ai_memories["ai_1"].suspicion["ai_4"] = 0.9

    scores = score_candidates(state, "ai_1")
    worlds = build_world_hypotheses(state, "ai_1", scores)

    assert worlds
    assert worlds[0].demon_candidates or worlds[0].summary
    assert worlds[0].demon_candidates[0] == "ai_4"
    assert "5號" in worlds[0].summary


def test_refresh_ai_brain_updates_isolated_memory_only() -> None:
    state = fixed_state()

    notebook = refresh_ai_brain(state, "ai_1")

    assert notebook.candidate_scores
    assert state.ai_memories["ai_1"].notebook.candidate_scores
    assert not state.ai_memories["ai_2"].notebook.candidate_scores


def test_candidate_score_marks_unspoken_players_without_judging_info() -> None:
    state = fixed_state()

    score = next(item for item in score_candidates(state, "ai_1") if item.player_id == "ai_5")

    assert not score.spoke_today
    assert score.public_speech_count == 0
    assert any("尚未發言" in reason for reason in score.reasons)
    assert not any("資訊怪" in reason or "矛盾" in reason for reason in score.reasons)


def test_candidate_score_tracks_last_public_statement() -> None:
    state = fixed_state()
    state.add_event(
        "6號許霜：我是共情者，數字是 1。",
        scope=AudienceScope.PUBLIC,
        type="public_speech",
        actor_id="ai_5",
    )

    score = next(item for item in score_candidates(state, "ai_1") if item.player_id == "ai_5")

    assert score.spoke_today
    assert score.public_speech_count == 1
    assert score.last_public_statement == "我是共情者，數字是 1。"
