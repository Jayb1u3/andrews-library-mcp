# Library MCP

A dependency-free Model Context Protocol (MCP) server for the public systems of AU JW Library. Every source is fully reachable: catalogs, holdings, hours, rooms, databases, guides, a complete and paginated Digital Commons harvest, legal open-access full-text saving, and browser link-outs for licensed content.

> **Community project:** This repository is not an official Andrews University, James White Library, EBSCO, LibCal, LibGuides, Moodle, or Anthology product. Public upstream services may change without notice.

## What it provides

The server exposes fourteen workflow-oriented tools:

| Tool | Use it for |
|---|---|
| `catalog_search` | Search books, media, scores, and other catalog records |
| `catalog_item` | Retrieve holdings, call numbers, status, and links for one record |
| `journal_lookup` | Check whether the library carries a specific journal, online (EZproxy links) or in print (call numbers, locations) |
| `course_reserves` | Find reserve materials by course, instructor, or title |
| `hours` | Get current and upcoming library opening hours |
| `rooms` | Find study-room availability and booking links |
| `databases` | Browse or search the databases A–Z list |
| `guides` | Find subject and course research guides |
| `digitalcommons` | List all Digital Commons collections, harvest recent or collection-scoped records, and follow resumption tokens for complete paginated harvests |
| `save_work` | Save open-access full text locally (DOI via Unpaywall → Semantic Scholar → OpenAlex, Digital Commons, or open PDF links); licensed content gets the browser+Zotero path |
| `ezproxy_link` | Wrap a publisher URL in Andrews' EZproxy sign-in URL |
| `library_links` | Retrieve curated links for major library services |
| `list_saved` | List open-access PDFs previously saved by `save_work` (read-only recall of what you already have) |
| `citation_export` | Export a catalog record as RIS or BibTeX for Zotero / a bibliography |

The tools never mutate library state and never touch credentials. The one local write is `save_work`, which stores a legally-obtained open-access PDF into `~/.hermes/andrews-library/files/`. All other authenticated actions (booking, EZproxy, ILL) happen only after the user opens a returned URL in their own browser.

## Privacy and trust boundary

- The repository contains **no user credential, API key, cookie, browser session, or licensed content**.
- Catalog access uses an anonymous guest token obtained dynamically and cached only in process memory.
- Usernames, passwords, SSO cookies, and proxy sessions never pass through MCP tool arguments.
- Public tenant, library, LibCal, and service identifiers are institutional configuration—not personal secrets.
- The Unpaywall contact email used by `save_work` is a neutral project address by default and configurable via the `ANDREWS_LIBRARY_UNPAYWALL_EMAIL` environment variable — never a personal address.
- Semantic Scholar and OpenAlex are credential-free DOI fallbacks; no API key or user account is stored or required.
- Runtime artifacts and common secret files are excluded by `.gitignore` and checked by `scripts/security_scan.py`.

## Requirements

- Python 3.9 or newer; standard library only
- Network access to the relevant public Andrews/EBSCO/LibCal/LibGuides/Digital Commons services
- An MCP client; the instructions below target [Hermes Agent](https://github.com/NousResearch/hermes-agent)

## Install for Hermes Agent

### 1. Clone the server

```bash
mkdir -p ~/.hermes/mcp-servers
git clone https://github.com/Jayb1u3/andrews-library-mcp.git \
  ~/.hermes/mcp-servers/andrews-library
```

Updating later is a normal fast-forward pull:

```bash
git -C ~/.hermes/mcp-servers/andrews-library pull --ff-only
```

### 2. Register it

Add the following entry under the existing `mcp_servers:` mapping in `~/.hermes/config.yaml`. Hermes resolves `${userHome}` at runtime.

```yaml
mcp_servers:
  andrews-library:
    command: /usr/bin/python3
    args:
      - ${userHome}/.hermes/mcp-servers/andrews-library/server.py
    connect_timeout: 30
    timeout: 120
    supports_parallel_tool_calls: true
    sampling:
      enabled: false
    elicitation:
      enabled: false
```

Use an absolute interpreter path. On macOS, `/usr/bin/python3` follows the local Hermes convention and requires no packages from the Hermes-managed virtual environment.

### 3. Test the real Hermes connection

```bash
hermes mcp test andrews-library
```

A successful test should discover fourteen tools. Start a fresh Hermes session or run `/reload-mcp` on a surface that supports it.

### 4. Try a user workflow

Ask Hermes:

> Use the Andrews Library MCP to search the catalog for Bonhoeffer's *Discipleship*. Show available editions and call numbers. Do not claim an item is available unless the holdings data says so.

## Guide for Hermes and other models

The model sees these tools as `mcp__andrews_library__<tool>` when the server key is `andrews-library`.

### Recommended selection order

1. Start with the task-shaped discovery tool (`catalog_search`, `course_reserves`, `databases`, `guides`, or `digitalcommons`).
2. Use `catalog_item` only with an `instance_id` returned by `catalog_search`; never guess identifiers.
3. For Digital Commons, use `digitalcommons` with `list_sets=true` to find collections, then harvest using `set` and `days`. For large collections, the tool follows resumption tokens automatically to ensure a complete harvest.
4. Use `save_work` for legal open-access PDFs. If a record is licensed/gated, the tool will return the sanctioned browser+Zotero path; do not attempt to bypass these refusals.
5. `catalog_item` surfaces a `doi` when the record has one — pass it straight to `save_work(doi=...)` to fetch the open-access copy. Use `citation_export` to produce a RIS/BibTeX citation from an `instance_id`; use `list_saved` to recall PDFs already downloaded.
6. Use `hours` for opening times and `rooms` for room availability; do not infer one from the other.
6. Use `ezproxy_link` only to construct a browser sign-in link. Never ask a user to give the model an Andrews password or browser cookie.
7. Treat availability, hours, and room slots as time-sensitive. State when the tool response was obtained and provide the source URL when present.
8. If a tool returns pagination or a continuation token, disclose that more results exist before deciding the search is exhaustive.

### Good agent prompts

- “Find James White Library holdings for ISBN `9780800697033`; show call number and current status.”
- “Find course reserves for `DEMO 201` and distinguish exact from partial matches.”
- “What are the library hours this week? Use `hours`, not general web search.”
- “Find a theology research guide for Adventist history.”
- “Create an Andrews EZproxy link for this DOI landing page; do not attempt to sign in.”

### Model safety rules

- Never request, store, or echo an institutional password, cookie, token, or session.
- Do not imply that an EZproxy or room URL proves authorization or guarantees a reservation.
- Do not present public catalog metadata as licensed full text.
- Do not scrape around tool limits by inventing URLs or identifiers.
- If an upstream service is unavailable, report the specific dependency and retry guidance rather than fabricating results.

## Authentication behavior

There is no MCP-side authentication setup. If a returned URL needs SSO, EZproxy, or LibCal authentication, the user signs in directly in their browser. The MCP neither sees nor stores that browser authentication.

## Development

### Repository layout

```text
server.py                 dependency-free stdio MCP server
manifest.yaml             server metadata
config.example.yaml       copyable Hermes configuration
test_server.py            offline unit tests
scripts/protocol_smoke.py dependency-free JSON-RPC smoke test
scripts/security_scan.py  runtime-artifact and common-secret gate
.github/workflows/ci.yml  Linux/macOS, Python 3.9/3.13 CI
AGENTS.md                  repository-local coding-agent invariants
CONTRIBUTING.md            human and automated-contributor workflow
ROADMAP.md                 future development direction
SECURITY.md                vulnerability and credential policy
```

### Run the complete local gate

```bash
/usr/bin/python3 -m unittest -q test_server.py
/usr/bin/python3 scripts/protocol_smoke.py
/usr/bin/python3 scripts/security_scan.py
/usr/bin/python3 -m py_compile server.py test_server.py scripts/*.py
```

Tests are offline and synthetic. Contributors must not commit captured authenticated responses, licensed documents, cookies, or user records as fixtures.

### Architecture

`server.py` intentionally uses only Python's standard library and line-delimited JSON-RPC over stdio. This avoids dependency drift in long-running Hermes gateways. Stdout belongs exclusively to MCP protocol frames; diagnostics must go to stderr. Tool schemas and public names are compatibility contracts.

See [AGENTS.md](AGENTS.md), [CONTRIBUTING.md](CONTRIBUTING.md), and [ROADMAP.md](ROADMAP.md) before changing tools or endpoint parsers.

## Troubleshooting

### Hermes reports that the command cannot be found

Use an absolute interpreter path and confirm it exists:

```bash
/usr/bin/python3 --version
```

### Hermes connects but tools are missing

Run the layers separately:

```bash
/usr/bin/python3 scripts/protocol_smoke.py
hermes mcp test andrews-library
```

Then check YAML indentation and start a fresh session.

### A public endpoint changed

Capture only the minimum **public, non-personal** response shape needed to reproduce the parser failure. Redact request IDs and headers, add a synthetic regression fixture, and open an issue describing the endpoint and date.

### A returned link asks for sign-in

That is expected for EZproxy, licensed resources, or booking. Sign in only in the browser. Never paste credentials into an issue or MCP tool call.

## Project status and roadmap

The server is functional and intentionally narrow. Planned work is tracked in [ROADMAP.md](ROADMAP.md). Tool removals or renames require deprecation because model prompts and MCP allowlists may depend on existing names.

## Security

Read [SECURITY.md](SECURITY.md) before reporting a vulnerability. Never open a public issue containing credentials, cookies, private library records, or authenticated content.

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), preserve the read-only and credential-free boundary, and run the full local gate before opening a pull request.

## License

[MIT](LICENSE)
