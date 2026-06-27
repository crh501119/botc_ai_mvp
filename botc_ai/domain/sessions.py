from __future__ import annotations

import secrets

from botc_ai.domain.models import PlayerSession, TruthState


class SessionError(ValueError):
    pass


def claim_human_seat(state: TruthState, player_id: str, player_name: str) -> PlayerSession:
    player = state.by_id(player_id)
    if not player.is_human:
        raise SessionError("只能加入真人座位。")
    if player_id in state.player_sessions:
        raise SessionError("這個座位已經有人加入。")
    clean_name = player_name.strip()[:40] or player.name
    player.name = clean_name
    session = PlayerSession(
        player_id=player_id,
        token=secrets.token_urlsafe(32),
        claimed_name=clean_name,
    )
    state.player_sessions[player_id] = session
    return session


def authenticate_human_seat(state: TruthState, player_id: str, token: str | None) -> None:
    player = state.by_id(player_id)
    if not player.is_human:
        raise SessionError("這不是真人座位。")
    if not state.player_sessions and player_id == state.human_id:
        return
    session = state.player_sessions.get(player_id)
    if session is None:
        raise SessionError("這個座位尚未加入。")
    if token is None or not secrets.compare_digest(session.token, token):
        raise SessionError("玩家憑證無效，請重新加入座位。")


def human_seat_claimed(state: TruthState, player_id: str) -> bool:
    if not state.player_sessions and player_id == state.human_id:
        return True
    return player_id in state.player_sessions


def open_human_seats(state: TruthState) -> list[str]:
    return [
        player.id
        for player in state.players
        if player.is_human and not human_seat_claimed(state, player.id)
    ]
