from __future__ import annotations

import pytest

from botc_ai.ai.provider import MockAIProvider
from botc_ai.domain.engine import GameEngine
from botc_ai.domain.setup import generate_game


@pytest.fixture
def mock_engine() -> GameEngine:
    return GameEngine(MockAIProvider())


def fixed_state(*roles: str, seed: int = 7):
    if not roles:
        roles = ("clockmaker", "investigator", "empath", "klutz", "scarlet_woman", "imp")
    return generate_game(seed=seed, force_roles=list(roles), mock_ai=True)
