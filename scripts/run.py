"""启动入口：python scripts/run.py（开发态把 src 加入路径）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from home_perception.main import main

if __name__ == "__main__":
    main()
