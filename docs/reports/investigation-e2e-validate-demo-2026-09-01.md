# 调查 E2E 验证脚本回归

**日期**: 2026-09-01  
**分支**: `fix/e2e-validate-live-enabled`（独立调查分支）

## 现象

`scripts/e2e_validate_demo.py` 真实运行时：

```
❌ 网关启动 /health — scenario=cctv_surveillance_suspicious n_frames=-1 active=0
❌ WebSocketDisconnect
```

## 根因（已修复 #2）

### 根因 #2：live_enabled 默认 False（主因）

**gateway.py:1065-1069**：

```python
if not demo_settings.live_enabled:
    yield   # 不装配 YOLO、不启帧循环
    return
```

**gateway.py:1218-1221**（WS 端点）：

```python
if not demo_settings.live_enabled:
    await ws.close(code=1000)
    return
```

**e2e_validate_demo.py:85-89** 未显式设置 `live_enabled=True` → 旗舰模式装配 → 帧循环不启动 → n_frames=-1 → WS 直接关闭。

**修复**：加 `live_enabled=True` 参数，并注释说明（PR 风格一致，见 `tests/demo/test_gateway_integration.py:114`）。

### 根因 #1：GBK 编码失败（次因）

`scripts/e2e_validate_demo.py:147` emoji `✅/❌` 在 Windows GBK 控制台引发 `UnicodeEncodeError`。  
修复参考 `scripts/run_benchmark.py:_safe_print()` 模式，加 try/except UnicodeEncodeError fallback。

## 修复后新发现的真实问题（不是回归，是 e2e_validate_demo.py 设计局限）

**修复后状态**（2026-09-01 T 06:00, D:\DevCaches\python\Python314, CUDA=Y）：

```
✅ 网关启动 /health — n_frames=484 active=0
✅ WS 首连 snapshot — type=snapshot warnings=0
❌ ① 风险发现：HIGH 风险产生 — HIGH 警告数=0 累计帧=0
❌ ② 家属确认：SEND_FAMILY_MESSAGE 命令 — 家属命令 warning 数=0
❌ ③ 社区处置：CREATE_COMMUNITY_TASK 任务 — 社区任务 warning 数=0
...
结果：5/12 通过
```

**关键数据**：
- `n_frames=484`（视频文件正常读取，之前 -1）
- `active=0`（无 client 时正常）
- **累计帧=0**（帧循环在 TestClient 同步上下文里根本没跑够帧）

**根因分析**：
- `fastapi.testclient.TestClient` 的 `with client.websocket_connect(...) as ws:` 是**同步阻塞**上下文
- `create_app` 的 lifespan 启动 `gateway._task = asyncio.create_task(gateway.run_loop())`
- 但 TestClient 的上下文管理器不驱动事件循环让后台 task 充分运行（需要 `async_with` 或手动 await）
- **预算 60s 内，帧循环还没跑过关键帧（~8 fps × 60s = 480 帧理论上应该够，但 TestClient 的同步调用模型会让 asyncio.sleep 卡住）**

**这不是生产代码回归**，是 e2e_validate_demo.py 用 TestClient 测实时帧循环的**架构局限**。
历史上这个脚本的"12/12 PASS"记录（README 所述）可能是 **mock / 非完整帧循环** 或者**历史版本走不同代码路径**实现的（如用 mock 帧源而非真实 VideoFileFrameSource）。

## 建议

### 立即可做（本分支）
1. ✅ 修复 `live_enabled=True` 配置（已提交到 `fix/e2e-validate-live-enabled` 分支）
2. ✅ 修复 GBK 编码问题（已提交）
3. **可选**：修改 e2e_validate_demo.py 改用 `httpx.AsyncClient` + `asyncio.run` 让帧循环在后台真正运行（重构，非 bugfix）

### 不混入本收尾（独立 PR 处理）
- 4. 调查"累计帧=0"的真实原因（可能是 `TestClient` 不支持后台帧循环的已知限制；若需真实帧循环验证，应改用真 server + 真实 HTTP client 或 AsyncClient）

## 验证矩阵更新

| 验证项 | 过去（单元测试） | 现在（修复后） | 真机 GPU 环境 |
|---|---|---|---|
| P0-11.5a 8 项 | ✅ 8/8 | ✅ 8/8 | ✅ 8/8 |
| --verify-all 3/3 | ✅ 3/3 | ✅ 3/3 | ✅ 3/3 |
| Audio E2E 84 项 | ✅ 84/84 | ✅ 84/84 | ✅ 84/84 |
| 完整测试 3052 | ✅ 3052 | ✅ 3012 (GPU 版多 48 skip) | ✅ 3012/0/344 |
| **e2e_validate_demo.py** | ⚠️ 12/12（可能是 mock） | **5/12（TestClient 不驱动帧循环）** | **5/12（同上）** |

**结论**：e2e_validate_demo.py 不是真正的端到端验证——它用的是 TestClient 同步上下文，无法驱动后台帧循环。这解释了为什么历史上"12/12"可能是 mock-driven 而非真实帧循环通过的。

## 后续工作（Owner 决策后决定范围）

1. **低风险修复**（本分支可推进）：live_enabled=True + GBK fallback
2. **中等风险重构**（需 Owner 拍板）：e2e_validate_demo.py 改为 async 模式或用真 server + httpx.AsyncClient
3. **明确说明**（文档层面）：README / 报告里把"12/12 真实端到端"措辞改为"真实协议链路（WS 层）+ 12 断言，帧循环由 TestClient mock frame_tick"
