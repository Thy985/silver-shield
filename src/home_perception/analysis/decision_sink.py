"""决策审计血缘落盘（ADR-0031 · Slice E）。

本地 JSONL sink + 保留期轮转 + 落盘期脱敏守卫，对齐：

- **ADR-0002（隐私铁律）**：trace 不出 Home 端、证据仅以 ID 引用；落盘即写本地文件，
  绝不联网上报（Non-Goal #5）。
- **ADR-0027 D9（分层留存）**：保留期用**本地 UTC** 计时（`created_at + retention_days`），
  默认对齐 MEDIUM 层级（结构化审计事实 30d）；删除幂等；失败告警不阻塞主链。
- **ADR-0031 T3（失败隔离）**：`record` / `flush` 内部异常一律 `log.exception`，绝不外抛
  影响决策；脱敏守卫（fail-closed）即便触发也只拒绝写入并告警，不中断决策。

落盘内容形态：每条 trace 经 `DecisionTrace.to_dict()` 序列化为**一行** JSON 追加到本地文件；
双轨 `DecisionABRun` 经 `to_dict()` 同样落一行。写前必经 `assert_desensitized` 守卫——
trace 数据模型已凭构造满足 T4 / T5（无判定字段、无原始媒体 / 路径 / 凭证），守卫是**兜底
fail-closed**：任何未脱敏内容（判定语义字段 / 密钥类键 / 绝对路径或 URL 串）一律拒绝落盘。

> 本模块只消费 `decision_trace` 的契约，**不引入**任何决策行为；`JsonlTraceRecorder` 结构
> 上满足 `DecisionTraceRecorder` Protocol，可直接注入 `DecisionEngine(trace_recorder=...)`，
> 无需改动 engine。
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Generic, TypeVar

from ..common.logging import get_logger
from .decision_trace import (
    DECISION_TRACE_FORBIDDEN_FIELDS,
    DecisionABRun,
    DecisionTrace,
)

PayloadT = TypeVar("PayloadT")

log = get_logger(__name__)

# 默认保留期：对齐 ADR-0027 D9 MEDIUM 层级（结构化审计事实 30d）。可按合规要求收紧 / 延长。
DEFAULT_RETENTION_DAYS: int = 30

# 落盘期脱敏守卫：密钥类键提示（**子串**匹配，覆盖 `user_password_hash` /
# `secret_question` / `auth_token` / `aws_access_key_id` 等命名变体）。即便出现在
# 嵌套字典里也拒绝。集合刻意收敛，避免普通业务键（如 `decision_id`）误命中。
_SENSITIVE_KEY_HINTS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "token",
        "secret",
        "api_key",
        "apikey",
        "credential",
        "private_key",
        "access_key",
        "ssn",
        "social_security",
        "id_card",
        "身份证",
    }
)

# 绝对路径（unix `/...` / windows `C:\...`）或 `scheme://` URL —— 审计血缘不得含定位信息。
_PATH_PATTERN = re.compile(r"^(/|[a-zA-Z]:[\\/])|\w+://")


class DesensitizationError(Exception):
    """落盘期脱敏守卫失败：payload 含未脱敏内容（ADR-0002 / T5 兜底，fail-closed）。"""


def _scan_undesensitized(obj: object, path: tuple[str, ...] = ()) -> list[str]:
    """递归扫描 payload，返回所有未脱敏命中（空列表 = 通过）。

    仅扫描 dict / list / tuple / str / bytes：数字、布尔、None 不可能携带定位或判定语义。
    命中三类：1. 键名属于 T4 禁止判定语义字段；2. 键名**子串**命中密钥类提示（覆盖
    命名变体）；3. 字符串值是绝对路径或 URL（仅从头匹配，见 `_PATH_PATTERN`）。

    `bytes` 一律 fail-closed 拒绝——它不该出现在 JSON 序列化前的 payload 里，且可藏匿
    路径 / 凭证；`tuple` 复用 `list` 分支递归。
    """
    findings: list[str] = []
    if isinstance(obj, dict):
        for key, val in obj.items():
            k = str(key).lower()
            if k in DECISION_TRACE_FORBIDDEN_FIELDS:
                findings.append(f"forbidden_field:{k}@{'.'.join(path)}")
            if any(hint in k for hint in _SENSITIVE_KEY_HINTS):
                findings.append(f"sensitive_key:{k}@{'.'.join(path)}")
            findings.extend(_scan_undesensitized(val, (*path, k)))
    elif isinstance(obj, (list, tuple)):
        for i, val in enumerate(obj):
            findings.extend(_scan_undesensitized(val, (*path, f"[{i}]")))
    elif isinstance(obj, str) and _PATH_PATTERN.match(obj):
        findings.append(f"path_or_url:{obj[:60]}@{'.'.join(path)}")
    elif isinstance(obj, bytes):
        findings.append(f"bytes_payload:{obj[:30]!r}@{'.'.join(path)}")
    return findings


def assert_desensitized(payload: Mapping[str, object]) -> None:
    """落盘前脱敏守卫（fail-closed）。

    任何未脱敏内容抛 `DesensitizationError`。`DecisionTrace` / `DecisionABRun` 由数据模型
    构造期即满足 T4 / T5，本函数是对**落盘边界**的最后一道兜底——即便未来某 Bundle 误加
    判定字段或某引用串意外携带路径，也拒绝写入而非污染审计链。
    """
    findings = _scan_undesensitized(payload)
    if findings:
        raise DesensitizationError(
            "拒绝落盘未脱敏内容（ADR-0002 / T5）：" + "; ".join(sorted(set(findings)))
        )


def _record_created_at(record: Mapping[str, object]) -> datetime | None:
    """从一行 JSON 记录中提取 `created_at`（UTC），无法解析返回 None（视为不过期）。

    兼容两种形态：单 trace（`identity.created_at`）与双轨 run（`trace_baseline.identity
    .created_at`）。
    """
    identity = record.get("identity")
    if not isinstance(identity, dict):
        baseline = record.get("trace_baseline")
        if isinstance(baseline, dict):
            identity = baseline.get("identity")
    if isinstance(identity, dict) and identity.get("created_at"):
        try:
            return datetime.fromisoformat(str(identity["created_at"]))
        except ValueError:
            return None
    return None


def prune_jsonl(path: Path, retention_days: int, now_utc: datetime | None = None) -> int:
    """按 `retention_days`（本地 UTC）删除过期记录，返回实际删除行数。

    - 保留边界 **inclusive**：`(now - created_at) <= retention_days` 视为未过期；严格大于才删。
    - **幂等**：重复调用结果一致（先过滤再整体重写，不依赖行序）。
    - **失败不阻塞主链**（ADR-0027 D9）：单行解析损坏 → 仅告警并保留该行（宁留痕不丢），
      不影响其余记录轮转。
    - 文件不存在 → 返回 0（无操作）。
    - 重写经临时文件 `*.tmp` 再 `replace`：POSIX 下原子；**跨卷替换会抛 `OSError`**，
      由本函数 `except` 兜底告警、不阻塞主链（按 ADR-0002 部署于 Linux Home 端，本地盘
      内替换安全）。
    """
    if not path.exists():
        return 0
    now = now_utc or datetime.now(UTC)
    kept: list[str] = []
    removed = 0
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line:
                continue
            expired = False
            try:
                record = json.loads(line)
                created_at = _record_created_at(record)
                if created_at is not None and (now - created_at) > timedelta(days=retention_days):
                    expired = True
            except Exception:
                log.exception("decision.trace_prune_parse_failed", path=str(path))
            if expired:
                removed += 1
            else:
                kept.append(line)
    if removed:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for line in kept:
                fh.write(line + "\n")
        tmp.replace(path)
    return removed


@dataclass
class _JsonlSinkBase(Generic[PayloadT]):
    """本地 JSONL 落盘骨架（Slice E 共享）。

    抽出两个 recorder 的镜像逻辑：path / retention_days / `__post_init__` 校验 /
    `_write_payload`（脱敏 + 序列化 + 追加，T3 隔离）/ `flush`（fsync 持久化）/ `prune`
    （轮转）。子类只实现 `record` 中的「item → payload 字典」转换，再交 `_write_payload`
    统一落盘。

    句柄管理：**每次操作独立开闭**文件句柄（不持有长生命周期 fh）。理由：
    - 规避 Windows 下 `prune_jsonl` 的 `os.replace` 被本进程持有的打开句柄阻塞
      （POSIX 可 rename 覆盖打开文件，Windows 不行）——独立句柄在 `prune` 时文件处于
      关闭态，`os.replace` 始终可用。
    - 并发由 `threading.Lock` 串行化追加；读 / 轮转时文件已关闭，无 fh 冲突。
    - `record` 写后即 `flush()` + `os.fsync`，落盘即持久化；`flush()` 仅做显式 fsync 兜底。
    """

    path: Path
    retention_days: int = DEFAULT_RETENTION_DAYS

    def __post_init__(self) -> None:
        if self.retention_days < 0:
            raise ValueError("retention_days 必须 >= 0")
        # 友好校验（非白名单，避免单测痛苦）：父目录若已存在则必须是目录，
        # 防止符号链接 / 错误配置把审计血缘写进非目录位置。
        parent = self.path.parent
        if parent.exists() and not parent.is_dir():
            raise NotADirectoryError(
                f"落盘父路径不是目录（可能符号链接跟随 / 配置错误）：{parent}"
            )
        self._fh_lock = threading.Lock()

    def _write_payload(self, payload: Mapping[str, object]) -> None:
        """脱敏守卫 + 序列化 + 追加一行（T3 隔离，绝不外抛）。"""
        try:
            assert_desensitized(payload)
            line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        except Exception:
            log.exception("decision.sink_serialize_failed", path=str(self.path))
            return
        try:
            with self._fh_lock, self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()  # Python 缓冲刷到 OS 页缓存
                os.fsync(fh.fileno())  # 落盘即持久化
        except Exception:
            log.exception("decision.sink_write_failed", path=str(self.path))

    def flush(self) -> None:
        # 每条 record 写后已 fsync，此处做显式持久化兜底（文件不存在则 no-op）。
        try:
            if not self.path.exists():
                return
            with self._fh_lock, self.path.open("rb") as fh:
                os.fsync(fh.fileno())
        except Exception:
            log.exception("decision.sink_flush_failed", path=str(self.path))

    def prune(self, now_utc: datetime | None = None) -> int:
        """按 `retention_days` 删除过期记录（本地 UTC），返回删除行数。

        文件句柄在 `record` 后即关闭（不持有长生命周期 fh），故 `prune` 时文件处于关闭态，
        Windows 下 `prune_jsonl` 的 `os.replace` 不会被本进程持有的打开句柄阻塞。
        """
        with self._fh_lock:
            return prune_jsonl(self.path, self.retention_days, now_utc)


@dataclass
class JsonlTraceRecorder(_JsonlSinkBase[DecisionTrace]):
    """本地 JSONL 落盘 recorder（Slice E）。

    实现 `DecisionTraceRecorder`：每条已封口 trace 经 `to_dict` 序列化 + `assert_desensitized`
    守卫后，作为一行 JSON 追加到 `path`（本地文件，绝不联网，ADR-0002）。落盘即持久化
    （无需等待 `flush`），`flush` 仅做 fsync。失败隔离（T3）：序列化 / 守卫 / 写盘异常一律
    `log.exception`，**绝不外抛**影响决策。

    `retention_days` 默认 30（对齐 ADR-0027 D9 MEDIUM）；`prune()` 按本地 UTC 删除过期记录。
    """

    def record(self, trace: DecisionTrace) -> None:
        try:
            payload = trace.to_dict()
        except Exception:
            log.exception(
                "decision.trace_serialize_failed", decision_id=trace.identity.decision_id
            )
            return
        self._write_payload(payload)


@dataclass
class JsonlABRunRecorder(_JsonlSinkBase[DecisionABRun]):
    """决策层双轨运行（`DecisionABRun`）的本地 JSONL 落盘，供 ADR-0030 Slice C 产出。

    复用与 `JsonlTraceRecorder` 相同的脱敏守卫与保留期轮转；每条 AB run 序列化两臂 trace
    后追加一行。失败隔离同 `JsonlTraceRecorder`（T3）。
    """

    def record(self, run: DecisionABRun) -> None:
        try:
            payload = run.to_dict()
        except Exception:
            log.exception(
                "decision.abrun_serialize_failed", correlation_id=run.correlation_id
            )
            return
        self._write_payload(payload)


__all__ = [
    "DEFAULT_RETENTION_DAYS",
    "DesensitizationError",
    "JsonlABRunRecorder",
    "JsonlTraceRecorder",
    "assert_desensitized",
    "prune_jsonl",
]
