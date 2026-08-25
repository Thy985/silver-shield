"""D0 · DOM Product Contract —— 产品表面契约测试套件（telephone_risk 场景适配器）。

SSOT：``docs/reports/DOM-E2E-UPGRADE-ACCEPTANCE-CHECKLIST-2026-08-24.md`` v3.2 §3.2。

通用基座：tests/visualizer/_dom_contract_base.py
    - V-01~V-06 / Dim① / Dim② / AU-01~AU-03 / AU-07b / AU-09：共享基座断言类
    - AU-08：通过 contract.provenance 参数化，不硬编码字符串
    - AU-04/05/05b/06/07a/010：telephone_risk 有音频表面，正常执行

运行前提（外部 fixture，模块级探测 skip）：
    python scripts/run_demo.py --live --scenario config/demo/scenarios/product_story_risk.yaml
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.visualizer._dom_contract_base import (
    _ALL_DIM1_RULES,
    _AUDIO_KIND_ZH_LABELS,
    _BARE_SCORE_RE,
    _DISTRESS_CAUTION_NOTE,
    _RAW_AUDIO_ENUM_RE,
    BEHAVIOR_TIMELINE_LIMIT,
    BODY_NODE_HARD_BOUND,
    CASE_TIME_MARK_LIMIT,
    ENGINEERING_FIELD_PATTERNS,
    INTERNAL_COMPONENT_NAMES,
    INTERNAL_CONCEPT_TERMS,
    PERCEPTION_FIELD_PATTERNS,
    PS_HISTORY_RENDER_LIMIT,
    PS_VISIBLE_LIMIT,
    TIMELINE_RUNTIME_LIMIT,
    _fmt_hits,
    _is_invisible_per_dim2,
    _poll_until,
    create_dom_fixtures,
    make_skipif,
    na_skip,
)
from tests.visualizer._scenario_contract import ProductStoryRiskContract

# ---------------------------------------------------------------------------
# 场景契约（telephone_risk 专属，provenance 与 render.py 对齐）
# ---------------------------------------------------------------------------
_CONTRACT = ProductStoryRiskContract()
_D0 = _CONTRACT.d0

pytestmark = make_skipif(_D0.scenario_id, _D0.skip_reason)

# ---------------------------------------------------------------------------
# 参数化 fixtures（从基座获取，注入本场景的 contract）
# ---------------------------------------------------------------------------
_browser, contract_risk, audio_lifecycle = create_dom_fixtures(_D0)


# ===========================================================================
# V 断言（§3.2.1 · 缺陷锚点 A1–A6）
# ===========================================================================

class TestVContractSurface:
    """双模隔离与人话化契约（product mode 为默认态）。"""

    def test_v01_demo_stat_dual_mode_isolated(self, contract_risk):
        """V-01（A1）：Demo 状态面板必须带 data-debug-only 且 product mode 下不可见。"""
        info = contract_risk["snapshot"]["demoStat"]
        if info is None:
            return
        detail = (
            f"display={info['display']} visibility={info['visibility']} "
            f"opacity={info['opacity']} rect={info['rect']}"
        )
        assert info["hasDebugAttr"], f"A1/V-01 demo-stat 缺少 data-debug-only 标记（{detail}）"
        assert _is_invisible_per_dim2(info), f"A1/V-01 product mode 下 demo-stat 可见（{detail}）"

    def test_v02_overlay_chips_dual_mode_isolated(self, contract_risk):
        """V-02（A2）：overlay chips（ov-frame-* / ov-time-*）同 V-01 双模隔离。"""
        snap = contract_risk["snapshot"]
        for chip_key, chip_desc in (("ovFrame", "ov-frame"), ("ovTime", "ov-time")):
            info = snap[chip_key]
            if info is None:
                continue
            detail = f"display={info['display']} hasDebugAttr={info['hasDebugAttr']}"
            assert info["hasDebugAttr"] and _is_invisible_per_dim2(info), (
                f"A2/V-02 overlay chip {chip_desc} 未双模隔离（{detail}）"
            )

    def test_v03_media_binding_humanized(self, contract_risk):
        """V-03（A3）：媒体源绑定行人话化，或不含内部组件名，或整行 data-debug-only 隔离。"""
        info = contract_risk["snapshot"]["binding"]
        if info is None:
            return
        hits = [name for name in INTERNAL_COMPONENT_NAMES if name in info["text"]]
        isolated = info["hasDebugAttr"] and _is_invisible_per_dim2(info)
        assert not hits or isolated, (
            f"A3/V-03 媒体源绑定行暴露内部组件名 {hits}：'{info['text'][:200]}'"
        )

    def test_v04_internal_terms_absent_from_visible_text(self, contract_risk):
        """V-04：内部概念术语清零（并入 §3.2.3 黑名单②③统一扫描用户可见文本）。"""
        body = contract_risk["snapshot"]["bodyText"]
        rules = [
            *((name, re.escape(name)) for name in INTERNAL_COMPONENT_NAMES),
            *((name, re.escape(name)) for name in INTERNAL_CONCEPT_TERMS),
        ]
        hits = _scan_hits(body, rules)
        assert not hits, f"V-04 用户可见文本命中内部命名/术语：{_fmt_hits(hits)}"

    def test_v05_perception_entries_humanized(self, contract_risk):
        """V-05（A5）：感知条目不含 conf/bbox 工程字段，以人话描述替代。"""
        if not contract_risk["perception_ready"]:
            pytest.skip("观察窗内无视觉检测条目（人物未入镜或检测流未通），V-05 无法评估")
        info = contract_risk["snapshot"]["perception"]
        hits = _scan_hits(info["text"], list(PERCEPTION_FIELD_PATTERNS))
        assert not hits, f"A5/V-05 感知条目暴露工程字段（应人话化）：{_fmt_hits(hits)}"

    def test_v06_session_timer_isolated(self, contract_risk):
        """V-06（A6）：Session 计时器（ds-session-*）同 V-01 双模隔离。"""
        info = contract_risk["snapshot"]["dsSession"]
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
    def test_dim1_body_text_rule(self, contract_risk, group: str, rule_id: str, pattern: str):
        hits = _scan_hits(contract_risk["snapshot"]["bodyText"], [(rule_id, pattern)])
        assert not hits, f"维度① innerText 命中黑名单[{group}/{rule_id}]：{_fmt_hits(hits)}"


# ===========================================================================
# 维度② Debug 元素隔离断言（§3.2.5 · 防"忘了 hidden / 藏一半"）
# ===========================================================================

class TestBlacklistDim2DebugIsolation:
    def test_dim2_debug_only_nodes_all_invisible(self, contract_risk):
        """集合为空直接 PASS；非空则逐节点验证五判据（OR）至少一项成立。"""
        nodes = contract_risk["snapshot"]["debugNodes"]
        offenders = [str(n["key"]) for n in nodes if not _is_invisible_per_dim2(n)]
        assert not offenders, f"维度② data-debug-only 节点在 product mode 可见：{offenders}"


# ===========================================================================
# AU 断言（§3.2.2 · 缺陷锚点 B1–B5/B7）
# ===========================================================================

class TestAuPerceptionSurfaces:
    def test_au01_audio_table_rows_humanized(self, contract_risk):
        """AU-01（B1）：audio-table 行不含 score=/conf=（表位于「详细证据」折叠区）。"""
        rows = contract_risk["snapshot"]["counts"]["audioTableRows"]
        if rows == 0:
            return
        table_text = str(contract_risk["page"].evaluate(
            "(document.querySelector('table.audio-table')||{}).textContent||''"
        ))
        hits = _scan_hits(table_text, list(ENGINEERING_FIELD_PATTERNS))
        assert not hits, (
            "B1/AU-01 audio-table 行暴露 score=/conf= 工程字段"
            f"（双源：renderer._render_audio_evidence + live_stream.js _buildAudioRow）："
            f"{_fmt_hits(hits)}"
        )

    def test_au02_no_bare_score_in_stream_entries(self, contract_risk):
        """AU-02（B2）：感知流/行为时间线条目不含裸 score 数值。"""
        if not contract_risk["behavior_ready"]:
            pytest.skip("观察窗内行为时间线无条目，AU-02 无法评估（数据缺口，如实上报）")
        snap = contract_risk["snapshot"]
        surfaces = (("感知流", snap["psRecentText"]), ("行为时间线", snap["behaviorText"]))
        for surface_name, text in surfaces:
            hits = _scan_hits(text, [("bare_score_decimal", _BARE_SCORE_RE)])
            assert not hits, f"B2/AU-02 {surface_name}出现裸 score 数值：{_fmt_hits(hits)}"

    def test_au03a_visible_rows_within_configured_limit(self, contract_risk):
        """AU-03 不变量①：各动态面可见行数 ≤ 配置显示上限（N 是产品配置事实快照）。"""
        counts = contract_risk["snapshot"]["counts"]
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

    def test_au03b_fold_accounting_conserved(self, contract_risk):
        """AU-03 不变量②：折叠账目守恒 collapsed_count = total_count − visible_count。"""
        snap = contract_risk["snapshot"]
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

    def test_au05_human_labels_regression_guard(self, contract_risk):
        """AU-05：distress/telephone 中文标签仍正确渲染（防 E 阶段修复误伤映射表）。"""
        body = contract_risk["snapshot"]["bodyText"]
        rendered = [zh for zh in _AUDIO_KIND_ZH_LABELS if zh in body]
        assert rendered, (
            "AU-05 页面未见任何五类声学中文标签"
            f"（{_AUDIO_KIND_ZH_LABELS}）；若音频确已播放，说明 AudioKind→中文映射回归被破坏"
        )

    def test_au05b_audio_sensor_kinds_humanized(self, contract_risk):
        """AU-05b（新发现登记）：AUDIO SENSOR 卡 Kinds detected 行不得暴露裸 audio_* 枚举。"""
        info = contract_risk["snapshot"]["sensorAudio"]
        if info is None:
            return
        hits = _scan_hits(info["text"], [("raw_audio_enum", _RAW_AUDIO_ENUM_RE)])
        assert not hits, (
            f"新增发现·AU-05b AUDIO SENSOR 卡暴露裸枚举 kind（应经中文映射人话化）："
            f"{_fmt_hits(hits)}"
        )


# ===========================================================================
# AU-08 · Provenance 显性化（参数化，不再硬编码场景字符串）
# ===========================================================================

class TestAu08Provenance:
    """AU-08 · Provenance Banner 必须准确描述当前 Scenario 的事实来源。

    契约抽象：
        provenance_banner_mounted        — .prov-modality 元素在 DOM 中
        provenance_segments_match        — DOM 中包含 contract.provenance 的每段文案
        provenance_no_hallucination      — 不出现其他场景特有的 provenance 字样
    """

    def test_au08_banner_mounted(self, contract_risk):
        """AU-08a：.prov-modality 声明行挂载到 DOM。"""
        mounted = bool(contract_risk["page"].evaluate(
            "!!document.querySelector('.prov-modality')"
        ))
        assert mounted, "AU-08 .prov-modality 声明行未挂载"

    def test_au08_provenance_segments_match(self, contract_risk):
        """AU-08b：DOM provenance 行包含 contract.provenance 配置的每段文案。"""
        body = contract_risk["snapshot"]["bodyText"]
        for key, expected in _D0.provenance.items():
            assert expected in body, (
                f"AU-08b provenance 缺少「{key}: {expected}」（合同配置）"
            )

    def test_au08_no_other_scenario_keywords(self, contract_risk):
        """AU-08c：不出现其他 scenario 专属 provenance 字样（防幻觉）。"""
        body = contract_risk["snapshot"]["bodyText"]
        other_scenarios = [
            ("telephone_risk_reality_check", "REAL_AUDIO_PIPELINE"),
            ("cctv_surveillance_suspicious", "无音频轨"),
        ]
        for scenario_id, keyword in other_scenarios:
            assert keyword not in body, (
                f"AU-08c {scenario_id} 专属 provenance「{keyword}」出现在"
                f" {_D0.scenario_id} 页面（跨场景污染）"
            )

    def test_au08_reload_synthetic_marker_for_audio_scenarios(self, contract_risk):
        """AU-08d：有音频表面时，reload 后 prov-banner 含合成回放标注。

        无音频场景（CCTV 等）跳过此条（改由 AU-08b 覆盖）。
        """
        if not _D0.has_audio_surface:
            na_skip("AU-08d", "无音频表面，by-design skip")
            return
        page = contract_risk["page"]
        audio_ready = _poll_until(
            page,
            "document.querySelectorAll('table.audio-table tr').length > 0",
            50_000,
        )
        assert audio_ready, (
            "AU-08d 观察窗 50s 内无任何 audio-table 行涌现"
            "（replay 注入未生效，无法验证合成回放声明）"
        )
        page.reload(wait_until="domcontentloaded", timeout=30_000)
        synthetic_ready = _poll_until(
            page,
            "document.body.innerText.indexOf('合成回放 (SYNTHETIC_REPLAY)') >= 0",
            30_000,
        )
        assert synthetic_ready, (
            "AU-08d reload 后 prov-banner 未见「合成回放 (SYNTHETIC_REPLAY)」"
            "（provenance 显性化回归：Simulation 与真实推理不可区分即违契约）"
        )


# ===========================================================================
# AU-09 · distress_cry 语义降级守护
# ===========================================================================

class TestAu09DistressCryGuard:
    def test_au09_distress_cry_semantic_downgrade_guard(self):
        """AU-09 · distress_cry 语义降级守护（Owner 裁决 2026-08-24 · v4.0 收紧）。"""
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
    def test_au10_audio_event_locator_positions_only(self, contract_risk):
        """AU-10 · 声学事件定位条（SSOT v4.0 T3 · Owner 裁决）。"""
        if not _D0.has_audio_surface:
            na_skip("AU-10", "无音频表面，by-design skip")
            return
        page = contract_risk["page"]
        ready = _poll_until(
            page,
            "document.querySelectorAll('table.audio-table tr').length > 0",
            50_000,
        )
        assert ready, (
            "AU-10 观察窗 50s 内 audio-table 无行涌现"
            "（replay 注入未生效，无法验证定位条）"
        )
        page.reload(wait_until="domcontentloaded", timeout=30_000)
        expanded = page.evaluate(
            """() => {
              const t = document.querySelector('table.audio-table');
              if (!t) return false;
              const d = t.closest('details');
              if (d) d.open = true;
              const loc = document.querySelector('.audio-event-locator');
              return !!loc;
            }"""
        )
        assert expanded, (
            "AU-10 详细证据展开后未见 .audio-event-locator"
            "（定位条渲染缺失或未挂接 audio-table 上方）"
        )
        result = page.evaluate(
            """() => {
              const loc = document.querySelector('.audio-event-locator');
              const rows = document.querySelectorAll('table.audio-table tr').length - 1;
              const txt = loc ? (loc.innerText || '') : '';
              return {
                dots: loc ? loc.querySelectorAll('circle').length : 0,
                rows: rows,
                text: txt,
              };
            }"""
        )
        assert result["dots"] == result["rows"], (
            f"AU-10 定位点数 {result['dots']} ≠ audio-table 数据行数 {result['rows']}"
            "（事件与可视化位置不一致即违契约）"
        )
        assert "不代表对声音语义的判定结论" in result["text"], (
            "AU-10 定位条缺少「非语义判定」红线文案"
        )
        for banned in ("哭诉", "哭腔", "求助"):
            assert banned not in result["text"], (
                f"AU-10 定位条出现语义背书词汇「{banned}」"
                "（波形只可标检测时刻，不得包装成信号解释）"
            )


# ===========================================================================
# AU-04 / AU-06 / AU-07a · 音频生命周期（telephone_risk 专属）
# ===========================================================================

class TestAuAudioLifecycle:
    def test_au04_audio_health_three_state_machine(self, audio_lifecycle):
        """AU-04（B5）：Audio Health 三态状态机（SPEC §2.4 契约补齐的真测试缺口）。"""
        states = audio_lifecycle["states"]
        assert states, "未采集到任何 Audio Health 状态"
        legal = {"RECENT_EVENT", "NO_RECENT_EVENT", "UNAVAILABLE"}
        unexpected = set(states) - legal
        assert not unexpected, f"AU-04 出现非法健康态：{unexpected}（序列 {states}）"
        if not audio_lifecycle["saw_recent"]:
            pytest.skip(f"观察窗内无音频事件（RECENT_EVENT 未出现），三态转换无法评估：{states}")
        assert "NO_RECENT_EVENT" in states, f"AU-04 状态序列缺少 NO_RECENT_EVENT：{states}"
        assert states[-1] == "NO_RECENT_EVENT", (
            f"AU-04 音频停息 5s 后应回落 NO_RECENT_EVENT，实测末态 {states[-1]}（序列 {states}）"
        )

    def test_au06_rms_canvas_exists_with_samples(self, audio_lifecycle):
        """AU-06（B4 DOM 侧拆层）：canvas 存在 + 尺寸 > 0 + 曾绘入样本（合理性归 D2）。"""
        canvas = audio_lifecycle["canvas_info"]
        if canvas is None:
            pytest.skip("音频事件未出现，canvas 样本检查无从进行")
        assert canvas["exists"], (
            "B4/AU-06 RMS 波形 canvas 不存在于 DOM（已知根因：product_story_risk 未注册 "
            "_SCENARIO_SURFACES 音频 Surface，has_audio_surface()=False 门控了渲染）"
        )
        assert canvas["w"] > 0 and canvas["h"] > 0, (
            f"B4/AU-06 canvas 尺寸非法：{canvas['w']}x{canvas['h']}"
        )
        assert int(canvas["nonBgSamples"]) > 0, (
            "B4/AU-06 canvas 存在但从未绘入任何 RMS 样本（evidence_delta.rms_window 未到达）"
        )

    def test_au07a_audio_bounded_after_replay_ends(self, audio_lifecycle):
        """AU-07a · Audio boundedness（v3.4 Owner 裁决拆分 · 保留不豁免）。"""
        baseline = audio_lifecycle["baseline"]
        assert baseline, "未采集到静音基线计数"
        samples = audio_lifecycle["samples"]
        assert samples, "静音观察窗未采样（fixture 异常）"
        for idx, sample in enumerate(samples, start=1):
            after, before = int(sample["audioTableRows"]), int(baseline["audioTableRows"])
            assert after == before, (
                f"AU-07a replay 结束后 audio-derived evidence 仍在增长："
                f"audioTableRows {before} → {after}（sample#{idx}）"
            )

    def test_au07b_visual_surface_bounded_during_runtime(self, audio_lifecycle):
        """AU-07b · Runtime visual boundedness（v3.4 Owner 裁决拆分）。"""
        samples = audio_lifecycle["samples"]
        assert samples, "运行期观察窗未采样（fixture 异常）"
        for idx, sample in enumerate(samples, start=1):
            tl = int(sample["timelineRuntimeLi"])
            assert tl <= TIMELINE_RUNTIME_LIMIT, (
                f"AU-07b 运行期视觉时间线节点 {tl} > 渲染上限 {TIMELINE_RUNTIME_LIMIT}"
                f"（sample#{idx}；裁剪机制失效）"
            )
            marks = int(sample["caseTimeMarks"])
            assert marks <= CASE_TIME_MARK_LIMIT, (
                f"AU-07b Case Time 刻度 {marks} > 上限 {CASE_TIME_MARK_LIMIT}（sample#{idx}）"
            )
