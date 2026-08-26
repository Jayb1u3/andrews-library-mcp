#!/usr/bin/env python3
"""Andrews University Library (James White Library) MCP server.

Built per hermes-local-conventions.md: pure stdlib, /usr/bin/python3 (3.9),
stdout protocol-only. NO credentials needed anywhere:

- Catalog  : EBSCO Locate = FOLIO/Okapi (okapi-andrews.locate.ebsco.com,
             tenant lt00001186). GET /opac-auth/guest-token returns an
             anonymous token in the x-okapi-token RESPONSE HEADER (204);
             /search/instances takes CQL. expandAll=true includes holdings
             + items with call numbers and live status.
- Hours    : LibCal public JSON (api_hours_today/grid, iid=0).
- Rooms    : LibCal /spaces (lid 5524, gid 9604) — availability read-only;
             BOOKING is always a browser link-out (needs user login).
- Databases: LibGuides az/databases HTML (server-rendered), cached.
- Guides   : LibGuides search HTML.
- DigCommons: bepress OAI-PMH at /do/oai/ (standardized, fully open).
- EZproxy  : link builder only — same rule as the jstor server: NEVER
             automate authenticated downloads through EZproxy.

Python 3.9 gotchas honored (from hermes-learninghub-mcp memory): catch
socket.timeout explicitly (it is not TimeoutError until 3.10).
"""

import html as html_mod
import json
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SERVER_NAME = "andrews-library"
SERVER_VERSION = "1.0.0"
MAX_TEXT = 60_000

OKAPI = "https://okapi-andrews.locate.ebsco.com"
TENANT = "lt00001186"
LOCATE = "https://andrews.locate.ebsco.com"
LIBCAL = "https://andrews.libcal.com"
LIBGUIDES = "https://libguides.andrews.edu"
DIGCOMMONS = "https://digitalcommons.andrews.edu"
EZPROXY_LOGIN = "https://ezproxy.andrews.edu/login?url="
SPACES_LID, SPACES_GID = 5524, 9604
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def log(msg):
    print("[andrews-library-mcp] {}".format(msg), file=sys.stderr, flush=True)


class ToolError(Exception):
    pass


def http(url, method="GET", headers=None, data=None, timeout=30):
    h = {"User-Agent": UA}
    h.update(headers or {})
    if isinstance(data, dict):
        data = urllib.parse.urlencode(data).encode()
        h.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")
    except socket.timeout:
        raise ToolError("request to {} timed out — retry once".format(
            urllib.parse.urlparse(url).netloc))


# --------------------------------------------------------------- catalog

_okapi_token = {"value": None, "obtained": 0}


def okapi_token(force=False):
    if not force and _okapi_token["value"] and time.time() - _okapi_token["obtained"] < 1800:
        return _okapi_token["value"]
    status, headers, _ = http(OKAPI + "/opac-auth/guest-token",
                              headers={"X-Okapi-Tenant": TENANT}, timeout=20)
    tok = None
    for k, v in headers.items():
        if k.lower() == "x-okapi-token":
            tok = v
    if status not in (200, 204) or not tok:
        raise ToolError("could not get a catalog guest token (HTTP {}) — the "
                        "Locate/Okapi service may be down; try again shortly "
                        "or use {} in the browser".format(status, LOCATE))
    _okapi_token.update(value=tok, obtained=time.time())
    return tok


def okapi_get(path, params=None, retry=True):
    url = OKAPI + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    status, _, body = http(url, headers={"X-Okapi-Tenant": TENANT,
                                         "X-Okapi-Token": okapi_token()})
    if status in (401, 403) and retry:
        okapi_token(force=True)
        return okapi_get(path, params, retry=False)
    if status != 200:
        raise ToolError("catalog API returned HTTP {} for {} — narrow the "
                        "query or retry".format(status, path))
    return json.loads(body)


def cql_escape(q):
    return q.replace('"', '\\"')


_locations_cache = {}


def location_name(loc_id):
    # /locations is 403 for guests; /opac-inventory/locations is the
    # guest-accessible mirror (101 locations, discoveryDisplayName).
    if not loc_id:
        return None
    if not _locations_cache:
        try:
            data = okapi_get("/opac-inventory/locations", {"limit": "500"})
            for l in data.get("locations", []):
                _locations_cache[l.get("id")] = l.get("discoveryDisplayName") or l.get("name")
        except Exception:
            _locations_cache["_unavailable"] = True
    return _locations_cache.get(loc_id, loc_id if _locations_cache.get("_unavailable") else None)


def slim_instance(i):
    pub = (i.get("publication") or [{}])[0]
    out = {
        "id": i.get("id"),
        "title": i.get("title"),
        "contributors": [c.get("name") for c in (i.get("contributors") or [])[:4]],
        "published": pub.get("dateOfPublication"),
        "publisher": pub.get("publisher"),
        "type": ", ".join(i.get("instanceFormats") or []) or None,
        "isbns": (i.get("isbns") or [])[:3] or None,
        "catalog_url": "{}/instances/{}".format(LOCATE, i.get("id")),
    }
    return {k: v for k, v in out.items() if v}


def tool_catalog_search(args):
    query = (args.get("query") or "").strip()
    if not query:
        raise ToolError('`query` required, e.g. {"query": "bonhoeffer discipleship"}')
    limit = min(int(args.get("limit") or 10), 30)
    offset = int(args.get("offset") or 0)
    field = args.get("field") or "keyword"
    if field not in ("keyword", "title", "contributors", "subject", "isbn"):
        raise ToolError("field must be keyword|title|contributors|subject|isbn")
    cql = '{} all "{}"'.format(field, cql_escape(query))
    data = okapi_get("/search/instances",
                     {"query": cql, "limit": str(limit), "offset": str(offset)})
    total = data.get("totalRecords", 0)
    out = {"query": query, "field": field, "total": total, "offset": offset,
           "results": [slim_instance(i) for i in data.get("instances", [])]}
    if total > offset + limit:
        out["more"] = "call again with offset={} for the next page".format(offset + limit)
    return out


def tool_catalog_item(args):
    inst_id = (args.get("instance_id") or "").strip()
    if not re.fullmatch(r"[0-9a-f-]{36}", inst_id):
        raise ToolError("instance_id must be the 36-char id from catalog_search")
    data = okapi_get("/search/instances",
                     {"query": "id=={}".format(inst_id), "expandAll": "true", "limit": "1"})
    if not data.get("instances"):
        raise ToolError("no instance {} — use an id from catalog_search".format(inst_id))
    i = data["instances"][0]
    out = slim_instance(i)
    out["subjects"] = [s.get("value") if isinstance(s, dict) else s
                      for s in (i.get("subjects") or [])[:8]] or None
    out["editions"] = i.get("editions") or None
    copies = []
    for it in (i.get("items") or [])[:15]:
        cn = (it.get("effectiveCallNumberComponents") or {}).get("callNumber")
        copies.append({
            "call_number": cn,
            "status": (it.get("status") or {}).get("name"),
            "location": location_name(it.get("effectiveLocationId")),
            "material": (it.get("materialType") or {}).get("name")
                        if isinstance(it.get("materialType"), dict) else it.get("materialType"),
            "barcode": it.get("barcode"),
        })
    out["copies"] = copies
    ea = [e.get("uri") for e in (i.get("electronicAccess") or [])[:3] if e.get("uri")]
    if ea:
        out["electronic_access"] = ea
    return {k: v for k, v in out.items() if v is not None}


def tool_course_reserves(args):
    query = (args.get("query") or "").strip()
    limit = min(int(args.get("limit") or 15), 40)
    cql = 'name all "{}"'.format(cql_escape(query)) if query else "name=*"
    data = okapi_get("/opac-courses/courses",
                     {"query": cql, "limit": str(limit)})
    courses = []
    for c in data.get("courses", []):
        courses.append({
            "course": c.get("name"),
            "number": c.get("courseNumber"),
            "department": (c.get("departmentObject") or {}).get("name"),
            "listing_id": c.get("courseListingId"),
        })
    return {"query": query or "(all)", "total": data.get("totalRecords"),
            "courses": courses,
            "reserves_page": LOCATE + "/course-reserves",
            "note": "Open reserves_page and search the course name to see the "
                    "actual reserve items and borrow them."}


# ----------------------------------------------------------------- hours


def tool_hours(args):
    weeks = min(int(args.get("weeks") or 1), 4)
    status, _, body = http("{}/api_hours_grid.php?iid=0&format=json&weeks={}"
                           .format(LIBCAL, weeks))
    if status != 200:
        raise ToolError("LibCal hours returned HTTP {} — see {}/hours/ in the "
                        "browser".format(status, LIBCAL))
    data = json.loads(body)
    out = []
    for loc in data.get("locations", []):
        days = []
        for week in loc.get("weeks", []):
            for day, info in week.items():
                t = info.get("times", {})
                if t.get("status") == "open":
                    hours = ", ".join("{}–{}".format(h.get("from"), h.get("to"))
                                      for h in t.get("hours", []))
                else:
                    hours = t.get("status", "unknown")
                days.append({"day": day, "date": info.get("date"), "hours": hours})
        out.append({"location": loc.get("name"), "days": days})
    return {"locations": out, "hours_page": LIBCAL + "/hours/"}


# ----------------------------------------------------------------- rooms


def tool_rooms(args):
    date = args.get("date") or time.strftime("%Y-%m-%d")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ToolError("date must be YYYY-MM-DD")
    result = {"date": date,
              "book_url": "{}/spaces?lid={}&gid={}".format(LIBCAL, SPACES_LID, SPACES_GID),
              "note": "Booking always happens in YOUR browser (login required) — "
                      "this tool only reads availability."}
    try:
        end = time.strftime("%Y-%m-%d", time.localtime(
            time.mktime(time.strptime(date, "%Y-%m-%d")) + 86400))
        status, _, body = http(LIBCAL + "/spaces/availability/grid", method="POST",
                               headers={"X-Requested-With": "XMLHttpRequest",
                                        "Referer": result["book_url"]},
                               data={"lid": SPACES_LID, "gid": SPACES_GID, "eid": -1,
                                     "seat": 0, "seatId": 0, "zone": 0,
                                     "start": date, "end": end,
                                     "pageIndex": 0, "pageSize": 18})
        grid = json.loads(body)
        slots = grid.get("slots", [])
        rooms = {}
        for s in slots:
            rid = s.get("itemId")
            r = rooms.setdefault(rid, {"space_id": rid, "free_slots": []})
            if not s.get("className"):        # booked slots carry s-lc-eq-checkout etc.
                r["free_slots"].append("{}–{}".format(
                    (s.get("start") or "")[11:16], (s.get("end") or "")[11:16]))
        for r in rooms.values():
            n = len(r["free_slots"])
            r["free_slot_count"] = n
            r["free_slots"] = r["free_slots"][:12]
            r["book_url"] = "{}/space/{}".format(LIBCAL, r["space_id"])
        result["rooms"] = sorted(rooms.values(),
                                 key=lambda r: -r["free_slot_count"])[:18]
        if not rooms:
            result["note"] = ("Availability grid returned no slots for this date "
                              "(closed day, or grid API changed) — use book_url.")
    except (json.JSONDecodeError, ToolError):
        result["note"] = ("Could not read the availability grid — open book_url "
                          "in the browser for live availability and booking.")
    return result


# ------------------------------------------------------------- databases

_db_cache = {"at": 0, "items": None}


def load_databases():
    if _db_cache["items"] is not None and time.time() - _db_cache["at"] < 86400:
        return _db_cache["items"]
    status, _, body = http(LIBGUIDES + "/az/databases")
    if status != 200:
        raise ToolError("LibGuides A-Z returned HTTP {} — see {}/az.php"
                        .format(status, LIBGUIDES))
    items = []
    # Andrews' theme renders each database as a <div class="mb-4"> block:
    # an <a href=EZproxy-wrapped-url onclick="...springTrack...">Name<i .../></a>
    # followed by an az-description div. The name is NOT directly followed
    # by </a> (an <i> icon sits in between) — match up to the first '<'.
    for block in body.split('<div class="mb-4">')[1:]:
        m = re.search(r'<a href="([^"]+)"[^>]*springTrack[^>]*>\s*([^<]+)', block)
        if not m:
            continue
        name = html_mod.unescape(m.group(2).strip())
        d = re.search(r'az-description[^>]*>(.*?)</div>', block, re.S)
        desc = ""
        if d:
            desc = re.sub(r"\s+", " ",
                          html_mod.unescape(re.sub(r"<[^>]+>", " ", d.group(1)))).strip()
        if name:
            items.append({"name": name, "url": m.group(1),
                          "description": desc[:250] or None})
    _db_cache.update(at=time.time(), items=items)
    return items


def tool_databases(args):
    query = (args.get("query") or "").strip().lower()
    items = load_databases()
    if query:
        items = [d for d in items
                 if query in (d.get("name") or "").lower()
                 or query in (d.get("description") or "").lower()
                 or any(query in (s or "").lower() for s in d.get("subjects") or [])]
    total = len(items)
    return {"total": total, "shown": min(total, 40), "databases": items[:40],
            "az_page": LIBGUIDES + "/az/databases",
            "note": None if total else
            "No match in the A-Z list — try a broader term or the az_page."}


# ---------------------------------------------------------------- guides


def tool_guides(args):
    query = (args.get("query") or "").strip()
    if not query:
        raise ToolError('`query` required, e.g. {"query": "church history"}')
    status, _, body = http(LIBGUIDES + "/srch.php?" +
                           urllib.parse.urlencode({"q": query}))
    if status != 200:
        raise ToolError("LibGuides search returned HTTP {}".format(status))
    results = []
    for m in re.finditer(
            r'<a[^>]+href="(https?://libguides\.andrews\.edu/[^"]+)"[^>]*>([^<]{3,120})</a>',
            body):
        url, name = m.group(1), html_mod.unescape(m.group(2).strip())
        if "az.php" in url or url.rstrip("/") == LIBGUIDES or name.lower() in ("home",):
            continue
        if not any(r["url"] == url for r in results):
            results.append({"name": name, "url": url})
    return {"query": query, "results": results[:20],
            "all_guides": LIBGUIDES + "/?b=g&d=a"}


# -------------------------------------------------------- digital commons


def oai(params):
    status, _, body = http(DIGCOMMONS + "/do/oai/?" + urllib.parse.urlencode(params),
                           timeout=40)
    if status != 200:
        raise ToolError("Digital Commons OAI returned HTTP {}".format(status))
    if "<error" in body:
        m = re.search(r'<error[^>]*code="([^"]+)"[^>]*>([^<]*)', body)
        raise ToolError("OAI error {}: {}".format(
            m.group(1) if m else "?", (m.group(2) if m else body[:150]).strip()))
    return body


def parse_oai_records(xml):
    recs = []
    for rm in re.finditer(r"<record>(.*?)</record>", xml, re.S):
        r = rm.group(1)
        def grab(tag, all_=False):
            vals = [html_mod.unescape(v.strip()) for v in
                    re.findall(r"<dc:{0}[^>]*>(.*?)</dc:{0}>".format(tag), r, re.S)]
            return vals if all_ else (vals[0] if vals else None)
        ident = re.search(r"<identifier>([^<]+)</identifier>", r)
        recs.append({k: v for k, v in {
            "title": grab("title"),
            "authors": grab("creator", all_=True) or None,
            "date": grab("date"),
            "type": grab("type"),
            "url": grab("identifier"),
            "abstract": (grab("description") or "")[:300] or None,
            "oai_id": ident.group(1) if ident else None,
        }.items() if v})
    token = re.search(r"<resumptionToken[^>]*>([^<]+)</resumptionToken>", xml)
    return recs, (token.group(1) if token else None)


def tool_digitalcommons(args):
    query = (args.get("query") or "").strip()
    days = int(args.get("days") or 0)
    token = args.get("resumption_token")
    if query and not days:
        return {"query": query,
                "search_url": DIGCOMMONS + "/do/search/?" + urllib.parse.urlencode(
                    {"q": query, "start": "0", "context": "8082725"}),
                "note": "Digital Commons keyword search is browser-only (its search "
                        "backend is not public) — open search_url. For harvesting "
                        "recent items use days=N; both can be combined with the "
                        "browse page " + DIGCOMMONS + "/communities.html"}
    params = {"verb": "ListRecords"}
    if token:
        params["resumptionToken"] = token
    else:
        params["metadataPrefix"] = "oai_dc"
        if days:
            params["from"] = time.strftime("%Y-%m-%d",
                                           time.localtime(time.time() - days * 86400))
    xml = oai(params)
    recs, next_token = parse_oai_records(xml)
    out = {"records": recs[:40], "count": len(recs)}
    if next_token:
        out["resumption_token"] = next_token
        out["more"] = "pass resumption_token to continue harvesting"
    return out


# ------------------------------------------------------------ links/proxy


def tool_ezproxy_link(args):
    url = (args.get("url") or "").strip()
    if not url.startswith("http"):
        raise ToolError("`url` must be an http(s) URL to proxy")
    return {"ezproxy_url": EZPROXY_LOGIN + urllib.parse.quote(url, safe=""),
            "note": "User opens this in their browser (institutional login). "
                    "Never automate downloads through EZproxy."}


def tool_library_links(args):
    return {
        "homepage": "https://www.andrews.edu/services/library/",
        "catalog": LOCATE,
        "worldcat": "https://andrewsuniversity.on.worldcat.org/discovery",
        "course_reserves": LOCATE + "/course-reserves",
        "databases_az": LIBGUIDES + "/az/databases",
        "research_guides": LIBGUIDES,
        "ejournal_portal": "http://ug3lf7jn4y.search.serialssolutions.com/ejp/?libHash=UG3LF7JN4Y",
        "digital_commons": DIGCOMMONS,
        "hours": LIBCAL + "/hours/",
        "room_booking": "{}/spaces?lid={}&gid={}".format(LIBCAL, SPACES_LID, SPACES_GID),
        "citation_help": LIBGUIDES + "/CitationHelps",
        "seminary_periodical_index": LIBGUIDES + "/SDAPI/",
        "ellen_white_writings": "http://egwwritings.org",
    }


# ------------------------------------------------------------ MCP wiring

TOOLS = [
    {"name": "catalog_search",
     "description": "Search the James White Library catalog (books, media, scores — "
                    "the physical + electronic holdings of Andrews University). "
                    "field: keyword (default) | title | contributors | subject | isbn. "
                    "Returns instance ids for catalog_item. No login needed.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string"},
         "field": {"type": "string",
                   "enum": ["keyword", "title", "contributors", "subject", "isbn"]},
         "limit": {"type": "integer", "description": "default 10, max 30"},
         "offset": {"type": "integer", "description": "for paging"}},
         "required": ["query"]}},
    {"name": "catalog_item",
     "description": "Full detail for one catalog record: call number(s), shelf "
                    "location, LIVE availability status (available/checked out), "
                    "subjects, electronic access links. Use the instance id from "
                    "catalog_search.",
     "inputSchema": {"type": "object", "properties": {
         "instance_id": {"type": "string"}}, "required": ["instance_id"]}},
    {"name": "course_reserves",
     "description": "Look up course reserves by course name/number (e.g. 'DEMO 201', "
                    "'Music Lit') — materials instructors placed on reserve at the "
                    "library. Returns matching courses; the reserves_page link shows "
                    "the actual items.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "course name or number; empty lists all"},
         "limit": {"type": "integer", "description": "default 15, max 40"}}}},
    {"name": "hours",
     "description": "James White Library opening hours (today + up to 4 weeks), "
                    "including live open/closed status.",
     "inputSchema": {"type": "object", "properties": {
         "weeks": {"type": "integer", "description": "1-4, default 1"}}}},
    {"name": "rooms",
     "description": "Study-room availability for a date (free time slots per room). "
                    "Read-only: BOOKING always happens in the user's browser via the "
                    "returned book_url (requires their login).",
     "inputSchema": {"type": "object", "properties": {
         "date": {"type": "string", "description": "YYYY-MM-DD, default today"}}}},
    {"name": "databases",
     "description": "Research databases A-Z (ATLA, JSTOR, ProQuest, EBSCO...) with "
                    "access links. Optional query filters by name/description/subject "
                    "(e.g. 'theology', 'nursing'). Links are already EZproxy-wrapped "
                    "where the library requires it.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string"}}}},
    {"name": "guides",
     "description": "Search the library's research guides (LibGuides) — subject "
                    "guides, course guides, how-tos (citation help, SDA resources...).",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string"}}, "required": ["query"]}},
    {"name": "digitalcommons",
     "description": "Andrews Digital Commons (institutional repository: dissertations, "
                    "theses, faculty publications, journals). days=N harvests items "
                    "added in the last N days (OAI-PMH); query returns the browser "
                    "search link (its search backend is not public).",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string"},
         "days": {"type": "integer",
                  "description": "harvest records from the last N days"},
         "resumption_token": {"type": "string",
                              "description": "continue a previous harvest"}}}},
    {"name": "ezproxy_link",
     "description": "Wrap any publisher/database URL in the Andrews EZproxy login "
                    "link so the user gets full-text access in their browser.",
     "inputSchema": {"type": "object", "properties": {
         "url": {"type": "string"}}, "required": ["url"]}},
    {"name": "library_links",
     "description": "Curated map of every key library page: catalog, WorldCat, course "
                    "reserves, e-journal portal, citation help, room booking, hours, "
                    "Digital Commons, SDA Periodical Index. Use when unsure where "
                    "something lives.",
     "inputSchema": {"type": "object", "properties": {}}},
]

HANDLERS = {
    "catalog_search": tool_catalog_search,
    "catalog_item": tool_catalog_item,
    "course_reserves": tool_course_reserves,
    "hours": tool_hours,
    "rooms": tool_rooms,
    "databases": tool_databases,
    "guides": tool_guides,
    "digitalcommons": tool_digitalcommons,
    "ezproxy_link": tool_ezproxy_link,
    "library_links": tool_library_links,
}


def handle(msg):
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}
    if msg_id is None:
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    if method == "initialize":
        return ok({"protocolVersion": params.get("protocolVersion") or "2025-03-26",
                   "capabilities": {"tools": {}},
                   "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        handler = HANDLERS.get(name)
        if not handler:
            return {"jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32602, "message": "unknown tool: {}".format(name)}}
        try:
            result = handler(params.get("arguments") or {})
            text = json.dumps(result, indent=2, ensure_ascii=False)
            if len(text) > MAX_TEXT:
                text = text[:MAX_TEXT] + "\n…[truncated — narrow the request]"
            return ok({"content": [{"type": "text", "text": text}], "isError": False})
        except ToolError as e:
            return ok({"content": [{"type": "text", "text": str(e)}], "isError": True})
        except (urllib.error.URLError, socket.timeout) as e:
            return ok({"content": [{"type": "text",
                                    "text": "Network error: {} — retry once".format(e)}],
                       "isError": True})
        except Exception as e:
            log("tool {} crashed: {}: {}".format(name, type(e).__name__, e))
            return ok({"content": [{"type": "text",
                                    "text": "{}: {}".format(type(e).__name__, e)}],
                       "isError": True})
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": -32601, "message": "method not found: {}".format(method)}}


def main():
    log("starting (python {})".format(sys.version.split()[0]))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            resp = handle(msg)
        except Exception as e:
            log("handler crashed: {}".format(e))
            resp = {"jsonrpc": "2.0", "id": msg.get("id"),
                    "error": {"code": -32603, "message": str(e)}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
