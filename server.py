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
import datetime
import ipaddress
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SERVER_NAME = "andrews-library"
SERVER_VERSION = "1.2.1"
FILES_DIR_NAME = "files"      # under ~/.hermes/andrews-library/
MAX_DOWNLOAD = 100 * 1024 * 1024
# Unpaywall's polite pool wants a contact email. Keep it configurable and
# never a real personal address in source — override via the
# ANDREWS_LIBRARY_UNPAYWALL_EMAIL environment variable if desired.
UNPAYWALL_EMAIL = os.environ.get(
    "ANDREWS_LIBRARY_UNPAYWALL_EMAIL", "andrews-library-mcp@users.noreply.github.com")
# Hosts whose content is licensed to the library — automated retrieval
# through or around EZproxy violates vendor licenses and can get the
# user's account and campus IP range suspended. save_work refuses these
# and explains the sanctioned path (browser + Zotero) instead.
GATED_HOST_MARKERS = ("ezproxy.andrews.edu", "jstor.org", "ebscohost.com",
                      "ebsco.com", "proquest.com",
                      "atla.com", "ovid.com", "sciencedirect.com",
                      "springer.com", "wiley.com", "tandfonline.com",
                      "sagepub.com", "oup.com", "cambridge.org")
MAX_TEXT = 120_000

OKAPI = "https://okapi-andrews.locate.ebsco.com"
TENANT = "lt00001186"
LOCATE = "https://andrews.locate.ebsco.com"
LIBCAL = "https://andrews.libcal.com"
LIBGUIDES = "https://libguides.andrews.edu"
DIGCOMMONS = "https://digitalcommons.andrews.edu"
DIGCOMMONS_HOST = "digitalcommons.andrews.edu"
EZPROXY_LOGIN = "https://ezproxy.andrews.edu/login?url="
SPACES_LID, SPACES_GID = 5524, 9604
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def log(msg):
    print("[andrews-library-mcp] {}".format(msg), file=sys.stderr, flush=True)


class ToolError(Exception):
    pass


def _as_int(value, default, what, minimum=None, maximum=None):
    """Coerce a model-supplied integer argument with an actionable error."""
    if value in (None, ""):
        result = default
    elif isinstance(value, bool):
        raise ToolError("`{}` must be a whole number (got {!r})".format(what, value))
    elif isinstance(value, int):
        result = value
    elif isinstance(value, float) and value.is_integer():
        result = int(value)
    elif isinstance(value, str):
        try:
            result = int(value)
        except ValueError:
            raise ToolError("`{}` must be a whole number (got {!r})".format(what, value))
    else:
        raise ToolError("`{}` must be a whole number (got {!r})".format(what, value))
    if minimum is not None and result < minimum:
        raise ToolError("`{}` must be at least {}".format(what, minimum))
    if maximum is not None and result > maximum:
        raise ToolError("`{}` must be at most {}".format(what, maximum))
    return result


def _as_bool(value, what, default=False):
    """Coerce a model-supplied boolean argument with an actionable error.

    Only true/false booleans, 1/0, and the strings 'true'/'false' (any case)
    are accepted — a string like "false" must not silently become True.
    """
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, float) and value in (0.0, 1.0):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
    raise ToolError("`{}` must be true or false (got {!r})".format(what, value))


def _is_digitalcommons(url):
    """Exact host match for Digital Commons (no substring/trailing-dot bypass)."""
    host = (urllib.parse.urlparse(url).hostname or "").lower().rstrip(".")
    return host == DIGCOMMONS_HOST or host.endswith("." + DIGCOMMONS_HOST)


def _http_url(url):
    """Return True only for an absolute HTTP(S) URL without userinfo."""
    try:
        p = urllib.parse.urlparse(url)
        return (p.scheme in ("http", "https") and bool(p.hostname)
                and p.username is None and p.password is None)
    except ValueError:
        return False


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


def _first_pub(instance):
    """First publication entry, tolerating upstream shape drift (list vs dict)."""
    pubs = instance.get("publication")
    if isinstance(pubs, dict):
        return pubs
    if isinstance(pubs, list) and pubs and isinstance(pubs[0], dict):
        return pubs[0]
    return {}


def _http_uris(entries):
    """Deduplicated http(s) URIs from an electronicAccess list (any shape)."""
    out = []
    for e in entries or []:
        uri = e.get("uri") if isinstance(e, dict) else None
        if _http_url(uri or "") and uri not in out:
            out.append(uri)
    return out


def slim_instance(i):
    pub = _first_pub(i)
    out = {
        "id": i.get("id"),
        "title": i.get("title"),
        "contributors": [c.get("name") for c in (i.get("contributors") or [])[:4]
                         if isinstance(c, dict)],
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
    limit = _as_int(args.get("limit"), 10, "limit", minimum=1, maximum=30)
    offset = _as_int(args.get("offset"), 0, "offset", minimum=0)
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
    ea = _http_uris(i.get("electronicAccess"))[:3]
    if ea:
        out["electronic_access"] = ea
    return {k: v for k, v in out.items() if v is not None}


# The tenant's FOLIO "serial" mode-of-issuance id, read off a live serial
# record (JBL) 2026-08-26. Guests cannot list /modes-of-issuance (403), so
# this is pinned; journal_lookup falls back to unfiltered search if the
# filtered query ever returns zero (reference data changed).
SERIAL_MODE_ID = "068b5344-e2a6-40df-9186-1829e13cd344"
EJP_PORTAL = ("http://ug3lf7jn4y.search.serialssolutions.com/ejp/"
              "?libHash=UG3LF7JN4Y#/?language=en-US&titleType=ALL")


def tool_journal_lookup(args):
    query = (args.get("query") or "").strip()
    if not query:
        raise ToolError('`query` required — a journal title, e.g. '
                        '{"query": "Journal of Biblical Literature"}')
    limit = _as_int(args.get("limit"), 5, "limit", minimum=1, maximum=15)
    filtered = 'title all "{}" and modeOfIssuanceId=="{}"'.format(
        cql_escape(query), SERIAL_MODE_ID)
    data = okapi_get("/search/instances",
                     {"query": filtered, "limit": str(limit), "expandAll": "true"})
    total = data.get("totalRecords", 0)
    note = None
    if not total:
        data = okapi_get("/search/instances",
                         {"query": 'title all "{}"'.format(cql_escape(query)),
                          "limit": str(limit), "expandAll": "true"})
        total = data.get("totalRecords", 0)
        if total:
            note = ("No serial-typed record matched — showing all catalog "
                    "matches instead (some may be books).")
    journals = []
    for i in data.get("instances", []):
        e_links = _http_uris(i.get("electronicAccess"))
        for h in i.get("holdings") or []:
            if isinstance(h, dict):
                e_links += _http_uris(h.get("electronicAccess"))
        e_links = e_links[:5]
        items = i.get("items") or []
        locs, call_nos, physical_items = {}, [], []
        for it in items:
            if not isinstance(it, dict):
                continue
            ln = location_name(it.get("effectiveLocationId"))
            cn = (it.get("effectiveCallNumberComponents") or {}).get("callNumber")
            if not ln and not cn:
                continue
            physical_items.append(it)
            if ln:
                locs[ln] = locs.get(ln, 0) + 1
            if cn and cn not in call_nos:
                call_nos.append(cn)
        entry = {
            "title": i.get("title"),
            "publication": _first_pub(i).get("publisher"),
            "catalog_url": "{}/instances/{}".format(LOCATE, i.get("id")),
        }
        if e_links:
            entry["online"] = [{"url": u,
                                "ezproxy_url": EZPROXY_LOGIN + urllib.parse.quote(u, safe="")}
                               for u in e_links]
        if physical_items:
            entry["print_holdings"] = {
                "pieces": len(physical_items),
                "call_numbers": call_nos[:3],
                "locations": sorted(locs, key=locs.get, reverse=True)[:3],
            }
        if not e_links and not physical_items:
            entry["holdings"] = "record has no items or e-links — check catalog_url"
        journals.append(entry)
    out = {"query": query, "total": total, "journals": journals,
           "ejournal_portal": EJP_PORTAL,
           "portal_note": "For exact online coverage dates (which years are "
                          "full-text where), the e-journal portal in the "
                          "browser is authoritative."}
    if note:
        out["note"] = note
    if not total:
        out["note"] = ("Not in the catalog under that title. Try a shorter "
                       "title fragment, or check the e-journal portal link.")
    return out


def tool_course_reserves(args):
    query = (args.get("query") or "").strip()
    limit = _as_int(args.get("limit"), 15, "limit", minimum=1, maximum=40)
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
    weeks = _as_int(args.get("weeks"), 1, "weeks", minimum=1, maximum=4)
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
    if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ToolError("date must be a YYYY-MM-DD string, e.g. 2026-09-01")
    try:
        date_value = datetime.date.fromisoformat(date)
    except ValueError:
        raise ToolError("date {} is not a real calendar day — use YYYY-MM-DD".format(date))
    result = {"date": date,
              "book_url": "{}/spaces?lid={}&gid={}".format(LIBCAL, SPACES_LID, SPACES_GID),
              "note": "Booking always happens in YOUR browser (login required) — "
                      "this tool only reads availability."}
    try:
        end = (date_value + datetime.timedelta(days=1)).isoformat()
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
                 or query in (d.get("description") or "").lower()]
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
    days = _as_int(args.get("days"), 0, "days", minimum=0)
    token = args.get("resumption_token")
    if args.get("list_sets"):
        sets, seen_specs, seen_tokens, set_token, pages = [], set(), set(), None, 0
        while True:
            params = ({"verb": "ListSets", "resumptionToken": set_token}
                      if set_token else {"verb": "ListSets"})
            xml = oai(params)
            pages += 1
            for s in re.finditer(
                    r"<setSpec>([^<]+)</setSpec>\s*<setName>([^<]+)</setName>", xml):
                spec = html_mod.unescape(s.group(1))
                if spec not in seen_specs:
                    seen_specs.add(spec)
                    sets.append({"set": spec, "name": html_mod.unescape(s.group(2))})
            m = re.search(r"<resumptionToken[^>]*>([^<]+)</resumptionToken>", xml)
            set_token = html_mod.unescape(m.group(1).strip()) if m else None
            if not set_token:
                break
            if set_token in seen_tokens or pages >= 100:
                raise ToolError("Digital Commons returned a repeated or excessive "
                                "ListSets continuation token")
            seen_tokens.add(set_token)
        return {"count": len(sets), "sets": sets, "pages": pages,
                "tip": "Pass one setSpec as `set` (with days) to harvest just "
                       "that collection, e.g. dissertations."}
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
        if args.get("set"):
            params["set"] = args["set"]
        if days:
            params["from"] = time.strftime("%Y-%m-%d",
                                           time.localtime(time.time() - days * 86400))
    xml = oai(params)
    recs, next_token = parse_oai_records(xml)
    out = {"records": recs, "count": len(recs)}
    if next_token:
        out["resumption_token"] = next_token
        out["more"] = "pass resumption_token to continue harvesting"
    return out


# ------------------------------------------------------------- save_work

from pathlib import Path


def _gated(url):
    host = (urllib.parse.urlparse(url).hostname or "").lower().rstrip(".")
    return any(host == marker or host.endswith("." + marker)
               for marker in GATED_HOST_MARKERS)


def _validate_public_http_url(url, resolve=False):
    """Reject non-web, credential-bearing, local, and private-network URLs.

    resolve=True also resolves the hostname and rejects any private/loopback/
    link-local/multicast/reserved address. Residual risk (accepted, documented):
    DNS rebinding between this validation resolution and urllib's later
    connect is possible in theory; connecting to the validated IP instead of
    re-resolving would require a custom opener, which the stdlib-only
    constraint makes disproportionate for a local single-user tool.
    """
    if not _http_url(url):
        raise ToolError("download URL must be a public http(s) URL without credentials")
    p = urllib.parse.urlparse(url)
    host = (p.hostname or "").lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        raise ToolError("download URL must be a public http(s) URL, not localhost")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    addresses = [literal] if literal else []
    if resolve and not literal:
        try:
            port = p.port or (443 if p.scheme == "https" else 80)
            addresses = [ipaddress.ip_address(info[4][0])
                         for info in socket.getaddrinfo(host, port,
                                                       type=socket.SOCK_STREAM)]
        except (OSError, ValueError) as e:
            raise ToolError("could not resolve public download host {}: {}".format(host, e))
    for address in addresses:
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_multicast or address.is_reserved or address.is_unspecified):
            raise ToolError("download URL must be a public http(s) URL, not {}"
                            .format(address))
    return url


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_http_url(newurl, resolve=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _sanctioned_path_msg(url_or_doi):
    return ("This is licensed content — automated retrieval violates the "
            "library's vendor licenses and risks the user's account and "
            "campus-wide access, so this tool will not fetch it. Sanctioned "
            "path: open the EZproxy link in the BROWSER (ezproxy_link tool), "
            "save the PDF there (Zotero's connector captures PDF + metadata "
            "in one click), and the zotero MCP can then read its full text "
            "locally. Reference: {}".format(url_or_doi))


def _fetch_public_page(url, timeout=30, max_bytes=2 * 1024 * 1024):
    """Fetch an HTML page with the same public-network + redirect guards as
    _download_pdf (SSRF-safe), returning (status, headers, body).

    Headers are the raw HTTPMessage so callers can read every Set-Cookie
    (http() collapses duplicates to the last one). The body read is capped
    at max_bytes so a hostile-but-public redirect target cannot exhaust
    memory.
    """
    _validate_public_http_url(url, resolve=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.build_opener(_PublicRedirectHandler()).open(
                req, timeout=timeout) as resp:
            body = resp.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ToolError("page exceeded the {} MB read cap".format(
                    max_bytes >> 20))
            return resp.status, resp.headers, body.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ToolError("error page exceeded the {} MB read cap".format(
                max_bytes >> 20))
        return e.code, e.headers, body.decode("utf-8", "replace")
    except socket.timeout:
        raise ToolError("request to {} timed out — retry once".format(
            urllib.parse.urlparse(url).netloc))


def _download_pdf(url, save_as=None, overwrite=False, referer=None, cookies=None):
    _validate_public_http_url(url, resolve=True)
    files_dir = Path.home() / ".hermes" / "andrews-library" / FILES_DIR_NAME
    files_dir.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": UA, "Accept": "application/pdf,*/*"}
    if referer:
        headers["Referer"] = referer
    if cookies:
        headers["Cookie"] = cookies
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.build_opener(_PublicRedirectHandler()).open(req, timeout=90)
    except socket.timeout:
        raise ToolError("download timed out — retry once")
    with resp:
        final_url = resp.geturl()
        _validate_public_http_url(final_url, resolve=True)
        if _gated(final_url):
            raise ToolError(_sanctioned_path_msg(final_url))
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "pdf" not in ctype and "octet-stream" not in ctype:
            raise ToolError("target did not return a PDF (Content-Type {}) — "
                            "it is probably a landing page or a login wall; "
                            "use the browser for it".format(ctype[:60] or "?"))
        name = save_as or ""
        if not name:
            cd = resp.headers.get("Content-Disposition") or ""
            m = re.search(r'filename="?([^";]+)', cd)
            name = (m.group(1) if m
                    else urllib.parse.unquote(
                        urllib.parse.urlparse(final_url).path.rsplit("/", 1)[-1])
                    or "download.pdf")
        name = re.sub(r"[^\w.\- ]", "_", name)[:180]
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        dest = files_dir / name
        if dest.exists() and not overwrite:
            raise ToolError("{} already exists — pass overwrite=true or a "
                            "different save_as".format(dest))
        part = dest.with_suffix(dest.suffix + ".part")
        total = 0
        promoted = False
        try:
            with open(part, "wb") as f:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_DOWNLOAD:
                        raise ToolError("file exceeds the {} MB cap".format(
                            MAX_DOWNLOAD >> 20))
                    f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(part), str(dest))
            promoted = True
        finally:
            if not promoted:
                part.unlink(missing_ok=True)
    return {"saved_to": str(dest), "size_bytes": total}


def resolve_oa_pdf(doi):
    """Legal OA copy via Unpaywall, Semantic Scholar, then OpenAlex."""
    completed = 0
    try:
        status, _, body = http("https://api.unpaywall.org/v2/{}?email={}".format(
            urllib.parse.quote(doi), UNPAYWALL_EMAIL))
        if status == 404:
            completed += 1
        elif status == 200:
            d = json.loads(body)
            if not isinstance(d, dict) or not isinstance(d.get("is_oa"), bool):
                raise ValueError("malformed Unpaywall response")
            if not d["is_oa"]:
                completed += 1
            else:
                loc = d.get("best_oa_location") or {}
                if not isinstance(loc, dict):
                    raise ValueError("malformed Unpaywall location")
                pdf = loc.get("url_for_pdf") or loc.get("url")
                if pdf:
                    return pdf, "unpaywall"
                locations = d.get("oa_locations") or []
                if not isinstance(locations, list):
                    raise ValueError("malformed Unpaywall locations")
                for loc in locations:
                    if not isinstance(loc, dict):
                        continue
                    if loc.get("url_for_pdf"):
                        return loc["url_for_pdf"], "unpaywall"
    except Exception:
        pass
    try:
        status, _, body = http(
            "https://api.semanticscholar.org/graph/v1/paper/DOI:{}?fields={}".format(
                urllib.parse.quote(doi, safe=""),
                urllib.parse.quote("isOpenAccess,openAccessPdf", safe=",")))
        if status == 404:
            completed += 1
        elif status == 200:
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError("malformed Semantic Scholar response")
            pdf = data.get("openAccessPdf")
            if pdf is not None and not isinstance(pdf, dict):
                raise ValueError("malformed Semantic Scholar openAccessPdf")
            if pdf and isinstance(pdf.get("url"), str) and pdf["url"]:
                return pdf["url"], "semantic-scholar"
            if data.get("isOpenAccess") is False:
                completed += 1
            elif not isinstance(data.get("isOpenAccess"), bool):
                raise ValueError("malformed Semantic Scholar OA status")
    except Exception:
        pass
    try:
        status, _, body = http("https://api.openalex.org/works/doi:{}?mailto={}".format(
            urllib.parse.quote(doi), UNPAYWALL_EMAIL))
        if status == 404:
            completed += 1
        elif status == 200:
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError("malformed OpenAlex response")
            oa = data.get("open_access") or {}
            if not isinstance(oa, dict) or not isinstance(oa.get("is_oa"), bool):
                raise ValueError("malformed OpenAlex open_access response")
            if oa.get("oa_url"):
                return oa["oa_url"], "openalex"
            if not oa["is_oa"]:
                completed += 1
    except Exception:
        pass
    if not completed:
        raise ToolError("Open-access resolvers were unavailable or returned malformed "
                        "responses — retry later; OA status is unknown.")
    return None, None


def tool_save_work(args):
    doi = (args.get("doi") or "").strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/",
                   "http://dx.doi.org/", "doi:"):
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix):]
            break
    url = (args.get("url") or "").strip()
    save_as = args.get("save_as")
    overwrite = _as_bool(args.get("overwrite"), "overwrite")
    if not doi and not url:
        raise ToolError("pass `doi` (e.g. 10.1234/abc) or `url` (a Digital "
                        "Commons page/PDF or any open-access PDF link)")

    if url:
        _validate_public_http_url(url)
    if url and _gated(url):
        raise ToolError(_sanctioned_path_msg(url))

    if doi:
        if not re.match(r"^10\.\d{4,}/\S+$", doi):
            raise ToolError("`doi` must look like 10.1234/xyz (got {!r})".format(doi[:60]))
        pdf, source = resolve_oa_pdf(doi)
        if not pdf:
            return {"saved": False, "doi": doi, "is_open_access": False,
                    "why": "No legal open-access copy exists for this DOI.",
                    "sanctioned_path": _sanctioned_path_msg("doi:" + doi),
                    "ezproxy_url": EZPROXY_LOGIN + urllib.parse.quote(
                        "https://doi.org/" + doi, safe="")}
        if _gated(pdf):
            return {"saved": False, "doi": doi, "is_open_access": True,
                    "why": "The OA copy resolves to a licensed host — use the "
                           "browser.", "oa_url": pdf}
        out = _download_pdf(pdf, save_as=save_as, overwrite=overwrite)
        out.update(saved=True, doi=doi, source=source, pdf_url=pdf)
        return out

    # URL path: Digital Commons page → citation_pdf_url; else direct open PDF.
    # bepress serves viewcontent.cgi only to sessions that visited the
    # article page first — carry its cookies + referer into the PDF request.
    if _is_digitalcommons(url):
        page_url = url
        cookies = None
        if "viewcontent.cgi" not in url:
            status, hdrs, body = _fetch_public_page(url)
            if status != 200:
                raise ToolError("Digital Commons page returned HTTP {}".format(status))
            m = re.search(r'citation_pdf_url"\s+content="([^"]+)"', body)
            if not m:
                raise ToolError("no PDF found on that Digital Commons page — "
                                "is it a metadata-only record?")
            pdf = html_mod.unescape(m.group(1))
            raw_cookies = [v.split(";")[0] for k, v in hdrs.items()
                           if k.lower() == "set-cookie"]
            cookies = "; ".join(raw_cookies) or None
        else:
            pdf = url
        try:
            out = _download_pdf(pdf, save_as=save_as, overwrite=overwrite,
                                referer=page_url, cookies=cookies)
            out.update(saved=True, source="digitalcommons", pdf_url=pdf)
            return out
        except (ToolError, urllib.error.HTTPError):
            # bepress's CDN 403s non-browser PDF fetches even though the
            # content is open access. Don't fight the bot wall — hand over
            # the direct PDF link; it downloads with one click, no login.
            return {"saved": False, "source": "digitalcommons", "pdf_url": pdf,
                    "why": "Digital Commons blocks non-browser downloads "
                           "(CDN bot wall) even though the content is open.",
                    "how": "Open pdf_url in the browser — it downloads "
                           "immediately, no login needed. Zotero's connector "
                           "also captures it in one click."}

    out = _download_pdf(url, save_as=save_as, overwrite=overwrite)
    out.update(saved=True, source="direct-open", pdf_url=url)
    return out


# ------------------------------------------------------------ links/proxy


def tool_ezproxy_link(args):
    url = (args.get("url") or "").strip()
    if not _http_url(url):
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
        "interlibrary_loan_form": "https://www.andrews.edu/services/library/1_services/illform.html",
        "melcat_ill": "https://www.andrews.edu/services/library/1_services/melcatill.html",
        "mel_elibrary": "https://elibrary.mel.org/",
        "ask_a_librarian": "https://andrews.libanswers.com/",
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
    {"name": "journal_lookup",
     "description": "Does the library have a specific JOURNAL, and how do I read it? "
                    "Searches the catalog for serial records by journal title and "
                    "returns online access links (EZproxy-wrapped) and/or print "
                    "holdings (call numbers, locations). Use for 'do we have Journal "
                    "of X' questions; for articles BY TOPIC use databases or "
                    "catalog_search instead.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "journal title, e.g. "
                                                    "'Journal of Biblical Literature'"},
         "limit": {"type": "integer", "description": "default 5, max 15"}},
         "required": ["query"]}},
    {"name": "course_reserves",
     "description": "Look up course reserves by course name/number (e.g. 'CHIS 674', "
                    "'Music Lit') — materials instructors placed on reserve at the "
                    "library. Returns matching courses only; the ITEMS on reserve are "
                    "not guest-readable (API 404s), so open reserves_page for them.",
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
                    "access links. Optional query filters by name/description "
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
                    "theses, faculty publications, journals). list_sets=true lists the "
                    "collections; days=N harvests items added in the last N days "
                    "(optionally scoped by `set`, e.g. the dissertations collection); "
                    "query returns the browser search link (its search backend is "
                    "not public).",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string"},
         "days": {"type": "integer",
                  "description": "harvest records from the last N days"},
         "set": {"type": "string",
                 "description": "setSpec from list_sets, scopes the harvest"},
         "list_sets": {"type": "boolean",
                       "description": "list all collections (setSpec + name)"},
         "resumption_token": {"type": "string",
                              "description": "continue a previous harvest"}}}},
    {"name": "save_work",
     "description": "SAVE research full text locally (to ~/.hermes/andrews-library/"
                    "files/) when a legal automated copy exists: pass `doi` to fetch "
                    "the open-access copy (Unpaywall/Semantic Scholar/OpenAlex), "
                    "or `url` for a "
                    "Digital Commons record or any open PDF link. Digital Commons "
                    "PDFs are CDN-gated to browsers: the tool then returns the "
                    "direct pdf_url for a one-click browser save. LICENSED content "
                    "(EZproxy/JSTOR/EBSCO/ProQuest...) is refused by design — the "
                    "response explains the browser+Zotero path; never try to work "
                    "around either refusal.",
     "inputSchema": {"type": "object", "properties": {
         "doi": {"type": "string", "description": "e.g. 10.1371/journal.pone.0263310"},
         "url": {"type": "string",
                 "description": "Digital Commons page/PDF or open-access PDF URL"},
         "save_as": {"type": "string", "description": "optional filename override"},
         "overwrite": {"type": "boolean", "description": "default false"}}}},
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

# Harness strictness contract: every inputSchema declares an explicit
# `required` list (even when empty) and rejects unknown properties.
for _t in TOOLS:
    _t["inputSchema"].setdefault("required", [])
    _t["inputSchema"].setdefault("additionalProperties", False)

HANDLERS = {
    "catalog_search": tool_catalog_search,
    "catalog_item": tool_catalog_item,
    "journal_lookup": tool_journal_lookup,
    "course_reserves": tool_course_reserves,
    "hours": tool_hours,
    "rooms": tool_rooms,
    "databases": tool_databases,
    "guides": tool_guides,
    "digitalcommons": tool_digitalcommons,
    "save_work": tool_save_work,
    "ezproxy_link": tool_ezproxy_link,
    "library_links": tool_library_links,
}


def _bounded_result_copy(value, max_string, max_items, depth=0):
    """Return a structurally valid, bounded preview of JSON-compatible data."""
    if depth >= 10:
        return "[truncated — nesting depth exceeded]"
    if isinstance(value, str):
        if len(value) <= max_string:
            return value
        omitted = len(value) - max_string
        return value[:max_string] + "… [truncated {} chars]".format(omitted)
    if isinstance(value, dict):
        items = list(value.items())
        out = {k: _bounded_result_copy(v, max_string, max_items, depth + 1)
               for k, v in items[:max_items]}
        if len(items) > max_items:
            out["__truncated_items__"] = len(items) - max_items
        return out
    if isinstance(value, (list, tuple)):
        out = [_bounded_result_copy(v, max_string, max_items, depth + 1)
               for v in value[:max_items]]
        if len(value) > max_items:
            out.append({"__truncated_items__": len(value) - max_items})
        return out
    return value


def _serialize_tool_result(result):
    """Serialize a tool result after structurally bounding oversized data."""
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if len(text) <= MAX_TEXT:
        return text
    hint = "Result exceeded the response budget; narrow the request."
    max_string = MAX_TEXT
    max_items = 100
    while max_string >= 32:
        candidate = json.dumps({
            "truncated": True,
            "preview": _bounded_result_copy(result, max_string, max_items),
            "hint": hint,
        }, indent=2, ensure_ascii=False)
        if len(candidate) <= MAX_TEXT:
            return candidate
        max_string //= 2
        max_items = max(1, max_items // 2)
    return json.dumps({"truncated": True, "preview": {}, "hint": hint})


def handle(msg):
    if not isinstance(msg, dict):
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32600,
                          "message": "invalid request: JSON-RPC message must be an object"}}
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg["params"] if "params" in msg else {}
    if msg_id is None:
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    if not isinstance(params, dict):
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32602, "message": "params must be an object"}}

    if method == "initialize":
        supported = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
        requested = params.get("protocolVersion")
        return ok({"protocolVersion": requested if requested in supported
                   else supported[-1],
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
        args_in = params["arguments"] if "arguments" in params else {}
        if not isinstance(args_in, dict):
            return ok({"content": [{"type": "text",
                                    "text": "arguments must be a JSON object of "
                                            "tool parameters, e.g. {\"query\": \"...\"}"}],
                       "isError": True})
        try:
            result = handler(args_in)
            text = _serialize_tool_result(result)
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
            sys.stdout.write(json.dumps(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32700, "message": "parse error: invalid JSON"}}) + "\n")
            sys.stdout.flush()
            continue
        try:
            resp = handle(msg)
        except Exception as e:
            log("handler crashed: {}".format(e))
            resp = {"jsonrpc": "2.0",
                    "id": msg.get("id") if isinstance(msg, dict) else None,
                    "error": {"code": -32603, "message": str(e)}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
