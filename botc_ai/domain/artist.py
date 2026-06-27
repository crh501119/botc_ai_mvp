from __future__ import annotations

import re
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, model_validator

from botc_ai.domain.models import PlayerTruth, TruthState
from botc_ai.domain.roles import ROLE_SPECS, Alignment, RoleType


class QueryKind(StrEnum):
    PLAYER_ALIGNMENT = "PLAYER_ALIGNMENT"
    PLAYER_ROLE = "PLAYER_ROLE"
    ROLE_IN_PLAY = "ROLE_IN_PLAY"
    DEMON_IN_SET = "DEMON_IN_SET"
    MINION_IN_SET = "MINION_IN_SET"
    IS_DEMON = "IS_DEMON"
    ALIVE_EVIL_COUNT_COMPARE = "ALIVE_EVIL_COUNT_COMPARE"
    AND = "AND"
    OR = "OR"
    NOT = "NOT"


class Comparator(StrEnum):
    EQ = "EQ"
    GTE = "GTE"
    LTE = "LTE"
    GT = "GT"
    LT = "LT"


class ArtistStructuredQuestion(BaseModel):
    kind: QueryKind
    player_id: str | None = None
    player_ids: list[str] = Field(default_factory=list)
    role: str | None = None
    alignment: Alignment | None = None
    comparator: Comparator | None = None
    count: int | None = None
    children: list[Self] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.kind == QueryKind.PLAYER_ALIGNMENT and (not self.player_id or not self.alignment):
            raise ValueError("PLAYER_ALIGNMENT requires player_id and alignment")
        if self.kind == QueryKind.PLAYER_ROLE and (not self.player_id or not self.role):
            raise ValueError("PLAYER_ROLE requires player_id and role")
        if self.kind == QueryKind.ROLE_IN_PLAY and not self.role:
            raise ValueError("ROLE_IN_PLAY requires role")
        if self.kind in {QueryKind.DEMON_IN_SET, QueryKind.MINION_IN_SET} and not self.player_ids:
            raise ValueError("set query requires player_ids")
        if self.kind == QueryKind.IS_DEMON and not self.player_id:
            raise ValueError("IS_DEMON requires player_id")
        if self.kind == QueryKind.ALIVE_EVIL_COUNT_COMPARE and (
            self.comparator is None or self.count is None
        ):
            raise ValueError("ALIVE_EVIL_COUNT_COMPARE requires comparator and count")
        if self.kind in {QueryKind.AND, QueryKind.OR} and len(self.children) < 2:
            raise ValueError("AND/OR require at least two children")
        if self.kind == QueryKind.NOT and len(self.children) != 1:
            raise ValueError("NOT requires exactly one child")
        return self


class ArtistParseResult(BaseModel):
    supported: bool
    query: ArtistStructuredQuestion | None = None
    message: str = ""


def evaluate_artist_query(state: TruthState, query: ArtistStructuredQuestion) -> bool:
    if query.kind == QueryKind.PLAYER_ALIGNMENT:
        return state.by_id(query.player_id or "").alignment == query.alignment
    if query.kind == QueryKind.PLAYER_ROLE:
        return state.by_id(query.player_id or "").true_role == query.role
    if query.kind == QueryKind.ROLE_IN_PLAY:
        return any(player.true_role == query.role for player in state.players)
    if query.kind == QueryKind.DEMON_IN_SET:
        return any(
            player.id in query.player_ids and player.true_role == "imp" for player in state.players
        )
    if query.kind == QueryKind.MINION_IN_SET:
        return any(
            player.id in query.player_ids
            and ROLE_SPECS[player.true_role].role_type == RoleType.MINION
            for player in state.players
        )
    if query.kind == QueryKind.IS_DEMON:
        return state.by_id(query.player_id or "").true_role == "imp"
    if query.kind == QueryKind.ALIVE_EVIL_COUNT_COMPARE:
        alive_evil = sum(
            player.alive and player.alignment == Alignment.EVIL for player in state.players
        )
        target = query.count or 0
        return {
            Comparator.EQ: alive_evil == target,
            Comparator.GTE: alive_evil >= target,
            Comparator.LTE: alive_evil <= target,
            Comparator.GT: alive_evil > target,
            Comparator.LT: alive_evil < target,
        }[query.comparator or Comparator.EQ]
    if query.kind == QueryKind.AND:
        return all(evaluate_artist_query(state, child) for child in query.children)
    if query.kind == QueryKind.OR:
        return any(evaluate_artist_query(state, child) for child in query.children)
    if query.kind == QueryKind.NOT:
        return not evaluate_artist_query(state, query.children[0])
    raise ValueError(f"Unsupported artist query kind: {query.kind}")


def parse_artist_question(text: str, state: TruthState) -> ArtistParseResult:
    question = text.strip().lower()
    if not question:
        return ArtistParseResult(supported=False, message="問題是空的，請重新表述。")

    # Simple deterministic parser used by Mock/offline mode and tests. The OpenAI provider can
    # produce the same ArtistStructuredQuestion schema before this evaluator runs.
    if " and " in question or " 且 " in question:
        parts = re.split(r"\s+and\s+| 且 ", question, maxsplit=1)
        parsed_children = [parse_artist_question(part, state) for part in parts]
        if all(item.supported and item.query for item in parsed_children):
            return ArtistParseResult(
                supported=True,
                query=ArtistStructuredQuestion(
                    kind=QueryKind.AND,
                    children=[item.query for item in parsed_children if item.query is not None],
                ),
            )
    if " or " in question or " 或 " in question:
        parts = re.split(r"\s+or\s+| 或 ", question, maxsplit=1)
        parsed_children = [parse_artist_question(part, state) for part in parts]
        if all(item.supported and item.query for item in parsed_children):
            return ArtistParseResult(
                supported=True,
                query=ArtistStructuredQuestion(
                    kind=QueryKind.OR,
                    children=[item.query for item in parsed_children if item.query is not None],
                ),
            )
    if question.startswith("not ") or question.startswith("不是"):
        child_text = question[4:] if question.startswith("not ") else question[2:]
        parsed = parse_artist_question(child_text, state)
        if parsed.supported and parsed.query:
            return ArtistParseResult(
                supported=True,
                query=ArtistStructuredQuestion(kind=QueryKind.NOT, children=[parsed.query]),
            )

    player = _find_player(question, state)
    role = _find_role(question)
    if player and ("demon" in question or "惡魔" in question or "小惡魔" in question):
        return ArtistParseResult(
            supported=True,
            query=ArtistStructuredQuestion(kind=QueryKind.IS_DEMON, player_id=player.id),
        )
    if player and role:
        return ArtistParseResult(
            supported=True,
            query=ArtistStructuredQuestion(
                kind=QueryKind.PLAYER_ROLE, player_id=player.id, role=role
            ),
        )
    if player and ("邪惡" in question or "evil" in question):
        return ArtistParseResult(
            supported=True,
            query=ArtistStructuredQuestion(
                kind=QueryKind.PLAYER_ALIGNMENT,
                player_id=player.id,
                alignment=Alignment.EVIL,
            ),
        )
    if player and ("善良" in question or "good" in question):
        return ArtistParseResult(
            supported=True,
            query=ArtistStructuredQuestion(
                kind=QueryKind.PLAYER_ALIGNMENT,
                player_id=player.id,
                alignment=Alignment.GOOD,
            ),
        )
    if role and ("在場" in question or "in play" in question):
        return ArtistParseResult(
            supported=True,
            query=ArtistStructuredQuestion(kind=QueryKind.ROLE_IN_PLAY, role=role),
        )

    player_ids = [
        p.id for p in state.players if p.name.lower() in question or p.id.lower() in question
    ]
    if player_ids and ("惡魔在" in question or "demon in" in question):
        return ArtistParseResult(
            supported=True,
            query=ArtistStructuredQuestion(kind=QueryKind.DEMON_IN_SET, player_ids=player_ids),
        )
    if player_ids and ("爪牙在" in question or "minion in" in question):
        return ArtistParseResult(
            supported=True,
            query=ArtistStructuredQuestion(kind=QueryKind.MINION_IN_SET, player_ids=player_ids),
        )

    if "存活邪惡" in question or "alive evil" in question:
        comparator = Comparator.EQ
        if ">=" in question or "至少" in question:
            comparator = Comparator.GTE
        elif "<=" in question or "至多" in question:
            comparator = Comparator.LTE
        elif ">" in question or "超過" in question:
            comparator = Comparator.GT
        elif "<" in question or "少於" in question:
            comparator = Comparator.LT
        match = re.search(r"\d+", question)
        if match:
            return ArtistParseResult(
                supported=True,
                query=ArtistStructuredQuestion(
                    kind=QueryKind.ALIVE_EVIL_COUNT_COMPARE,
                    comparator=comparator,
                    count=int(match.group(0)),
                ),
            )
    return ArtistParseResult(
        supported=False,
        message="目前只支援玩家陣營、玩家角色、角色在場、集合內惡魔/爪牙與存活邪惡人數比較。",
    )


def _find_player(text: str, state: TruthState) -> PlayerTruth | None:
    for player in state.players:
        if player.id.lower() in text or player.name.lower() in text or str(player.seat + 1) in text:
            return player
    return None


def _find_role(text: str) -> str | None:
    for slug, spec in ROLE_SPECS.items():
        if slug in text or spec.zh_name.lower() in text:
            return slug
    return None
