from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class Alignment(StrEnum):
    GOOD = "good"
    EVIL = "evil"


class RoleType(StrEnum):
    TOWNSFOLK = "townsfolk"
    OUTSIDER = "outsider"
    MINION = "minion"
    DEMON = "demon"


class RoleSpec(BaseModel, frozen=True):
    slug: str
    zh_name: str
    role_type: RoleType
    alignment: Alignment
    ability: str

    @property
    def zh_type(self) -> str:
        return {
            RoleType.TOWNSFOLK: "鎮民",
            RoleType.OUTSIDER: "外來者",
            RoleType.MINION: "爪牙",
            RoleType.DEMON: "惡魔",
        }[self.role_type]


ROLE_SPECS: dict[str, RoleSpec] = {
    "clockmaker": RoleSpec(
        slug="clockmaker",
        zh_name="鐘錶匠",
        role_type=RoleType.TOWNSFOLK,
        alignment=Alignment.GOOD,
        ability="第一夜得知惡魔到最近爪牙的座位距離，相鄰為 1。",
    ),
    "investigator": RoleSpec(
        slug="investigator",
        zh_name="調查員",
        role_type=RoleType.TOWNSFOLK,
        alignment=Alignment.GOOD,
        ability="第一夜得知兩名玩家，其中一名是某個在場爪牙。",
    ),
    "empath": RoleSpec(
        slug="empath",
        zh_name="共情者",
        role_type=RoleType.TOWNSFOLK,
        alignment=Alignment.GOOD,
        ability="每夜得知兩名最近存活鄰居中邪惡玩家的數量。",
    ),
    "chambermaid": RoleSpec(
        slug="chambermaid",
        zh_name="侍女",
        role_type=RoleType.TOWNSFOLK,
        alignment=Alignment.GOOD,
        ability="每夜選兩名存活且不是自己的玩家，得知其中幾人因自身能力醒來。",
    ),
    "artist": RoleSpec(
        slug="artist",
        zh_name="藝術家",
        role_type=RoleType.TOWNSFOLK,
        alignment=Alignment.GOOD,
        ability="每局一次，白天私下問說書人一個可判定的是非問題。",
    ),
    "sage": RoleSpec(
        slug="sage",
        zh_name="賢者",
        role_type=RoleType.TOWNSFOLK,
        alignment=Alignment.GOOD,
        ability="若被惡魔夜間攻擊殺死，得知兩名玩家，其中一名是惡魔。",
    ),
    "drunk": RoleSpec(
        slug="drunk",
        zh_name="酒鬼",
        role_type=RoleType.OUTSIDER,
        alignment=Alignment.GOOD,
        ability="你不知道自己是酒鬼；你以為自己是一個未在場的鎮民，但能力實際無效。",
    ),
    "klutz": RoleSpec(
        slug="klutz",
        zh_name="笨蛋",
        role_type=RoleType.OUTSIDER,
        alignment=Alignment.GOOD,
        ability="你死亡時必須公開選一名存活玩家；若選中邪惡玩家，善良立即落敗。",
    ),
    "scarlet_woman": RoleSpec(
        slug="scarlet_woman",
        zh_name="紅唇女郎",
        role_type=RoleType.MINION,
        alignment=Alignment.EVIL,
        ability="若惡魔死亡且死亡前至少 5 人存活，你存活時會成為新的小惡魔。",
    ),
    "baron": RoleSpec(
        slug="baron",
        zh_name="男爵",
        role_type=RoleType.MINION,
        alignment=Alignment.EVIL,
        ability="設置時使外來者數量增加；本六人劇本中形成 2 鎮民、2 外來者、男爵、小惡魔。",
    ),
    "imp": RoleSpec(
        slug="imp",
        zh_name="小惡魔",
        role_type=RoleType.DEMON,
        alignment=Alignment.EVIL,
        ability="第一夜不殺人；之後每夜選一名存活玩家死亡，可選自己並傳位給存活爪牙。",
    ),
}

TOWNSFOLK = [
    "clockmaker",
    "investigator",
    "empath",
    "chambermaid",
    "artist",
    "sage",
]
OUTSIDERS = ["drunk", "klutz"]
MINIONS = ["scarlet_woman", "baron"]
DEMONS = ["imp"]
SCRIPT = [*TOWNSFOLK, *OUTSIDERS, *MINIONS, *DEMONS]


def role_spec(slug: str) -> RoleSpec:
    return ROLE_SPECS[slug]


def role_alignment(slug: str) -> Alignment:
    return role_spec(slug).alignment


def role_type(slug: str) -> RoleType:
    return role_spec(slug).role_type
