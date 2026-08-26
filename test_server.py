#!/usr/bin/env python3
"""Test suite for the andrews-library MCP server (run: /usr/bin/python3
test_server.py from this directory). Offline by default — network functions
are monkeypatched; set RUN_LIVE=1 to add live smoke tests against the real
library systems."""

import json
import os
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

    def test_library_links_complete(self):
        out = server.tool_library_links({})
        for key in ("catalog", "interlibrary_loan_form", "ask_a_librarian",
                    "room_booking", "databases_az", "digital_commons",
                    "ejournal_portal", "hours"):
            self.assertIn(key, out)


class ResultBudget(unittest.TestCase):
    def test_truncation_cap(self):
        big = {"x": "y" * (server.MAX_TEXT + 1000)}
        with mock.patch.dict(server.HANDLERS, {"library_links": lambda a: big}):
            r = server.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                               "params": {"name": "library_links"}})
        text = r["result"]["content"][0]["text"]
        self.assertLessEqual(len(text), server.MAX_TEXT + 100)
        self.assertIn("truncated", text)


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
