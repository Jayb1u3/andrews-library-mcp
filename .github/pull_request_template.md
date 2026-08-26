## Summary

Describe the user-visible change and why it belongs in this MCP.

## Trust-boundary impact

- [ ] Read-only behavior is unchanged
- [ ] No credentials, cookies, personal data, or authenticated content are introduced
- [ ] External-call timeouts and bounded outputs are preserved

## Verification

List exact commands and results.

```text
/usr/bin/python3 -m unittest -q test_server.py
/usr/bin/python3 scripts/protocol_smoke.py
/usr/bin/python3 scripts/security_scan.py
```

## Compatibility

Describe tool-name/schema changes, or state “No public tool-contract change.”

## Rollback

Describe the exact revert/rollback path.
