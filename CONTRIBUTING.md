# Contributing

Thank you for improving Andrews Library MCP. Contributions must preserve three invariants: **read-only behavior**, **no user credentials**, and **dependency-free local execution**.

## Before opening an issue

- Confirm the problem still occurs against the current `main` branch.
- Identify the affected tool and upstream public service.
- Remove cookies, authorization headers, request IDs, personal names, and licensed content.
- For a parser regression, describe the public response shape or provide a minimal synthetic fixture. Do not upload a raw authenticated capture.

Security problems belong in a private security advisory; see [SECURITY.md](SECURITY.md).

## Development setup

```bash
git clone https://github.com/Jayb1u3/andrews-library-mcp.git
cd andrews-library-mcp
/usr/bin/python3 --version
```

No package installation or virtual environment is required. Python 3.9+ is supported.

## Change workflow

1. Create a focused branch: `feat/...`, `fix/...`, `docs/...`, or `test/...`.
2. Read [AGENTS.md](AGENTS.md) and the model-operating and architecture sections of [README.md](README.md) before changing `server.py` or schemas.
3. Add or update synthetic offline tests for behavior changes.
4. Keep stdout protocol-only; use stderr for diagnostics.
5. Run the complete gate:

```bash
/usr/bin/python3 -m unittest -q test_server.py
/usr/bin/python3 scripts/protocol_smoke.py
/usr/bin/python3 scripts/security_scan.py
/usr/bin/python3 -m py_compile server.py test_server.py scripts/*.py
```

6. Inspect the staged diff and confirm no local runtime artifacts are included:

```bash
git status --short
git diff --cached
```

7. Open a pull request describing the user-visible behavior, evidence, risks, and rollback.

## Tool-contract rules

- Tool names are public API. Do not rename or remove them without a deprecation period.
- Tool descriptions must say when to use the tool, when not to, and expected argument formats.
- Validate model-supplied inputs at the boundary.
- Bound untrusted response sizes and preserve pagination/continuation metadata.
- Errors must state the bad input, expected format, and next recovery action.
- Public endpoint parsers must fail clearly rather than return invented or stale values.

## Tests

Tests must be deterministic, offline, and non-personal. Preferred fixtures are small synthetic dictionaries or HTML/JSON fragments that preserve only the minimum response shape.

A pull request changing a tool schema should test:

- `tools/list` exposes the intended schema;
- valid representative input works;
- malformed and boundary input produce actionable errors;
- stdout remains parseable JSON-RPC;
- shutdown reaches clean EOF.

## Pull-request checklist

- [ ] Read-only and credential-free boundaries remain intact
- [ ] No user, account, cookie, token, licensed document, or private response was added
- [ ] Existing tool names remain compatible or are explicitly deprecated
- [ ] Tests cover the change and all local gates pass
- [ ] Documentation and roadmap are updated when behavior or direction changes
- [ ] Rollback is a clean `git revert`

## Release approach

Use semantic versioning. Patch releases fix parsers or documentation without changing tool contracts; minor releases add backward-compatible tools or fields; major releases may change/remove public tool contracts after deprecation.

## License

By contributing, you agree that your contribution is licensed under the repository's [MIT License](LICENSE).
