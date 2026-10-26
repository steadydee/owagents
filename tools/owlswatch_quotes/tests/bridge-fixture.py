"""Offline subprocess behaviors for the quote bridge; no service calls."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

args = json.load(sys.stdin)
mode = args.get("mode", "ok")
if mode == "stderr":
    sys.stderr.write("private diagnostic fixture\n" * 100000)
elif mode == "large":
    print("x" * 300000)
    sys.exit(0)
elif mode == "invalid":
    print("not json")
    sys.exit(0)
elif mode == "nonzero":
    print('{"ok":true}')
    sys.exit(7)
elif mode == "hang":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child = subprocess.Popen([sys.executable, "-c", "import signal,time;signal.signal(signal.SIGTERM, signal.SIG_IGN);time.sleep(60)"])
    Path(args["pids"]).write_text(json.dumps([os.getpid(), child.pid]))
    time.sleep(60)
print(json.dumps({"ok": True, "fixture": "complete"}))
