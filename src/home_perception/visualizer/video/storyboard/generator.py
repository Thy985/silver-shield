"""ADR-0035 D3 · StoryboardGenerator（阶段 4 · 分镜 · 时间维 · 强制中间层）。

``generate_storyboard``：NarrativePlan + ScenarioTemplate + evidence → ``Storyboard``。
- 自动产出（确定性零作者成本）：每 shot 的 evidence_refs 由模板 ref_kinds 落入真实图节点；
  字幕（narration）由证据值 + 文案常量填充（**非自由生成**，禁 PII）。
- 受众维度：顶层 ``audience``；同图不同受众 → 不同 Storyboard，但 evidence_refs 始终来自同一图。
- 作者覆盖：``override``（解析自 ``visualizer/video/scenarios/<demo_id>.yaml``）可覆盖
  audience / audience_need / evidence_refs / purpose；覆盖的 ref 必须能在 graph 解析（fail-closed）。
- 产物伴生：``storyboard_to_yaml`` / ``storyboard_from_yaml`` 提供 YAML 落盘与 round-trip。

层边界契约（§2.4.1）：本模块只产语义层（时间/意图/受众），**不**产空间字段。

见设计文档 §2.3（StoryboardGenerator）、§3（Storyboard / ShotSpec）、§6（伴生文件）。
"""

from __future__ import annotations

from typing import Any

import yaml

from home_perception.visualizer.video.narrative.compiler import NarrativePlan
from home_perception.visualizer.video.narrative.templates import (
    ScenarioTemplate,
    materialize_shot_refs,
)
from home_perception.visualizer.video.storyboard.decision_canvas import build_decision_steps
from home_perception.visualizer.video.storyboard.schema import ShotSpec, Storyboard
from home_perception.visualizer.video.text_safety import desensitize, sanitize_display_text


def generate_storyboard(
    plan: NarrativePlan,
    evidence: dict,
    template: ScenarioTemplate,
    audience: str | None = None,
    override: dict | None = None,
) -> Storyboard:
    """产出 Storyboard（语义层 · 时间维）。

    ``audience`` 缺省沿用 ``plan.audience``；``override`` 为作者 YAML 解析出的 storyboard 覆盖段。
    所有 evidence_refs 必须 ∈ EvidenceGraph.nodes（fail-closed，见 §8 验收 9）。
    """
    audience = audience or plan.audience
    scenario_id = evidence["scenario_id"]
    shot_refs = materialize_shot_refs(evidence, template)

    override_shots = {}
    if override:
        audience = override.get("audience", audience)
        for shot in override.get("shots", []) or []:
            override_shots[shot["name"]] = shot

    shots: list[ShotSpec] = []
    for shot in template.shots:
        name = shot["name"]
        refs = list(shot_refs.get(name, []))
        ov = override_shots.get(name, {})
        if "evidence_refs" in ov:
            refs = list(ov["evidence_refs"])  # 作者覆盖（仍需 graph 解析，下方断言）
        purpose = ov.get("purpose", template.default_purposes.get(name, ""))
        audience_need = ov.get("audience_need", "")
        duration_s = template.default_durations_s.get(name, 4.0)
        # decision shot：叙事由决策解释链（decision_steps）驱动，字幕 = 各步 caption
        # 同步；语义层只编排步骤与 highlight/fade，空间排版交给表达层 decision_canvas。
        if name == "decision":
            decision_steps = build_decision_steps(evidence)
            narration = [s.caption for s in decision_steps]
        else:
            decision_steps = []
            narration = _build_narration(name, refs, evidence)
        shots.append(
            ShotSpec(
                name=name,
                kind=shot["kind"],
                duration_s=duration_s,
                purpose=purpose,
                audience_need=audience_need,
                evidence_refs=refs,
                narration=narration,
                decision_steps=decision_steps,
            )
        )

    storyboard = Storyboard(
        demo_id=scenario_id,
        title_zh=f"案例视频 · {scenario_id}",
        scenario_ref=scenario_id,
        audience=audience,
        shots=shots,
        version=1,
    )
    _assert_refs_resolvable(storyboard, evidence)  # fail-closed：伪造 ref 必须报错
    return storyboard


def _build_narration(shot_name: str, refs: list[str], evidence: dict) -> list[str]:
    """由证据值 + 文案常量填充字幕逐句（确定性、脱敏、非自由文本）。

    只使用场景类别/计数/指令等系统生成字段，**绝不**引入姓名/地址/设备序列号（D3-4）。

    「只填系统字段」是设计意图，不是保证：``decision_evidence[].value``、
    ``scenario_id`` 等值最终来自 artifact（外部数据），一旦上游写入了路径或长数字串，
    就会原样渲进帧。故此处**每条产出都过一遍脱敏**（``text_safety`` 唯一实现），
    与渲染层的二次脱敏共同构成 D3-4 双保险。
    """
    return [_safe(line) for line in _narration_lines(shot_name, refs, evidence)]


def _safe(line: str) -> str:
    """单条字幕：脱敏 + 控制字符净化（宽度截断留给渲染层，语义层不做视觉决策）。"""
    return sanitize_display_text(desensitize(line))


def _narration_lines(shot_name: str, refs: list[str], evidence: dict) -> list[str]:
    """字幕原文填充（未脱敏；唯一调用方为 ``_build_narration``）。"""
    sid = evidence["scenario_id"]
    counts = evidence.get("counts") or {}
    if shot_name == "context":
        verdict = "正常闭环" if evidence.get("ok") else "需关注"
        gate = "通过" if evidence.get("gate_passed") else "未通过"
        return [
            f"场景标识：{sid}",
            f"处理结论：{verdict}",
            f"闭环校验：{gate}",
        ]
    if shot_name == "detection":
        events = ", ".join(evidence.get("event_types") or ()) or "无"
        return [
            f"感知事件类型：{events}",
            f"预警数：{counts.get('warnings', 0)}",
        ]
    if shot_name == "reasoning":
        ev = evidence.get("decision_evidence") or ()
        if not ev:
            return ["暂无决策解释视图"]
        return [f"{de['label']}：{de['value']}" for de in ev][:4]
    if shot_name == "decision":
        actions = ", ".join(evidence.get("recommended_actions") or ()) or "无"
        cmds = ", ".join(evidence.get("command_types") or ()) or "无"
        return [
            f"推荐动作：{actions}",
            f"指令类型：{cmds}",
        ]
    if shot_name == "closure":
        episodes = counts.get("episodes", 0)
        landed = ", ".join(evidence.get("episode_action_command_types") or ()) or "无"
        return [
            f"生成 Episode：{episodes} 条",
            f"已落地指令：{landed}",
        ]
    if shot_name == "cross_modal":
        links = counts.get("cross_modal_links", 0)
        return [f"跨模态关联：{links} 条（视觉/音频互相印证）"]
    return [f"引用证据 {len(refs)} 项"]


def _assert_refs_resolvable(storyboard: Storyboard, evidence: dict) -> None:
    """fail-closed：所有 evidence_refs 必须 ∈ EvidenceGraph.nodes（§8 验收 9 Story consistency）。"""
    node_ids = {n["id"] for n in evidence["graph"]["nodes"]}
    for shot in storyboard.shots:
        for ref in shot.evidence_refs:
            if ref not in node_ids:
                raise ValueError(
                    f"Storyboard 引用了图中不存在的证据节点 ref={ref!r}（shot={shot.name!r}）；"
                    "伪造 ref 违反 D3-12 证据所有权边界"
                )


def storyboard_to_yaml(storyboard: Storyboard) -> str:
    """Storyboard → YAML 字符串（伴生文件 storyboard.yaml 落盘）。"""
    data: dict[str, Any] = storyboard.model_dump(mode="json")
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def storyboard_from_yaml(text: str) -> Storyboard:
    """YAML 字符串 → Storyboard（round-trip / 作者覆盖解析）。"""
    data = yaml.safe_load(text)
    return Storyboard(**data)


__all__ = [
    "generate_storyboard",
    "storyboard_from_yaml",
    "storyboard_to_yaml",
]
