#!/usr/bin/env python3
"""
Codex Session Manager & Markdown Converter  (v2)
-------------------------------------------------
An interactive tool to browse, filter, and convert OpenAI Codex
session logs (.jsonl) into readable Markdown documents.

Features:
  • Browse all Codex sessions with preview
  • Interactive per-section filter with line counts
  • Toggle sections on/off with arrow keys
  • Presets for common export scenarios
  • Clean-content mode to strip IDE scaffolding
"""

import os
import sys
import json
import glob
import re
import tty
import termios
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set

# ──────────────────────────────────────────────────────────────
# Terminal Styling
# ──────────────────────────────────────────────────────────────
class Style:
    HEADER  = '\033[95m'
    BLUE    = '\033[94m'
    CYAN    = '\033[96m'
    GREEN   = '\033[92m'
    YELLOW  = '\033[93m'
    RED     = '\033[91m'
    BOLD    = '\033[1m'
    UNDERLINE = '\033[4m'
    DIM     = '\033[2m'
    REVERSE = '\033[7m'
    RESET   = '\033[0m'
    BG_GRAY = '\033[48;5;236m'

    @staticmethod
    def title(msg): return f"{Style.BOLD}{Style.HEADER}{msg}{Style.RESET}"
    @staticmethod
    def info(msg): return f"{Style.BLUE}ℹ {msg}{Style.RESET}"
    @staticmethod
    def success(msg): return f"{Style.GREEN}✔ {msg}{Style.RESET}"
    @staticmethod
    def error(msg): return f"{Style.RED}✖ {msg}{Style.RESET}"
    @staticmethod
    def warn(msg): return f"{Style.YELLOW}⚠ {msg}{Style.RESET}"

# ──────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────
DEFAULT_CODEX_HOME = os.path.expanduser("~/.codex")
ENV_CODEX_HOME = os.environ.get("CODEX_HOME")
CODEX_PATH = Path(ENV_CODEX_HOME) if ENV_CODEX_HOME else Path(DEFAULT_CODEX_HOME)
SESSIONS_DIR = CODEX_PATH / "sessions"
SESSION_INDEX_PATH = CODEX_PATH / "session_index.jsonl"
USER_MESSAGE_BEGIN = "## My request for Codex:"
INTERACTIVE_SESSION_SOURCES = {"cli", "vscode", "atlas", "chatgpt"}

# ──────────────────────────────────────────────────────────────
# Section Definitions  (key, display_name, emoji, default_on)
#
# These are ALL the section types discovered across every Codex
# session file.  Each maps to one or more JSONL entry patterns.
# ──────────────────────────────────────────────────────────────
# Function name → sub-category mapping
# Built from scanning 223 session files (21 unique function names)
TERMINAL_FUNCS = {'exec_command', 'shell', 'shell_command', 'write_stdin', 'send_input'}
OTHER_TOOL_FUNCS = {'spawn_agent', 'wait_agent', 'close_agent', 'update_plan',
                    'request_user_input', 'view_image', 'list_mcp_resources',
                    'read_mcp_resource', 'list_mcp_resource_templates'}

def classify_tool(name: str) -> str:
    """Classify a function_call name into a sub-category key."""
    if name in TERMINAL_FUNCS:       return 'terminal_cmd'
    if name.startswith('mcp__'):     return 'mcp_tool'
    if name in OTHER_TOOL_FUNCS:     return 'other_tool'
    return 'terminal_cmd'

SECTION_DEFS = [
    ('user_message',       'User Messages',        '👤', True ),
    ('agent_message',      'Agent Messages',       '🤖', True ),
    ('agent_reasoning',    'Agent Reasoning',      '🧠', False),
    ('reasoning',          'Internal Reasoning',   '🔒', False),
    ('terminal_cmd',       'Terminal Commands',    '💻', True ),
    ('terminal_output',    'Terminal Outputs',     '📤', True ),
    ('mcp_tool',           'MCP Calls',            '🔌', False),
    ('mcp_tool_output',    'MCP Outputs',          '🔗', False),
    ('custom_tool_call',   'Patches',              '🔧', True ),
    ('custom_tool_output', 'Patch Outputs',        '🔨', True ),
    ('other_tool',         'Other Tools',          '🧩', False),
    ('other_tool_output',  'Other Tool Outputs',   '📎', False),
    ('web_search',         'Web Searches',         '🔍', False),
    ('token_count',        'Token & Rate Limits',  '📊', False),
    ('turn_context',       'Turn Context',         '🔄', False),
    ('task_event',         'Task Events',          '📌', False),
    ('system_message',     'System Messages',      '⚙️ ', False),
    ('git_snapshot',       'Git Snapshots',        '📸', False),
    ('session_event',      'Session Events',       '🔔', False),
    ('session_meta',       'Session Metadata',     '📝', True ),
]

ALL_SECTION_KEYS = {s[0] for s in SECTION_DEFS}

TOOL_OUTPUT_MAP = {
    'terminal_cmd':  'terminal_output',
    'mcp_tool':      'mcp_tool_output',
    'other_tool':    'other_tool_output',
}

# ──────────────────────────────────────────────────────────────
# Filter Presets  (name, enabled_keys | None=defaults, clean)
# ──────────────────────────────────────────────────────────────
FILTER_PRESETS = [
    ('Defaults',        None, False),
    ('Chat Only',       {'user_message', 'agent_message', 'session_meta'}, False),
    ('Chat (Clean)',    {'user_message', 'agent_message', 'session_meta'}, True),
    ('Chat + Terminal', {'user_message', 'agent_message', 'terminal_cmd', 'terminal_output',
                         'custom_tool_call', 'custom_tool_output', 'session_meta'}, False),
    ('Terminal Only',   {'terminal_cmd', 'terminal_output'}, False),
    ('Outputs Only',    {'terminal_output', 'custom_tool_output', 'mcp_tool_output',
                         'other_tool_output'}, False),
    ('Full Export',     ALL_SECTION_KEYS, False),
]

# ──────────────────────────────────────────────────────────────
# Parsing Utilities  (kept from v1)
# ──────────────────────────────────────────────────────────────
def clean_filename(text: str) -> str:
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'[*_`]', '', text)
    text = re.sub(r'[^\w\s-]', '', text).strip().lower()
    text = re.sub(r'[-\s]+', '-', text)
    return text[:60] if text else "untitled-session"

def trim_chat_content(content: str) -> str:
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    content = re.sub(
        r'(?is)<([A-Za-z0-9_-]*context[A-Za-z0-9_-]*)>\s*.*?</\1>\s*', '\n', content,
    )
    content = re.sub(
        r'(?is)<subagent_notification>\s*.*?</subagent_notification>\s*', '\n', content,
    )

    dropped_block_prefixes = (
        "## active file:", "## active selection of the file:",
        "## open tabs:", "## files mentioned by the user:",
    )
    dropped_line_prefixes = (
        "# context from my ide setup:", "## my request for codex:", "## my request:",
    )

    cleaned_lines: List[str] = []
    lines = content.split('\n')
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        lowered = stripped.lower()
        if any(lowered.startswith(p) for p in dropped_line_prefixes):
            index += 1; continue
        if any(lowered.startswith(p) for p in dropped_block_prefixes):
            active_sel = lowered.startswith("## active selection of the file:")
            index += 1
            while index < len(lines):
                nl = lines[index].strip().lower()
                if any(nl.startswith(p) for p in dropped_line_prefixes):
                    break
                if not active_sel and re.match(r'#{1,6}\s', lines[index].strip()):
                    break
                index += 1
            continue
        cleaned_lines.append(lines[index])
        index += 1
    cleaned = "\n".join(cleaned_lines)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()

def normalize_title_candidate(text: str) -> str:
    text = re.sub(r'\s+', ' ', text.strip())
    return text[:80] + ("..." if len(text) > 80 else "")

def strip_user_message_prefix(text: str) -> str:
    idx = text.find(USER_MESSAGE_BEGIN)
    if idx != -1:
        return text[idx + len(USER_MESSAGE_BEGIN):].strip()
    return text.strip()

def extract_first_user_line(text: str) -> str:
    message = strip_user_message_prefix(text)
    if not message:
        return ""
    for line in message.splitlines():
        candidate = line.strip()
        if candidate:
            return normalize_title_candidate(candidate)
    return ""

def is_title_noise(line: str) -> bool:
    lowered = line.strip().lower()
    if not lowered: return True
    if lowered.startswith('<') and lowered.endswith('>'): return True
    if "system role:" in lowered: return True
    if re.match(r'^\d+\s+\d{4}-\d{2}-\d{2}\s+', lowered): return True
    if re.match(r'^[-=]{3,}$', lowered): return True
    noise_prefixes = (
        "hi chatgpt", "hi claude", "hello chatgpt", "hello claude",
        "i've hit my", "i have hit my", "here is the exact context",
        "here is the current context", "perfect.", "okay,", "ok,",
        "❯", "●", "searched for ", "read ", "listed ", "brought in ",
        "update(", "write(", "bash(", "error:", "caused by:",
    )
    if lowered.startswith(noise_prefixes): return True
    noise_headings = (
        "system context", "server topology", "the story so far",
        "the roadblock", "your mission & execution steps",
        "system context & the new strategy", "diagnostics & fixes",
        "speedtest results", "live production deployment report",
        "codebase cleanup & compatibility report",
    )
    if lowered.startswith('#'):
        heading = lowered.lstrip('#').strip(': ').strip()
        return any(heading.startswith(p) for p in noise_headings)
    return False

def extract_title_from_content(content: str) -> str:
    title = extract_first_user_line(content)
    return title or "Empty or Technical Session"

def format_size(num_bytes: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(num_bytes)
    unit = units[0]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            break
        size /= 1024
    if unit == "B":
        return f"{int(size)}{unit}"
    return f"{size:.1f}{unit}"

# ──────────────────────────────────────────────────────────────
# Thread / Session Index
# ──────────────────────────────────────────────────────────────
def load_thread_names() -> Dict[str, str]:
    names: Dict[str, str] = {}
    if not SESSION_INDEX_PATH.exists():
        return names
    try:
        with open(SESSION_INDEX_PATH, 'r', encoding='utf-8', errors='ignore') as fp:
            for line in fp:
                line = line.strip()
                if not line: continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tid = entry.get('id')
                tname = entry.get('thread_name')
                if isinstance(tid, str) and isinstance(tname, str) and tname.strip():
                    names[tid] = tname.strip()
    except Exception:
        return {}
    return names

THREAD_NAMES = load_thread_names()

# ──────────────────────────────────────────────────────────────
# Session Scanning  (fast head-scan for preview list)
# ──────────────────────────────────────────────────────────────
def read_session_summary(filepath: Path, head_limit=10, user_scan_limit=200):
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as fp:
            session_meta = None
            first_user_message = None
            saw_user_event = False
            lines_scanned = 0
            while lines_scanned < head_limit or (
                session_meta is not None and not saw_user_event
                and lines_scanned < head_limit + user_scan_limit
            ):
                line = fp.readline()
                if not line: break
                trimmed = line.strip()
                if not trimmed: continue
                lines_scanned += 1
                try:
                    entry = json.loads(trimmed)
                except json.JSONDecodeError:
                    continue
                if entry.get('type') == 'session_meta' and session_meta is None:
                    session_meta = entry.get('payload', {})
                    continue
                if (entry.get('type') == 'event_msg'
                        and entry.get('payload', {}).get('type') == 'user_message'):
                    saw_user_event = True
                    if first_user_message is None:
                        message = strip_user_message_prefix(entry['payload'].get('message', ''))
                        if message:
                            first_user_message = message
                    if session_meta is not None:
                        break
            return session_meta, first_user_message, saw_user_event
    except Exception:
        return None, None, False

def is_interactive_session_meta(meta: Optional[Dict]) -> bool:
    if not meta: return False
    source = meta.get('source')
    if not isinstance(source, str): return False
    return source.lower() in INTERACTIVE_SESSION_SOURCES

def get_session_preview_title(filepath: Path) -> str:
    meta, first_user_message, saw_user_event = read_session_summary(filepath)
    if not is_interactive_session_meta(meta) or not saw_user_event:
        return "Untitled / System Log"
    thread_id = meta.get('id') if isinstance(meta.get('id'), str) else None
    if thread_id and thread_id in THREAD_NAMES:
        return normalize_title_candidate(THREAD_NAMES[thread_id])
    if not first_user_message:
        return "[Image]"
    title = extract_first_user_line(first_user_message)
    return title or "Untitled / System Log"

# ──────────────────────────────────────────────────────────────
# Session Parser  (full parse of a .jsonl file)
# ──────────────────────────────────────────────────────────────
class SessionParser:
    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.data: List[Dict] = []
        self.metadata: Dict = {}
        self.title = "Untitled Session"
        self.start_time = None
        self._load()

    # --- loading ---
    def _load(self):
        if not self.filepath.exists():
            return
        with open(self.filepath, 'r', encoding='utf-8', errors='replace') as f:
            for line_num, line in enumerate(f):
                line = line.strip()
                if not line: continue
                try:
                    entry = json.loads(line)
                    self._process_entry(entry, line_num)
                except json.JSONDecodeError:
                    continue

        # Derive title
        thread_id = self.metadata.get('id')
        title_found = False
        if isinstance(thread_id, str) and thread_id in THREAD_NAMES:
            self.title = normalize_title_candidate(THREAD_NAMES[thread_id])
            title_found = True
        else:
            for item in self.data:
                if item['type'] == 'user_message':
                    t = extract_title_from_content(item['content'])
                    if t != "Empty or Technical Session":
                        self.title = t
                        title_found = True
                        break
        if not title_found and self.metadata.get('id'):
            self.title = f"System Session {self.metadata['id'][:8]}"

    # --- entry processing (ALL 23 discovered patterns) ---
    def _process_entry(self, entry: Dict, line_num: int):
        ts = entry.get('timestamp')
        if line_num == 0 and not self.start_time:
            self.start_time = ts

        etype = entry.get('type', '')
        payload = entry.get('payload', {})
        if not isinstance(payload, dict):
            payload = {}
        ptype = payload.get('type', '')
        role  = payload.get('role', '')

        # 1 ─ Session metadata
        if etype == 'session_meta':
            self.metadata = payload
            return

        # 2 ─ User message  (event_msg wrapper)
        if etype == 'event_msg' and ptype == 'user_message':
            msg = payload.get('message', '')
            if msg:
                self.data.append({'type': 'user_message', 'timestamp': ts, 'content': msg})
            return

        # 3 ─ Agent reasoning  (event_msg wrapper)
        if etype == 'event_msg' and ptype == 'agent_reasoning':
            text = payload.get('text', '')
            if text:
                self.data.append({'type': 'agent_reasoning', 'timestamp': ts, 'content': text})
            return

        # 4 ─ Agent message  (event_msg wrapper)
        if etype == 'event_msg' and ptype == 'agent_message':
            msg = payload.get('message', '')
            if msg:
                self.data.append({'type': 'agent_message', 'timestamp': ts, 'content': msg})
            return

        # 5 ─ Token count & rate limits
        if etype == 'event_msg' and ptype == 'token_count':
            rl = payload.get('rate_limits', {})
            parts = []
            if isinstance(rl, dict):
                pri = rl.get('primary', {})
                sec = rl.get('secondary', {})
                if pri:
                    parts.append(f"primary {pri.get('used_percent', '?')}%")
                if sec:
                    parts.append(f"secondary {sec.get('used_percent', '?')}%")
            info = payload.get('info')
            if info and isinstance(info, dict):
                parts.append(f"in={info.get('input_tokens', '?')}, out={info.get('output_tokens', '?')}")
            content = ', '.join(parts) if parts else 'token count event'
            self.data.append({
                'type': 'token_count', 'timestamp': ts,
                'content': content,
            })
            return

        # 6 ─ Task lifecycle
        if etype == 'event_msg' and ptype in ('task_started', 'task_complete'):
            detail = ''
            if ptype == 'task_started':
                model = payload.get('collaboration_mode_kind', '')
                ctx = payload.get('model_context_window', '')
                if model or ctx:
                    detail = f" ({model}, ctx={ctx:,})" if ctx else f" ({model})"
            self.data.append({
                'type': 'task_event', 'timestamp': ts,
                'event': ptype,
                'content': ptype.replace('_', ' ').title() + detail,
            })
            return

        # 7 ─ Context compacted / thread rolled back / turn aborted / item completed
        if etype == 'event_msg' and ptype in ('context_compacted', 'thread_rolled_back', 'turn_aborted', 'item_completed'):
            detail = ''
            if ptype == 'thread_rolled_back':
                detail = f" ({payload.get('num_turns', '?')} turns)"
            elif ptype == 'turn_aborted':
                detail = f" (reason: {payload.get('reason', '?')})"
            elif ptype == 'item_completed':
                idata = payload.get('item', {})
                detail = f" ({idata.get('type', '?')})"
            self.data.append({
                'type': 'session_event', 'timestamp': ts,
                'event': ptype,
                'content': ptype.replace('_', ' ').title() + detail,
            })
            return

        # 8 ─ Compacted top-level entry
        if etype == 'compacted':
            self.data.append({
                'type': 'session_event', 'timestamp': ts,
                'event': 'context_compacted',
                'content': 'Context Compacted',
            })
            return

        # 9 ─ Turn context
        if etype == 'turn_context':
            model  = payload.get('model', '?')
            effort = payload.get('effort', '?')
            cwd    = payload.get('cwd', '?')
            self.data.append({
                'type': 'turn_context', 'timestamp': ts,
                'content': f"model={model}, effort={effort}, cwd={cwd}",
            })
            return

        # 10 ─ response_item entries
        if etype == 'response_item':

            # 10a ─ Internal reasoning  (usually encrypted, summary may be empty)
            if ptype == 'reasoning':
                parts = payload.get('summary', [])
                summary = '\n'.join(p.get('text', '') for p in parts if p.get('text', ''))
                encrypted = bool(payload.get('encrypted_content'))
                self.data.append({
                    'type': 'reasoning', 'timestamp': ts,
                    'content': summary,
                    'encrypted': encrypted,
                })
                return

            # 10b ─ Function call  (classified by function name)
            if ptype == 'function_call':
                fname = payload.get('name', '')
                cat = classify_tool(fname)
                call_id = payload.get('call_id')
                self.data.append({
                    'type': cat, 'timestamp': ts,
                    'name': fname,
                    'arguments': payload.get('arguments', ''),
                    'call_id': call_id,
                    '_tool_cat': cat,  # save category for output matching
                })
                # Remember call_id → category for output pairing
                if not hasattr(self, '_call_id_map'):
                    self._call_id_map = {}
                if call_id:
                    self._call_id_map[call_id] = cat
                return

            # 10c ─ Function call output  (matched to its call's category)
            if ptype == 'function_call_output':
                call_id = payload.get('call_id')
                if not hasattr(self, '_call_id_map'):
                    self._call_id_map = {}
                parent_cat = self._call_id_map.get(call_id, 'terminal_cmd')
                out_cat = TOOL_OUTPUT_MAP.get(parent_cat, 'terminal_output')
                self.data.append({
                    'type': out_cat, 'timestamp': ts,
                    'output': payload.get('output', ''),
                    'call_id': call_id,
                })
                return

            # 10d ─ Custom tool call  (apply_patch, etc.)
            if ptype == 'custom_tool_call':
                self.data.append({
                    'type': 'custom_tool_call', 'timestamp': ts,
                    'name': payload.get('name', 'custom_tool'),
                    'content': payload.get('input', ''),
                    'call_id': payload.get('call_id'),
                })
                return

            # 10e ─ Custom tool output
            if ptype == 'custom_tool_call_output':
                self.data.append({
                    'type': 'custom_tool_output', 'timestamp': ts,
                    'content': payload.get('output', ''),
                    'call_id': payload.get('call_id'),
                })
                return

            # 10f ─ Web search
            if ptype == 'web_search_call':
                self.data.append({
                    'type': 'web_search', 'timestamp': ts,
                    'content': 'Web search executed',
                })
                return

            # 10g ─ Ghost snapshot (git)
            if ptype == 'ghost_snapshot':
                gc = payload.get('ghost_commit', {})
                cid = gc.get('id', '?')[:12]
                self.data.append({
                    'type': 'git_snapshot', 'timestamp': ts,
                    'content': f"commit {cid}",
                    'commit': gc,
                })
                return

            # 10h ─ Developer / system messages
            if ptype == 'message' and role == 'developer':
                parts = payload.get('content', [])
                if isinstance(parts, list):
                    text = '\n'.join(p.get('text', '') for p in parts if p.get('type') == 'input_text')
                else:
                    text = str(parts)
                if text:
                    self.data.append({'type': 'system_message', 'timestamp': ts, 'content': text})
                return

            # 10i ─ response_item message (assistant / user raw) — skip, covered by event_msg
            if ptype == 'message' and role in ('assistant', 'user'):
                return

        # Everything else is silently ignored.

    # --- line counting per section ---
    def count_lines_by_section(self) -> Dict[str, int]:
        """Estimate markdown output lines for each section type."""
        counts: Dict[str, int] = {}
        # Tool-call sub-categories (ones that have 'arguments')
        tool_call_types = {'terminal_cmd', 'mcp_tool', 'other_tool'}
        tool_output_types = {'terminal_output', 'mcp_tool_output', 'other_tool_output'}

        for item in self.data:
            itype = item['type']
            content = item.get('content', '')

            if itype == 'user_message':
                lines = content.count('\n') + 4
            elif itype == 'agent_message':
                lines = content.count('\n') + 4
            elif itype == 'agent_reasoning':
                lines = 2
            elif itype == 'reasoning':
                lines = 2 if not content else content.count('\n') + 3
            elif itype in tool_call_types:
                args = item.get('arguments', '')
                try:
                    args = json.dumps(json.loads(args), indent=2)
                except Exception:
                    pass
                lines = args.count('\n') + 5
            elif itype in tool_output_types:
                out = item.get('output', '')
                lines = out.count('\n') + 5 if out.strip() else 0
            elif itype == 'custom_tool_call':
                lines = content.count('\n') + 5
            elif itype == 'custom_tool_output':
                lines = content.count('\n') + 5 if content.strip() else 0
            elif itype in ('web_search', 'token_count', 'turn_context',
                           'task_event', 'git_snapshot', 'session_event'):
                lines = 2
            elif itype == 'system_message':
                lines = content.count('\n') + 5
            else:
                lines = 1

            counts[itype] = counts.get(itype, 0) + lines

        # Session metadata block
        counts['session_meta'] = 5 if self.metadata else 0

        return counts

    # --- markdown rendering with filter ---
    def to_markdown(self, section_filter: Optional[Dict[str, bool]] = None,
                    clean_content: bool = False, output_cap: int = 0) -> str:
        if section_filter is None:
            section_filter = {s[0]: True for s in SECTION_DEFS}

        def _cap_text(text: str) -> str:
            """Truncate output text to output_cap lines (last N lines)."""
            if output_cap <= 0 or not text:
                return text
            lines = text.split('\n')
            if len(lines) <= output_cap:
                return text
            kept = lines[-output_cap:]
            return f'... ({len(lines) - output_cap} lines trimmed) ...\n' + '\n'.join(kept)

        md: List[str] = []
        md.append(f"# {self.title}\n")
        last_rendered_message = None

        # Session metadata
        if section_filter.get('session_meta', False) and self.metadata:
            md.append("```yaml")
            md.append(f"Source: {self.filepath.name}")
            if self.start_time:
                md.append(f"Date: {self.start_time}")
            if self.metadata.get('cwd'):
                md.append(f"CWD: {self.metadata['cwd']}")
            md.append("```\n")

        for item in self.data:
            itype = item['type']
            if not section_filter.get(itype, False):
                continue

            content = item.get('content', '')

            if itype == 'user_message':
                if clean_content:
                    content = trim_chat_content(content)
                    if not content: continue
                    mk = ('user', content)
                    if mk == last_rendered_message: continue
                    last_rendered_message = mk
                md.append(f"## 👤 User\n\n{content}\n")

            elif itype == 'agent_message':
                if clean_content:
                    content = trim_chat_content(content)
                    if not content: continue
                    mk = ('agent', content)
                    if mk == last_rendered_message: continue
                    last_rendered_message = mk
                md.append(f"## 🤖 Agent\n\n{content}\n")

            elif itype == 'agent_reasoning':
                clean = content.replace('**', '').strip()
                md.append(f"> 🧠 **Reasoning:** {clean}\n")

            elif itype == 'reasoning':
                if content:
                    md.append(f"> 🔒 **Internal Reasoning:**\n> {content}\n")
                else:
                    md.append("> 🔒 *Internal reasoning (encrypted)*\n")

            elif itype in ('terminal_cmd', 'mcp_tool', 'other_tool'):
                name = item.get('name', 'tool')
                args = item.get('arguments', '')
                try:
                    args = json.dumps(json.loads(args), indent=2)
                    lang = "json"
                except Exception:
                    lang = "text"
                emoji_map = {'terminal_cmd': '💻', 'mcp_tool': '🔌', 'other_tool': '🧩'}
                em = emoji_map.get(itype, '🛠️')
                md.append(f"### {em} Tool: `{name}`\n\n```{lang}\n{args}\n```\n")

            elif itype in ('terminal_output', 'mcp_tool_output', 'other_tool_output'):
                out = _cap_text(item.get('output', ''))
                if out.strip():
                    md.append(f"**Output:**\n\n```text\n{out}\n```\n")

            elif itype == 'custom_tool_call':
                name = item.get('name', 'custom_tool')
                md.append(f"### 🔧 Custom Tool: `{name}`\n\n```text\n{content}\n```\n")

            elif itype == 'custom_tool_output':
                out = _cap_text(content)
                if out.strip():
                    md.append(f"**Custom Tool Output:**\n\n```text\n{out}\n```\n")

            elif itype == 'web_search':
                md.append(f"> 🔍 **Web Search**\n")

            elif itype == 'token_count':
                md.append(f"> 📊 **Tokens:** {content}\n")

            elif itype == 'turn_context':
                md.append(f"> 🔄 **Turn:** {content}\n")

            elif itype == 'task_event':
                md.append(f"> 📌 **{content}**\n")

            elif itype == 'system_message':
                # Truncate very long system prompts
                display = content[:800]
                if len(content) > 800:
                    display += f"\n... ({len(content) - 800} chars truncated)"
                md.append(f"### ⚙️ System Message\n\n```text\n{display}\n```\n")

            elif itype == 'git_snapshot':
                md.append(f"> 📸 **Git Snapshot:** {content}\n")

            elif itype == 'session_event':
                md.append(f"> 🔔 **{content}**\n")

        return "\n".join(md)

# ──────────────────────────────────────────────────────────────
# Keyboard Input (raw terminal, single keypress)
# ──────────────────────────────────────────────────────────────
def read_key() -> str:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == '\x1b':
            ch2 = sys.stdin.read(1)
            if ch2 == '[':
                ch3 = sys.stdin.read(1)
                return {'A': 'UP', 'B': 'DOWN', 'C': 'RIGHT', 'D': 'LEFT'}.get(ch3, '')
            return 'ESC'
        if ch in ('\r', '\n'):
            return 'ENTER'
        if ch == ' ':
            return 'SPACE'
        if ch == '\x03':
            raise KeyboardInterrupt
        return ch.upper()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

# ──────────────────────────────────────────────────────────────
# Interactive Section Filter
# ──────────────────────────────────────────────────────────────

# Output sections whose blocks can be capped
OUTPUT_SECTIONS = {'terminal_output', 'mcp_tool_output', 'other_tool_output',
                   'custom_tool_output'}
# Cap steps for ◀ ▶ control  (0 = no cap, show all)
CAP_STEPS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20, 30, 50, 100, 200, 500]

def interactive_filter(parsers: List[SessionParser]) -> Tuple[Dict[str, bool], bool, int]:
    """
    Full-screen interactive filter.
    Returns (section_filter, clean_content, output_cap).
    """
    # ── Pre-compute data ──
    agg_lines: Dict[str, int] = {}
    for parser in parsers:
        for key, count in parser.count_lines_by_section().items():
            agg_lines[key] = agg_lines.get(key, 0) + count

    # Per-output-block content line counts (for cap estimation)
    output_blocks: Dict[str, List[int]] = {}
    for parser in parsers:
        for item in parser.data:
            itype = item['type']
            if itype in OUTPUT_SECTIONS:
                text = item.get('output', item.get('content', ''))
                if text.strip():
                    output_blocks.setdefault(itype, []).append(text.count('\n') + 1)

    # Lines affected by clean chat (user + agent messages)
    chat_lines = agg_lines.get('user_message', 0) + agg_lines.get('agent_message', 0)

    def capped_lines_for(section_key: str, cap: int) -> int:
        """Compute output section lines with a cap applied."""
        if cap == 0:
            return agg_lines.get(section_key, 0)
        blocks = output_blocks.get(section_key, [])
        return sum(min(cl, cap) + 4 for cl in blocks)

    def effective_lines(section_key: str) -> int:
        """Line count for a section, respecting current output_cap."""
        if section_key in OUTPUT_SECTIONS and output_cap > 0:
            return capped_lines_for(section_key, output_cap)
        return agg_lines.get(section_key, 0)

    # ── State ──
    fstate: Dict[str, bool] = {s[0]: s[3] for s in SECTION_DEFS}
    clean_content = False
    output_cap = 8  # default: 8 lines per output block
    cap_idx = CAP_STEPS.index(8)

    cursor = 0
    # Rows: sections + clean_chat + output_cap
    ROW_CLEAN = len(SECTION_DEFS)
    ROW_CAP   = len(SECTION_DEFS) + 1
    num_items = len(SECTION_DEFS) + 2

    while True:
        # ── Compute totals ──
        total_lines = sum(effective_lines(s[0]) for s in SECTION_DEFS)
        selected_lines = sum(
            effective_lines(s[0]) for s in SECTION_DEFS if fstate.get(s[0], False)
        )
        pct = (selected_lines / total_lines * 100) if total_lines > 0 else 0

        # ── Render ──
        os.system('clear')
        sessions_label = f"{len(parsers)} session{'s' if len(parsers) > 1 else ''}"
        print(f"\n  {Style.BOLD}{Style.HEADER}SECTION FILTER{Style.RESET}  {Style.DIM}({sessions_label}){Style.RESET}")
        print(f"  {Style.DIM}{'━' * 62}{Style.RESET}\n")

        for i, (key, name, emoji, _default) in enumerate(SECTION_DEFS):
            is_cursor = (i == cursor)
            is_on     = fstate.get(key, False)
            lines     = effective_lines(key)
            full      = agg_lines.get(key, 0)

            arrow = f'{Style.BOLD}{Style.YELLOW}▸{Style.RESET}' if is_cursor else ' '

            if is_on:
                toggle = f'{Style.GREEN}██{Style.RESET}'
            else:
                toggle = f'{Style.DIM}░░{Style.RESET}'

            if is_cursor and is_on:
                nstyle = f'{Style.BOLD}{Style.GREEN}'
            elif is_cursor and not is_on:
                nstyle = f'{Style.BOLD}{Style.RED}'
            elif is_on:
                nstyle = ''
            else:
                nstyle = Style.DIM

            # Line count — show capped vs full if cap is active
            if key in OUTPUT_SECTIONS and output_cap > 0 and full != lines:
                if lines == 0:
                    count_str = f'{Style.DIM}     0{Style.RESET}'
                elif is_on:
                    count_str = f'{Style.CYAN}{lines:>6,}{Style.RESET}{Style.DIM}↓{full:,}{Style.RESET}'
                else:
                    count_str = f'{Style.DIM}{lines:>6,}↓{full:,}{Style.RESET}'
            else:
                if lines == 0:
                    count_str = f'{Style.DIM}     0{Style.RESET}'
                elif is_on:
                    count_str = f'{Style.CYAN}{lines:>6,}{Style.RESET}'
                else:
                    count_str = f'{Style.DIM}{lines:>6,}{Style.RESET}'

            visible_name = f'{emoji} {name}'
            pad_len = max(1, 44 - len(visible_name))
            dots = f'{Style.DIM}{"·" * pad_len}{Style.RESET}'

            print(f'  {arrow} {toggle} {nstyle}{visible_name}{Style.RESET} {dots} {count_str}')

        # ── Settings rows ──
        print(f'  {Style.DIM}{"─" * 62}{Style.RESET}')

        # Clean Chat
        cc_on    = clean_content
        cc_cur   = (cursor == ROW_CLEAN)
        cc_arrow = f'{Style.BOLD}{Style.YELLOW}▸{Style.RESET}' if cc_cur else ' '
        cc_tog   = f'{Style.GREEN}██{Style.RESET}' if cc_on else f'{Style.DIM}░░{Style.RESET}'
        cc_st    = f'{Style.BOLD}' if cc_cur else Style.DIM
        cc_val   = f'{Style.GREEN}ON {Style.RESET}' if cc_on else f'{Style.DIM}OFF{Style.RESET}'
        print(f'  {cc_arrow} {cc_tog} {cc_st}✂️  Clean Chat{Style.RESET}'
              f' {Style.DIM}(strips IDE context from 👤🤖){Style.RESET}'
              f'  {Style.DIM}{chat_lines:,}L{Style.RESET}  {cc_val}')

        # Output Cap
        cap_cur   = (cursor == ROW_CAP)
        cap_arrow = f'{Style.BOLD}{Style.YELLOW}▸{Style.RESET}' if cap_cur else ' '
        cap_st    = f'{Style.BOLD}' if cap_cur else Style.DIM
        if output_cap == 0:
            cap_label = f'{Style.DIM}ALL{Style.RESET}'
        else:
            cap_label = f'{Style.YELLOW}{output_cap}{Style.RESET}'
        hint = f' {Style.DIM}◀▶{Style.RESET}' if cap_cur else ''
        print(f'  {cap_arrow}    {cap_st}📏 Output Cap{Style.RESET}'
              f' {Style.DIM}(max lines per block){Style.RESET}'
              f'  {cap_label}{hint}')

        # ── Footer ──
        print(f'\n  {Style.DIM}{"━" * 62}{Style.RESET}')
        bar_w = 30
        filled = int(bar_w * pct / 100)
        bar = f'{Style.GREEN}{"█" * filled}{Style.DIM}{"░" * (bar_w - filled)}{Style.RESET}'
        sel_c = Style.GREEN if pct > 0 else Style.RED
        print(f'  {bar}  {sel_c}{Style.BOLD}{selected_lines:,}{Style.RESET}'
              f'{Style.DIM}/{Style.RESET}{total_lines:,}  {Style.DIM}({pct:.0f}%){Style.RESET}')
        print(f'\n  {Style.DIM}↑↓ move  ⏎ toggle  ◀▶ cap  Q export  A all  N none  D defaults  1-7 presets{Style.RESET}')

        # ── Read key ──
        key = read_key()

        if key == 'UP':
            cursor = (cursor - 1) % num_items
        elif key == 'DOWN':
            cursor = (cursor + 1) % num_items
        elif key in ('ENTER', 'SPACE'):
            if cursor < len(SECTION_DEFS):
                skey = SECTION_DEFS[cursor][0]
                fstate[skey] = not fstate[skey]
            elif cursor == ROW_CLEAN:
                clean_content = not clean_content
            # ROW_CAP: enter does nothing — use ◀▶
        elif key == 'LEFT':
            cap_idx = max(0, cap_idx - 1)
            output_cap = CAP_STEPS[cap_idx]
        elif key == 'RIGHT':
            cap_idx = min(len(CAP_STEPS) - 1, cap_idx + 1)
            output_cap = CAP_STEPS[cap_idx]
        elif key == 'A':
            for s in SECTION_DEFS:
                fstate[s[0]] = True
        elif key == 'N':
            for s in SECTION_DEFS:
                fstate[s[0]] = False
        elif key == 'I':
            for s in SECTION_DEFS:
                fstate[s[0]] = not fstate[s[0]]
        elif key == 'D':
            for s in SECTION_DEFS:
                fstate[s[0]] = s[3]
            clean_content = False
            output_cap = 8
            cap_idx = CAP_STEPS.index(8)
        elif key == 'Q':
            break
        elif key == 'ESC':
            break
        elif key.isdigit():
            pi = int(key) - 1
            if 0 <= pi < len(FILTER_PRESETS):
                _pname, pkeys, pclean = FILTER_PRESETS[pi]
                if pkeys is None:
                    for s in SECTION_DEFS:
                        fstate[s[0]] = s[3]
                else:
                    for s in SECTION_DEFS:
                        fstate[s[0]] = s[0] in pkeys
                clean_content = pclean

    return fstate, clean_content, output_cap

# ──────────────────────────────────────────────────────────────
# Session List & Main Loop
# ──────────────────────────────────────────────────────────────
def get_all_sessions() -> List[Path]:
    if not SESSIONS_DIR.exists():
        return []
    pattern = str(SESSIONS_DIR / "**" / "rollout-*.jsonl")
    files = glob.glob(pattern, recursive=True)
    session_files = []
    for f in files:
        path = Path(f)
        meta, _, saw_user_event = read_session_summary(path)
        if is_interactive_session_meta(meta) and saw_user_event:
            session_files.append(path)
    return sorted(session_files, key=os.path.getmtime, reverse=True)

def print_menu_header():
    os.system('cls' if os.name == 'nt' else 'clear')
    print(f"\n{Style.BOLD}CODEX SESSION MANAGER{Style.RESET}  {Style.DIM}v2{Style.RESET}")
    print(f"{Style.DIM}Directory: {SESSIONS_DIR}{Style.RESET}")
    print(f"{Style.DIM}Output:    {Path(__file__).parent.resolve()}{Style.RESET}\n")

def format_relative_time(mtime: float) -> str:
    now = datetime.now().timestamp()
    diff = int(now - mtime)
    if diff < 0: diff = 0
    if diff < 60:
        return "(just now)"
    mins = diff // 60
    hours = mins // 60
    days = hours // 24
    if days > 0:
        return f"({days}d {hours % 24}h ago)"
    elif hours > 0:
        return f"({hours}h {mins % 60}m ago)"
    else:
        return f"({mins}m ago)"

def list_sessions_table(files: List[Path]):
    print(f"{Style.BOLD}{'ID':<4} {'DATE':<32} {'TITLE':<52} {'SIZE'}{Style.RESET}")
    print(f"{Style.DIM}{'-'*100}{Style.RESET}")

    for idx, f in enumerate(files):
        try:
            stat = f.stat()
            dt_str = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            rel_str = format_relative_time(stat.st_mtime)
            dt_full = f"{dt_str} {Style.DIM}{rel_str}{Style.RESET}"

            size_label = format_size(stat.st_size)
            title = get_session_preview_title(f)

            tag = ""
            row_color = Style.RESET
            if idx == 0:
                tag = " [LATEST]"
                row_color = Style.GREEN
            elif idx < 3:
                tag = " [NEW]"
                row_color = Style.BLUE
            elif idx % 2 == 0:
                row_color = Style.CYAN

            display_title = f"{title}{tag}"
            if len(display_title) > 50:
                display_title = display_title[:47] + "..."

            # We use ansi escape codes in dt_full, so padding with spaces in f-string 
            # won't work correctly with fixed-width natively.
            # So we pad the raw string length then apply colors.
            raw_dt_len = len(f"{dt_str} {rel_str}")
            padding = " " * max(0, 32 - raw_dt_len)
            
            print(f"{row_color}{idx+1:<4} {dt_full}{padding} {display_title:<52} {size_label}{Style.RESET}")
        except Exception:
            continue
    print(f"{Style.DIM}{'-'*100}{Style.RESET}")

def copy_to_clipboard(text: str) -> bool:
    import subprocess
    try:
        if sys.platform == 'win32':
            subprocess.run(['clip'], input=text.encode('utf-16le'), check=True)
        elif sys.platform == 'darwin':
            subprocess.run(['pbcopy'], input=text.encode('utf-8'), check=True)
        else:
            try:
                subprocess.run(['xclip', '-selection', 'clipboard'], input=text.encode('utf-8'), check=True, stderr=subprocess.DEVNULL)
            except FileNotFoundError:
                subprocess.run(['xsel', '--clipboard', '--input'], input=text.encode('utf-8'), check=True, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False

def process_conversion(indices_str: str, files: List[Path]):
    if not indices_str.strip():
        return

    try:
        raw_parts = [x.strip() for x in indices_str.split(',')]
        indices = [int(x) - 1 for x in raw_parts if x.isdigit()]
    except ValueError:
        print(Style.error("Invalid input format."))
        return

    valid_files: List[Path] = []
    for idx in indices:
        if 0 <= idx < len(files):
            valid_files.append(files[idx])
        else:
            print(Style.warn(f"ID {idx+1} is out of range."))

    if not valid_files:
        return

    # Parse all selected sessions
    print(f"\n{Style.info('Parsing selected session(s)...')}")
    parsers: List[SessionParser] = []
    for f in valid_files:
        try:
            parsers.append(SessionParser(f))
        except Exception as e:
            print(Style.error(f"Failed to parse {f.name}: {e}"))

    if not parsers:
        return

    # Interactive filter
    section_filter, clean_content, output_cap = interactive_filter(parsers)

    # Check anything is selected
    if not any(section_filter.values()):
        print(Style.warn("Nothing selected — skipping export."))
        input(f"\n{Style.DIM}Press Enter to return to menu...{Style.RESET}")
        return

    os.system('clear')
    
    # Ask for export destination
    dest_choice = ''
    while dest_choice not in ('f', 'c', 'b'):
        print(f"\n  {Style.BOLD}Export Destination:{Style.RESET}")
        print(f"    {Style.YELLOW}[F]{Style.RESET}ile (save to disk)       {Style.DIM}[Default]{Style.RESET}")
        print(f"    {Style.YELLOW}[C]{Style.RESET}lipboard (copy directly)")
        print(f"    {Style.YELLOW}[B]{Style.RESET}oth")
        dest_choice = input(f"\n  {Style.BOLD}Select > {Style.RESET}").strip().lower()
        if not dest_choice: 
            dest_choice = 'f'

    # Export
    print(f"\n{Style.info(f'Processing {len(parsers)} session(s)...')}")

    try:
        out_dir = Path(__file__).parent.resolve()
    except NameError:
        out_dir = Path.cwd()

    clipboard_md = []

    for parser in parsers:
        try:
            md_content = parser.to_markdown(
                section_filter=section_filter,
                clean_content=clean_content,
                output_cap=output_cap,
            )

            date_prefix = datetime.fromtimestamp(
                parser.filepath.stat().st_mtime
            ).strftime("%Y%m%d")
            safe_title = clean_filename(parser.title)
            out_filename = f"{date_prefix}_{safe_title}.md"
            line_count = md_content.count('\n') + 1

            # Accumulate for clipboard
            if dest_choice in ('c', 'b'):
                if len(parsers) > 1:
                    clipboard_md.append(f"<!-- Session: {out_filename} -->\n" + md_content)
                else:
                    clipboard_md.append(md_content)

            # Write to file
            if dest_choice in ('f', 'b'):
                out_path = out_dir / out_filename
                with open(out_path, 'w', encoding='utf-8') as outfile:
                    outfile.write(md_content)
                print(f"  {Style.GREEN}➜{Style.RESET} Saved: {out_filename}  "
                      f"{Style.CYAN}({line_count:,} lines){Style.RESET}")
                
        except Exception as e:
            print(f"  {Style.error(f'Failed {parser.filepath.name}: {e}')}")

    if dest_choice in ('c', 'b') and clipboard_md:
        full_text = "\n\n---\n\n".join(clipboard_md)
        success = copy_to_clipboard(full_text)
        if success:
            total_lines_copied = full_text.count('\n')
            print(f"  {Style.GREEN}➜{Style.RESET} Copied to clipboard! "
                  f"{Style.CYAN}({total_lines_copied:,} total lines){Style.RESET}")
        else:
            print(f"  {Style.RED}➜{Style.RESET} Failed to copy to clipboard! (Is xclip/xsel installed?)")

    input(f"\n{Style.DIM}Press Enter to return to menu...{Style.RESET}")

def interactive_loop():
    while True:
        print_menu_header()

        files = get_all_sessions()
        if not files:
            print(Style.error(f"No sessions found in {SESSIONS_DIR}"))
            print(Style.info("Check CODEX_HOME environment variable."))
            sys.exit(1)

        list_sessions_table(files[:15])

        if len(files) > 15:
            print(f"{Style.DIM}(Showing 15 of {len(files)} sessions. Older files hidden){Style.RESET}\n")

        print(f"{Style.BOLD}OPTIONS:{Style.RESET}")
        print(f"  {Style.GREEN}[ID, ID]{Style.RESET} : Convert specific sessions (e.g. '1, 3')")
        print(f"  {Style.YELLOW}[a]{Style.RESET}      : Convert ALL listed sessions")
        print(f"  {Style.RED}[q]{Style.RESET}      : Quit")

        choice = input(f"\n{Style.BOLD}Select > {Style.RESET}").strip().lower()

        if choice == 'q':
            print("Bye.")
            sys.exit(0)
        elif choice == 'a':
            confirm = input(f"{Style.warn('Convert ALL displayed sessions? (y/n): ')}")
            if confirm.lower() == 'y':
                process_conversion(",".join([str(i+1) for i in range(len(files[:15]))]), files)
        elif choice:
            process_conversion(choice, files)

if __name__ == "__main__":
    try:
        interactive_loop()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(0)
