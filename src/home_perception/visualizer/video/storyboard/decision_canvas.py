"""ADR-0035 D3 · Decision Canvas（决策解释链 · 语义层规格）。

``decision`` shot 的「决策幕」规格来源：把「为什么是这个动作而非其他」拆成一条可逐步
揭示的因果链（Observation → Risk → Policy → Candidate → Selected → Execution → Closure）。

设计边界（关键）：
- **语义层**：本模块只产出决策步骤的**语义/锚点**（阶段、字幕、画布节点 id、highlight/fade），
  不携带任何坐标/颜色/字号（那些属于 ``scene/schema.py`` 表达层）。
- **受控常量闭集**：候选动作（``COMMAND_CANDIDATES``）与策略名（``CATEGORY_TO_POLICY``）
  是系统常量，不是按证据值生成的叙事分支——避免退化为规则引擎（§2.2 / §9 D3-9）。
- **fail-closed 锚定**：每个画布节点 id 必须落在 ``_CLOSED_CANVAS_IDS``，且其 ``anchor``
  必须能在真实 evidence 中解析（如 ``recommended_actions`` 含该动作）。`_assert_decision_canvas`
  在落盘前复校，杜绝「画布宣称 NOTIFY_FAMILY 但证据实为 MONITOR」的漂移。

见设计文档 §3（Storyboard / DecisionStep）、§4（Decision Canvas）。
"""

from __future__ import annotations

from home_perception.visualizer.video.storyboard.schema import DecisionStep

# 决策画布候选动作闭集（策略动作空间，系统常量；非按证据值生成）。
COMMAND_CANDIDATES: tuple[str, ...] = ("MONITOR", "NOTIFY_FAMILY", "ESCALATE_COMMUNITY")

# 场景类别 → 策略名（确定性常量映射）。
CATEGORY_TO_POLICY: dict[str, str] = {
    "elderly_warning": "elderly_warning_policy",
    "generic": "generic_policy",
}

# 决策画布节点 id 闭集（dc:<stage> 或 dc:cand:<ACTION>）。
_CLOSED_CANVAS_IDS: frozenset[str] = frozenset(
    [
        "dc:observation",
        "dc:risk",
        "dc:policy",
        *[f"dc:cand:{a}" for a in COMMAND_CANDIDATES],
        "dc:selected",
        "dc:execution",
        "dc:closure",
    ]
)

# 画布节点在屏幕上的标准呈现顺序（同时用于 highlight/fade 的补集计算）。
# 顺序经 review #5 修订：``dc:selected`` 置于候选动作之前——决策幕「候选步」已揭示选中动作
# （候选步 highlight 选中的候选、dc:selected 显示同样结论），故 dc:selected 须在候选步保持
# 中性可见，而非作为「尚未抵达节点」被淡出（否则候选步画布仍缺关键结论框）。
CANONICAL_CHAIN: tuple[str, ...] = (
    "dc:observation",
    "dc:risk",
    "dc:policy",
    "dc:selected",
    *[f"dc:cand:{a}" for a in COMMAND_CANDIDATES],
    "dc:execution",
    "dc:closure",
)


def _ev_get(evidence: object, key: str, default: object = None) -> object:
    """兼容 dict 与 pydantic ``ScenarioEvidence`` 的取值。

    真实管线（``compiler``）传入的是 loader 投影出的 ``ScenarioEvidence`` 对象；测试
    夹具（``conftest.make_evidence``）传入的是 dict。两者都须被本模块正确消费，避免
    ``evidence.get`` 在 pydantic 对象上抛 ``AttributeError``。
    """
    if isinstance(evidence, dict):
        return evidence.get(key, default)
    return getattr(evidence, key, default)


def _decision_map(evidence: object) -> dict[str, object]:
    """decision_evidence → {label: value}（兼容 dict 与 pydantic 两种形态）。

    真实 loader 使用**前缀标签**（``Observation · 检测证据（事件类型）`` 等），测试夹具
    使用**无前缀标签**（``检测证据（事件类型）``），两者都须可被 ``_decision_value`` 命中。
    """
    raw = _ev_get(evidence, "decision_evidence") or ()
    out: dict[str, object] = {}
    for d in raw:
        if isinstance(d, dict):
            label, value = d.get("label"), d.get("value")
        else:
            label, value = getattr(d, "label", None), getattr(d, "value", None)
        if label is not None:
            out[label] = value
    return out


def _category_of(evidence: object) -> str:
    """由 scenario_id / event_types 推导场景类别（固定映射，非叙事决策）。"""
    sid = (_ev_get(evidence, "scenario_id") or "").lower()
    events = [e.lower() for e in (_ev_get(evidence, "event_types") or ())]
    if "elderly" in sid or any(("elderly" in e or "dwell" in e or "fall" in e) for e in events):
        return "elderly_warning"
    return "generic"


def _policy_name(evidence: object) -> str:
    """策略名（受控常量，由类别映射，非证据值生成）。"""
    return CATEGORY_TO_POLICY.get(_category_of(evidence), "generic_policy")


def _decision_value(evidence: object, *labels: str) -> str:
    """从 decision_evidence 取首个命中 label 的 value（缺省 '—'）。

    兼容前缀/无前缀两套标签：真实 loader 用 ``Observation · 检测证据（事件类型）``，
    测试夹具用 ``检测证据（事件类型）``；调用方传入候选标签时**先写前缀、后写无前缀**
    即可同时覆盖两种来源。
    """
    de = _decision_map(evidence)
    for lab in labels:
        if lab in de and de[lab] is not None:
            return str(de[lab])
    return "—"


def collect_canvas_ids(steps: list[DecisionStep]) -> list[str]:
    """收集决策步骤引用到的全部画布节点 id（按 CANONICAL_CHAIN 排序，去重）。

    公开包装：语义层的节点收集是合法对外能力，不应以 ``_`` 私有名被表达层跨模块导入
    （review #1 封装边界）。
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for step in steps:
        for nid in (*step.highlight, *step.fade):
            if nid not in seen:
                seen.add(nid)
                ordered.append(nid)
    ordered.sort(key=lambda nid: CANONICAL_CHAIN.index(nid) if nid in CANONICAL_CHAIN else len(CANONICAL_CHAIN))
    return ordered


def canvas_node_spec(evidence: object, node_id: str) -> dict:
    """画布节点 id → 表达层规格（label / stage / synthetic / anchor）。

    fail-closed：id 不在闭集即报错；synthetic 节点（policy / candidate）由受控常量派生，
    non-synthetic 节点锚定真实 evidence 取值。``anchor`` 供 ``_assert_decision_canvas`` 复校。
    """
    if node_id not in _CLOSED_CANVAS_IDS:
        raise AssertionError(f"决策画布节点 id 不在闭集：{node_id!r}（违反决策画布边界）")
    sub = node_id.split(":", 1)[1]
    if sub == "observation":
        _obs = _decision_value(
            evidence,
            "Observation · 检测证据（事件类型）", "检测证据（事件类型）", "感知事件类型",
        )
        obs = _obs if _obs not in ("", "—") else ((_ev_get(evidence, "event_types") or ("无",))[0])
        return {"id": node_id, "label": f"观测\n{obs}", "stage": "observation", "synthetic": False,
                "anchor": f"decision_evidence:观测:{obs}"}
    if sub == "risk":
        risk = _decision_value(evidence, "Reasoning · 风险级别", "风险级别")
        return {"id": node_id, "label": f"风险\n{risk}", "stage": "risk", "synthetic": False,
                "anchor": f"decision_evidence:风险级别:{risk}"}
    if sub == "policy":
        pol = _policy_name(evidence)
        return {"id": node_id, "label": f"策略\n{pol}", "stage": "policy", "synthetic": True,
                "anchor": f"policy:{pol}"}
    if sub.startswith("cand:"):
        action = sub.split(":", 1)[1]
        if action not in COMMAND_CANDIDATES:
            raise AssertionError(f"决策画布候选动作不在闭集：{action!r}")
        return {"id": node_id, "label": f"动作\n{action}", "stage": "candidate", "synthetic": True,
                "anchor": f"candidate:{action}"}
    if sub == "selected":
        recs = list(_ev_get(evidence, "recommended_actions") or ()) or ["无"]
        return {"id": node_id, "label": f"选中\n{recs[0]}", "stage": "selected", "synthetic": False,
                "anchor": f"recommended_actions:{recs[0]}"}
    if sub == "execution":
        cmds = list(_ev_get(evidence, "command_types") or ()) or ["无"]
        return {"id": node_id, "label": f"执行\n{cmds[0]}", "stage": "execution", "synthetic": False,
                "anchor": f"command_types:{cmds[0]}"}
    if sub == "closure":
        return {"id": node_id, "label": "闭环", "stage": "closure", "synthetic": False,
                "anchor": "episodes"}
    raise AssertionError(f"未识别的决策画布节点 id：{node_id!r}")


def _reveal_fade(chain: tuple[str, ...], active: str) -> list[str]:
    """逐步揭示：active 节点及其之前的节点保持可见，其后的节点淡出（尚未抵达该步）。

    这是「同一张决策图随时间逐步揭示」的底层规则：每一步只把 active 节点高亮、把**
    尚未揭示**的后续节点淡出，已揭示节点以中性灰保持可见——避免旧实现把全部非 active
    节点淡出导致步骤间画布几乎全黑、切换回看时剧烈抖动（review #5）。
    """
    idx = chain.index(active)
    return [n for i, n in enumerate(chain) if i > idx]


def _step(stage: str, caption: str, active: str, chain: tuple[str, ...]) -> DecisionStep:
    """单步：高亮 active 节点，淡出其后的未揭示节点（之前节点保持可见）。"""
    return DecisionStep(
        stage=stage,
        caption=caption,
        highlight=[active],
        fade=_reveal_fade(chain, active),
    )


def build_decision_steps(evidence: object) -> list[DecisionStep]:
    """构造 decision shot 的决策解释链（语义层 · 确定性 · 非规则引擎）。

    顺序固定为 Observation → Risk → Policy → Candidate → Selected → Execution → Closure；
    候选动作步显式高亮选中项、淡出其余，回答「为什么是这个动作而非其他」。Execution 步
    不掩盖「推荐动作 ≠ 实际命令」的语义差异，反而将其转为解释点（增强可信度）。
    """
    _obs = _decision_value(
        evidence,
        "Observation · 检测证据（事件类型）", "检测证据（事件类型）", "感知事件类型",
    )
    obs = _obs if _obs not in ("", "—") else ((_ev_get(evidence, "event_types") or ("无",))[0])
    risk = _decision_value(evidence, "Reasoning · 风险级别", "风险级别")
    outcome = _decision_value(
        evidence,
        "Reasoning · 决策结果（trace outcome）", "决策结果（trace outcome）", "决策结果",
    )
    pol = _policy_name(evidence)
    recs = list(_ev_get(evidence, "recommended_actions") or ()) or ["无"]
    selected = recs[0]
    cmds = list(_ev_get(evidence, "command_types") or ()) or ["无"]
    candidates = list(COMMAND_CANDIDATES)

    chain = CANONICAL_CHAIN
    steps: list[DecisionStep] = [
        _step("observation", f"Observation · 观测证据：{obs}", "dc:observation", chain),
        _step("risk", f"Risk · 风险级别：{risk}（{outcome}）", "dc:risk", chain),
        _step("policy", f"Policy · 命中策略：{pol}", "dc:policy", chain),
    ]
    # 候选动作步：benign（无推荐动作）与正常场景分流。
    if selected in COMMAND_CANDIDATES:
        cand_active = f"dc:cand:{selected}"
        other_cands = [f"dc:cand:{a}" for a in candidates if a != selected]
        # 候选步只淡出「其余两个候选」+「尚未抵达的后续节点（selected/execution/closure）」；
        # 已揭示的 observation/risk/policy 保持中性可见（review #5：防全黑抖动）。
        # 明确回答「为什么是这个动作而非其他」。
        cand_fade = list(dict.fromkeys((*other_cands, *_reveal_fade(chain, cand_active))))
        steps.append(
            DecisionStep(
                stage="candidate",
                caption=f"候选动作：{[a for a in candidates]} → 选中 {selected}",
                highlight=[cand_active],
                fade=cand_fade,
            )
        )
    else:
        # benign：policy 跑过但未产生任何推荐动作 → 三个候选一并淡出，选中节点承接到「无」。
        all_cands = [f"dc:cand:{a}" for a in candidates]
        sel_idx = chain.index("dc:selected")
        future = [n for i, n in enumerate(chain) if i > sel_idx]  # execution / closure
        cand_fade = list(dict.fromkeys((*all_cands, *future)))
        steps.append(
            DecisionStep(
                stage="candidate",
                caption="候选动作：MONITOR / NOTIFY_FAMILY / ESCALATE_COMMUNITY → 本场景无推荐动作（benign 闭环）",
                highlight=["dc:selected"],
                fade=cand_fade,
            )
        )
    steps.append(_step("selected", f"Decision · 选中动作：{selected}", "dc:selected", chain))
    # Execution：推荐动作 ≠ 实际指令 且二者为真实动作时，显式解释差异（增强可信度）；
    # benign 两者均为「无」→ 不套用「≠」框架（那会误导为存在分歧）。
    rec_str = "、".join(recs)
    cmd_str = "、".join(cmds)
    real_actions = rec_str not in ("", "无") and cmd_str not in ("", "无")
    if real_actions and rec_str != cmd_str:
        exec_caption = (
            f"Execution · 推荐动作 {rec_str} ≠ 实际指令 {cmd_str}："
            f"本场景策略建议 {rec_str}，但执行通道仅产出 {cmd_str} 指令"
        )
    else:
        exec_caption = f"Execution · 推荐动作 {rec_str} / 实际指令 {cmd_str}"
    steps.append(
        DecisionStep(
            stage="execution",
            caption=exec_caption,
            highlight=["dc:execution"],
            fade=_reveal_fade(chain, "dc:execution"),
        )
    )
    steps.append(_step("closure", "Closure · Memory ✓ Episode 已存；Notification ✓ 指令已下发", "dc:closure", chain))
    return steps


def assert_decision_canvas(evidence: object, canvas_nodes: list) -> None:
    """决策画布一致性（fail-closed）：节点 id 闭集 + 规格与 evidence 锚定一致。

    重新派生每个节点的权威规格（``canvas_node_spec``）并与画布节点比对 label，再校验其
    ``anchor`` 能在证据中解析——例如 ``recommended_actions:NOTIFY_FAMILY`` 要求 evidence
    确实含该动作。通过「重新派生而非信任画布自带字段」避免同义反复（tautology）。
    """
    recs = set(_ev_get(evidence, "recommended_actions") or ())
    cmds = set(_ev_get(evidence, "command_types") or ())
    pol = _policy_name(evidence)
    de_values: set[str] = set()
    for d in (_ev_get(evidence, "decision_evidence") or ()):
        if isinstance(d, dict):
            v = d.get("value")
        else:
            v = getattr(d, "value", None)
        if v is not None:
            de_values.add(str(v))
    event_types = set(_ev_get(evidence, "event_types") or ())
    for node in canvas_nodes:
        # 渲染/校验边界：画布节点必须是 pydantic DecisionCanvasNode（extra='forbid'），
        # 不接受 dict 旁路——否则绕过 schema 的测试也能通过，等于校验空转（review #4）。
        if isinstance(node, dict):
            raise TypeError(
                f"决策画布节点必须是 DecisionCanvasNode，收到 dict（绕过 schema 校验）：{node!r}"
            )
        nid = node.id
        if nid not in _CLOSED_CANVAS_IDS:
            raise AssertionError(f"决策画布含闭集外节点 id={nid!r}（违反决策画布边界）")
        spec = canvas_node_spec(evidence, nid)
        label = node.label
        if label != spec["label"]:
            raise AssertionError(
                f"决策画布节点标签与规格不符：id={nid!r} 画布={label!r} 规格={spec['label']!r}"
            )
        anchor = spec["anchor"]
        if anchor.startswith("decision_evidence:观测:"):
            val = anchor.split(":", 2)[2]
            if val == "无":
                if event_types:
                    raise AssertionError(
                        f"决策画布观测为'无'但证据含事件类型：{anchor!r} vs {sorted(event_types)}"
                    )
            elif val not in de_values and val not in event_types:
                raise AssertionError(f"决策画布 anchor 与证据不符（观测）：{anchor!r}")
        elif anchor.startswith("decision_evidence:风险级别:"):
            val = anchor.split(":", 2)[2]
            if val != "—" and val not in de_values:
                raise AssertionError(f"决策画布 anchor 与证据不符（风险）：{anchor!r}")
        elif anchor.startswith("policy:"):
            if anchor != f"policy:{pol}":
                raise AssertionError(f"决策画布策略 anchor 与类别不符：{anchor!r} ≠ policy:{pol}")
        elif anchor.startswith("candidate:"):
            action = anchor.split(":", 1)[1]
            if action not in COMMAND_CANDIDATES:
                raise AssertionError(f"决策画布候选 anchor 不在闭集：{anchor!r}")
        elif anchor.startswith("recommended_actions:"):
            action = anchor.split(":", 1)[1]
            if action == "无":
                if recs:
                    raise AssertionError(
                        f"决策画布选中为'无'但证据含推荐动作：{anchor!r} vs {sorted(recs)}"
                    )
            elif action not in recs:
                raise AssertionError(f"决策画布选中动作不在 recommended_actions：{anchor!r}")
        elif anchor.startswith("command_types:"):
            cmd = anchor.split(":", 1)[1]
            if cmd == "无":
                if cmds:
                    raise AssertionError(
                        f"决策画布执行为'无'但证据含指令：{anchor!r} vs {sorted(cmds)}"
                    )
            elif cmd not in cmds:
                raise AssertionError(f"决策画布执行指令不在 command_types：{anchor!r}")


__all__ = [
    "CANONICAL_CHAIN",
    "COMMAND_CANDIDATES",
    "assert_decision_canvas",
    "build_decision_steps",
    "canvas_node_spec",
    "collect_canvas_ids",
]
