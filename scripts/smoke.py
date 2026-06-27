from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from botc_ai.ai.provider import MockAIProvider  # noqa: E402
from botc_ai.domain.engine import GameEngine  # noqa: E402
from botc_ai.domain.setup import generate_game  # noqa: E402


async def main() -> None:
    state = generate_game(seed=1, mock_ai=True)
    await GameEngine(MockAIProvider()).auto_play(state)
    if state.result is None:
        raise SystemExit("Mock game did not finish")
    print(
        f"Mock game complete: winner={state.result.winner.value}, "
        f"reason={state.result.reason}, day={state.result.day}, calls={len(state.api_usage)}"
    )


if __name__ == "__main__":
    asyncio.run(main())
