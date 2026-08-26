#!/usr/bin/env python3
"""Fail when a repository contains runtime credentials or common secret forms."""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
FORBIDDEN_NAMES = {
    "auth.json", "preferences.json", "token_redirect.txt", "connect_result.json",
    "cookies.json", ".env", "stderr.log",
}
SKIP_PARTS = {".git", "__pycache__"}
PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    "API key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "Moodle token": re.compile(r"(?i)\b[0-9a-f]{32}\b"),
    "bearer token": re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    "personal home path": re.compile(r"/Users/(?!yourname\b)[^/\s\"']+"),
}
findings = []
for path in ROOT.rglob("*"):
    if not path.is_file() or any(part in SKIP_PARTS for part in path.parts):
        continue
    rel = path.relative_to(ROOT)
    if rel == pathlib.Path("scripts/security_scan.py"):
        continue  # The scanner necessarily contains its own detection signatures.
    if path.name in FORBIDDEN_NAMES:
        findings.append("forbidden runtime artifact: {}".format(rel))
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    for label, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append("{}:{}: {}".format(rel, line, label))
if findings:
    print("SECURITY SCAN FAILED")
    print("\n".join(findings))
    raise SystemExit(1)
print("PASS: no forbidden runtime artifacts or common secret patterns")
