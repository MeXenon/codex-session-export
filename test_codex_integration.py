"""Combined integration coverage for the two Codex exporter PRs.

Uses synthetic text and disposable filesystem trees. Run with
uv run --no-project --with backports.zstd python -m unittest discover -v
"""
import contextlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from compression import zstd
except ImportError:
    try:
        from backports import zstd
    except ImportError:
        zstd = None

SPEC = importlib.util.spec_from_file_location(
    'codex_export_integration', Path(__file__).with_name('codex-md.py'))
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)
THREAD = '11111111-1111-4111-8111-111111111111'
FORK = '22222222-2222-4222-8222-222222222222'


def row(kind, payload, ts):
    return {'type': kind, 'timestamp': ts, 'payload': payload}


def turns(text, ts):
    return [
        row('event_msg', {'type': 'task_started'}, ts),
        row('event_msg', {'type': 'user_message', 'message': text}, ts),
        row('event_msg', {'type': 'agent_message', 'message': 'Reply to ' + text}, ts),
        row('event_msg', {'type': 'task_complete'}, ts),
    ]


def meta(thread, ts, base=None):
    data = {'id': thread, 'timestamp': ts, 'cwd': '/synthetic/project', 'source': 'cli'}
    if base is not None:
        data['history_base'] = base
    return row('session_meta', data, ts)


def as_bytes(items):
    return b''.join((json.dumps(x) + '\n').encode('utf-8') for x in items)


def boundary(items, thread):
    inherited = (items[0]['payload'].get('history_base') or {}).get(
        'end_ordinal_exclusive', 0)
    return {'thread_id': thread, 'end_ordinal_exclusive': inherited + len(items),
            'end_byte_offset': len(as_bytes(items))}


@unittest.skipIf(zstd is None, 'real Zstandard backend required')
class CombinedHistoryCompressionTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = Path(d.name)
        self.sessions = self.root / 'sessions'
        self.sessions.mkdir()

    def rollout(self, name, rows, compressed=False):
        target = self.sessions / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zstd.compress(as_bytes(rows)) if compressed else as_bytes(rows))
        return target

    def test_compressed_parent_and_compressed_child_export_all_turns(self):
        a = [meta(THREAD, '2026-01-01')] + turns('alpha', '2026-01-01')
        base = self.rollout(f'rollout-first-{THREAD}.jsonl.zst', a, True)
        b = [meta(THREAD, '2026-01-02', boundary(a, THREAD))]
        b += turns('beta', '2026-01-02')
        tail = self.rollout(f'rollout-last-{THREAD}.jsonl.zst', b, True)
        parser = exporter.SessionParser(tail)
        self.assertEqual(parser.source_paths, [base, tail])
        self.assertEqual(parser.get_turn_count(), 2)
        md = parser.to_markdown()
        for needle in ('alpha', 'beta', 'Reply to alpha', 'Reply to beta'):
            self.assertEqual(md.count(needle if needle.startswith('Reply to') else 'Reply to '+needle), 1)
        with patch.object(exporter, 'SESSIONS_DIR', self.sessions):
            records = exporter.scan_sessions()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].path, tail)

    def test_plain_base_and_compressed_fork_respects_checkpoint(self):
        a = [meta(THREAD, '2026-01-01')] + turns('included', '2026-01-01')
        later = a + turns('excluded', '2026-01-02')
        self.rollout(f'rollout-base-{THREAD}.jsonl', later)
        fork = self.rollout(f'rollout-fork-{FORK}.jsonl.zst', [
            meta(FORK, '2026-01-03', boundary(a, THREAD)),
            *turns('branch', '2026-01-03')
        ], True)
        parser = exporter.SessionParser(fork)
        md = parser.to_markdown()
        self.assertEqual(parser.get_turn_count(), 2)
        self.assertIn('Reply to included', md)
        self.assertIn('Reply to branch', md)
        self.assertNotIn('Reply to excluded', md)

    def test_compressed_base_missing_or_damaged_fails_closed(self):
        a = [meta(THREAD, '2026-01-01')] + turns('original', '2026-01-01')
        p = self.rollout(f'rollout-base-{THREAD}.jsonl.zst', a, True)
        tail = self.rollout(f'rollout-tail-{THREAD}.jsonl.zst', [
            meta(THREAD, '2026-01-02', boundary(a, THREAD)),
            *turns('partial', '2026-01-02')
        ], True)
        p.write_bytes(b'bad compression frame')
        with self.assertRaisesRegex(ValueError, 'Missing base history'):
            exporter.SessionParser(tail)

    def test_same_uuid_independent_files_remain_independent(self):
        self.rollout(f'rollout-first-{THREAD}.jsonl', [
            meta(THREAD, '2026-01-01'), *turns('one', '2026-01-01')])
        self.rollout(f'rollout-second-{THREAD}.jsonl.zst', [
            meta(THREAD, '2026-01-02'), *turns('two', '2026-01-02')], True)
        with patch.object(exporter, 'SESSIONS_DIR', self.sessions):
            records = exporter.scan_sessions()
        self.assertEqual(len(records), 2)


if __name__ == '__main__':
    unittest.main()
