from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from botc_ai.ai.provider import MockAIProvider, OpenAIProvider
from botc_ai.api.schemas import (
    ActionResponse,
    ArtistQuestionRequest,
    BudgetUpdateRequest,
    ChambermaidChoiceRequest,
    CreateGameRequest,
    JoinGameRequest,
    KlutzChoiceRequest,
    NightTargetRequest,
    NominationRequest,
    PhaseReadyRequest,
    PrivateChatRequest,
    PublicSpeechRequest,
    VoteRequest,
)
from botc_ai.domain.context import build_game_view, build_postgame_reveal
from botc_ai.domain.engine import GameEngine
from botc_ai.domain.models import GameView, Phase
from botc_ai.domain.sessions import (
    SessionError,
    all_human_seats_claimed,
    authenticate_human_seat,
    claim_human_seat,
    human_seat_claimed,
    open_human_seats,
)
from botc_ai.domain.setup import AI_PERSONAS, generate_game
from botc_ai.infra.db import SessionLocal, init_db
from botc_ai.infra.repository import GameRepository
from botc_ai.settings import Settings, get_settings

_GAME_ACTION_LOCKS: dict[str, asyncio.Lock] = {}


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def make_provider(settings: Settings, *, mock_ai: bool) -> Any:
    if mock_ai or not settings.openai_api_key:
        return MockAIProvider()
    return OpenAIProvider(
        api_key=settings.openai_api_key,
        dialogue_model=settings.ai_dialogue_model,
        decision_model=settings.ai_decision_model,
        store=settings.openai_store,
    )


def make_engine_for_state(settings: Settings, mock_ai: bool) -> GameEngine:
    return GameEngine(make_provider(settings, mock_ai=mock_ai))


def openai_sdk_version() -> str | None:
    try:
        return version("openai")
    except PackageNotFoundError:
        return None


def create_app() -> FastAPI:
    settings = get_settings()
    init_db()
    app = FastAPI(title="BOTC AI MVP", version="0.1.0")
    origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict[str, str | bool]:
        return {"ok": True, "mock_ai_default": settings.mock_ai}

    @app.get("/api/config")
    def config() -> dict[str, object]:
        return {
            "dialogue_model": settings.ai_dialogue_model,
            "decision_model": settings.ai_decision_model,
            "mock_ai": settings.mock_ai,
            "openai_configured": bool(settings.openai_api_key),
            "openai_sdk_version": openai_sdk_version(),
            "budget_usd": settings.game_budget_usd,
            "dev_reveal": settings.dev_reveal,
            "personas": [persona.__dict__ for persona in AI_PERSONAS],
        }

    @app.post("/api/games", response_model=GameView)
    async def create_game(
        request: CreateGameRequest,
        session: Session = Depends(get_db),
    ) -> GameView:
        mock_ai = settings.mock_ai if request.mock_ai is None else request.mock_ai
        state = generate_game(
            human_name=request.human_name,
            human_count=request.human_count,
            discussion_mode=request.discussion_mode,
            shuffle_seats_on_start=request.shuffle_seats_on_start,
            seed=request.seed,
            force_minion=request.force_minion,
            budget_usd=request.budget_usd,
            mock_ai=mock_ai,
            night_seconds=request.night_seconds,
            day_discussion_seconds=request.day_discussion_seconds,
            private_chat_seconds=request.private_chat_seconds,
            nominations_seconds=request.nominations_seconds,
            voting_seconds=request.voting_seconds,
        )
        session_claim = claim_human_seat(state, "human", request.human_name)
        GameRepository(session).save_state(state)
        session.commit()
        return build_game_view(
            state,
            "human",
            dev_reveal=settings.dev_reveal,
            session_token=session_claim.token,
        )

    @app.get("/api/games")
    def list_games(session: Session = Depends(get_db)) -> list[dict[str, object]]:
        games = GameRepository(session).list_games()
        return [
            {
                "id": game.id,
                "day": game.day,
                "phase": game.phase,
                "seed": game.seed,
                "updated_at": game.updated_at.isoformat() if game.updated_at else None,
                "mock_ai": game.mock_ai,
            }
            for game in games
        ]

    @app.get("/api/games/{game_id}/lobby")
    def game_lobby(game_id: str, session: Session = Depends(get_db)) -> dict[str, object]:
        state = _load(session, game_id)
        return {
            "game_id": state.game_id,
            "day": state.day,
            "phase": state.phase,
            "mock_ai": state.mock_ai,
            "discussion_mode": state.discussion_mode,
            "host_player_id": state.host_player_id,
            "human_seats_ready": all_human_seats_claimed(state),
            "open_human_seats": open_human_seats(state),
            "players": [
                {
                    "id": player.id,
                    "name": player.name,
                    "seat": player.seat,
                    "is_human": player.is_human,
                    "alive": player.alive,
                    "claimed": human_seat_claimed(state, player.id) if player.is_human else True,
                }
                for player in sorted(state.players, key=lambda item: item.seat)
            ],
        }

    @app.post("/api/games/{game_id}/join", response_model=GameView)
    def join_game(
        game_id: str,
        request: JoinGameRequest,
        session: Session = Depends(get_db),
    ) -> GameView:
        state = _load(session, game_id)
        if request.token:
            _require_human_session(state, request.player_id, request.token)
            token = request.token
        else:
            if state.phase != Phase.SETUP:
                raise HTTPException(status_code=403, detail="遊戲已開始，不能再認領新座位。")
            try:
                token = claim_human_seat(state, request.player_id, request.player_name).token
            except SessionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
        GameRepository(session).save_state(state)
        session.commit()
        return build_game_view(
            state,
            request.player_id,
            dev_reveal=settings.dev_reveal,
            session_token=token,
        )

    @app.post("/api/games/{game_id}/start", response_model=GameView)
    async def start_game(
        game_id: str,
        player_id: str = Query(default="human"),
        player_token: str | None = Header(default=None, alias="X-Player-Token"),
        session: Session = Depends(get_db),
    ) -> GameView:
        state = _load(session, game_id)
        _require_human_session(state, player_id, player_token)
        _require_host(state, player_id)
        if not all_human_seats_claimed(state):
            raise HTTPException(status_code=400, detail="所有真人玩家入座後才能開始。")
        engine = make_engine_for_state(settings, mock_ai=state.mock_ai)
        await engine.start_game(state)
        await engine.advance_phase(state)
        GameRepository(session).save_state(state)
        session.commit()
        return build_game_view(
            state, player_id, dev_reveal=settings.dev_reveal, session_token=player_token
        )

    @app.get("/api/games/{game_id}", response_model=GameView)
    def get_game(
        game_id: str,
        player_id: str = Query(default="human"),
        player_token: str | None = Header(default=None, alias="X-Player-Token"),
        session: Session = Depends(get_db),
    ) -> GameView:
        state = _load(session, game_id)
        _require_human_session(state, player_id, player_token)
        return build_game_view(
            state, player_id, dev_reveal=settings.dev_reveal, session_token=player_token
        )

    @app.post("/api/games/{game_id}/advance", response_model=GameView)
    async def advance_game(
        game_id: str,
        player_id: str = Query(default="human"),
        player_token: str | None = Header(default=None, alias="X-Player-Token"),
        session: Session = Depends(get_db),
    ) -> GameView:
        state = _load(session, game_id)
        _require_human_session(state, player_id, player_token)
        engine = make_engine_for_state(settings, mock_ai=state.mock_ai)
        await engine.advance_phase(state)
        GameRepository(session).save_state(state)
        session.commit()
        return build_game_view(
            state, player_id, dev_reveal=settings.dev_reveal, session_token=player_token
        )

    @app.post("/api/games/{game_id}/ai-tick", response_model=GameView)
    async def ai_tick(
        game_id: str,
        player_id: str = Query(default="human"),
        player_token: str | None = Header(default=None, alias="X-Player-Token"),
        session: Session = Depends(get_db),
    ) -> GameView:
        async with _game_action_lock(game_id):
            state = _load(session, game_id)
            _require_human_session(state, player_id, player_token)
            engine = make_engine_for_state(settings, mock_ai=state.mock_ai)
            await engine.ai_tick(state)
            GameRepository(session).save_state(state)
            session.commit()
        return build_game_view(
            state, player_id, dev_reveal=settings.dev_reveal, session_token=player_token
        )

    @app.post("/api/games/{game_id}/ai-until-human", response_model=GameView)
    async def ai_until_human(
        game_id: str,
        player_id: str = Query(default="human"),
        player_token: str | None = Header(default=None, alias="X-Player-Token"),
        session: Session = Depends(get_db),
    ) -> GameView:
        async with _game_action_lock(game_id):
            state = _load(session, game_id)
            _require_human_session(state, player_id, player_token)
            engine = make_engine_for_state(settings, mock_ai=state.mock_ai)
            await engine.run_until_human_decision(state)
            GameRepository(session).save_state(state)
            session.commit()
        return build_game_view(
            state, player_id, dev_reveal=settings.dev_reveal, session_token=player_token
        )

    @app.post("/api/games/{game_id}/auto-play", response_model=GameView)
    async def auto_play(
        game_id: str,
        player_id: str = Query(default="human"),
        player_token: str | None = Header(default=None, alias="X-Player-Token"),
        session: Session = Depends(get_db),
    ) -> GameView:
        state = _load(session, game_id)
        _require_human_session(state, player_id, player_token)
        if not settings.dev_reveal:
            raise HTTPException(status_code=403, detail="開發者模式未啟用。")
        engine = GameEngine(MockAIProvider())
        await engine.auto_play(state)
        GameRepository(session).save_state(state)
        session.commit()
        return build_game_view(
            state, player_id, dev_reveal=settings.dev_reveal, session_token=player_token
        )

    @app.post("/api/games/{game_id}/speech", response_model=GameView)
    async def public_speech(
        game_id: str,
        request: PublicSpeechRequest,
        player_token: str | None = Header(default=None, alias="X-Player-Token"),
        session: Session = Depends(get_db),
    ) -> GameView:
        state = _load(session, game_id)
        _require_human_session(state, request.player_id, player_token)
        engine = make_engine_for_state(settings, mock_ai=state.mock_ai)
        if state.result is None and state.phase == Phase.DAWN:
            await engine.advance_phase(state)
        result = engine.add_human_public_speech(state, request.player_id, request.speech)
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.message)
        state.last_ai_tick_at = datetime.now(UTC)
        if state.result is None and state.phase in {Phase.DAY_DISCUSSION, Phase.NOMINATIONS}:
            await engine.run_reactive_discussion(
                state,
                trigger_player_id=request.player_id,
                speech=request.speech,
                limit=_reactive_limit_for_speech(request.speech),
            )
        GameRepository(session).save_state(state)
        session.commit()
        return build_game_view(
            state, request.player_id, dev_reveal=settings.dev_reveal, session_token=player_token
        )

    @app.post("/api/games/{game_id}/speech-skip", response_model=GameView)
    async def skip_speech(
        game_id: str,
        player_id: str = Query(default="human"),
        player_token: str | None = Header(default=None, alias="X-Player-Token"),
        session: Session = Depends(get_db),
    ) -> GameView:
        state = _load(session, game_id)
        _require_human_session(state, player_id, player_token)
        engine = make_engine_for_state(settings, mock_ai=state.mock_ai)
        result = engine.skip_ordered_speech(state, player_id)
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.message)
        GameRepository(session).save_state(state)
        session.commit()
        return build_game_view(
            state, player_id, dev_reveal=settings.dev_reveal, session_token=player_token
        )

    @app.post("/api/games/{game_id}/night-target", response_model=GameView)
    async def night_target(
        game_id: str,
        request: NightTargetRequest,
        player_token: str | None = Header(default=None, alias="X-Player-Token"),
        session: Session = Depends(get_db),
    ) -> GameView:
        state = _load(session, game_id)
        _require_human_session(state, request.player_id, player_token)
        engine = make_engine_for_state(settings, mock_ai=state.mock_ai)
        result = await engine.submit_night_target(state, request.player_id, request.target_id)
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.message)
        GameRepository(session).save_state(state)
        session.commit()
        return build_game_view(
            state, request.player_id, dev_reveal=settings.dev_reveal, session_token=player_token
        )

    @app.post("/api/games/{game_id}/chambermaid-choice", response_model=GameView)
    async def chambermaid_choice(
        game_id: str,
        request: ChambermaidChoiceRequest,
        player_token: str | None = Header(default=None, alias="X-Player-Token"),
        session: Session = Depends(get_db),
    ) -> GameView:
        state = _load(session, game_id)
        _require_human_session(state, request.player_id, player_token)
        engine = make_engine_for_state(settings, mock_ai=state.mock_ai)
        result = await engine.submit_chambermaid_choice(
            state, request.player_id, request.target_ids
        )
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.message)
        GameRepository(session).save_state(state)
        session.commit()
        return build_game_view(
            state, request.player_id, dev_reveal=settings.dev_reveal, session_token=player_token
        )

    @app.post("/api/games/{game_id}/phase-ready", response_model=GameView)
    async def phase_ready(
        game_id: str,
        request: PhaseReadyRequest,
        player_token: str | None = Header(default=None, alias="X-Player-Token"),
        session: Session = Depends(get_db),
    ) -> GameView:
        state = _load(session, game_id)
        _require_human_session(state, request.player_id, player_token)
        engine = make_engine_for_state(settings, mock_ai=state.mock_ai)
        result = await engine.mark_phase_ready(state, request.player_id)
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.message)
        GameRepository(session).save_state(state)
        session.commit()
        return build_game_view(
            state, request.player_id, dev_reveal=settings.dev_reveal, session_token=player_token
        )

    @app.post("/api/games/{game_id}/private-chat", response_model=GameView)
    async def private_chat(
        game_id: str,
        request: PrivateChatRequest,
        player_token: str | None = Header(default=None, alias="X-Player-Token"),
        session: Session = Depends(get_db),
    ) -> GameView:
        state = _load(session, game_id)
        _require_human_session(state, request.from_id, player_token)
        engine = make_engine_for_state(settings, mock_ai=state.mock_ai)
        result = await engine.add_private_chat(
            state, request.from_id, request.to_id, request.message
        )
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.message)
        GameRepository(session).save_state(state)
        session.commit()
        return build_game_view(
            state, request.from_id, dev_reveal=settings.dev_reveal, session_token=player_token
        )

    @app.post("/api/games/{game_id}/nominations", response_model=GameView)
    async def nominate(
        game_id: str,
        request: NominationRequest,
        player_token: str | None = Header(default=None, alias="X-Player-Token"),
        session: Session = Depends(get_db),
    ) -> GameView:
        async with _game_action_lock(game_id):
            state = _load(session, game_id)
            _require_human_session(state, request.nominator_id, player_token)
            engine = make_engine_for_state(settings, mock_ai=state.mock_ai)
            try:
                await engine.create_nomination(
                    state, request.nominator_id, request.nominee_id, request.reason
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            GameRepository(session).save_state(state)
            session.commit()
        return build_game_view(
            state,
            request.nominator_id,
            dev_reveal=settings.dev_reveal,
            session_token=player_token,
        )

    @app.post("/api/games/{game_id}/vote", response_model=GameView)
    async def vote(
        game_id: str,
        request: VoteRequest,
        player_token: str | None = Header(default=None, alias="X-Player-Token"),
        session: Session = Depends(get_db),
    ) -> GameView:
        state = _load(session, game_id)
        _require_human_session(state, request.player_id, player_token)
        engine = make_engine_for_state(settings, mock_ai=state.mock_ai)
        try:
            await engine.cast_human_vote(state, request.player_id, vote=request.vote)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        GameRepository(session).save_state(state)
        session.commit()
        return build_game_view(
            state, request.player_id, dev_reveal=settings.dev_reveal, session_token=player_token
        )

    @app.post("/api/games/{game_id}/artist", response_model=ActionResponse)
    async def artist(
        game_id: str,
        request: ArtistQuestionRequest,
        player_token: str | None = Header(default=None, alias="X-Player-Token"),
        session: Session = Depends(get_db),
    ) -> ActionResponse:
        state = _load(session, game_id)
        _require_human_session(state, request.player_id, player_token)
        engine = make_engine_for_state(settings, mock_ai=state.mock_ai)
        result = await engine.artist_question(state, request.player_id, request.question)
        GameRepository(session).save_state(state)
        session.commit()
        return ActionResponse(ok=result.ok, message=result.message)

    @app.post("/api/games/{game_id}/klutz", response_model=GameView)
    async def klutz(
        game_id: str,
        request: KlutzChoiceRequest,
        player_token: str | None = Header(default=None, alias="X-Player-Token"),
        session: Session = Depends(get_db),
    ) -> GameView:
        state = _load(session, game_id)
        _require_human_session(state, request.player_id, player_token)
        engine = make_engine_for_state(settings, mock_ai=state.mock_ai)
        result = await engine.choose_klutz(state, request.player_id, request.target_id)
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.message)
        GameRepository(session).save_state(state)
        session.commit()
        return build_game_view(
            state, request.player_id, dev_reveal=settings.dev_reveal, session_token=player_token
        )

    @app.post("/api/games/{game_id}/budget", response_model=GameView)
    def update_budget(
        game_id: str,
        request: BudgetUpdateRequest,
        player_id: str = Query(default="human"),
        player_token: str | None = Header(default=None, alias="X-Player-Token"),
        session: Session = Depends(get_db),
    ) -> GameView:
        state = _load(session, game_id)
        _require_human_session(state, player_id, player_token)
        state.budget_usd = request.budget_usd
        if request.mock_ai is not None:
            state.mock_ai = request.mock_ai
        if request.budget_usd > 0:
            state.ai_budget_paused = False
        GameRepository(session).save_state(state)
        session.commit()
        return build_game_view(
            state, player_id, dev_reveal=settings.dev_reveal, session_token=player_token
        )

    @app.get("/api/games/{game_id}/export.json")
    def export_json(game_id: str, session: Session = Depends(get_db)) -> dict[str, object]:
        state = _load(session, game_id)
        if state.result is None:
            raise HTTPException(status_code=403, detail="遊戲結束後才能匯出完整 transcript。")
        return build_postgame_reveal(state).model_dump(mode="json")

    @app.get("/api/games/{game_id}/export.md", response_class=PlainTextResponse)
    def export_markdown(game_id: str, session: Session = Depends(get_db)) -> str:
        state = _load(session, game_id)
        if state.result is None:
            raise HTTPException(status_code=403, detail="遊戲結束後才能匯出完整 transcript。")
        reveal = build_postgame_reveal(state)
        lines = [
            "# No Greater Joy Transcript",
            "",
            f"- Game: {state.game_id}",
            f"- Winner: {state.result.winner.value if state.result else 'unknown'}",
            f"- Reason: {state.result.reason if state.result else ''}",
            "",
            "## Players",
        ]
        for player in reveal.players:
            lines.append(f"- {player['name']}: {player['true_role_zh']} ({player['alignment']})")
        lines.extend(["", "## Timeline"])
        for event in reveal.all_events:
            lines.append(f"- Day {event.day} [{event.scope.value}] {event.message}")
        return "\n".join(lines)

    @app.delete("/api/games/{game_id}", response_model=ActionResponse)
    def delete_game(
        game_id: str,
        player_id: str = Query(default="human"),
        player_token: str | None = Header(default=None, alias="X-Player-Token"),
        session: Session = Depends(get_db),
    ) -> ActionResponse:
        state = _load(session, game_id)
        _require_human_session(state, player_id, player_token)
        _require_host(state, player_id)
        GameRepository(session).delete_game(game_id)
        session.commit()
        return ActionResponse(ok=True, message="遊戲紀錄已刪除。")

    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(frontend_dist / "index.html")

    return app


def _load(session: Session, game_id: str) -> Any:
    try:
        return GameRepository(session).get_state(game_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="找不到遊戲。") from exc


def _require_human_session(state: Any, player_id: str, token: str | None) -> None:
    try:
        authenticate_human_seat(state, player_id, token)
    except (KeyError, SessionError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _require_host(state: Any, player_id: str) -> None:
    if player_id != state.host_player_id:
        raise HTTPException(status_code=403, detail="只有房主可以執行這個動作。")


def _game_action_lock(game_id: str) -> asyncio.Lock:
    lock = _GAME_ACTION_LOCKS.get(game_id)
    if lock is None:
        lock = asyncio.Lock()
        _GAME_ACTION_LOCKS[game_id] = lock
    return lock


def _reactive_limit_for_speech(speech: str) -> int:
    del speech
    return 1


app = create_app()
