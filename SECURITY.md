# Security Policy

## Supported versions

Security fixes are applied to the latest release and current `main` branch.

## Report privately

Use GitHub's **Security → Report a vulnerability** flow for suspected vulnerabilities. Do not open a public issue when a report includes or could reveal credentials, cookies, authenticated content, exploitable URLs, or personal data.

Include:

- affected version or commit;
- affected tool and public upstream service;
- minimal reproduction using synthetic/non-personal data;
- expected versus actual behavior;
- impact and suggested mitigation, if known.

Do **not** include real institutional credentials, browser sessions, licensed documents, or private patron records. If a credential was exposed, revoke/rotate it first; deleting Git history alone is not remediation.

## Security model

This server is local, read-only, and credential-free. It fetches public data, dynamically obtains an anonymous catalog guest token, and returns browser links for any authenticated user action. It must never collect passwords, cookies, or SSO sessions.

## Out of scope

- Availability or correctness failures in third-party public services, unless the server handles them unsafely
- Social engineering against users or institutions
- Vulnerabilities requiring credentials or content submitted in violation of this policy

## Disclosure

Please allow maintainers reasonable time to reproduce, patch, test, and release a fix before public disclosure.
