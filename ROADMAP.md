# Roadmap

This roadmap describes intended direction, not guaranteed dates. Security, read-only behavior, and stable tool contracts take precedence over speed.

## Current baseline

- Eleven read-only, task-shaped tools (journal lookup added in 1.1.0)
- Public catalog, holdings, reserves, hours, rooms, databases, guides, Digital Commons, EZproxy-link, and navigation coverage
- Pure Python standard-library stdio server
- Offline tests, protocol smoke gate, and secret/runtime-artifact scan
- Hermes installation and model-operating guidance

## Near term

- Expand synthetic parser fixtures for upstream HTML/JSON shape changes
- ~~Add deterministic timeout and malformed-upstream tests~~ (1.1.0: malformed-input and error-path coverage via test suite + contract harness)
- Improve continuation metadata consistency across paginated tools
- Document supported Python/Hermes versions per release
- Add release tags and generated checksums

## Next

- Evaluate result-size budgets against realistic catalog and Digital Commons queries
- Add model-in-the-loop evaluation cases for tool selection and identifier handling
- Improve accessibility and booking-link context without crossing into authenticated automation
- Add endpoint health diagnostics that reveal service status without leaking response bodies
- Establish deprecation metadata for future tool-contract evolution

## Later / research

- Optional packaging/catalog metadata for one-command Hermes MCP installation
- Reusable institution-configuration layer for forks targeting other libraries
- Contract tests against documented public upstream schemas where available
- Automated drift alerts for public endpoint changes

## Non-goals

- Storing institutional credentials or browser sessions
- Downloading licensed full text through the MCP
- Automating reservations, renewals, checkouts, fines, or account mutations
- Bypassing EZproxy, SSO, access controls, robots restrictions, or provider terms
- Replacing librarians or presenting catalog metadata as authoritative legal/licensing advice

## How roadmap items graduate

A proposal needs a concrete user workflow, trust-boundary analysis, synthetic tests, backward-compatible tool design, bounded output, and a documented rollback before implementation.
