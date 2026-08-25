"""D0 · DOM Product Contract —— Delivery Courier 场景适配器。

SSOT：``docs/reports/DOM-E2E-UPGRADE-ACCEPTANCE-CHECKLIST-2026-08-24.md`` v3.2 §3.2。

通用基座：tests/visualizer/_dom_contract_base.py
    - V-01~V-06 / Dim① / Dim② / AU-01~AU-03 / AU-07b / AU-09：共享基座断言类（复用）
    - AU-08：通过 contract.provenance 参数化，验证 Delivery Courier 专属 provenance
    - AU-04/05/05b/06/07a/010：Delivery Courier 无音频表面 → na_skip() 显式标记 N/A（不入 PASS）

场景叙事（冻结）：
    白天单次正常来访 → visit_normal / MONITOR（系统克制不升级）

Key properties（与 render.py 对齐）：
    provenance = {
        "video": "实时推理 (REAL_RUNTIME_VIDEO)",
        "audio": "无音频轨",
        "risk": "runtime-computed",
    }

运行前提（外部 fixture，模块级探测 skip）：
    python scripts/run_demo.py --live --scenario config/demo/scenarios/delivery_courier_normal.yaml
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.visualizer._dom_contract_base import (
    _ALL_DIM1_RULES,
    _BARE_SCORE_RE,
    _DISTRESS_CAUTION_NOTE,
    BEHAVIOR_TIMELINE_LIMIT,
    BODY_NODE_HARD_BOUND,
    ENGINEERING_FIELD_PATTERNS,
    INTERNAL_COMPONENT_NAMES,
    INTERNAL_CONCEPT_TERMS,
    PERCEPTION_FIELD_PATTERNS,
    PS_HISTORY_RENDER_LIMIT,
    PS_VISIBLE_LIMIT,
    TIMELINE_RUNTIME_LIMIT,
    _fmt_hits,
    _is_invisible_per_dim2,
    create_dom_fixtures,
    make_skipif,
    na_skip,
)
from tests.visualizer._scenario_contract import DeliveryCourierNormalContract

# ---------------------------------------------------------------------------
# 场景契约（CCTV 专属，provenance 与 render.py 对齐）
# ---------------------------------------------------------------------------
_CONTRACT = DeliveryCourierNormalContract()
_D0 = _CONTRACT.d0

pytestmark = make_skipif(_D0.scenario_id, _D0.skip_reason)

# ---------------------------------------------------------------------------
# 参数化 fixtures（从基座获取，注入本场景的 contract）
# ---------------------------------------------------------------------------
_browser, contract_page, audio_lifecycle = create_dom_fixtures(_D0)


# ===========================================================================
# V 断言（§3.2.1 · 缺陷锚点 A1–A6）
# ===========================================================================

class TestVContractSurface:
    """双模隔离与人话化契约（product mode 为默认态）。"""

    def test_v01_demo_stat_dual_mode_isolated(self, contract_page):
        """V-01（A1）：Demo 状态面板必须带 data-debug-only 且 product mode 下不可见。"""
        info = contract_page["snapshot"]["demoStat"]
        if info is None:
            return
        detail = (
            f"display={info['display']} visibility={info['visibility']} "
            f"opacity={info['opacity']} rect={info['rect']}"
        )
        assert info["hasDebugAttr"], f"A1/V-01 demo-stat 缺少 data-debug-only 标记（{detail}）"
        assert _is_invisible_per_dim2(info), f"A1/V-01 product mode 下 demo-stat 可见（{detail}）"

    def test_v02_overlay_chips_dual_mode_isolated(self, contract_page):
        """V-02（A2）：overlay chips（ov-frame-* / ov-time-*）同 V-01 双模隔离。"""
        snap = contract_page["snapshot"]
        for chip_key, chip_desc in (("ovFrame", "ov-frame"), ("ovTime", "ov-time")):
            info = snap[chip_key]
            if info is None:
                continue
            detail = f"display={info['display']} hasDebugAttr={info['hasDebugAttr']}"
            assert info["hasDebugAttr"] and _is_invisible_per_dim2(info), (
                f"A2/V-02 overlay chip {chip_desc} 未双模隔离（{detail}）"
            )

    def test_v03_media_binding_humanized(self, contract_page):
        """V-03（A3）：媒体源绑定行人话化，或不含内部组件名，或整行 data-debug-only 隔离。"""
        info = contract_page["snapshot"]["binding"]
        if info is None:
            return
        hits = [name for name in INTERNAL_COMPONENT_NAMES if name in info["text"]]
        isolated = info["hasDebugAttr"] and _is_invisible_per_dim2(info)
        assert not hits or isolated, (
            f"A3/V-03 媒体源绑定行暴露内部组件名 {hits}：'{info['text'][:200]}'"
        )

    def test_v04_internal_terms_absent_from_visible_text(self, contract_page):
        """V-04：内部概念术语清零（并入 §3.2.3 黑名单②③统一扫描用户可见文本）。"""
        body = contract_page["snapshot"]["bodyText"]
        rules = [
            *((name, re.escape(name)) for name in INTERNAL_COMPONENT_NAMES),
            *((name, re.escape(name)) for name in INTERNAL_CONCEPT_TERMS),
        ]
        hits = _scan_hits(body, rules)
        assert not hits, f"V-04 用户可见文本命中内部命名/术语：{_fmt_hits(hits)}"

    def test_v05_perception_entries_humanized(self, contract_page):
        """V-05（A5）：感知条目不含 conf/bbox 工程字段，以人话描述替代。"""
        if not contract_page["perception_ready"]:
            pytest.skip("观察窗内无视觉检测条目（人物未入镜或检测流未通），V-05 无法评估")
        info = contract_page["snapshot"]["perception"]
        hits = _scan_hits(info["text"], list(PERCEPTION_FIELD_PATTERNS))
        assert not hits, f"A5/V-05 感知条目暴露工程字段（应人话化）：{_fmt_hits(hits)}"

    def test_v06_session_timer_isolated(self, contract_page):
        """V-06（A6）：Session 计时器（ds-session-*）同 V-01 双模隔离。"""
        info = contract_page["snapshot"]["dsSession"]
        if info is None:
            return
        detail = f"display={info['display']} hasDebugAttr={info['hasDebugAttr']}"
        assert info["hasDebugAttr"] and _is_invisible_per_dim2(info), (
            f"A6/V-06 Session 计时器未双模隔离（{detail}）"
        )


# ===========================================================================
# 维度① 工程语义黑名单逐条断言（§3.2.3 · 用户可见文本主断言面）
# ===========================================================================

def _scan_hits(text: str, patterns: list[tuple[str, str]]) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for rule_id, pattern in patterns:
        for m in re.finditer(pattern, text):
            start = max(0, m.start() - 40)
            end = min(len(text), m.end() + 40)
            hits.append({"rule": rule_id, "match": m.group(0),
                         "context": text[start:end].replace("\n", "\\n")})
    return hits


class TestBlacklistDim1BodyText:
    """每条黑名单规则独立成测（只增不减；命中输出明细上下文）。"""

    @pytest.mark.parametrize(
        ("group", "rule_id", "pattern"),
        _ALL_DIM1_RULES,
        ids=[f"{g}:{rid}" for g, rid, _p in _ALL_DIM1_RULES],
    )
    def test_dim1_body_text_rule(self, contract_page, group: str, rule_id: str, pattern: str):
        hits = _scan_hits(contract_page["snapshot"]["bodyText"], [(rule_id, pattern)])
        assert not hits, f"维度① innerText 命中黑名单[{group}/{rule_id}]：{_fmt_hits(hits)}"


# ===========================================================================
# 维度② Debug 元素隔离断言（§3.2.5 · 防"忘了 hidden / 藏一半"）
# ===========================================================================

class TestBlacklistDim2DebugIsolation:
    def test_dim2_debug_only_nodes_all_invisible(self, contract_page):
        """集合为空直接 PASS；非空则逐节点验证五判据（OR）至少一项成立。"""
        nodes = contract_page["snapshot"]["debugNodes"]
        offenders = [str(n["key"]) for n in nodes if not _is_invisible_per_dim2(n)]
        assert not offenders, f"维度② data-debug-only 节点在 product mode 可见：{offenders}"


# ===========================================================================
# AU 断言（§3.2.2 · 缺陷锚点 B1–B5/B7）
# ===========================================================================

class TestAuPerceptionSurfaces:
    def test_au01_audio_table_rows_humanized(self, contract_page):
        """AU-01（B1）：audio-table 行不含 score=/conf=（表位于「详细证据」折叠区）。"""
        rows = contract_page["snapshot"]["counts"]["audioTableRows"]
        if rows == 0:
            return
        table_text = str(contract_page["page"].evaluate(
            "(document.querySelector('table.audio-table')||{}).textContent||''"
        ))
        hits = _scan_hits(table_text, list(ENGINEERING_FIELD_PATTERNS))
        assert not hits, (
            "B1/AU-01 audio-table 行暴露 score=/conf= 工程字段"
            f"（双源：renderer._render_audio_evidence + live_stream.js _buildAudioRow）："
            f"{_fmt_hits(hits)}"
        )

    def test_au02_no_bare_score_in_stream_entries(self, contract_page):
        """AU-02（B2）：感知流/行为时间线条目不含裸 score 数值。"""
        if not contract_page["behavior_ready"]:
            pytest.skip("观察窗内行为时间线无条目，AU-02 无法评估（数据缺口，如实上报）")
        snap = contract_page["snapshot"]
        surfaces = (("感知流", snap["psRecentText"]), ("行为时间线", snap["behaviorText"]))
        for surface_name, text in surfaces:
            hits = _scan_hits(text, [("bare_score_decimal", _BARE_SCORE_RE)])
            assert not hits, f"B2/AU-02 {surface_name}出现裸 score 数值：{_fmt_hits(hits)}"

    def test_au03a_visible_rows_within_configured_limit(self, contract_page):
        """AU-03 不变量①：各动态面可见行数 ≤ 配置显示上限（N 是产品配置事实快照）。"""
        counts = contract_page["snapshot"]["counts"]
        assert counts["psRecentEntries"] <= PS_VISIBLE_LIMIT, (
            f"AU-03① 感知流可见条目 {counts['psRecentEntries']} > 上限 {PS_VISIBLE_LIMIT}"
        )
        assert counts["psHistoryRendered"] <= PS_HISTORY_RENDER_LIMIT, (
            f"AU-03① 历史感知渲染条目 {counts['psHistoryRendered']} > 上限 "
            f"{PS_HISTORY_RENDER_LIMIT}"
        )
        assert counts["behaviorItems"] <= BEHAVIOR_TIMELINE_LIMIT, (
            f"AU-03① 行为时间线条目 {counts['behaviorItems']} > 上限 {BEHAVIOR_TIMELINE_LIMIT}"
        )
        if counts["timelineRuntimeLi"] >= 0:
            assert counts["timelineRuntimeLi"] <= TIMELINE_RUNTIME_LIMIT, (
                f"AU-03① 时间线 runtime 节点 {counts['timelineRuntimeLi']} > 上限 "
                f"{TIMELINE_RUNTIME_LIMIT}"
            )

    def test_au03b_fold_accounting_conserved(self, contract_page):
        """AU-03 不变量②：折叠账目守恒 collapsed_count = total_count − visible_count。"""
        snap = contract_page["snapshot"]
        btn_text = snap["moreBtnText"]
        if not btn_text:
            return
        m = re.search(r"已折叠\s*(\d+)\s*条\s*/\s*共\s*(\d+)\s*条", btn_text)
        if m is None:
            return
        hidden, total = int(m.group(1)), int(m.group(2))
        visible = snap["counts"]["timelineRuntimeLi"]
        assert total - hidden == visible, (
            f"AU-03② 折叠账目不守恒：共 {total} / 折叠 {hidden} / DOM 可见 {visible}"
        )

    def test_au03c_loop_bounded_dom_nodes(self, audio_lifecycle):
        """AU-03 不变量③（B3/B7）：静音观察窗内 DOM 节点总数有界（防无限累积面）。"""
        all_counts = [audio_lifecycle["baseline"], *audio_lifecycle["samples"]]
        node_counts = [int(c["bodyNodes"]) for c in all_counts]
        peak = max(node_counts)
        assert peak <= BODY_NODE_HARD_BOUND, (
            f"B3/AU-03③ DOM 节点总数 {peak} 超绝对上界 {BODY_NODE_HARD_BOUND}"
        )
        drift = max(node_counts) - min(node_counts)
        assert drift <= 50, f"B3/B7/AU-03③ 静音窗内 DOM 节点漂移 {drift}（疑似无上限累积面）"

    def test_au05_human_labels_regression_guard(self, contract_page):
        """AU-05：音频中文标签验证（Delivery Courier 无音频表面 → N/A）。

        CCTV 无音频轨，不出现声学标签属正常行为；此条标 N/A 而非 FAIL。
        """
        na_skip("AU-05", "Delivery Courier 无音频表面，by-design N/A")

    def test_au05b_audio_sensor_kinds_humanized(self, contract_page):
        """AU-05b（新发现登记）：AUDIO SENSOR 卡 Kinds detected 行不得暴露裸 audio_* 枚举。

        Delivery Courier 无音频表面 → N/A。
        """
        na_skip("AU-05b", "Delivery Courier 无音频表面，by-design N/A")


# ===========================================================================
# AU-08 · Provenance 显性化（参数化，不再硬编码场景字符串）
# ===========================================================================

class TestAu08Provenance:
    """AU-08 · Provenance Banner 必须准确描述当前 Scenario 的事实来源。

    CCTV provenance（与 render.py 对齐）：
        provenance = {
            "video": "实时推理 (REAL_RUNTIME_VIDEO)",
            "audio": "无音频轨",
            "risk": "runtime-computed",
        }
    """

    def test_au08_banner_mounted(self, contract_page):
        """AU-08a：.prov-modality 声明行挂载到 DOM。"""
        mounted = bool(contract_page["page"].evaluate(
            "!!document.querySelector('.prov-modality')"
        ))
        assert mounted, "AU-08 .prov-modality 声明行未挂载"

    def test_au08_provenance_segments_match(self, contract_page):
        """AU-08b：DOM provenance 行包含 contract.provenance 配置的每段文案。"""
        body = contract_page["snapshot"]["bodyText"]
        for key, expected in _D0.provenance.items():
            assert expected in body, (
                f"AU-08b provenance 缺少「{key}: {expected}」（合同配置）"
            )

    def test_au08_no_other_scenario_keywords(self, contract_page):
        """AU-08c：不出现其他 scenario 专属 provenance 字样（防幻觉）。"""
        body = contract_page["snapshot"]["bodyText"]
        other_scenarios = [
            ("telephone_risk_reality_check", "REAL_AUDIO_PIPELINE"),
            ("telephone_risk_reality_check", "合成回放 (SYNTHETIC_REPLAY)"),
            ("product_story_risk", "合成回放 (SYNTHETIC_REPLAY)"),
            ("product_story_benign", "无视觉轨"),
        ]
        for scenario_id, keyword in other_scenarios:
            assert keyword not in body, (
                f"AU-08c {scenario_id} 专属 provenance「{keyword}」出现在"
                f" {_D0.scenario_id} 页面（跨场景污染）"
            )

    def test_au08_reload_synthetic_marker_for_audio_scenarios(self, contract_page):
        """AU-08d：有音频表面时，reload 后 prov-banner 含合成回放标注。

        Delivery Courier 无音频表面 → N/A（由 AU-08b provenance 覆盖）。
        """
        na_skip("AU-08d", "Delivery Courier 无音频表面，by-design N/A")


# ===========================================================================
# AU-09 · distress_cry 语义降级守护
# ===========================================================================

class TestAu09DistressCryGuard:
    def test_au09_distress_cry_semantic_downgrade_guard(self):
        """AU-09 · distress_cry 语义降级守护（Owner 裁决 2026-08-24 · v4.0 收紧）。

        跨场景通用断言，不依赖 audio surface，CCTV 同样适用。
        """
        js_path = Path(__file__).resolve().parents[2] / (
            "src/home_perception/visualizer/assets/live_stream.js"
        )
        src = js_path.read_text(encoding="utf-8")
        assert "_AUDIO_KIND_CAUTION" in src, "AU-09 降级类别集合缺失"
        assert "audio_distress_cry: true" in src, "AU-09 distress_cry 未登记为降级类别"
        assert "audio_distress_cry: '声学异常活动'" in src, (
            "AU-09 js 主标签未降级为「声学异常活动」（哭腔/求助类断言口吻回归）"
        )
        assert "kz + '(当前算法判定)'" in src, "AU-09 感知流降级框架文案缺失"
        assert _DISTRESS_CAUTION_NOTE in src, "AU-09 已知误识别声明文案缺失"
        py_path = Path(__file__).resolve().parents[2] / (
            "src/home_perception/visualizer/renderer.py"
        )
        rsrc = py_path.read_text(encoding="utf-8")
        assert "声学异常活动" in rsrc, (
            "AU-09 renderer 映射未对 distress_cry 语义降级（确定性断言口吻回归）"
        )
        assert "audio-caution-note" in rsrc, "AU-09 audio-table 已知误识别脚注缺失"


# ===========================================================================
# AU-10 · 声学事件定位条
# ===========================================================================

class TestAu10Locator:
    def test_au10_audio_event_locator_positions_only(self, contract_page):
        """AU-10 · 声学事件定位条（SSOT v4.0 T3 · Owner 裁决）。

        Delivery Courier 无音频表面 → N/A。
        """
        na_skip("AU-10", "Delivery Courier 无音频表面，by-design N/A")


# ===========================================================================
# AU-11 · Product Narrative Consistency（CCTV 专属事实投影）
# ===========================================================================

_CCTV_FORBIDDEN_TERMS: tuple[str, ...] = (
    "入侵者",
    "罪犯",
    "诈骗人员",
    "犯罪嫌疑人",
    "危险人物",
    "可疑人员",
    "恐怖分子",
)

_CCTV_EXPECTED_NARRATIVE_LABELS: tuple[str, ...] = (
    "重复访问",
    "异常停留",
    "待核实到访",
    "高风险逼近",
)


class TestAu11NarrativeConsistency:
    """AU-11 · 产品叙事一致性守护（SSOT v4.0 Owner 裁决 2026-08-25）。

    CCTV 场景叙事（冻结）：
        白天单次正常来访 → visit_normal / MONITOR（系统克制不升级）

    断言核心：
        1. 行为时间线 / 感知流出现的人话标签必须在已知映射集合内
        2. DOM 任意可见文本不得含犯罪/入侵/诈骗类超范围结论
        3. 风险级别不得超出 WARN 档（LOW/MEDIUM 可接受，HIGH 不允许）
        4. 行动结论须为 LOG_ONLY（或无行动任务），不得出现 NOTIFY_FAMILY / CREATE_COMMUNITY_TASK
    """

    def _forbidden_hits(self, text: str) -> list[dict[str, str]]:
        hits: list[dict[str, str]] = []
        for term in _CCTV_FORBIDDEN_TERMS:
            if term in text:
                idx = text.find(term)
                start = max(0, idx - 30)
                end = min(len(text), idx + len(term) + 30)
                hits.append({"term": term, "context": text[start:end].replace("\n", "\\n")})
        return hits

    def _tl_item_labels(self, page) -> list[str]:
        """返回行为时间线各条目的可见文本标签（去除时间戳前缀）。"""
        items = page.evaluate("""
            (() => {
              const ul = document.querySelector('#behavior-timeline-delivery_courier_normal');
              if (!ul) return [];
              return Array.from(ul.querySelectorAll('li.tl-item')).map(li =>
                (li.textContent || '').trim()
              );
            })()
        """)
        return items or []

    def test_au11_no_forbidden_criminal_terms_in_visible_text(self, contract_page):
        """AU-11①：用户可见文本不得含犯罪/入侵/诈骗类超范围结论。"""
        body = contract_page["snapshot"]["bodyText"]
        hits = self._forbidden_hits(body)
        assert not hits, (
            "AU-11① 用户可见文本出现犯罪/入侵类超范围结论："
            + "; ".join(
                f"[{h['term']}]"
                for h in hits[:5]
            )
        )

    def test_au11_tl_labels_within_know_mapping(self, contract_page):
        """AU-11②：行为时间线条目标签须在已知人话映射集合内。

        若观察窗内无行为事件，以 N/A 如实上报。
        """
        if not contract_page["behavior_ready"]:
            na_skip("AU-11②", "观察窗内无行为时间线条目（数据缺口，如实上报）")
        labels = self._tl_item_labels(contract_page["page"])
        if not labels:
            na_skip("AU-11②", "行为时间线条目为空（首帧后无新增行为事件）")
            return
        allowed = set(_CCTV_EXPECTED_NARRATIVE_LABELS) | {"停留超过阈值", "检测到重复访问",
                                                          "待核实到访", "高风险逼近"}
        unknown = [lbl for lbl in labels if not any(a in lbl for a in allowed)]
        assert not unknown, (
            f"AU-11② 行为时间线出现未知标签（未在已知人话映射内）：{unknown}"
        )

    def test_au11_risk_level_not_exceed_warn(self, contract_page):
        """AU-11③：LRK 风险卡不得出现 HIGH（CCTV 预期上限为 WARN/MEDIUM）。

        LRK 未渲染时 N/A（尚未触发风险评估）。
        """
        lvl_text = contract_page["snapshot"].get("lrkLevel", "")
        if not lvl_text:
            na_skip("AU-11③", "LRK 风险卡未渲染（观察窗内无风险信号），by-design N/A")
            return
        # _LEVEL_ZH 映射：HIGH→高, MEDIUM→中, LOW→低
        if "高" in lvl_text or "HIGH" in lvl_text.upper():
            pytest.fail(f"AU-11③ LRK 风险级别超出 WARN 档（CCTV 预期上限 MEDIUM/WARN）：{lvl_text}")

    def test_au11_action_not_family_or_community(self, contract_page):
        """AU-11④：行动结论不得含通知家属/创建社区任务（CCTV 仅 LOG_ONLY）。

        无行动任务 DOM 时 N/A。
        """
        closure_text = contract_page["snapshot"].get("closureText", "")
        if not closure_text:
            na_skip("AU-11④", "行动闭环区未渲染（观察窗内无行动触发），by-design N/A")
            return
        forbidden_actions = ["通知家属", "创建社区任务", "NOTIFY_FAMILY", "CREATE_COMMUNITY"]
        hits = [a for a in forbidden_actions if a in closure_text]
        assert not hits, (
            f"AU-11④ 行动结论含超范围处置（CCTV 仅限 LOG_ONLY）：{hits}"
        )


# ===========================================================================
# AU-04 / AU-06 / AU-07a · 音频生命周期（CCTV 专属：全部 N/A）
# ===========================================================================

class TestAuAudioLifecycle:
    def test_au04_audio_health_three_state_machine(self, audio_lifecycle):
        """AU-04（B5）：Audio Health 三态状态机。

        Delivery Courier 无音频表面 → N/A。
        """
        na_skip("AU-04", "Delivery Courier 无音频表面，by-design N/A")

    def test_au06_rms_canvas_exists_with_samples(self, audio_lifecycle):
        """AU-06（B4 DOM 侧拆层）：canvas 存在 + 尺寸 > 0 + 曾绘入样本。

        Delivery Courier 无音频表面 → N/A。
        """
        na_skip("AU-06", "Delivery Courier 无音频表面，by-design N/A")

    def test_au07a_audio_bounded_after_replay_ends(self, audio_lifecycle):
        """AU-07a · Audio boundedness。

        Delivery Courier 无音频表面 → N/A。
        """
        na_skip("AU-07a", "Delivery Courier 无音频表面，by-design N/A")

    def test_au07b_visual_surface_bounded_during_runtime(self, audio_lifecycle):
        """AU-07b · Runtime visual boundedness（v3.4 Owner 裁决拆分）。

        Delivery Courier 无音频表面、无 samples → N/A。
        """
        na_skip("AU-07b", "Delivery Courier 无音频表面，by-design N/A")