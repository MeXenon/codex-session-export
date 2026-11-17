import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location('codex_md', Path(__file__).with_name('codex-md.py'))
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)

THREAD = '11111111-1111-4111-8111-111111111111'
FORK = '22222222-2222-4222-8222-222222222222'


def entry(kind, payload, timestamp='2026-01-01T00:00:00Z'):
    return {'type': kind, 'timestamp': timestamp, 'payload': payload}


def message(role, text, text_type='input_text'):
    return {'type': 'message', 'role': role, 'content': [{'type': text_type, 'text': text}]}


def task(text, timestamp='2026-01-01T00:00:00Z'):
    return [
        entry('event_msg', {'type': 'task_started'}, timestamp),
        entry('response_item', message('user', '# AGENTS.md instructions for /project'), timestamp),
        entry('response_item', message('user', text), timestamp),
        entry('response_item', message('assistant', 'Answer: ' + text, 'output_text'), timestamp),
        entry('event_msg', {'type': 'task_complete'}, timestamp),
    ]


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.sessions = self.home / 'sessions'
        self.sessions.mkdir()

    def rollout(self, name, rows):
        path = self.sessions / name
        path.write_bytes(b''.join((json.dumps(row) + '\n').encode('utf-8') for row in rows))
        return path

    def meta(self, thread=THREAD, timestamp='2026-01-01T00:00:00Z', base=None):
        payload = {'id': thread, 'timestamp': timestamp, 'source': 'cli', 'history_mode': 'paginated'}
        if base is not None:
            payload['history_base'] = base
        return entry('session_meta', payload, timestamp)

    def base_reference(self, rows, thread=THREAD):
        inherited = rows[0]['payload'].get('history_base', {}).get('end_ordinal_exclusive', 0)
        return {
            'thread_id': thread,
            'end_ordinal_exclusive': inherited + len(rows),
            'end_byte_offset': len(b''.join((json.dumps(row) + '\n').encode('utf-8') for row in rows)),
        }

    def projection(self, rows):
        boundary = self.base_reference(rows)
        with contextlib.closing(sqlite3.connect(self.home / 'thread_history_1.sqlite')) as connection:
            with connection:
                connection.execute(
                    'CREATE TABLE thread_history_projection_state '
                    '(thread_id TEXT, next_rollout_ordinal INTEGER, next_rollout_byte_offset INTEGER)'
                )
                connection.execute('INSERT INTO thread_history_projection_state VALUES (?, ?, ?)', (
                    THREAD, boundary['end_ordinal_exclusive'], boundary['end_byte_offset'],
                ))

    def test_paginated_same_thread_includes_completed_projected_turn(self):
        checkpoint = [self.meta()] + task('first')
        base_rows = checkpoint + task('second', '2026-01-02T00:00:00Z')
        base = self.rollout(f'rollout-base-{THREAD}.jsonl', base_rows)
        self.projection(base_rows)
        tail = self.rollout(f'rollout-tail-{THREAD}_{FORK}.jsonl', [
            self.meta(timestamp='2026-01-03T00:00:00Z', base=self.base_reference(checkpoint)),
        ] + task('third', '2026-01-03T00:00:00Z'))
        parser = exporter.SessionParser(tail)
        self.assertEqual(parser.source_paths, [base, tail])
        self.assertEqual(parser.get_turn_count(), 3)
        for text in ('first', 'second', 'third'):
            self.assertEqual(parser.to_markdown().count('Answer: ' + text), 1)
        parser.trim_to_live_context()
        self.assertEqual(parser.get_turn_count(), 3)
        parser.trim_to_last_n_turns(2)
        self.assertEqual(parser.get_turn_count(), 2)
        self.assertNotIn('Answer: first', parser.to_markdown())
        self.assertIn('Answer: second', parser.to_markdown())

    def test_fork_respects_checkpoint_even_with_later_projected_history(self):
        checkpoint = [self.meta()] + task('included')
        rows = checkpoint + task('excluded', '2026-01-02T00:00:00Z')
        self.rollout(f'rollout-base-{THREAD}.jsonl', rows)
        self.projection(rows)
        fork = self.rollout(f'rollout-fork-{FORK}.jsonl', [
            self.meta(FORK, '2026-01-03T00:00:00Z', self.base_reference(checkpoint)),
        ] + task('fork', '2026-01-03T00:00:00Z'))
        parser = exporter.SessionParser(fork)
        self.assertEqual(parser.get_turn_count(), 2)
        self.assertNotIn('Answer: excluded', parser.to_markdown())
        self.assertEqual(parser.metadata['id'], FORK)

    def test_chained_history_without_database(self):
        first_rows = [self.meta()] + task('first')
        self.rollout(f'rollout-first-{THREAD}.jsonl', first_rows)
        second_rows = [self.meta(timestamp='2026-01-02T00:00:00Z', base=self.base_reference(first_rows))]
        second_rows += task('second', '2026-01-02T00:00:00Z')
        self.rollout(f'rollout-second-{THREAD}_{FORK}.jsonl', second_rows)
        last = self.rollout(f'rollout-last-{THREAD}.jsonl', [
            self.meta(timestamp='2026-01-03T00:00:00Z', base=self.base_reference(second_rows)),
        ] + task('third', '2026-01-03T00:00:00Z'))
        parser = exporter.SessionParser(last)
        self.assertEqual(parser.get_turn_count(), 3)
        self.assertEqual(len(parser.source_paths), 3)

    def test_missing_base_fails_instead_of_exporting_partial_history(self):
        path = self.rollout(f'rollout-tail-{FORK}.jsonl', [
            self.meta(FORK, '2026-01-03T00:00:00Z', self.base_reference([self.meta()])),
        ] + task('partial'))
        with self.assertRaisesRegex(ValueError, 'Missing base history'):
            exporter.SessionParser(path)

    def test_fork_continuation_uses_inherited_ordinals_and_local_bytes(self):
        parent_rows = [self.meta()] + task('parent')
        parent = self.rollout(f'rollout-parent-{THREAD}.jsonl', parent_rows)
        fork_checkpoint = [self.meta(FORK, '2026-01-02T00:00:00Z', self.base_reference(parent_rows))]
        fork_checkpoint += task('fork first', '2026-01-02T00:00:00Z')
        fork_rows = fork_checkpoint + task('fork second', '2026-01-02T01:00:00Z')
        fork = self.rollout(f'rollout-fork-{FORK}.jsonl', fork_rows)
        boundary = self.base_reference(fork_rows, FORK)
        with contextlib.closing(sqlite3.connect(self.home / 'thread_history_1.sqlite')) as connection:
            with connection:
                connection.execute(
                    'CREATE TABLE thread_history_projection_state '
                    '(thread_id TEXT, next_rollout_ordinal INTEGER, next_rollout_byte_offset INTEGER)'
                )
                connection.execute('INSERT INTO thread_history_projection_state VALUES (?, ?, ?)', (
                    FORK, boundary['end_ordinal_exclusive'], boundary['end_byte_offset'],
                ))
        tail = self.rollout(f'rollout-continuation-{FORK}.jsonl', [
            self.meta(FORK, '2026-01-03T00:00:00Z', self.base_reference(fork_checkpoint, FORK)),
        ] + task('continued', '2026-01-03T00:00:00Z'))
        parser = exporter.SessionParser(tail)
        self.assertEqual(parser.source_paths, [parent, fork, tail])
        self.assertEqual(parser.get_turn_count(), 4)
        for text in ('parent', 'fork first', 'fork second', 'continued'):
            self.assertEqual(parser.to_markdown().count('Answer: ' + text), 1)

    def test_boundary_checks_both_cumulative_ordinal_and_byte_offset(self):
        rows = [self.meta()] + task('first')
        path = self.rollout(f'rollout-{THREAD}.jsonl', rows)
        boundary = self.base_reference(rows)
        for limit in ((boundary['end_ordinal_exclusive'] + 1, boundary['end_byte_offset']),
                      (boundary['end_ordinal_exclusive'], boundary['end_byte_offset'] - 1)):
            with self.assertRaisesRegex(ValueError, 'expected ordinal/bytes'):
                exporter._read_rollout_entries(path, limit)

    def test_parse_error_waits_for_acknowledgement(self):
        output = io.StringIO()
        with patch.object(exporter, 'SessionParser', side_effect=ValueError('missing history')):
            with patch('builtins.input', return_value='') as acknowledge, contextlib.redirect_stdout(output):
                exporter.convert_files([Path('missing.jsonl')])
        acknowledge.assert_called_once()
        self.assertIn('Press Enter', acknowledge.call_args.args[0])
        self.assertIn('missing history', output.getvalue())

    def test_browser_groups_pages_and_preserves_filename_aliases(self):
        rows = [self.meta()] + task('first')
        self.rollout(f'rollout-first-{THREAD}.jsonl', rows)
        tail = self.rollout(f'rollout-second-{THREAD}_{FORK}.jsonl', [
            self.meta(timestamp='2026-01-02T00:00:00Z', base=self.base_reference(rows)),
        ] + task('second', '2026-01-02T00:00:00Z'))
        with patch.object(exporter, 'SESSIONS_DIR', self.sessions):
            records = exporter.scan_sessions()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].path, tail)
        self.assertTrue({THREAD, FORK}.issubset(records[0].aliases))

    def test_legacy_turns_and_rollback(self):
        rows = [self.meta(), entry('response_item', message('user', '# AGENTS.md instructions for /project'))]
        rows += [entry('event_msg', {'type': 'user_message', 'message': 'first'}),
                 entry('response_item', message('user', 'first'))]
        rows += [entry('response_item', message('assistant', 'Answer: first', 'output_text'))]
        rows += task('second')
        rows += [entry('event_msg', {'type': 'thread_rolled_back', 'num_turns': 1})]
        parser = exporter.SessionParser(self.rollout(f'rollout-{THREAD}.jsonl', rows))
        self.assertEqual(parser.get_turn_count(), 2)
        parser.trim_to_live_context()
        self.assertEqual(parser.get_turn_count(), 1)
        self.assertNotIn('Answer: second', parser.to_markdown())

    def test_compaction_keeps_assistant_text_and_tool_pairs(self):
        replacement = [message('user', 'retained'), message('assistant', 'retained answer', 'output_text'),
                       {'type': 'function_call', 'name': 'exec_command', 'call_id': 'call', 'arguments': '{"cmd":"echo retained"}'},
                       {'type': 'function_call_output', 'call_id': 'call', 'output': 'retained output'}]
        rows = [self.meta()] + task('discarded')
        rows += [entry('compacted', {'replacement_history': replacement})] + task('new')
        parser = exporter.SessionParser(self.rollout(f'rollout-{THREAD}.jsonl', rows))
        parser.trim_to_live_context()
        markdown = parser.to_markdown()
        self.assertNotIn('Answer: discarded', markdown)
        self.assertIn('retained answer', markdown)
        self.assertIn('retained output', markdown)
        self.assertEqual(parser.get_turn_count(), 2)

    def test_full_export_preserves_system_and_tool_output(self):
        system = ('system instructions ' * 100).strip()
        output = '\n'.join(f'line {i}' for i in range(20))
        rows = [self.meta(), entry('response_item', message('developer', system))]
        rows += [entry('response_item', {'type': 'function_call', 'name': 'exec_command', 'call_id': 'call', 'arguments': '{}'}),
                 entry('response_item', {'type': 'function_call_output', 'call_id': 'call', 'output': output})]
        parser = exporter.SessionParser(self.rollout(f'rollout-{THREAD}.jsonl', rows))
        self.assertIn(system, parser.to_markdown())
        self.assertIn(output, parser.to_markdown())
        self.assertNotIn('line 0\n', parser.to_markdown(output_cap=8))
        for key in ('A', '7'):
            keys = ['DOWN'] * (len(exporter.SECTION_DEFS) + 1) + ['RIGHT', key, 'Q']
            with patch.object(exporter, 'read_key', side_effect=keys), contextlib.redirect_stdout(io.StringIO()):
                settings = exporter.interactive_filter([parser])
            self.assertTrue(all(settings[0].values()))
            self.assertEqual(settings[1:], (False, 0, 0, 0, 0, 0))


if __name__ == '__main__':
    unittest.main()
