#!/usr/bin/env python3
"""Test suite for the andrews-library MCP server (run: /usr/bin/python3
test_server.py from this directory). Offline by default — network functions
are monkeypatched; set RUN_LIVE=1 to add live smoke tests against the real
library systems."""

import json
import os
import subprocess
import sys
import unittest
from unittest import mock

import server


def fake_http_factory(routes):
    """routes: list of (substring, status, headers, body) matched in order."""
    def fake_http(url, method="GET", headers=None, data=None, timeout=30):
        for sub, status, hdrs, body in routes:
            if sub in url:
                return status, hdrs, body
        raise AssertionError("unexpected URL in test: " + url)
    return fake_http


class Protocol(unittest.TestCase):
    def test_initialize_echoes_version(self):
        r = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(r["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", r["result"]["capabilities"])

    def test_notification_gets_no_response(self):
        self.assertIsNone(server.handle({"jsonrpc": "2.0",
                                         "method": "notifications/initialized"}))

    def test_unknown_method_errors(self):
        r = server.handle({"jsonrpc": "2.0", "id": 2, "method": "bogus/x"})
        self.assertEqual(r["error"]["code"], -32601)

    def test_unknown_tool_errors(self):
        r = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                           "params": {"name": "nope"}})
        self.assertEqual(r["error"]["code"], -32602)

    def test_tools_and_handlers_agree(self):
        names = {t["name"] for t in server.TOOLS}
        self.assertEqual(names, set(server.HANDLERS))
        for t in server.TOOLS:
            self.assertGreaterEqual(len(t["description"]), 40, t["name"])
            self.assertIn("inputSchema", t)
            self.assertIn("required", t["inputSchema"])
            self.assertIs(t["inputSchema"].get("additionalProperties"), False)

    def test_falsy_nonobject_params_are_rejected(self):
        for value in ([], "", 0, False):
            r = server.handle({"jsonrpc": "2.0", "id": 10,
                               "method": "tools/list", "params": value})
            self.assertEqual(r["error"]["code"], -32602, repr(value))

    def test_falsy_nonobject_arguments_are_rejected(self):
        for value in ([], "", 0, False):
            r = server.handle({"jsonrpc": "2.0", "id": 11,
                               "method": "tools/call", "params": {
                                   "name": "library_links", "arguments": value}})
            self.assertTrue(r["result"]["isError"], repr(value))
            self.assertIn("JSON object", r["result"]["content"][0]["text"])

    def test_top_level_nonobject_is_invalid_request(self):
        for value in ([], "text", 1, False, None):
            r = server.handle(value)
            self.assertEqual(r["error"]["code"], -32600, repr(value))
            self.assertIsNone(r["id"])

    def test_main_recovers_after_malformed_and_nonobject_json(self):
        ping = json.dumps({"jsonrpc": "2.0", "id": 12, "method": "ping"})
        proc = subprocess.run(
            [sys.executable, server.__file__], input="{bad json}\n[]\n" + ping + "\n",
            text=True, capture_output=True, timeout=10, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        frames = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual([f.get("error", {}).get("code") for f in frames[:2]],
                         [-32700, -32600])
        self.assertEqual(frames[2]["result"], {})

    def test_unknown_protocol_falls_back_to_supported_version(self):
        r = server.handle({"jsonrpc": "2.0", "id": 13, "method": "initialize",
                           "params": {"protocolVersion": "2099-01-01"}})
        self.assertNotEqual(r["result"]["protocolVersion"], "2099-01-01")


class InputValidation(unittest.TestCase):
    def test_as_int_defaults_and_bounds(self):
        self.assertEqual(server._as_int(None, 10, "limit"), 10)
        self.assertEqual(server._as_int("", 10, "limit"), 10)
        self.assertEqual(server._as_int("7", 10, "limit"), 7)
        self.assertEqual(server._as_int(7.0, 10, "limit"), 7)
        with self.assertRaises(server.ToolError):
            server._as_int("abc", 10, "limit")
        with self.assertRaises(server.ToolError):
            server._as_int(True, 10, "limit")
        with self.assertRaises(server.ToolError):
            server._as_int([1], 10, "limit")
        with self.assertRaises(server.ToolError):
            server._as_int(5, 10, "limit", minimum=6)
        with self.assertRaises(server.ToolError):
            server._as_int(50, 10, "limit", maximum=30)

    def test_catalog_search_rejects_bad_limit(self):
        with self.assertRaises(server.ToolError) as cm:
            server.tool_catalog_search({"query": "x", "limit": "abc"})
        self.assertIn("limit", str(cm.exception))

    def test_rooms_rejects_calendar_invalid_date(self):
        with self.assertRaises(server.ToolError) as cm:
            server.tool_rooms({"date": "2026-02-30"})
        self.assertIn("real calendar day", str(cm.exception))

    def test_rooms_rejects_nonstring_date(self):
        for bad in (20260801, ["2026-08-01"], b"2026-08-01"):
            with self.assertRaises(server.ToolError):
                server.tool_rooms({"date": bad})

    def test_hours_bounds(self):
        with self.assertRaises(server.ToolError):
            server.tool_hours({"weeks": 0})
        with self.assertRaises(server.ToolError):
            server.tool_hours({"weeks": 9})

    def test_digitalcommons_days_bounds(self):
        with self.assertRaises(server.ToolError):
            server.tool_digitalcommons({"days": -3})

    def test_as_int_huge_and_fractional(self):
        with self.assertRaises(server.ToolError):
            server._as_int(10 ** 400, 10, "limit", maximum=30)
        with self.assertRaises(server.ToolError):
            server._as_int(7.5, 10, "limit")

    def test_as_int_limits_require_positive(self):
        with self.assertRaises(server.ToolError):
            server.tool_catalog_search({"query": "x", "limit": -5})
        with self.assertRaises(server.ToolError):
            server.tool_journal_lookup({"query": "x", "limit": 0})
        with self.assertRaises(server.ToolError):
            server.tool_course_reserves({"query": "x", "limit": 0})

    def test_as_bool_strict_parsing(self):
        self.assertFalse(server._as_bool(None, "overwrite"))
        self.assertFalse(server._as_bool("", "overwrite"))
        self.assertFalse(server._as_bool("false", "overwrite"))
        self.assertFalse(server._as_bool("0", "overwrite"))
        self.assertFalse(server._as_bool("no", "overwrite"))
        self.assertTrue(server._as_bool(True, "overwrite"))
        self.assertTrue(server._as_bool(1, "overwrite"))
        self.assertTrue(server._as_bool("true", "overwrite"))
        with self.assertRaises(server.ToolError):
            server._as_bool("maybe", "overwrite")
        with self.assertRaises(server.ToolError):
            server._as_bool(2, "overwrite")
        with self.assertRaises(server.ToolError):
            server._as_bool(10 ** 400, "overwrite")  # must not OverflowError

    def test_fetch_public_page_caps_body_size(self):
        class FakeResp:
            headers = {}
            status = 200
            def read(self, n=-1):
                return b"x" * 3000
        with mock.patch("urllib.request.build_opener") as bo:
            opener = mock.MagicMock()
            opener.open.return_value.__enter__.return_value = FakeResp()
            bo.return_value = opener
            with self.assertRaises(server.ToolError) as cm:
                server._fetch_public_page("https://example.com/page", max_bytes=100)
        self.assertIn("cap", str(cm.exception))

    def test_save_work_overwrite_string_false_not_truthy(self):
        # regression: bool("false") was True; _as_bool must not repeat that
        with mock.patch.object(server, "resolve_oa_pdf", return_value=(None, None)):
            out = server.tool_save_work({"doi": "10.1234/x", "overwrite": "false"})
        self.assertFalse(out["saved"])

    def test_is_digitalcommons_exact_host(self):
        self.assertTrue(server._is_digitalcommons(
            "https://digitalcommons.andrews.edu/auss/vol56/iss1/6/"))
        self.assertTrue(server._is_digitalcommons(
            "https://lib.digitalcommons.andrews.edu/x"))
        self.assertFalse(server._is_digitalcommons(
            "http://digitalcommons.andrews.edu.127.0.0.1.nip.io/x"))
        self.assertFalse(server._is_digitalcommons(
            "http://digitalcommons.andrews.edu.evil.com/x"))
        self.assertFalse(server._is_digitalcommons(
            "http://notdigitalcommons.andrews.edu/x"))

    def test_save_work_rejects_dc_bypass_hosts_before_network(self):
        for u in ("http://digitalcommons.andrews.edu.127.0.0.1.nip.io/x",
                  "http://digitalcommons.andrews.edu.evil.com/x"):
            with mock.patch("urllib.request.urlopen",
                            side_effect=AssertionError("network must not be called")):
                with self.assertRaises(server.ToolError):
                    server.tool_save_work({"url": u})

    def test_save_work_doi_prefix_normalization(self):
        for doi in ("https://doi.org/10.1234/abc", "http://dx.doi.org/10.1234/abc",
                    "doi:10.1234/abc"):
            with mock.patch.object(server, "resolve_oa_pdf",
                                   return_value=(None, None)) as m:
                server.tool_save_work({"doi": doi})
            self.assertEqual(m.call_args[0][0], "10.1234/abc", doi)


    def test_slim_instance_tolerates_publication_dict_and_string_contributors(self):
        out = server.slim_instance({
            "id": "i1", "title": "T",
            "publication": {"dateOfPublication": "2020", "publisher": "P"},
            "contributors": ["Plain String", {"name": "Doe, J."}],
        })
        self.assertEqual(out["published"], "2020")
        self.assertEqual(out["contributors"], ["Doe, J."])

    def test_http_uris_skips_non_dict_entries(self):
        self.assertEqual(server._http_uris(
            ["https://x.org/1", {"uri": "https://x.org/2"}, {"uri": "javascript:bad"},
             {"uri": "https://x.org/2"}]), ["https://x.org/2"])

    def test_first_pub_tolerates_shape_drift(self):
        self.assertEqual(server._first_pub({"publication": []}), {})
        self.assertEqual(server._first_pub({"publication": [{"publisher": "P"}]}),
                         {"publisher": "P"})
        self.assertEqual(server._first_pub({"publication": {"publisher": "P"}}),
                         {"publisher": "P"})
        self.assertEqual(server._first_pub({"publication": "weird"}), {})

    def test_databases_query_uses_name_and_description_only(self):
        with mock.patch.object(server, "load_databases", return_value=[
                {"name": "ATLA", "description": "Religion & theology index.",
                 "url": "https://x.org"}]):
            out = server.tool_databases({"query": "theology"})
            self.assertEqual(out["total"], 1)


class OkapiAuth(unittest.TestCase):
    def setUp(self):
        server._okapi_token.update(value=None, obtained=0)

    def test_token_from_response_header(self):
        with mock.patch.object(server, "http", fake_http_factory(
                [("guest-token", 204, {"x-okapi-token": "tok123"}, "")])):
            self.assertEqual(server.okapi_token(), "tok123")
        # cached — no second call needed
        with mock.patch.object(server, "http",
                               side_effect=AssertionError("should be cached")):
            self.assertEqual(server.okapi_token(), "tok123")

    def test_token_failure_teaches_recovery(self):
        with mock.patch.object(server, "http", fake_http_factory(
                [("guest-token", 500, {}, "boom")])):
            with self.assertRaises(server.ToolError) as cm:
                server.okapi_token()
            self.assertIn("guest token", str(cm.exception))

    def test_okapi_get_retries_once_on_401(self):
        calls = {"n": 0}
        def fake(url, method="GET", headers=None, data=None, timeout=30):
            if "guest-token" in url:
                return 204, {"x-okapi-token": "t2"}, ""
            calls["n"] += 1
            if calls["n"] == 1:
                return 401, {}, "expired"
            return 200, {}, json.dumps({"ok": True})
        with mock.patch.object(server, "http", fake):
            self.assertEqual(server.okapi_get("/search/instances"), {"ok": True})
        self.assertEqual(calls["n"], 2)


class CatalogTools(unittest.TestCase):
    def test_field_validation(self):
        with self.assertRaises(server.ToolError) as cm:
            server.tool_catalog_search({"query": "x", "field": "bogus"})
        self.assertIn("keyword|title", str(cm.exception))

    def test_bad_instance_id(self):
        with self.assertRaises(server.ToolError):
            server.tool_catalog_item({"instance_id": "not-a-uuid"})

    def test_cql_escape(self):
        self.assertEqual(server.cql_escape('a"b'), 'a\\"b')

    def test_slim_instance_drops_empties(self):
        out = server.slim_instance({"id": "i1", "title": "T", "contributors": [],
                                    "publication": [{}]})
        self.assertNotIn("isbns", out)
        self.assertEqual(out["title"], "T")


class JournalLookup(unittest.TestCase):
    def test_serial_filter_then_fallback(self):
        seen = []
        def fake_get(path, params=None, retry=True):
            seen.append(params["query"])
            if server.SERIAL_MODE_ID in params["query"]:
                return {"totalRecords": 0, "instances": []}
            return {"totalRecords": 1, "instances": [
                {"id": "a" * 36, "title": "Some Journal",
                 "electronicAccess": [{"uri": "https://x.org/j"},
                                      {"uri": "https://x.org/j"}],
                 "items": [], "holdings": [], "publication": [{}]}]}
        with mock.patch.object(server, "okapi_get", fake_get):
            out = server.tool_journal_lookup({"query": "Some Journal"})
        self.assertEqual(len(seen), 2)          # filtered, then fallback
        self.assertIn("note", out)
        j = out["journals"][0]
        self.assertEqual(len(j["online"]), 1)   # deduped
        self.assertTrue(j["online"][0]["ezproxy_url"].startswith(server.EZPROXY_LOGIN))

    def test_print_holdings_aggregation(self):
        inst = {"id": "b" * 36, "title": "Print Only", "publication": [{}],
                "electronicAccess": [], "holdings": [],
                "items": [{"effectiveLocationId": "L1",
                           "effectiveCallNumberComponents": {"callNumber": "PER 1"}},
                          {"effectiveLocationId": "L1",
                           "effectiveCallNumberComponents": {"callNumber": "PER 1"}}]}
        with mock.patch.object(server, "okapi_get",
                               return_value={"totalRecords": 1, "instances": [inst]}), \
             mock.patch.object(server, "location_name", lambda x: "Lower Level"):
            out = server.tool_journal_lookup({"query": "Print Only"})
        ph = out["journals"][0]["print_holdings"]
        self.assertEqual(ph["pieces"], 2)
        self.assertEqual(ph["call_numbers"], ["PER 1"])
        self.assertEqual(ph["locations"], ["Lower Level"])

    def test_requires_query(self):
        with self.assertRaises(server.ToolError):
            server.tool_journal_lookup({})

    def test_only_http_links_are_actionable(self):
        inst = {"id": "c" * 36, "title": "Mixed Links", "publication": [{}],
                "electronicAccess": [
                    {"uri": "javascript:alert(1)"},
                    {"uri": "httpx://invalid.example"},
                    {"uri": "https://valid.example/journal"}],
                "holdings": [], "items": []}
        with mock.patch.object(server, "okapi_get",
                               return_value={"totalRecords": 1, "instances": [inst]}):
            out = server.tool_journal_lookup({"query": "Mixed Links"})
        self.assertEqual([x["url"] for x in out["journals"][0]["online"]],
                         ["https://valid.example/journal"])

    def test_metadata_poor_items_are_not_claimed_as_print_holdings(self):
        inst = {"id": "d" * 36, "title": "Unknown Format", "publication": [{}],
                "electronicAccess": [], "holdings": [], "items": [{}]}
        with mock.patch.object(server, "okapi_get",
                               return_value={"totalRecords": 1, "instances": [inst]}), \
             mock.patch.object(server, "location_name", return_value=None):
            out = server.tool_journal_lookup({"query": "Unknown Format"})
        self.assertNotIn("print_holdings", out["journals"][0])
        self.assertIn("holdings", out["journals"][0])


class DatabasesParser(unittest.TestCase):
    AZ_HTML = ('<html><div class="mb-4"><div class="az-image"></div>'
               '<a href="https://ezproxy.andrews.edu/login?URL=https://x.org"  '
               'target="_blank"  onclick="return springSpace.springTrack.trackLink('
               '{link: this});" data-landing-page="">ATLA Religion<i class="fa"></i>'
               '</a><div class="az-description  mt-2"><p>Religion &amp; theology '
               'index.</p></div></div>'
               '<div class="mb-4"><a href="https://y.org" onclick="springTrack">'
               'No Description DB</a></div></html>')

    def setUp(self):
        server._db_cache.update(at=0, items=None)

    def test_parse_and_filter(self):
        with mock.patch.object(server, "http",
                               fake_http_factory([("az/databases", 200, {}, self.AZ_HTML)])):
            out = server.tool_databases({})
            self.assertEqual(out["total"], 2)
            self.assertEqual(out["databases"][0]["name"], "ATLA Religion")
            self.assertIn("theology", out["databases"][0]["description"])
            hit = server.tool_databases({"query": "religion"})
            self.assertEqual(hit["total"], 1)
            miss = server.tool_databases({"query": "zzzz"})
            self.assertEqual(miss["total"], 0)
            self.assertIn("note", miss)


class DigitalCommons(unittest.TestCase):
    OAI_XML = ('<OAI-PMH><ListRecords><record><header>'
               '<identifier>oai:x:1</identifier></header><metadata>'
               '<dc:title>A Thesis</dc:title><dc:creator>Doe, J.</dc:creator>'
               '<dc:date>2026-01-01</dc:date>'
               '<dc:identifier>https://digitalcommons.andrews.edu/t/1</dc:identifier>'
               '</metadata></record>'
               '<resumptionToken>tok9</resumptionToken></ListRecords></OAI-PMH>')

    def test_record_parse_and_token(self):
        recs, token = server.parse_oai_records(self.OAI_XML)
        self.assertEqual(recs[0]["title"], "A Thesis")
        self.assertEqual(recs[0]["authors"], ["Doe, J."])
        self.assertEqual(token, "tok9")

    def test_oai_error_surfaces(self):
        with mock.patch.object(server, "http", fake_http_factory(
                [("do/oai", 200, {}, '<OAI-PMH><error code="badArgument">nope'
                                     '</error></OAI-PMH>')])):
            with self.assertRaises(server.ToolError) as cm:
                server.oai({"verb": "ListRecords"})
            self.assertIn("badArgument", str(cm.exception))

    def test_list_sets(self):
        xml = ('<OAI-PMH><ListSets><set><setSpec>publication:diss</setSpec>'
               '<setName>Dissertations</setName></set></ListSets></OAI-PMH>')
        with mock.patch.object(server, "http",
                               fake_http_factory([("do/oai", 200, {}, xml)])):
            out = server.tool_digitalcommons({"list_sets": True})
        self.assertEqual(out["sets"][0]["set"], "publication:diss")

    def test_set_param_passed(self):
        captured = {}
        def fake_oai(params):
            captured.update(params)
            return self.OAI_XML
        with mock.patch.object(server, "oai", fake_oai):
            server.tool_digitalcommons({"days": 7, "set": "publication:diss"})
        self.assertEqual(captured.get("set"), "publication:diss")
        self.assertIn("from", captured)

    def test_query_returns_browser_link(self):
        out = server.tool_digitalcommons({"query": "hebrews"})
        self.assertIn("do/search", out["search_url"])

    def test_list_sets_follows_resumption_tokens_without_truncation(self):
        first_sets = "".join(
            "<set><setSpec>s{}</setSpec><setName>Set {}</setName></set>".format(i, i)
            for i in range(151))
        pages = {
            None: "<OAI-PMH><ListSets>{}<resumptionToken>next</resumptionToken>"
                  "</ListSets></OAI-PMH>".format(first_sets),
            "next": ("<OAI-PMH><ListSets><set><setSpec>s151</setSpec>"
                     "<setName>Set 151</setName></set></ListSets></OAI-PMH>"),
        }
        def fake_oai(params):
            return pages.get(params.get("resumptionToken"))
        with mock.patch.object(server, "oai", fake_oai):
            out = server.tool_digitalcommons({"list_sets": True})
        self.assertEqual(out["count"], 152)
        self.assertEqual(len(out["sets"]), 152)
        self.assertEqual(out["sets"][-1]["set"], "s151")

    def test_record_page_is_not_silently_truncated(self):
        records = "".join(
            "<record><header><identifier>oai:x:{0}</identifier></header><metadata>"
            "<dc:title>Record {0}</dc:title></metadata></record>".format(i)
            for i in range(41))
        xml = "<OAI-PMH><ListRecords>{}</ListRecords></OAI-PMH>".format(records)
        with mock.patch.object(server, "oai", return_value=xml):
            out = server.tool_digitalcommons({"days": 7})
        self.assertEqual(out["count"], 41)
        self.assertEqual(len(out["records"]), 41)


class HoursRoomsLinks(unittest.TestCase):
    def test_hours_parses_open_and_closed(self):
        grid = {"locations": [{"name": "JWL", "weeks": [{
            "Monday": {"date": "2026-08-24",
                       "times": {"status": "open",
                                 "hours": [{"from": "8am", "to": "12am"}]}},
            "Sunday": {"date": "2026-08-23", "times": {"status": "closed"}}}]}]}
        with mock.patch.object(server, "http", fake_http_factory(
                [("api_hours_grid", 200, {}, json.dumps(grid))])):
            out = server.tool_hours({})
        days = {d["day"]: d["hours"] for d in out["locations"][0]["days"]}
        self.assertEqual(days["Monday"], "8am–12am")
        self.assertEqual(days["Sunday"], "closed")

    def test_rooms_date_validation(self):
        with self.assertRaises(server.ToolError):
            server.tool_rooms({"date": "8/24/2026"})

    def test_ezproxy_link(self):
        out = server.tool_ezproxy_link({"url": "https://www.jstor.org/stable/1"})
        self.assertTrue(out["ezproxy_url"].startswith(server.EZPROXY_LOGIN))
        with self.assertRaises(server.ToolError):
            server.tool_ezproxy_link({"url": "ftp://x"})
        with self.assertRaises(server.ToolError):
            server.tool_ezproxy_link({"url": "httpx://not-http.example"})

    def test_library_links_complete(self):
        out = server.tool_library_links({})
        for key in ("catalog", "interlibrary_loan_form", "ask_a_librarian",
                    "room_booking", "databases_az", "digital_commons",
                    "ejournal_portal", "hours"):
            self.assertIn(key, out)


class SaveWork(unittest.TestCase):
    def test_requires_doi_or_url(self):
        with self.assertRaises(server.ToolError):
            server.tool_save_work({})

    def test_refuses_gated_hosts_with_guidance(self):
        for u in ("https://www-jstor-org.ezproxy.andrews.edu/stable/1",
                  "https://www.jstor.org/stable/pdf/1.pdf",
                  "https://search.ebscohost.com/x.pdf"):
            with self.assertRaises(server.ToolError) as cm:
                server.tool_save_work({"url": u})
            self.assertIn("Zotero", str(cm.exception))

    def test_gated_host_matching_uses_domain_boundaries(self):
        self.assertTrue(server._gated("https://www.jstor.org/stable/1"))
        self.assertFalse(server._gated("https://notjstor.org/open.pdf"))

    def test_refuses_nonpublic_and_nonhttp_download_urls_before_network(self):
        for url in ("file:///tmp/local.pdf", "https://127.0.0.1/private.pdf",
                    "http://localhost/internal.pdf"):
            with mock.patch("urllib.request.urlopen",
                            side_effect=AssertionError("network must not be called")):
                with self.assertRaises(server.ToolError) as cm:
                    server.tool_save_work({"url": url})
            self.assertIn("public http", str(cm.exception).lower())

    def test_bad_doi_format(self):
        with self.assertRaises(server.ToolError):
            server.tool_save_work({"doi": "not-a-doi"})

    def test_non_oa_doi_returns_sanctioned_path(self):
        with mock.patch.object(server, "resolve_oa_pdf", return_value=(None, None)):
            out = server.tool_save_work({"doi": "10.1234/gated"})
        self.assertFalse(out["saved"])
        self.assertIn("ezproxy_url", out)
        self.assertIn("Zotero", out["sanctioned_path"])

    def test_oa_resolving_to_gated_host_not_fetched(self):
        with mock.patch.object(server, "resolve_oa_pdf",
                               return_value=("https://www.jstor.org/x.pdf", "unpaywall")):
            out = server.tool_save_work({"doi": "10.1234/odd"})
        self.assertFalse(out["saved"])

    def test_digitalcommons_pdf_extraction(self):
        page = ('<meta name="citation_pdf_url" content='
                '"https://digitalcommons.andrews.edu/cgi/viewcontent.cgi'
                '?article=1&amp;context=auss"/>')
        captured = {}
        def fake_dl(url, **kw):
            captured["url"] = url
            return {"saved_to": "/tmp/x.pdf", "size_bytes": 10}
        def fake_page(url):
            return 200, {"Set-Cookie": "be_cookie=1; Path=/"}, page
        with mock.patch.object(server, "_fetch_public_page", fake_page), \
             mock.patch.object(server, "_download_pdf", fake_dl):
            out = server.tool_save_work(
                {"url": "https://digitalcommons.andrews.edu/auss/vol56/iss1/6/"})
        self.assertTrue(out["saved"])
        self.assertIn("viewcontent.cgi?article=1&context=auss", captured["url"])

    def test_digitalcommons_bot_wall_degrades_gracefully(self):
        page = ('<meta name="citation_pdf_url" content='
                '"https://digitalcommons.andrews.edu/cgi/viewcontent.cgi'
                '?article=1&amp;context=auss"/>')
        def fake_dl(url, **kw):
            raise server.ToolError("target did not return a PDF")
        def fake_page(url):
            return 200, {}, page
        with mock.patch.object(server, "_fetch_public_page", fake_page), \
             mock.patch.object(server, "_download_pdf", fake_dl):
            out = server.tool_save_work(
                {"url": "https://digitalcommons.andrews.edu/auss/vol56/iss1/6/"})
        self.assertFalse(out["saved"])
        self.assertIn("viewcontent.cgi", out["pdf_url"])
        self.assertIn("browser", out["how"])

    def test_unpaywall_parse(self):
        body = json.dumps({"is_oa": True, "best_oa_location":
                           {"url_for_pdf": "https://open.org/a.pdf"}})
        with mock.patch.object(server, "http",
                               fake_http_factory([("unpaywall", 200, {}, body)])):
            pdf, src = server.resolve_oa_pdf("10.1/x")
        self.assertEqual((pdf, src), ("https://open.org/a.pdf", "unpaywall"))


class ResultBudget(unittest.TestCase):
    def test_truncation_cap(self):
        big = {"x": "y" * (server.MAX_TEXT + 1000)}
        with mock.patch.dict(server.HANDLERS, {"library_links": lambda a: big}):
            r = server.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                               "params": {"name": "library_links"}})
        text = r["result"]["content"][0]["text"]
        self.assertLessEqual(len(text), server.MAX_TEXT + 100)
        self.assertIn("truncated", text)


class SavedAndCitation(unittest.TestCase):
    def test_list_saved_empty_dir_is_clean(self):
        with mock.patch.object(server.Path, "home", return_value=server.Path("/tmp")):
            out = server.tool_list_saved({})
        self.assertEqual(out["count"], 0)
        self.assertIn("No saved files", out["note"])

    def test_list_saved_rejects_bad_detail(self):
        with self.assertRaises(server.ToolError) as cm:
            server.tool_list_saved({"detail": "verbose"})
        self.assertIn("concise", str(cm.exception))

    def test_citation_export_rejects_bad_id(self):
        with self.assertRaises(server.ToolError) as cm:
            server.tool_citation_export({"instance_id": "nope"})
        self.assertIn("36-char", str(cm.exception))

    def test_citation_export_rejects_bad_format(self):
        with mock.patch.object(server, "okapi_get",
                                return_value={"instances": [{"id": "i", "title": "T"}]}):
            with self.assertRaises(server.ToolError) as cm:
                server.tool_citation_export({"instance_id": "a" * 36, "format": "apa"})
        self.assertIn("ris", str(cm.exception))

    def test_citation_export_ris_and_bibtex(self):
        inst = {"id": "a" * 36, "title": "Discipleship",
                "contributors": [{"name": "Dietrich Bonhoeffer"}],
                "publication": [{"dateOfPublication": "1959", "publisher": "Fortress"}],
                "isbns": ["9780800697033"]}
        for fmt in ("ris", "bibtex"):
            with mock.patch.object(server, "okapi_get",
                                    return_value={"instances": [inst]}):
                out = server.tool_citation_export({"instance_id": "a" * 36,
                                                   "format": fmt})
            self.assertEqual(out["format"], fmt)
            cit = out["citation"]
            self.assertIn("Discipleship", cit)
            if fmt == "ris":
                self.assertIn("TY  - BOOK", cit)
                self.assertIn("AU  - Dietrich Bonhoeffer", cit)
                self.assertIn("SN  - 9780800697033", cit)
            else:
                self.assertIn("@book{", cit)
                self.assertIn("bonhoeffer1959", cit.split("{", 1)[1].split(",")[0])

    def test_slim_instance_surfaces_doi(self):
        inst = {"id": "i1", "title": "T",
                "identifiers": [{"identifierTypeId": "isbn", "value": "1"},
                                {"identifierTypeId": "DOI", "value": "10.1/abc"}]}
        out = server.slim_instance(inst)
        self.assertEqual(out["doi"], "10.1/abc")

    def test_slim_instance_no_doi_when_absent(self):
        out = server.slim_instance({"id": "i1", "title": "T", "identifiers": []})
        self.assertNotIn("doi", out)


@unittest.skipUnless(os.environ.get("RUN_LIVE") == "1", "set RUN_LIVE=1 for live smokes")
class LiveSmokes(unittest.TestCase):
    def test_catalog_search_live(self):
        out = server.tool_catalog_search({"query": "bonhoeffer", "limit": 2})
        self.assertGreater(out["total"], 0)

    def test_journal_lookup_live(self):
        out = server.tool_journal_lookup({"query": "Journal of Biblical Literature"})
        self.assertGreater(out["total"], 0)
        self.assertTrue(any(j.get("print_holdings") for j in out["journals"]))

    def test_hours_live(self):
        out = server.tool_hours({})
        self.assertTrue(out["locations"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
