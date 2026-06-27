from __future__ import annotations

from botc_ai.domain.context import (
    build_ai_context,
    build_game_view,
    build_private_view,
    build_public_state,
)
from botc_ai.domain.models import AudienceScope
from botc_ai.domain.sessions import SessionError, authenticate_human_seat, claim_human_seat
from botc_ai.domain.setup import generate_game
from tests.conftest import fixed_state


def test_player_view_never_contains_other_true_roles() -> None:
    state = fixed_state("clockmaker", "investigator", "empath", "klutz", "scarlet_woman", "imp")
    view = build_private_view(state, "human").model_dump()
    assert "true_role" not in str(view)
    assert "scarlet_woman" not in str(view["private_events"])
    assert "imp" not in str(view["private_events"])


def test_public_state_never_contains_hidden_alignment() -> None:
    state = fixed_state()
    public = build_public_state(state).model_dump()
    assert "alignment" not in str(public)
    assert "true_role" not in str(public)


def test_ai_context_does_not_contain_truth_state() -> None:
    state = fixed_state("clockmaker", "investigator", "empath", "klutz", "scarlet_woman", "imp")
    context = build_ai_context(state, "ai_1", purpose="test")
    assert "TruthState" not in context
    assert "true_role" not in context
    assert '"ai_5","true_role":"imp"' not in context
    assert "persona" in context
    assert "humanlike_guidance" in context
    assert "anti_echo_rules" in context


def test_ai_context_contains_public_claim_conflicts_without_truth() -> None:
    state = fixed_state()
    state.ai_memories["ai_1"].known_claims["human"] = "artist"
    state.ai_memories["ai_1"].known_claims["ai_2"] = "artist"

    context = build_ai_context(state, "ai_1", purpose="test", max_chars=12000)

    assert "claim_conflicts" in context
    assert "artist" in context
    assert "true_role" not in context


def test_private_chat_only_visible_to_participants() -> None:
    state = fixed_state()
    state.add_event(
        "A secret message",
        scope=AudienceScope.PRIVATE_CHAT_PARTICIPANTS,
        type="private_chat",
        participants=["ai_1", "ai_2"],
    )
    assert "A secret message" in str(build_private_view(state, "ai_1").private_chats)
    assert "A secret message" not in str(build_private_view(state, "human").private_chats)


def test_postgame_reveal_only_after_game_over() -> None:
    state = fixed_state()
    view = build_game_view(state, "human", dev_reveal=False)
    assert view.postgame is None
    state.result = __import__("botc_ai.domain.models", fromlist=["GameResult"]).GameResult(
        winner="good", reason="test", day=1
    )
    assert build_game_view(state, "human", dev_reveal=False).postgame is not None


def test_dev_reveal_disabled_by_default() -> None:
    state = fixed_state()
    assert build_game_view(state, "human", dev_reveal=False).dev_reveal is None
    assert build_game_view(state, "human", dev_reveal=True).dev_reveal is not None


def test_api_serialization_has_no_hidden_fields_before_postgame() -> None:
    state = fixed_state()
    payload = build_game_view(state, "human", dev_reveal=False).model_dump_json()
    assert "true_role" not in payload
    assert "STORYTELLER_INTERNAL" not in payload


def test_multiplayer_session_tokens_do_not_leak_between_players() -> None:
    state = generate_game(human_count=2, seed=101, mock_ai=True)
    first = claim_human_seat(state, "human", "甲")
    second = claim_human_seat(state, "human_2", "乙")

    payload = build_game_view(
        state, "human", dev_reveal=False, session_token=first.token
    ).model_dump_json()

    assert first.token in payload
    assert second.token not in payload
    assert "true_role" not in payload


def test_wrong_multiplayer_session_token_is_rejected() -> None:
    state = generate_game(human_count=2, seed=102, mock_ai=True)
    claim_human_seat(state, "human", "甲")
    claim_human_seat(state, "human_2", "乙")

    try:
        authenticate_human_seat(state, "human_2", "wrong-token")
    except SessionError:
        pass
    else:
        raise AssertionError("wrong token should be rejected")
