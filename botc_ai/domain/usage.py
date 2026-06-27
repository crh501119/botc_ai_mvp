from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from botc_ai.domain.models import ApiUsageRecord, TruthState, UsageSummary

ROOT = Path(__file__).resolve().parents[2]
PRICING_PATH = ROOT / "config" / "model-pricing.json"


def load_pricing(path: Path = PRICING_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"models": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"models": {}}
    return data


def estimate_cost_usd(
    *,
    model: str,
    input_tokens: int,
    cached_input_tokens: int = 0,
    output_tokens: int = 0,
    reasoning_tokens: int = 0,
    pricing: dict[str, Any] | None = None,
) -> float | None:
    pricing = pricing or load_pricing()
    model_price = pricing.get("models", {}).get(model)
    if not model_price:
        return None
    fields = {
        "input_per_1m": input_tokens,
        "cached_input_per_1m": cached_input_tokens,
        "output_per_1m": output_tokens,
        "reasoning_per_1m": reasoning_tokens,
    }
    total = 0.0
    for price_key, tokens in fields.items():
        price = model_price.get(price_key)
        if price is None:
            return None
        total += (float(tokens) / 1_000_000.0) * float(price)
    return round(total, 8)


def summarize_usage(state: TruthState) -> UsageSummary:
    calls = len(state.api_usage)
    input_tokens = sum(record.input_tokens for record in state.api_usage)
    cached = sum(record.cached_input_tokens for record in state.api_usage)
    output_tokens = sum(record.output_tokens for record in state.api_usage)
    reasoning = sum(record.reasoning_tokens for record in state.api_usage)
    known_costs = [
        record.estimated_usd for record in state.api_usage if record.estimated_usd is not None
    ]
    estimated = round(sum(known_costs), 8) if len(known_costs) == calls else None
    remaining = None if estimated is None else round(max(state.budget_usd - estimated, 0.0), 8)
    summary = UsageSummary(
        calls=calls,
        input_tokens=input_tokens,
        cached_input_tokens=cached,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning,
        estimated_usd=estimated,
        budget_usd=state.budget_usd,
        remaining_usd=remaining,
    )
    for record in state.api_usage:
        _add_usage(summary.by_player, record.player_id or "system", record)
        _add_usage(summary.by_purpose, record.purpose, record)
    return summary


def _add_usage(
    bucket: dict[str, dict[str, float | int | None]], key: str, record: ApiUsageRecord
) -> None:
    item = bucket.setdefault(
        key,
        {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "estimated_usd": 0.0,
        },
    )
    item["calls"] = int(item["calls"] or 0) + 1
    item["input_tokens"] = int(item["input_tokens"] or 0) + record.input_tokens
    item["output_tokens"] = int(item["output_tokens"] or 0) + record.output_tokens
    item["reasoning_tokens"] = int(item["reasoning_tokens"] or 0) + record.reasoning_tokens
    if item["estimated_usd"] is None or record.estimated_usd is None:
        item["estimated_usd"] = None
    else:
        item["estimated_usd"] = round(float(item["estimated_usd"]) + record.estimated_usd, 8)


def record_usage(
    state: TruthState,
    *,
    player_id: str | None,
    model: str,
    purpose: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> ApiUsageRecord:
    cost = estimate_cost_usd(
        model=model,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
    )
    record = ApiUsageRecord(
        game_id=state.game_id,
        player_id=player_id,
        model=model,
        purpose=purpose,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        estimated_usd=cost,
    )
    state.api_usage.append(record)
    estimated = summarize_usage(state).estimated_usd
    if estimated is not None and estimated >= state.budget_usd:
        state.ai_budget_paused = True
    return record
