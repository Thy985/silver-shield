"""Start server and run specific CCTV DOM tests."""
import subprocess
import sys
import time

import requests

BASE = "http://127.0.0.1:8765"
TESTS = sys.argv[1:] if len(sys.argv) > 1 else []


def wait_for_server(timeout: int = 30) -> None:
    for i in range(timeout):
        try:
            requests.get(f"{BASE}/live", timeout=2)
            print(f"Server 就绪（{i+1}s）")
            return
        except requests.exceptions.RequestException:
            pass
        time.sleep(1)
    raise RuntimeError("Server 未在 30s 内就绪")


if __name__ == "__main__":
    proc = subprocess.Popen(
        [sys.executable, "scripts/run_demo.py", "--live",
         "--scenario", "config/demo/scenarios/delivery_courier_normal.yaml"],
        cwd="D:/Projects/Active/silver-shield",
    )
    try:
        wait_for_server()
        cmd = [sys.executable, "-m", "pytest",
               "tests/visualizer/test_delivery_courier_dom_contract.py"] + TESTS
        result = subprocess.run(
            cmd,
            cwd="D:/Projects/Active/silver-shield",
            check=False,
        )
        sys.exit(result.returncode)
    finally:
        proc.terminate()
        proc.wait()
