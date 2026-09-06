import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("codex_md", ROOT / "codex-md.py")
codex_md = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(codex_md)


class TerminalCompatibilityTests(unittest.TestCase):
    def parse(self, entries):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "rollout-test.jsonl"
            path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")
            return codex_md.SessionParser(path)

    @staticmethod
    def types(parser):
        return [item["type"] for item in parser.data]

    def test_legacy_function_call_and_output(self):
        parser = self.parse([
            {"timestamp": "2026-06-01T00:00:00Z", "type": "response_item", "payload": {
                "type": "function_call", "name": "exec_command",
                "arguments": json.dumps({"cmd": "echo legacy"}), "call_id": "call-old"}},
            {"timestamp": "2026-06-01T00:00:01Z", "type": "response_item", "payload": {
                "type": "function_call_output", "call_id": "call-old", "output": "legacy\n"}},
        ])
        self.assertEqual(self.types(parser).count("terminal_cmd"), 1)
        self.assertEqual(self.types(parser).count("terminal_output"), 1)
        markdown = parser.to_markdown({"terminal_cmd": True, "terminal_output": True})
        self.assertIn("echo legacy", markdown)
        self.assertIn("legacy", markdown)

    def test_code_mode_exec_wrapper_is_terminal_fallback(self):
        parser = self.parse([
            {"timestamp": "2026-08-01T00:00:00Z", "type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "exec", "call_id": "call-code",
                "input": 'const r = await tools.shell_command({command:"echo code-mode",workdir:"C:\\\\tmp"}); text(r);'}},
            {"timestamp": "2026-08-01T00:00:01Z", "type": "response_item", "payload": {
                "type": "custom_tool_call_output", "call_id": "call-code",
                "output": [{"type": "input_text", "text": "Script completed\nOutput:\n"},
                           {"type": "input_text", "text": "code-mode\n"}]}},
        ])
        self.assertEqual(self.types(parser), ["terminal_cmd", "terminal_output"])
        self.assertEqual(parser.data[0]["arguments"], "echo code-mode")
        self.assertEqual(parser.data[1]["output"], "Script completed\nOutput:\ncode-mode\n")

    def test_current_command_execution_replaces_code_mode_wrapper(self):
        parser = self.parse([
            {"timestamp": "2026-09-01T00:00:00Z", "type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "exec", "call_id": "call-current",
                "input": 'text(await tools.exec_command({cmd:"echo current",max_output_tokens:1000}));'}},
            {"timestamp": "2026-09-01T00:00:01Z", "type": "response_item", "payload": {
                "type": "custom_tool_call_output", "call_id": "call-current",
                "output": [{"type": "input_text", "text": "current\n"}]}},
            {"timestamp": "2026-09-01T00:00:02Z", "type": "event_msg", "payload": {
                "type": "item_completed", "item": {
                    "type": "CommandExecution", "id": "exec-1",
                    "command": ["pwsh.exe", "-NoProfile", "-Command", "echo current"],
                    "parsed_cmd": [{"type": "unknown", "cmd": "echo current"}],
                    "stdout": "current\r\n", "stderr": "", "aggregated_output": "current\r\n",
                    "formatted_output": "current\r\n", "exit_code": 0, "status": "completed"}}},
        ])
        self.assertEqual(self.types(parser), ["terminal_cmd", "terminal_output"])
        self.assertEqual(parser.data[0]["arguments"], "echo current")
        self.assertEqual(parser.data[1]["output"], "current\r\n")
        self.assertNotIn("session_event", self.types(parser))

    def test_legacy_exec_command_end_is_supported(self):
        parser = self.parse([
            {"timestamp": "2025-01-03T12:00:00Z", "type": "event_msg", "payload": {
                "type": "exec_command_end", "call_id": "legacy-end", "command": ["echo", "ok"],
                "cwd": "file:///tmp", "parsed_cmd": [], "stdout": "ok", "stderr": "",
                "aggregated_output": "ok", "formatted_output": "ok", "exit_code": 0,
                "status": "completed"}},
        ])
        self.assertEqual(self.types(parser), ["terminal_cmd", "terminal_output"])
        self.assertEqual(parser.data[0]["arguments"], "echo ok")

    def test_unknown_namespaced_function_is_not_terminal(self):
        parser = self.parse([
            {"timestamp": "2026-09-01T00:00:00Z", "type": "response_item", "payload": {
                "type": "function_call", "name": "future_collaboration_tool", "namespace": "collaboration",
                "arguments": "{}", "call_id": "call-other"}},
            {"timestamp": "2026-09-01T00:00:01Z", "type": "response_item", "payload": {
                "type": "function_call_output", "call_id": "call-other", "output": "done"}},
        ])
        self.assertEqual(self.types(parser), ["other_tool", "other_tool_output"])

    def test_real_custom_patch_stays_custom_and_array_output_normalizes(self):
        parser = self.parse([
            {"timestamp": "2026-09-01T00:00:00Z", "type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "apply_patch", "call_id": "call-patch",
                "input": "*** Begin Patch\n*** End Patch"}},
            {"timestamp": "2026-09-01T00:00:01Z", "type": "response_item", "payload": {
                "type": "custom_tool_call_output", "call_id": "call-patch",
                "output": [{"type": "input_text", "text": "Done!"}]}},
        ])
        self.assertEqual(self.types(parser), ["custom_tool_call", "custom_tool_output"])
        self.assertEqual(parser.data[1]["content"], "Done!")

    def test_code_mode_mcp_wrapper_routes_to_mcp_section(self):
        parser = self.parse([
            {"timestamp": "2026-09-01T00:00:00Z", "type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "exec", "call_id": "call-mcp",
                "input": 'text(await tools.mcp__codex_apps__system_file_mcp_read_text_file({vpath:"C:\\\\tmp\\\\x.py"}));'}},
            {"timestamp": "2026-09-01T00:00:01Z", "type": "response_item", "payload": {
                "type": "custom_tool_call_output", "call_id": "call-mcp",
                "output": [{"type": "input_text", "text": "MCP result"}]}},
        ])
        self.assertEqual(self.types(parser), ["mcp_tool", "mcp_tool_output"])
        self.assertEqual(parser.data[0]["name"], "mcp__codex_apps__system_file_mcp_read_text_file")

    def test_code_mode_nonterminal_exec_is_not_a_patch(self):
        parser = self.parse([
            {"timestamp": "2026-09-01T00:00:00Z", "type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "exec", "call_id": "call-plan",
                "input": 'text(await tools.update_plan({plan:[]}));'}},
            {"timestamp": "2026-09-01T00:00:01Z", "type": "response_item", "payload": {
                "type": "custom_tool_call_output", "call_id": "call-plan", "output": "{}"}},
        ])
        self.assertEqual(self.types(parser), ["other_tool", "other_tool_output"])
        self.assertEqual(parser.data[0]["name"], "update_plan")


if __name__ == "__main__":
    unittest.main()
