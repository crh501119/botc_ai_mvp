from __future__ import annotations

from collections import Counter

from botc_ai.domain.roles import ROLE_SPECS, RoleType
from botc_ai.domain.setup import generate_game


def _counts(state):
    return Counter(ROLE_SPECS[player.true_role].role_type for player in state.players)


def test_six_player_basic_setup() -> None:
    state = generate_game(seed=2, force_minion="scarlet_woman", mock_ai=True)
    counts = _counts(state)
    assert len(state.players) == 6
    assert counts[RoleType.TOWNSFOLK] == 3
    assert counts[RoleType.OUTSIDER] == 1
    assert counts[RoleType.MINION] == 1
    assert counts[RoleType.DEMON] == 1


def test_baron_setup() -> None:
    state = generate_game(seed=1, force_minion="baron", mock_ai=True)
    counts = _counts(state)
    assert counts[RoleType.TOWNSFOLK] == 2
    assert counts[RoleType.OUTSIDER] == 2
    assert counts[RoleType.MINION] == 1
    assert any(player.true_role == "baron" for player in state.players)


def test_each_game_has_exactly_one_demon() -> None:
    state = generate_game(seed=10, mock_ai=True)
    assert sum(player.true_role == "imp" for player in state.players) == 1
    assert state.current_demon_id in {
        player.id for player in state.players if player.true_role == "imp"
    }


def test_drunk_fake_role_not_in_play() -> None:
    state = generate_game(
        seed=3,
        force_roles=["drunk", "clockmaker", "investigator", "empath", "scarlet_woman", "imp"],
        mock_ai=True,
    )
    drunk = next(player for player in state.players if player.true_role == "drunk")
    assert drunk.apparent_role is not None
    assert drunk.apparent_role not in {player.true_role for player in state.players}
    assert ROLE_SPECS[drunk.apparent_role].role_type == RoleType.TOWNSFOLK


def test_random_seed_reproducible() -> None:
    first = generate_game(seed=99, mock_ai=True)
    second = generate_game(seed=99, mock_ai=True)
    assert [(p.seat, p.true_role, p.apparent_role) for p in first.players] == [
        (p.seat, p.true_role, p.apparent_role) for p in second.players
    ]


def test_blank_seed_generates_persisted_random_seed() -> None:
    state = generate_game(seed=None, mock_ai=True)
    assert state.seed is not None
    assert state.seed > 0


def test_six_player_no_demon_minion_starting_info() -> None:
    state = generate_game(seed=4, force_minion="baron", mock_ai=True)
    private_messages = "\n".join(event.message for event in state.events if event.target_ids)
    assert "隊友" not in private_messages
    assert any(event.type == "no_starting_info" for event in state.events)
