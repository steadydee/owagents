#!/usr/bin/env python3
"""Fresh-session model UAT with offline tools; never connects to Telegram/Operations.

Set DEEPSEEK_API_KEY in the process environment. This tests model + OpenClaw +
plugin instructions, not the real Operations HTTP contract or Telegram ingress.
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
assert os.environ.get("DEEPSEEK_API_KEY"), "Set DEEPSEEK_API_KEY in the environment (never in arguments)."

with tempfile.TemporaryDirectory(prefix="cuenta-bootstrap-uat-") as tmp:
    root = Path(tmp)
    workspace = root / "workspace"
    shutil.copytree(ROOT / "openclaw/agents/cuenta", workspace)
    tool_dir = workspace / "tools/owlswatch_intake"
    shutil.copytree(ROOT / "tools/owlswatch_intake", tool_dir)
    shutil.copyfile(tool_dir / "tests/fixture_server.py", tool_dir / "server.py")
    profile = json.loads((ROOT / "openclaw/profiles/owlswatch/openclaw.example.json").read_text())
    agent = next(a for a in profile["agents"]["list"] if a["id"] == "cuenta")
    agent.update(workspace=str(workspace), agentDir=str(root / "agent"), model="deepseek/deepseek-reasoner")
    config = {
        "agents": {"defaults": {"model": {"primary": "deepseek/deepseek-reasoner"}}, "list": [agent]},
        "gateway": {"mode": "local"},
        "models": {"providers": {"deepseek": {
            "baseUrl": "https://api.deepseek.com", "api": "openai-completions",
            "apiKey": "${DEEPSEEK_API_KEY}", "models": [{
                "id": "deepseek-reasoner", "name": "DeepSeek Reasoner", "reasoning": True,
                "input": ["text"], "contextWindow": 128000, "maxTokens": 8192,
            }],
        }}},
        "plugins": {"allow": ["owlswatch-intake", "deepseek"], "load": {"paths": [str(tool_dir)]},
                    "entries": {"owlswatch-intake": {"enabled": True}, "deepseek": {"enabled": True}}},
    }
    config_path = root / "openclaw.json"
    config_path.write_text(json.dumps(config))
    env = dict(os.environ, OPENCLAW_CONFIG_PATH=str(config_path), OPENCLAW_STATE_DIR=str(root / "state"),
               OPENCLAW_HOME=str(root / "home"), PYTHONPYCACHEPREFIX=str(root / "pycache"))
    # The fixture process cannot accidentally inherit application or Telegram credentials.
    for key in list(env):
        if key.startswith(("TELEGRAM_", "EXPENSE_", "OPERATIONS_", "OWLSWATCH_")):
            del env[key]
    for scenario in ("success", "vision_failure"):
        calls = root / f"{scenario}-calls.jsonl"
        env.update(CUENTA_UAT_CALLS=str(calls), CUENTA_UAT_SCENARIO=scenario)
        prompt = (
            "Receipt photo received. Caption: groceries. "
            "Telegram metadata: chat_id=-1000000000000, message_id=42, message_thread_id=2, "
            "file_id=fixture_photo, sender_name=Test User, date=2026-09-18. "
            "Process this receipt."
        )
        result = subprocess.run([
            "openclaw", "agent", "--local", "--agent", "cuenta", "--session-key",
            f"agent:cuenta:uat-{scenario}", "--message", prompt, "--json", "--timeout", "180",
        ], env=env, capture_output=True, text=True, timeout=240)
        if result.returncode:
            diagnostic = (result.stderr + result.stdout)[-3000:].replace(os.environ["DEEPSEEK_API_KEY"], "[redacted]")
            raise AssertionError(f"OpenClaw UAT failed ({scenario}); no real receipt was created.\n{diagnostic}")
        entries = [json.loads(line) for line in calls.read_text().splitlines()]
        names = [e["name"] for e in entries]
        assert "owlswatch_telegram_download_file" in names, names
        assert "owlswatch_operations_upload_attachment" in names, names
        assert "owlswatch_vision_extract_receipt" in names, names
        creates = [e["args"]["payload"] for e in entries if e["name"] == "owlswatch_operations_create_expense_draft"]
        assert len(creates) == 1, names
        assert creates[0]["idempotencyKey"] == "telegram--1000000000000-42"
        assert creates[0]["attachments"], "The draft must retain its receipt."
        if scenario == "vision_failure":
            assert creates[0]["agent"]["extractionStatus"] == "failed"
            expense = creates[0]["expense"]
            assert not expense.get("vendorName") and not expense.get("vendor_name"), "Do not invent a vendor."
            assert expense.get("totalAmount") is None and expense.get("total_amount") is None, "Do not invent an amount."
        assert "read" not in names
        assert "owlswatch_telegram_send_message" not in names
        transcripts = list((root / "state/agents/cuenta/sessions").glob("*.jsonl"))
        assert transcripts, "UAT must inspect the actual model tool-call transcript."
        for transcript in transcripts:
            for line in transcript.read_text().splitlines():
                message = json.loads(line).get("message", {})
                for part in message.get("content", []) if isinstance(message.get("content"), list) else []:
                    assert not (part.get("type") == "toolCall" and part.get("name") == "read"), "Unavailable read regression"
        assert "https://operations.example.test/expenses/test-expense" in result.stdout
        print(f"PASS {scenario}: fresh session, preserved photo, one idempotent draft, review link, no read loop", flush=True)
