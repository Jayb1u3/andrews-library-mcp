#!/usr/bin/env python3
"""Dependency-free MCP stdio smoke test for server.py."""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER = ROOT / "server.py"
requests = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": "repo-smoke", "version": "1"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
]
payload = "".join(json.dumps(item) + "\n" for item in requests)
proc = subprocess.run(
    [sys.executable, str(SERVER)], input=payload, text=True,
    capture_output=True, timeout=15, check=False,
)
if proc.returncode != 0:
    raise SystemExit("server exited {}: {}".format(proc.returncode, proc.stderr[-1000:]))
lines = [line for line in proc.stdout.splitlines() if line.strip()]
if len(lines) != 2:
    raise SystemExit("expected 2 JSON-RPC responses, got {}".format(len(lines)))
responses = [json.loads(line) for line in lines]
if responses[0].get("result", {}).get("protocolVersion") != "2025-11-25":
    raise SystemExit("protocol negotiation failed")
tools = responses[1].get("result", {}).get("tools")
if not isinstance(tools, list) or not tools:
    raise SystemExit("tools/list returned no tools")
print("PASS: clean stdio protocol, {} tools, clean EOF".format(len(tools)))
