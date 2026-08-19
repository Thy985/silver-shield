import subprocess
import sys
import os

# Run the gateway in the foreground
os.chdir(r'D:\Projects\Active\silver-shield')
proc = subprocess.Popen([sys.executable, 'scripts/run_demo.py', '--live', '--scenario', 'golden_repeated_visit'])
try:
    proc.wait()
except KeyboardInterrupt:
    proc.terminate()