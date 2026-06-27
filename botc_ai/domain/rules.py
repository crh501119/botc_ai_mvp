from __future__ import annotations

from math import ceil

from botc_ai.domain.models import PlayerTruth, TruthState
from botc_ai.domain.roles import Alignment


def circular_distance(total_seats: int, seat_a: int, seat_b: int) -> int:
    diff = abs(seat_a - seat_b)
    return min(diff, total_seats - diff)


def nearest_living_neighbors(state: TruthState, player_id: str) -> tuple[PlayerTruth, PlayerTruth]:
    target = state.by_id(player_id)
    total = len(state.players)
    left: PlayerTruth | None = None
    right: PlayerTruth | None = None
    by_seat = sorted(state.players, key=lambda player: player.seat)
    for step in range(1, total):
        candidate = by_seat[(target.seat - step) % total]
        if candidate.alive:
            left = candidate
            break
    for step in range(1, total):
        candidate = by_seat[(target.seat + step) % total]
        if candidate.alive:
            right = candidate
            break
    if left is None or right is None:
        raise ValueError("At least two living neighbors are required")
    return left, right


def empath_count(state: TruthState, player_id: str) -> int:
    left, right = nearest_living_neighbors(state, player_id)
    return sum(neighbor.alignment == Alignment.EVIL for neighbor in (left, right))


def vote_threshold(living_count: int) -> int:
    return ceil(living_count / 2)


def seat_order(state: TruthState) -> list[PlayerTruth]:
    return sorted(state.players, key=lambda player: player.seat)
