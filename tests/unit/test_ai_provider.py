from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from botc_ai.ai.provider import (
    AIProviderError,
    MockAIProvider,
    OpenAIProvider,
    _apply_memory_update,
    _safe_fallback_action,
)
from botc_ai.ai.schemas import (
    AIMemoryUpdate,
    ChambermaidChoice,
    NightTargetAction,
    PublicSpeechAction,
)
from botc_ai.domain.models import AudienceScope
from botc_ai.domain.usage import record_usage
from tests.conftest import fixed_state


def test_structured_output_validation() -> None:
    action = NightTargetAction.model_validate({"target_id": "ai_1"})
    assert action.target_id == "ai_1"
    with pytest.raises(ValidationError):
        ChambermaidChoice.model_validate({"target_ids": ["ai_1"]})


@pytest.mark.asyncio
async def test_invalid_player_id_rejected(mock_engine) -> None:
    state = fixed_state()
    with pytest.raises(KeyError):
        state.by_id("missing")


@pytest.mark.asyncio
async def test_illegal_action_rejected(mock_engine) -> None:
    state = fixed_state()
    state.by_id("human").alive = False
    with pytest.raises(ValueError):
        await mock_engine.create_nomination(state, "human", "ai_1", "illegal")


@pytest.mark.asyncio
async def test_retry_after_fallback_uses_mock_safely() -> None:
    state = fixed_state()
    provider = MockAIProvider()
    action = await provider.night_target(state, "ai_5", ["human"])
    assert action.target_id == "human"


@pytest.mark.asyncio
async def test_mock_night_target_uses_memory_not_hidden_alignment() -> None:
    state = fixed_state()
    provider = MockAIProvider()
    memory = state.ai_memories["ai_5"]
    memory.suspicion["human"] = 0.05
    memory.suspicion["ai_4"] = 0.95

    action = await provider.night_target(state, "ai_5", ["human", "ai_4"])

    assert action.target_id == "ai_4"


def test_five_ai_memory_fully_separate() -> None:
    state = fixed_state()
    assert len(state.ai_memories) == 5
    ids = {id(memory) for memory in state.ai_memories.values()}
    assert len(ids) == 5
    state.ai_memories["ai_1"].summary = "only ai_1"
    assert all(
        memory.summary != "only ai_1" for key, memory in state.ai_memories.items() if key != "ai_1"
    )


@pytest.mark.asyncio
async def test_mock_ai_provider_does_not_call_network() -> None:
    state = fixed_state()
    provider = MockAIProvider()
    speech = await provider.public_speech(state, "ai_1")
    assert speech.speech
    assert state.api_usage[-1].model == "mock"


@pytest.mark.asyncio
async def test_mock_info_role_volunteers_real_table_information(mock_engine) -> None:
    state = fixed_state("sage", "clockmaker", "empath", "klutz", "scarlet_woman", "imp")
    mock_engine._resolve_clockmaker(state, "ai_1")

    speech = await MockAIProvider().public_speech(state, "ai_1")

    assert "鐘錶匠" in speech.speech
    assert "步數" in speech.speech
    assert speech.claimed_role == "clockmaker"


@pytest.mark.asyncio
async def test_mock_ai_opening_avoids_template_dogpile_on_human() -> None:
    state = fixed_state(seed=21)
    provider = MockAIProvider()
    speeches: list[str] = []
    for player_id in ["ai_1", "ai_2", "ai_3", "ai_4", "ai_5"]:
        action = await provider.public_speech(state, player_id)
        speeches.append(action.speech)
        state.add_event(
            f"{state.by_id(player_id).name}：{action.speech}",
            scope=AudienceScope.PUBLIC,
            type="public_speech",
            actor_id=player_id,
        )
    assert sum("旅人" in speech for speech in speeches) <= 2
    assert all("這會影響我的提名門檻" not in speech for speech in speeches)
    assert len(set(speeches)) >= 4


@pytest.mark.asyncio
async def test_mock_vote_reasons_vary_by_persona() -> None:
    state = fixed_state(seed=21)
    provider = MockAIProvider()

    reasons = [
        (await provider.vote(state, player_id, "human", "測試", "辯護")).public_reason
        for player_id in ["ai_1", "ai_2", "ai_3", "ai_4", "ai_5"]
    ]

    assert len(set(reasons)) >= 4
    assert "票數和理由還不夠。" not in reasons
    assert "依目前矛盾與票型，我選擇支持。" not in reasons


@pytest.mark.asyncio
async def test_mock_nomination_reason_is_not_fixed_template() -> None:
    state = fixed_state(seed=1)
    provider = MockAIProvider()

    action = await provider.nominate(state, "ai_3", ["human", "ai_1"])

    assert action.nominate
    assert action.reason != "需要用提名測試 林鏡 的說法、反應與票型。"


def test_budget_reached_stops_new_calls() -> None:
    state = fixed_state()
    state.budget_usd = 0.000001
    record_usage(
        state,
        player_id="ai_1",
        model="test-priced-model",
        purpose="test",
        input_tokens=2_000_000,
        output_tokens=0,
    )
    assert state.ai_budget_paused


def test_openai_memory_update_is_validated_and_isolated() -> None:
    state = fixed_state()
    _apply_memory_update(
        state,
        "ai_1",
        AIMemoryUpdate(
            suspicion_delta={"ai_2": 4.0, "missing": 1.0, "ai_1": -1.0},
            known_claims={"ai_2": "artist", "ai_3": "imp", "missing": "clockmaker"},
            public_claim="clockmaker",
            current_bluff="sage",
            next_intent="壓力測試 ai_2",
            summary="ai_2 的說法需要查證。",
        ),
    )
    memory = state.ai_memories["ai_1"]
    assert memory.suspicion["ai_2"] == 0.85
    assert "missing" not in memory.suspicion
    assert memory.known_claims["ai_2"] == "artist"
    assert memory.public_claim == "clockmaker"
    assert memory.current_bluff == "sage"
    assert "查證" in memory.summary


def test_openai_provider_safe_fallback_public_speech() -> None:
    state = fixed_state()
    action = _safe_fallback_action(state, "ai_1", "public_speech", PublicSpeechAction)
    assert isinstance(action, PublicSpeechAction)
    assert action.speech


@pytest.mark.asyncio
async def test_openai_failure_records_visible_diagnostic_and_failed_usage() -> None:
    class FailingOpenAIProvider(OpenAIProvider):
        def __post_init__(self) -> None:
            self.client = None

        async def _responses_parse(self, prompt, schema, model):  # type: ignore[no-untyped-def]
            raise AIProviderError(f"model_not_found: The model {model} does not exist")

    state = fixed_state()
    provider = FailingOpenAIProvider(
        api_key="test",
        dialogue_model="missing-dialogue-model",
        decision_model="missing-decision-model",
    )

    action = await provider.public_speech(state, "ai_1")

    assert action.speech == "我先保留一下，等下一輪再把判斷講清楚。"
    assert state.api_usage[-1].purpose.startswith("failed:public_speech")
    assert state.api_usage[-1].input_tokens == 0
    assert "模型 missing-dialogue-model 不存在" in state.ai_last_status
    assert any(event.type == "ai_api_error" for event in state.events)


@pytest.mark.asyncio
async def test_openai_responses_parse_helper_returns_pydantic_action() -> None:
    class FakeResponses:
        def __init__(self) -> None:
            self.parse_calls = 0
            self.create_calls = 0

        async def parse(self, **kwargs):  # type: ignore[no-untyped-def]
            self.parse_calls += 1
            assert kwargs["text_format"] is PublicSpeechAction
            return SimpleNamespace(
                output_parsed=PublicSpeechAction(speech="我先聽一輪，再對票型下判斷。")
            )

        async def create(self, **kwargs):  # type: ignore[no-untyped-def]
            self.create_calls += 1
            raise AssertionError("JSON mode fallback should not be used")

    class FakeClient:
        def __init__(self) -> None:
            self.responses = FakeResponses()

    class Provider(OpenAIProvider):
        def __post_init__(self) -> None:
            self.client = FakeClient()

    provider = Provider(api_key="test", dialogue_model="test-model", decision_model="test-model")

    action = await provider._responses_parse("公開發言", PublicSpeechAction, "test-model")

    assert action.speech == "我先聽一輪，再對票型下判斷。"
    assert provider.client.responses.parse_calls == 1
    assert provider.client.responses.create_calls == 0


@pytest.mark.asyncio
async def test_openai_structured_output_schema_error_falls_back_to_json_mode() -> None:
    class FakeResponses:
        def __init__(self) -> None:
            self.parse_calls = 0
            self.create_calls = 0

        async def parse(self, **kwargs):  # type: ignore[no-untyped-def]
            self.parse_calls += 1
            raise RuntimeError("Invalid schema for response_format json_schema")

        async def create(self, **kwargs):  # type: ignore[no-untyped-def]
            self.create_calls += 1
            assert kwargs["text"]["format"]["type"] == "json_object"
            return SimpleNamespace(
                output_text=json.dumps(
                    {
                        "speech": "我不會急著跳角色，先看誰在硬推。",
                        "claimed_role": None,
                        "concise_rationale": "schema fallback",
                        "memory_update": {},
                    },
                    ensure_ascii=False,
                )
            )

    class FakeClient:
        def __init__(self) -> None:
            self.responses = FakeResponses()

    class Provider(OpenAIProvider):
        def __post_init__(self) -> None:
            self.client = FakeClient()

    provider = Provider(api_key="test", dialogue_model="test-model", decision_model="test-model")

    action = await provider._responses_parse("公開發言", PublicSpeechAction, "test-model")

    assert action.speech == "我不會急著跳角色，先看誰在硬推。"
    assert provider.client.responses.parse_calls == 1
    assert provider.client.responses.create_calls == 1
