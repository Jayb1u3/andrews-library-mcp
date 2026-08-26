# Changelog

All notable changes to this project will be documented here. The project follows [Semantic Versioning](https://semver.org/).

## [1.2.0] - 2026-08-26

### Added

- `save_work`: save research full text locally (to `~/.hermes/andrews-library/files/`) when a legitimately open copy exists
  - `doi` → open-access copy via Unpaywall, with OpenAlex fallback
  - `url` → Digital Commons records (extracts `citation_pdf_url`) or any open-access PDF link
  - Shared download engine: atomic `.part`→rename, 100 MB cap, filename sanitization, overwrite guard, real PDF `Content-Type` check
  - Digital Commons CDN bot wall (HTTP 403) degrades gracefully to a direct `pdf_url` for one-click browser save
- Offline `SaveWork` test class covering the above (no gate/refusal assertions; personal-email-leak guard)

### Fixed

- Unpaywall contact email is now configurable via the `ANDREWS_LIBRARY_UNPAYWALL_EMAIL` environment variable and defaults to a placeholder — no personal address in source

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
