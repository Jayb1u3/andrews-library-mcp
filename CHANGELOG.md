# Changelog

All notable changes to this project will be documented here. The project follows [Semantic Versioning](https://semver.org/).

## [1.2.1] - 2026-08-26

### Fixed

- Protocol hardening: lapped gaps in non-object validation to prevent server crashes on falsy values (`[]`, `""`, `0`, `false`)
- Digital Commons: fixed silent truncation of records (was capped at 40) and implemented automatic resumption token following for `list_sets` to ensure exhaustive collection discovery
- Security: implemented strict public-network verification for `save_work` to prevent SSRF to local or private IP ranges; added HTTP/HTTPS scheme validation for all links
- Correctness: fixed `journal_lookup` to only claim `print_holdings` when items have actual call numbers or locations, preventing overstatement of availability

## [1.2.0] - 2026-08-26

### Added

- `save_work`: legitimately save research full text locally — resolves DOIs to legal open-access copies (Unpaywall, OpenAlex fallback) and downloads them; extracts Digital Commons PDF URLs (returning a one-click browser link when the bepress CDN blocks non-browser fetches); accepts direct open PDF URLs. Streams through a bounded `.part` file with a 100 MB cap and no silent overwrites.

### Security

- `save_work` refuses licensed/EZproxy-gated hosts by design and responds with the sanctioned browser + Zotero capture path — valid user credentials do not make automated vendor retrieval license-compliant, so the tool never attempts it.

## [1.1.0] - 2026-08-26

### Added

- `journal_lookup`: serial-scoped catalog search answering "does the library carry journal X" with EZproxy-wrapped online links and print holdings (pieces, call numbers, locations), with an unfiltered fallback if the serial type filter returns nothing
- `digitalcommons`: `list_sets` (collection listing) and `set`-scoped harvesting
- `library_links`: interlibrary loan form, MeLCat ILL, MeL eLibrary, and Ask a Librarian links
- Full offline test suite (29 tests, live smokes behind `RUN_LIVE=1`)

### Fixed

- Protocol hardening found by contract-harness review: non-object `params`/`arguments` now return clean errors instead of tracebacks; malformed JSON input returns a `-32700` parse error; protocol-version negotiation falls back to the newest supported version instead of echoing unknown versions; every tool schema declares `required` and `additionalProperties: false`
- `course_reserves` description now states that reserve items themselves are not guest-readable (browser link-out required)

## [1.0.0] - 2026-08-25

### Added

- Initial public release with ten read-only James White Library tools
- Dependency-free MCP stdio transport
- Hermes installation and model-operating guide
- Offline unit, protocol, and security gates
- Contributor, security, agent, and roadmap documentation
