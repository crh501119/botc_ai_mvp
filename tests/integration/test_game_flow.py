from __future__ import annotations

from contextlib import suppress

from sqlalchemy.orm import sessionmaker

from botc_ai.ai.provider import MockAIProvider
from botc_ai.domain.context import build_postgame_reveal
from botc_ai.domain.engine import GameEngine
from botc_ai.domain.models import Phase
from botc_ai.domain.roles import Alignment
from botc_ai.domain.setup import generate_game
from botc_ai.infra.db import Base, make_engine
from botc_ai.infra.repository import GameRepository


async def _autoplay(seed: int):
    state = generate_game(seed=seed, mock_ai=True)
    await GameEngine(MockAIProvider()).auto_play(state)
    return state


async def test_mock_ai_full_game_good_win() -> None:
    state = await _autoplay(4)
    assert state.result is not None
    assert state.result.winner == Alignment.GOOD
    assert state.phase == "GAME_OVER"


async def test_mock_ai_full_game_evil_win() -> None:
    state = await _autoplay(2)
    assert state.result is not None
    assert state.result.winner == Alignment.EVIL
    assert state.phase == "GAME_OVER"


async def test_save_reload_continue(tmp_path) -> None:
    db = tmp_path / "game.sqlite3"
    engine = make_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    state = generate_game(seed=7, mock_ai=True)
    await GameEngine(MockAIProvider()).start_game(state)
    with Session() as session:
        repo = GameRepository(session)
        repo.save_state(state)
        session.commit()
    with Session() as session:
        repo = GameRepository(session)
        loaded = repo.get_state(state.game_id)
        await GameEngine(MockAIProvider()).advance_phase(loaded)
        repo.save_state(loaded)
        session.commit()
    with Session() as session:
        reloaded = GameRepository(session).get_state(state.game_id)
        assert reloaded.day == 1
        assert reloaded.phase in {"DAY_DISCUSSION", "PRIVATE_CHAT", "NOMINATIONS"}


async def test_ai_tick_autonomously_progresses_day_flow() -> None:
    state = generate_game(seed=12, mock_ai=True)
    state.ai_cooldown_seconds = 0
    engine = GameEngine(MockAIProvider())
    await engine.start_game(state)
    await engine.ai_tick(state)
    assert state.phase == Phase.DAY_DISCUSSION
    assert sum(event.type == "public_speech" for event in state.events) == 1
    assert [event.actor_id for event in state.events if event.type == "public_speech"] == ["ai_1"]
    await engine.ai_tick(state)
    assert [event.actor_id for event in state.events if event.type == "public_speech"][:2] == [
        "ai_1",
        "ai_2",
    ]
    for _ in range(11):
        if state.phase != Phase.DAY_DISCUSSION:
            break
        await engine.ai_tick(state)
    assert state.phase == Phase.PRIVATE_CHAT
    for _ in range(5):
        if state.phase != Phase.PRIVATE_CHAT:
            break
        await engine.ai_tick(state)
    assert state.phase in {Phase.NOMINATIONS, Phase.VOTING}


async def test_run_until_human_decision_stops_before_required_human_action() -> None:
    state = generate_game(seed=12, mock_ai=True)
    state.ai_cooldown_seconds = 60
    engine = GameEngine(MockAIProvider())
    await engine.start_game(state)

    result = await engine.run_until_human_decision(state, max_steps=30)

    assert result.ok
    assert state.phase in {Phase.VOTING, Phase.GAME_OVER} or state.pending_klutz_id == "human"


async def test_human_speech_gets_limited_reactive_ai_responses() -> None:
    state = generate_game(seed=14, mock_ai=True)
    engine = GameEngine(MockAIProvider())
    await engine.start_game(state)
    state.phase = Phase.DAY_DISCUSSION
    before = sum(
        event.type == "public_speech" and event.actor_id != "human" for event in state.events
    )
    result = engine.add_human_public_speech(state, "human", "我想聽林鏡和沈炬對票型的看法")
    assert result.ok
    await engine.run_reactive_discussion(
        state,
        trigger_player_id="human",
        speech="我想聽林鏡和沈炬對票型的看法",
        limit=2,
    )
    after = sum(
        event.type == "public_speech" and event.actor_id != "human" for event in state.events
    )
    assert after - before == 2
    assert state.ai_last_status


async def test_ai_tick_respects_cooldown() -> None:
    state = generate_game(seed=13, mock_ai=True)
    state.ai_cooldown_seconds = 60
    engine = GameEngine(MockAIProvider())
    await engine.start_game(state)
    await engine.ai_tick(state)
    event_count = len(state.events)
    phase = state.phase
    result = await engine.ai_tick(state)
    assert result.message == "AI 冷卻中。"
    assert len(state.events) == event_count
    assert state.phase == phase


async def test_api_failure_after_recovery() -> None:
    class FailsOnce(MockAIProvider):
        def __init__(self) -> None:
            self.failed = False

        async def public_speech(self, state, player_id):
            if not self.failed:
                self.failed = True
                raise RuntimeError("temporary")
            return await super().public_speech(state, player_id)

    state = generate_game(seed=8, mock_ai=True)
    engine = GameEngine(FailsOnce())
    await engine.start_game(state)
    with suppress(RuntimeError):
        await engine.run_public_discussion(state, rounds=1)
    await engine.run_public_discussion(state, rounds=1)
    assert any(event.type == "public_speech" for event in state.events)


async def test_transcript_export_correct() -> None:
    state = await _autoplay(1)
    reveal = build_postgame_reveal(state)
    assert len(reveal.players) == 6
    assert any(event.type == "game_over" for event in reveal.all_events)
