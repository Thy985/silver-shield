"""pytest 公共配置：把 src 加入路径，便于导入 home_perception 包。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
