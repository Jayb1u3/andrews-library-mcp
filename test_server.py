#!/usr/bin/env python3
"""Offline contract checks for the Andrews Library MCP."""
import importlib.util
import pathlib
import unittest

SERVER_PATH = pathlib.Path(__file__).with_name("server.py")
SPEC = importlib.util.spec_from_file_location("andrews_library_server", str(SERVER_PATH))
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


class AndrewsLibraryTests(unittest.TestCase):
    def test_tool_registry_is_complete_and_unique(self):
        names = [tool["name"] for tool in server.TOOLS]
        self.assertEqual(10, len(names))
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), set(server.HANDLERS))

    def test_initialize_and_tool_listing(self):
        initialized = server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        })
        self.assertEqual("andrews-library", initialized["result"]["serverInfo"]["name"])
        listed = server.handle({
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        })
        self.assertEqual(10, len(listed["result"]["tools"]))

    def test_no_tool_requires_credentials(self):
        serialized = repr(server.TOOLS).lower()
        self.assertNotIn("password", serialized)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("access_token", serialized)


if __name__ == "__main__":
    unittest.main()
