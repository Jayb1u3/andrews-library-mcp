# Changelog

All notable changes to this project will be documented here. The project follows [Semantic Versioning](https://semver.org/).

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
