# 贡献指南（CONTRIBUTING）

> 权威规范见 `AGENTS.md`（AI 协作强制规范）与 `docs/04_development_standards.md`。
> 本文档是给人类 / AI 贡献者的快速上手；与 `AGENTS.md` 冲突时以 `AGENTS.md` 为准。

---

## 0. 开始之前

1. 读 `AGENTS.md` + `docs/API_REFERENCE.md` + `docs/CONTRACTS.md`。
2. 确认改动落在允许的阶段：当前为 **Phase 0 / MVP**。**不在本模块**做的事：
   - 输出「诈骗 / fraud / suspect」判定或字段（模块边界铁律）；
   - 做 LLM 解释（推迟 v2）；
   - 做中心风控聚合 / 决策；
   - 引入重模型 / 重依赖拖垮边缘 CPU。

---

## 1. 分支与提交

- **分支类型**：`feat/` `fix/` `refactor/` `docs/` `test/` `chore/`（注意用 `feat/`，不是 `feature/`）。
- **提交格式**：`<type>(<scope>): <subject>`，body 必须含 `Task scope: ROADMAP P0-x | issue #n | governance`。
- **scope**：`ingestion` / `detection` / `analysis` / `evidence` / `output` / `core` / `config` / `deps` / `ci` / `docs`。
- **AI 必须遵守**：branch + commit + PR；**禁止直推 / merge `main`**（AGENTS §6.3）。
- **Owner 专属文件**（改动需 Owner 明确授权）：`docs/02_architecture.md`、`docs/08_roadmap.md`、`docs/ADR/*`、`AGENTS.md`、`CONTRIBUTING.md`。

---

## 2. 写代码前四问（Task Contract）

1. 改动是否跨模块 / 契约？→ 先提 ADR（见 `docs/ADR/README.md`）。
2. 是否在允许阶段内？
3. 最小改动？（能改一行不改两行）
4. 有测试吗？（新功能 / 修 bug 必须有 `pytest`；契约模型变更加 schema 测试）

---

## 3. 测试与质量门禁

- `ruff check src tests` 无 error。
- `pytest tests/ -q` 全绿。
- 契约变更：额外跑 `tests/contract/`。
- **不删 / 不改坏测试以通过 CI**（AGENTS §6.2 #6）。
- 提交前确认 `.gitignore` 已覆盖新产物（凭证 / 模型权重 `*.pt` / 证据片段 / venv / 缓存 / 构建产物），不得 `git add -f` 强加。

---

## 4. 冻结契约纪律

- 改 `PerceptionEvent` / `WarningEvent` / `ActionCommand` 字段、5 类枚举、ABC 签名 → **BREAKING**，须 ADR + Owner review（详见 `docs/CONTRACTS.md`）。
- 新增「诈骗 / fraud」类字段 → **禁止**（模块边界铁律）。
- 新增配置项 → 必须加 pydantic 校验（拒绝负值 / NaN / 范围越界 / 非法枚举 / bool 误传，见 `core/config.py`）。

---

## 5. 提交前自检（AGENTS §6 逐条）

- [ ] 不输出「诈骗人员 / fraud / suspect」判定或字段；
- [ ] 不破坏事件 Schema / MQTT 契约（BREAKING 已走 Owner 评审）；
- [ ] 默认不存全量视频，原视频不上传中心；
- [ ] 代码未硬编码凭证 / 设备序列号 / token；
- [ ] 未引入 LLM 解释逻辑（v2 才做）；
- [ ] 未越过本模块职责做中心风控聚合 / 决策；
- [ ] 未提交 `.env` / `config/devices.yaml` / `prototypes/`；
- [ ] 未用裸 `print()`（用 structlog）；
- [ ] 无 `except:` / `except Exception:` 后静默吞异常；
- [ ] 无全局 `pip install` 污染（重依赖必须 venv）；
- [ ] 新功能 / 修 bug 有测试；契约模型变更加 schema 测试；
- [ ] PR 前 `ruff` + `pytest` 全绿；
- [ ] 未自行 merge / 直推 `main`；
- [ ] 未夹带未说明的改动。

---

## 6. PR 模板

PR 须满足 `docs/05` §3.2 + `.github/PULL_REQUEST_TEMPLATE.md` 自检清单（ruff 全绿 + pytest 全绿 + 契约说明）。
