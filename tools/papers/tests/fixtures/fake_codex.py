#!/usr/bin/env python3
"""Offline CLI stand-in: exercises real pipes, timeouts and process cleanup."""
import json
import os
from pathlib import Path
import sys
import time

args = sys.argv[1:]
if "--help" in args:
    print("--output-schema --ephemeral --ignore-user-config --skip-git-repo-check --image")
    raise SystemExit(0)
if "--version" in args:
    print("codex-cli test-fixture")
    raise SystemExit(0)

model = args[args.index("--model") + 1]
effort = next(value.split("=", 1)[1].strip('"') for value in args if value.startswith("model_reasoning_effort="))
print(f"model: {model}\nreasoning effort: {effort}\nuser", file=sys.stderr, flush=True)
prompt = sys.stdin.read()
record = {"args": args, "prompt": prompt, "codex_home": os.environ.get("CODEX_HOME"),
          "has_zotero_key": "ZOTERO_API_KEY" in os.environ,
          "has_parent_session": "CODEX_SESSION_ID" in os.environ}
Path("received.json").write_text(json.dumps(record))
print("received prompt", file=sys.stderr, flush=True)
time.sleep(float(os.environ.get("PAPER_TEST_DELAY", "0")))
output = Path(args[args.index("--output-last-message") + 1])
output.write_text(Path(__file__).with_name("paper.json").read_text())
