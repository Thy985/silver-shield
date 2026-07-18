## 关联
- Issue: #<num>
- ROADMAP: <P0-x>

## 改动说明
<what + why>

## 测试方式
- [ ] 手动测试：<步骤>
- [ ] 自动测试：`pytest tests/ -q`（或 `pytest tests/ -k "<pattern>"`）

## 影响范围
- [ ] 影响对外事件 Schema（docs/07）
- [ ] 影响上报接口契约（docs/06）
- [ ] 影响性能
- [ ] 无影响（仅文档 / 重构）

## 自检
- [ ] `ruff check src tests` 无 error
- [ ] `pytest tests/ -q` 全部通过
- [ ] 文档已同步（如需）
- [ ] 已写测试（新功能 / bug 修复）
- [ ] 提交前已确认 `.gitignore` 覆盖新增产物（无凭证 / 大文件 / 缓存误提交）
- [ ] commit message 含 `Task scope`
