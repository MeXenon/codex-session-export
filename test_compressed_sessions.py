#!/usr/bin/env python3
"""
Tests for compressed Codex session logs (rollout-*.jsonl.zst).

Every fixture is synthetic and lives in a temporary directory.  CODEX_HOME is
redirected to a temporary directory *before* codex-md.py is imported, because
the module reads the session index at import time.  No real Codex session,
credential or configuration file is ever touched.

Run with:
    python -m unittest test_compressed_sessions -v
"""

import importlib.util
import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SCRIPT_PATH = REPO_ROOT / "codex-md.py"

SESSION_ID = "11111111-2222-3333-4444-555555555555"
OTHER_SESSION_ID = "99999999-8888-7777-6666-555555555555"
ROLLOUT_STEM = "rollout-2026-01-01T00-00-00-" + SESSION_ID

try:  # Python 3.14+ ships a Zstandard implementation.
    from compression import zstd as _zstd
except ImportError:  # pragma: no cover - depends on the interpreter
    _zstd = None

try:  # Optional backport for older interpreters.
    from backports import zstd as _backports_zstd
except ImportError:  # pragma: no cover - depends on the environment
    _backports_zstd = None


def load_script():
    """Import codex-md.py under a private module name."""
    spec = importlib.util.spec_from_file_location("codex_md_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_TEMP_HOME = None
_PREVIOUS_CODEX_HOME = os.environ.get("CODEX_HOME")
SCRIPT = None


def setUpModule():
    """Isolate CODEX_HOME, then import the script under test."""
    global _TEMP_HOME, SCRIPT
    _TEMP_HOME = tempfile.TemporaryDirectory(prefix="codex-md-home-")
    os.environ["CODEX_HOME"] = _TEMP_HOME.name
    SCRIPT = load_script()


def tearDownModule():
    global _TEMP_HOME
    if _PREVIOUS_CODEX_HOME is None:
        os.environ.pop("CODEX_HOME", None)
    else:
        os.environ["CODEX_HOME"] = _PREVIOUS_CODEX_HOME
    if _TEMP_HOME is not None:
        _TEMP_HOME.cleanup()
        _TEMP_HOME = None


def build_entries(session_id=SESSION_ID, cwd="/tmp/demo-project"):
    """A small rollout transcript exercising metadata, Unicode and tools."""
    return [
        {
            "timestamp": "2026-01-01T00:00:00.000Z",
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "timestamp": "2026-01-01T00:00:00.000Z",
                "cwd": cwd,
                "originator": "codex_cli_rs",
                "cli_version": "0.42.0",
                "instructions": None,
                "git": {"commit_hash": "abc123"},
            },
        },
        {
            "timestamp": "2026-01-01T00:00:01.000Z",
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": "## My request for Codex:\nAdd a greeting — héllo 🌍",
            },
        },
        {
            "timestamp": "2026-01-01T00:00:02.000Z",
            "type": "event_msg",
            "payload": {"type": "agent_reasoning", "text": "Think about the greeting."},
        },
        {
            "timestamp": "2026-01-01T00:00:03.000Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "shell_command",
                "call_id": "call-1",
                "arguments": json.dumps({"command": ["echo", "hi"]}),
            },
        },
        {
            "timestamp": "2026-01-01T00:00:04.000Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": "hi\n",
            },
        },
        {
            "timestamp": "2026-01-01T00:00:05.000Z",
            "type": "event_msg",
            "payload": {"type": "agent_message", "message": "Done — the greeting says héllo 🌍."},
        },
    ]


def transcript_bytes(entries):
    """Serialize entries exactly the way a rollout log stores them."""
    return "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries).encode("utf-8")


def compress_bytes(payload):
    """Compress with the same backend family the script supports."""
    if _zstd is not None:
        return _zstd.compress(payload)
    if _backports_zstd is not None:
        return _backports_zstd.compress(payload)
    raise unittest.SkipTest("no Zstandard backend is available in this environment")


def write_jsonl(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fp:
        fp.write(transcript_bytes(entries).decode("utf-8"))
    return path


def write_zst(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(compress_bytes(transcript_bytes(entries)))
    return path


def normalize_source(markdown):
    """Hide the Source: line, which names the concrete file on disk."""
    return re.sub(r"^Source: .*$", "Source: <rollout>", markdown, flags=re.MULTILINE)


class ScriptTestCase(unittest.TestCase):
    """Base class that points the script at a throwaway sessions directory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-md-case-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.sessions_dir = self.root / "sessions"
        self.sessions_dir.mkdir(parents=True)

        self._saved = {
            "SESSIONS_DIR": SCRIPT.SESSIONS_DIR,
            "SESSION_INDEX_PATH": SCRIPT.SESSION_INDEX_PATH,
            "THREAD_NAMES": SCRIPT.THREAD_NAMES,
        }
        SCRIPT.SESSIONS_DIR = self.sessions_dir
        SCRIPT.SESSION_INDEX_PATH = self.root / "session_index.jsonl"
        SCRIPT.THREAD_NAMES = {}
        self.addCleanup(self._restore_globals)

    def _restore_globals(self):
        for name, value in self._saved.items():
            setattr(SCRIPT, name, value)

    def plain_rollout(self, stem=ROLLOUT_STEM, **kwargs):
        return write_jsonl(self.sessions_dir / f"{stem}.jsonl", build_entries(**kwargs))

    def compressed_rollout(self, stem=ROLLOUT_STEM, **kwargs):
        return write_zst(self.sessions_dir / f"{stem}.jsonl.zst", build_entries(**kwargs))

    def broken_rollout(self, stem=ROLLOUT_STEM + "-broken"):
        path = self.sessions_dir / f"{stem}.jsonl.zst"
        path.write_bytes(b"definitely not a zstd frame")
        return path


class DiscoveryTests(ScriptTestCase):
    def test_scan_finds_plain_and_compressed_rollouts(self):
        plain = self.plain_rollout()
        compressed = self.compressed_rollout(stem=ROLLOUT_STEM + "-other",
                                             session_id=OTHER_SESSION_ID)

        records = SCRIPT.scan_sessions()

        found = {record.path for record in records}
        self.assertIn(plain, found)
        self.assertIn(compressed, found)
        self.assertEqual(len(records), 2)
        compressed_record = next(r for r in records if r.path == compressed)
        self.assertEqual(compressed_record.session_id, OTHER_SESSION_ID)
        self.assertEqual(compressed_record.cwd, "/tmp/demo-project")

    def test_find_by_session_id_matches_compressed_rollout(self):
        compressed = self.compressed_rollout()
        records = SCRIPT.scan_sessions()

        by_id = SCRIPT.find_records_by_session_id(records, SESSION_ID)
        by_name = SCRIPT.find_records_by_session_id(records, compressed.name)

        self.assertEqual([record.path for record in by_id], [compressed])
        self.assertEqual([record.path for record in by_name], [compressed])

    def test_plain_log_behaviour_is_unchanged(self):
        plain = self.plain_rollout()

        records = SCRIPT.scan_sessions()
        meta, first_message, saw_user_message = SCRIPT.read_session_summary(plain)
        parser = SCRIPT.SessionParser(plain)

        self.assertEqual([record.path for record in records], [plain])
        self.assertEqual(meta.get("id"), SESSION_ID)
        self.assertTrue(saw_user_message)
        self.assertIn("héllo 🌍", first_message)
        self.assertIn("héllo 🌍", parser.to_markdown())
        self.assertIn(f"Source: {plain.name}", parser.to_markdown())


class SummaryTests(ScriptTestCase):
    def test_summary_reads_compressed_log(self):
        compressed = self.compressed_rollout()

        meta, first_message, saw_user_message = SCRIPT.read_session_summary(compressed)

        self.assertIsInstance(meta, dict)
        self.assertEqual(meta.get("id"), SESSION_ID)
        self.assertEqual(meta.get("cwd"), "/tmp/demo-project")
        self.assertTrue(saw_user_message)
        self.assertIn("héllo 🌍", first_message)
        self.assertNotIn("## My request for Codex:", first_message)

    def test_preview_title_comes_from_compressed_log(self):
        compressed = self.compressed_rollout()

        self.assertEqual(SCRIPT.get_session_preview_title(compressed),
                         "Add a greeting — héllo 🌍")


class FullExportTests(ScriptTestCase):
    def test_compressed_export_matches_plain_export(self):
        plain = self.plain_rollout()
        compressed = write_zst(self.root / "solo" / f"{ROLLOUT_STEM}.jsonl.zst",
                               build_entries())

        plain_parser = SCRIPT.SessionParser(plain)
        compressed_parser = SCRIPT.SessionParser(compressed)

        self.assertEqual(plain_parser.metadata, compressed_parser.metadata)
        self.assertEqual(plain_parser.data, compressed_parser.data)
        self.assertEqual(plain_parser.title, compressed_parser.title)
        self.assertEqual(plain_parser.start_time, compressed_parser.start_time)
        self.assertEqual(normalize_source(plain_parser.to_markdown()),
                         normalize_source(compressed_parser.to_markdown()))

    def test_compressed_export_keeps_order_unicode_and_metadata(self):
        compressed = self.compressed_rollout()

        parser = SCRIPT.SessionParser(compressed)

        self.assertEqual([item["type"] for item in parser.data],
                         ["user_message", "agent_reasoning", "terminal_cmd",
                          "terminal_output", "agent_message"])
        self.assertIn("héllo 🌍", parser.data[0]["content"])
        self.assertEqual(parser.metadata.get("git", {}).get("commit_hash"), "abc123")
        self.assertEqual(parser.start_time, "2026-01-01T00:00:00.000Z")

        markdown = parser.to_markdown()
        self.assertIn("héllo 🌍", markdown)
        self.assertIn(f"Source: {compressed.name}", markdown)


class DuplicateTests(ScriptTestCase):
    def test_duplicate_rollout_is_listed_once_and_plain_wins(self):
        plain = self.plain_rollout()
        compressed = self.compressed_rollout()

        records = SCRIPT.scan_sessions()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].path, plain)
        self.assertEqual(SCRIPT.preferred_session_paths([compressed, plain]), [plain])

    def test_distinct_rollouts_with_same_session_id_are_not_conflated(self):
        first = self.plain_rollout(stem=ROLLOUT_STEM + "-page1")
        second = self.compressed_rollout(stem=ROLLOUT_STEM + "-page2")

        records = SCRIPT.scan_sessions()

        self.assertEqual(len(records), 2)
        self.assertEqual({record.path for record in records}, {first, second})

    def test_same_rollout_name_in_different_folders_is_not_conflated(self):
        first = self.plain_rollout()
        second = write_zst(self.sessions_dir / "other" / f"{ROLLOUT_STEM}.jsonl.zst",
                           build_entries())

        records = SCRIPT.scan_sessions()

        self.assertEqual(len(records), 2)
        self.assertEqual({record.path for record in records}, {first, second})


class FailureTests(ScriptTestCase):
    def test_corrupt_compressed_log_raises_session_read_error(self):
        broken = self.broken_rollout()

        with self.assertRaises(SCRIPT.SessionReadError) as ctx:
            SCRIPT.read_session_summary(broken)

        self.assertIn("Failed to decompress", str(ctx.exception))
        self.assertIn(broken.name, str(ctx.exception))

    def test_corrupt_compressed_log_raises_in_the_full_parser(self):
        broken = self.broken_rollout()

        with self.assertRaises(SCRIPT.SessionReadError) as ctx:
            SCRIPT.SessionParser(broken)

        self.assertIn("Failed to decompress", str(ctx.exception))

    def test_truncated_compressed_log_is_rejected_by_the_full_parser(self):
        truncated = self.sessions_dir / f"{ROLLOUT_STEM}-truncated.jsonl.zst"
        payload = compress_bytes(transcript_bytes(build_entries()))
        truncated.write_bytes(payload[:len(payload) // 2])

        with self.assertRaises(SCRIPT.SessionReadError) as ctx:
            SCRIPT.SessionParser(truncated)

        self.assertIn("Failed to decompress", str(ctx.exception))

    def test_broken_compressed_log_is_skipped_and_the_scan_continues(self):
        healthy = self.compressed_rollout(stem=ROLLOUT_STEM + "-healthy")
        broken = self.broken_rollout()

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            records = SCRIPT.scan_sessions()

        self.assertEqual([record.path for record in records], [healthy])
        self.assertIn("Failed to decompress", buffer.getvalue())
        self.assertIn(broken.name, buffer.getvalue())

    def test_each_scan_reports_a_broken_file_once(self):
        self.broken_rollout()

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            SCRIPT.scan_sessions()
            SCRIPT.scan_sessions()

        self.assertEqual(buffer.getvalue().count("Failed to decompress"), 2)

    def test_missing_backend_raises_an_actionable_error(self):
        compressed = self.compressed_rollout()
        original = SCRIPT.load_zstd_backend
        SCRIPT.load_zstd_backend = lambda: None
        self.addCleanup(setattr, SCRIPT, "load_zstd_backend", original)

        with self.assertRaises(SCRIPT.SessionReadError) as ctx:
            SCRIPT.read_session_summary(compressed)

        message = str(ctx.exception)
        self.assertIn("backports.zstd", message)
        self.assertIn("3.14", message)


@unittest.skipUnless(_backports_zstd is not None,
                     "the optional 'backports.zstd' package is not installed")
class BackportBackendTests(ScriptTestCase):
    def test_real_backport_backend_reads_compressed_logs(self):
        compressed = self.compressed_rollout()
        original = SCRIPT.load_zstd_backend
        SCRIPT.load_zstd_backend = lambda: _backports_zstd
        self.addCleanup(setattr, SCRIPT, "load_zstd_backend", original)

        meta, first_message, saw_user_message = SCRIPT.read_session_summary(compressed)
        parser = SCRIPT.SessionParser(compressed)

        self.assertEqual(meta.get("id"), SESSION_ID)
        self.assertTrue(saw_user_message)
        self.assertIn("héllo 🌍", first_message)
        self.assertIn("héllo 🌍", parser.to_markdown())


if __name__ == "__main__":
    unittest.main()
