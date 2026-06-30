from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from botc_ai.domain.roles import ROLE_SPECS


class RoleKnowledge(BaseModel):
    model_config = ConfigDict(frozen=True)

    slug: str
    info_shape: str
    knows_exact_identity: bool
    legal_claim_examples: list[str]
    invalid_followups: list[str]
    good_followups: list[str]
    deduction_limits: list[str]


ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "clockmaker": ("鐘錶匠", "钟表匠", "clockmaker"),
    "investigator": ("調查員", "调查员", "investigator"),
    "empath": ("共情者", "共情", "empath"),
    "chambermaid": ("侍女", "女僕", "女仆", "chambermaid"),
    "artist": ("藝術家", "艺术家", "artist"),
    "sage": ("賢者", "贤者", "sage"),
    "drunk": ("酒鬼", "醉鬼", "drunk"),
    "klutz": ("笨蛋", "klutz"),
    "scarlet_woman": ("紅唇女郎", "红唇女郎", "猩紅女郎", "猩红女郎", "scarlet woman"),
    "baron": ("男爵", "baron"),
    "imp": ("小惡魔", "小恶魔", "惡魔", "恶魔", "imp"),
}

ROLE_KNOWLEDGE: dict[str, RoleKnowledge] = {
    "clockmaker": RoleKnowledge(
        slug="clockmaker",
        info_shape="demon_minion_distance",
        knows_exact_identity=False,
        legal_claim_examples=["我是鐘錶匠，數字 2。"],
        invalid_followups=["不要要求鐘錶匠指出惡魔或爪牙本人。"],
        good_followups=["請桌面用座位距離交叉排世界。", "追問是否願意公開數字與可能組合。"],
        deduction_limits=["鐘錶匠數字只能限制惡魔與最近爪牙距離，不能直接定人。"],
    ),
    "investigator": RoleKnowledge(
        slug="investigator",
        info_shape="two_candidates_one_is_minion_role",
        knows_exact_identity=False,
        legal_claim_examples=["我是調查員，2、6 有一個紅唇女郎。"],
        invalid_followups=["不要追問調查員兩人中到底哪一位是爪牙；角色本來就不知道唯一答案。"],
        good_followups=["請兩名候選人給角色範圍。", "比較兩名候選人的票型、死亡與私聊承諾。"],
        deduction_limits=["調查員資訊表示二選一，不表示兩人都是邪惡，也不表示可唯一鎖定。"],
    ),
    "empath": RoleKnowledge(
        slug="empath",
        info_shape="alive_neighbors_evil_count",
        knows_exact_identity=False,
        legal_claim_examples=["我是共情者，我兩邊 3、5 是 2。"],
        invalid_followups=["不要要求共情者任選兩人查驗；共情者只能看最近存活鄰居。"],
        good_followups=["核對當晚最近存活鄰居是誰。", "死亡後重算鄰居並比較數字變化。"],
        deduction_limits=["共情者數字是兩名最近存活鄰居的邪惡數，不直接指出哪位邪惡。"],
    ),
    "chambermaid": RoleKnowledge(
        slug="chambermaid",
        info_shape="two_targets_woke_count",
        knows_exact_identity=False,
        legal_claim_examples=["我是侍女，查 2、4 得 1。"],
        invalid_followups=["不要把侍女數字當成邪惡數或陣營查驗。"],
        good_followups=["請侍女說明查了哪兩名存活玩家。", "核對該夜哪些角色理應因自身能力醒來。"],
        deduction_limits=["侍女只知道兩人中幾人因自身能力醒來，不知道角色或陣營。"],
    ),
    "artist": RoleKnowledge(
        slug="artist",
        info_shape="one_yes_no_question",
        knows_exact_identity=False,
        legal_claim_examples=["我是藝術家，還沒問。", "我是藝術家，我問 X，答案是是。"],
        invalid_followups=["不要要求藝術家在未使用能力前提供夜間資訊。"],
        good_followups=["請藝術家說明問題是否已使用。", "若已使用，請公開原問題與答案以便檢查。"],
        deduction_limits=["藝術家答案只對被接受的問題成立，無法自動推出完整魔典。"],
    ),
    "sage": RoleKnowledge(
        slug="sage",
        info_shape="on_demon_kill_two_candidates_one_demon",
        knows_exact_identity=False,
        legal_claim_examples=["我是賢者，被夜殺後拿到 2、5 有惡魔。"],
        invalid_followups=["賢者未被惡魔夜殺時不會有兩人資訊。"],
        good_followups=["確認死亡原因是否可能是惡魔夜殺。", "請兩名候選人給角色範圍並看票型。"],
        deduction_limits=["賢者資訊是二選一惡魔，不直接指出唯一惡魔。"],
    ),
    "drunk": RoleKnowledge(
        slug="drunk",
        info_shape="hidden_outsider_false_role",
        knows_exact_identity=False,
        legal_claim_examples=["玩家通常不會知道自己是酒鬼。"],
        invalid_followups=["不要要求玩家主動自證自己是酒鬼；酒鬼不知道自己是酒鬼。"],
        good_followups=["若資訊矛盾，可把酒鬼世界列為可能性。"],
        deduction_limits=["酒鬼是解釋資訊錯誤的可能世界，不是沒有證據時的萬用結論。"],
    ),
    "klutz": RoleKnowledge(
        slug="klutz",
        info_shape="death_choice_live_player",
        knows_exact_identity=False,
        legal_claim_examples=["我是笨蛋，死後要選一個活人。"],
        invalid_followups=["不要說笨蛋可以安全出局自證；選中邪惡會讓善良立刻輸。"],
        good_followups=["請笨蛋提前整理可信名單。"],
        deduction_limits=["笨蛋死亡選人是高風險驗證，不是安全驗證。"],
    ),
    "scarlet_woman": RoleKnowledge(
        slug="scarlet_woman",
        info_shape="minion_demon_replacement",
        knows_exact_identity=False,
        legal_claim_examples=["通常作為調查員結果角色或邪惡 bluff 出現。"],
        invalid_followups=["六人局紅唇女郎不會開局知道惡魔是誰。"],
        good_followups=["若有人被調查員二選一點到，請其給角色範圍並看惡魔死亡後是否接任。"],
        deduction_limits=["紅唇女郎在場不等於知道隊友；六人 Teensyville 惡方不互認。"],
    ),
    "baron": RoleKnowledge(
        slug="baron",
        info_shape="setup_outsider_modification",
        knows_exact_identity=False,
        legal_claim_examples=["通常作為邪惡 bluff 或調查員結果角色出現。"],
        invalid_followups=["不要要求男爵提供夜間資訊；男爵只影響設置。"],
        good_followups=["用外來者數量與宣稱分布判斷是否像男爵局。"],
        deduction_limits=["男爵能力只改配置，本身不提供查驗資訊。"],
    ),
    "imp": RoleKnowledge(
        slug="imp",
        info_shape="night_kill_or_starpass",
        knows_exact_identity=False,
        legal_claim_examples=["通常不會公開真跳小惡魔，除非末盤或特殊策略。"],
        invalid_followups=["六人局小惡魔不會開局知道爪牙是誰。"],
        good_followups=["看夜死、處決與可能 starpass 時機。"],
        deduction_limits=["夜死只能證明有惡魔殺人，不直接證明某名玩家是惡魔。"],
    ),
}


def role_aliases_for(slug: str) -> tuple[str, ...]:
    return ROLE_ALIASES.get(slug, (ROLE_SPECS[slug].zh_name, slug))


def role_from_alias(text: str) -> str | None:
    compact = text.lower()
    matches: list[tuple[int, int, str]] = []
    for slug, aliases in ROLE_ALIASES.items():
        for alias in aliases:
            index = compact.find(alias.lower())
            if index >= 0:
                matches.append((index, -len(alias), slug))
    if not matches:
        return None
    return sorted(matches)[0][2]


def roles_mentioned(text: str) -> list[str]:
    compact = text.lower()
    found: list[tuple[int, str]] = []
    for slug, aliases in ROLE_ALIASES.items():
        for alias in aliases:
            index = compact.find(alias.lower())
            if index >= 0:
                found.append((index, slug))
                break
    return [slug for _, slug in sorted(found)]


def role_knowledge_payload() -> dict[str, dict[str, object]]:
    return {
        slug: {
            "zh_name": ROLE_SPECS[slug].zh_name,
            "info_shape": knowledge.info_shape,
            "knows_exact_identity": knowledge.knows_exact_identity,
            "legal_claim_examples": knowledge.legal_claim_examples,
            "invalid_followups": knowledge.invalid_followups,
            "good_followups": knowledge.good_followups,
            "deduction_limits": knowledge.deduction_limits,
        }
        for slug, knowledge in ROLE_KNOWLEDGE.items()
    }
