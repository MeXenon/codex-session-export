"""Tests for codex-md.py session title derivation (run: python -m pytest tests/ -q)."""

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "codex-md.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("codex_md", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_numeric_session_id_does_not_crash_title(tmp_path):
    codex_md = _load_module()
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({
            "type": "session_meta",
            "timestamp": "2026-01-01T00:00:00Z",
            "payload": {"id": 12345678901234567890},
        }) + "\n",
        encoding="utf-8",
    )
    parser = codex_md.SessionParser(session)
    assert parser.title == "System Session 12345678"


def test_string_session_id_keeps_first_eight_chars(tmp_path):
    codex_md = _load_module()
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({
            "type": "session_meta",
            "timestamp": "2026-01-01T00:00:00Z",
            "payload": {"id": "abcdefab-1234-5678-9abc-def012345678"},
        }) + "\n",
        encoding="utf-8",
    )
    parser = codex_md.SessionParser(session)
    assert parser.title == "System Session abcdefab"
