"""pytest 公共配置：把 src 与仓库根加入路径。

- src：便于导入 home_perception 包
- 仓库根：便于导入 benchmark 包（P0-4 性能基准）
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, _ROOT)
