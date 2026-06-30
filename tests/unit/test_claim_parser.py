from __future__ import annotations

from botc_ai.domain.ai_brain import refresh_ai_brain
from botc_ai.domain.claim_parser import parse_public_claims, public_role_claims_from_events
from botc_ai.domain.models import AudienceScope
from tests.conftest import fixed_state


def test_parse_investigator_two_candidate_minion_claim() -> None:
    state = fixed_state()
    state.add_event(
        "1號 旅人：我是調查員，2、6 有一個紅唇女郎。",
        scope=AudienceScope.PUBLIC,
        type="public_speech",
        actor_id="human",
    )

    claim = parse_public_claims(state)[0]

    assert claim.claimed_role == "investigator"
    assert claim.claim_type == "investigator_two_candidates_one_minion"
    assert claim.target_ids == ["ai_1", "ai_5"]
    assert claim.target_seats == [2, 6]
    assert claim.result_role == "scarlet_woman"
    assert claim.is_complete_format
    assert any("不要追問調查員" in item for item in claim.invalid_followups)


def test_parse_clockmaker_distance_claim() -> None:
    state = fixed_state()
    state.add_event(
        "1號 旅人：我先半開：我是鐘錶匠，昨夜拿到的數字是 2。",
        scope=AudienceScope.PUBLIC,
        type="public_speech",
        actor_id="human",
    )

    claim = parse_public_claims(state)[0]

    assert claim.claimed_role == "clockmaker"
    assert claim.claim_type == "clockmaker_distance"
    assert claim.number == 2
    assert claim.target_ids == []
    assert claim.is_complete_format


def test_parse_empath_neighbor_count_claim() -> None:
    state = fixed_state()
    state.add_event(
        "5號 許霜：我是共情者，昨晚 3號旅人和 5號林鏡之間有 2 名邪惡。",
        scope=AudienceScope.PUBLIC,
        type="public_speech",
        actor_id="ai_4",
    )

    claim = parse_public_claims(state)[0]

    assert claim.claimed_role == "empath"
    assert claim.claim_type == "empath_alive_neighbor_evil_count"
    assert claim.target_seats == [3, 5]
    assert claim.number == 2
    assert claim.is_complete_format


def test_parse_chambermaid_woke_count_claim() -> None:
    state = fixed_state()
    state.add_event(
        "2號 林鏡：我是侍女，查 2、4 得 1。",
        scope=AudienceScope.PUBLIC,
        type="public_speech",
        actor_id="ai_1",
    )

    claim = parse_public_claims(state)[0]

    assert claim.claimed_role == "chambermaid"
    assert claim.claim_type == "chambermaid_two_targets_woke_count"
    assert claim.target_seats == [2, 4]
    assert claim.number == 1
    assert claim.is_complete_format


def test_parse_artist_role_claim_without_forcing_info() -> None:
    state = fixed_state()
    state.add_event(
        "6號 祁風：我今天只先半開自己是藝術家，能力還沒問。",
        scope=AudienceScope.PUBLIC,
        type="public_speech",
        actor_id="ai_5",
    )

    claim = parse_public_claims(state)[0]

    assert claim.claimed_role == "artist"
    assert claim.claim_type == "role_claim"
    assert claim.answer is None
    assert claim.is_complete_format


def test_public_role_claims_feed_table_notebook_without_truth() -> None:
    state = fixed_state()
    state.add_event(
        "1號 旅人：我是調查員，2、6 有一個紅唇女郎。",
        scope=AudienceScope.PUBLIC,
        type="public_speech",
        actor_id="human",
    )

    role_claims = public_role_claims_from_events(state)
    notebook = refresh_ai_brain(state, "ai_1")
    payload = notebook.model_dump_json()

    assert role_claims["human"] == "investigator"
    assert notebook.claims["human"] == "investigator"
    assert notebook.parsed_claims[0].claim_type == "investigator_two_candidates_one_minion"
    assert notebook.claim_warnings
    assert "true_role" not in payload
    assert "current_demon_id" not in payload
