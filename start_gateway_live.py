import subprocess
import time
import requests
import sys

proc = subprocess.Popen(
    [sys.executable, 'scripts/run_demo.py', '--live', '--scenario', 'golden_repeated_visit'],
    cwd=r'D:\Projects\Active\silver-shield'
)
time.sleep(10)
try:
    r = requests.get('http://127.0.0.1:8765/health', timeout=5)
    print(r.json())
except Exception as e:
    print(f'Error: {e}')
finally:
    proc.terminate()