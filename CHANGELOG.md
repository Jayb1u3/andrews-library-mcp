# Changelog

All notable changes to this project will be documented here. The project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `save_work` now checks Semantic Scholar between Unpaywall and OpenAlex when resolving a DOI to a legal open-access PDF. The credential-free fallback handles positive, negative, not-found, rate-limited, unavailable, and malformed responses without suppressing the remaining resolvers.

### Fixed

- Oversized MCP results are now structurally bounded and remain parseable JSON instead of slicing serialized JSON syntax.
- Resolver outages and malformed responses no longer become unsupported “no open-access copy exists” claims when no resolver completed successfully.
- Interrupted PDF downloads remove partial files, flush and `fsync` completed data, and atomically promote the file with `os.replace`.
- Study-room date ranges now use calendar-day arithmetic and remain correct across daylight-saving transitions.

## [1.2.1+merge] - 2026-08-26

### Fixed

- Reconciled with `origin/main` (which had reverted `save_work` and was later force-pushed as a squashed v1.2.0-era commit): the merge keeps the full-access, hardened v1.2.1 content — `save_work` stays enabled with its SSRF protection and licensed-host refusal, per the final recommendations; adopted the remote's `ANDREWS_LIBRARY_UNPAYWALL_EMAIL` environment variable (configurable, never a personal address in source).
- Security (SSRF): `save_work` Digital Commons detection now uses an exact host match (`_is_digitalcommons`) instead of a substring check, closing a blind-SSRF bypass via hostnames like `digitalcommons.andrews.edu.127.0.0.1.nip.io`; the Digital Commons page fetch runs through a new `_fetch_public_page` with the same public-network + redirect guards as PDF downloads; documented the residual DNS-rebinding risk.
- Input validation: all numeric tool arguments (`limit`, `offset`, `weeks`, `days`) now use a strict coercion helper (`_as_int`) that rejects booleans, fractional floats, non-numeric strings, huge/overflowing integers, and out-of-bounds values with actionable errors instead of raw `ValueError`/`OverflowError` tracebacks; `overwrite` uses strict `_as_bool` parsing so a string like `"false"` no longer silently means true; `rooms` rejects non-string and calendar-invalid dates (e.g. `2026-02-30`).
- Robustness: catalog/journal parsers tolerate upstream shape drift (publication as dict vs list, string entries in `electronicAccess`/`contributors`, non-dict holdings items) via `_first_pub`/`_http_uris`; removed the dead `subjects` filter from `databases` (the A-Z page exposes no subject metadata) and corrected the tool description; `save_work` now strips all common DOI prefixes (`doi:`, `http(s)://doi.org`, `http(s)://dx.doi.org`).
- Documentation accuracy: README, ROADMAP, SECURITY, CONTRIBUTING, and manifest now agree on twelve tools and the single local write (`save_work`).

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
