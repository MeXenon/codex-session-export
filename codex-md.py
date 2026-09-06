#!/usr/bin/env python3
"""
Codex Session Manager & Markdown Converter  (v2.6.4)
-------------------------------------------------
An interactive tool to browse, filter, and convert OpenAI Codex
session logs (.jsonl) into readable Markdown documents.

Features:
  • Browse all Codex sessions with preview
  • Group & browse sessions by project (working directory)
  • Paginated browser for large session/project histories
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
import ast
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set

# Platform-specific terminal handling
_IS_WINDOWS = sys.platform == 'win32'
if not _IS_WINDOWS:
    import tty
    import termios

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
ROLLOUT_ID_RE = re.compile(
    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
    re.IGNORECASE,
)

# ──────────────────────────────────────────────────────────────
# Section Definitions  (key, display_name, emoji, default_on)
#
# These are ALL the section types discovered across every Codex
# session file.  Each maps to one or more JSONL entry patterns.
# ──────────────────────────────────────────────────────────────
# Function name → sub-category mapping
# Built from scanning 223 session files (21 unique function names)
TERMINAL_FUNCS = {'exec_command', 'execute_command', 'run_command', 'shell', 'shell_command', 'write_stdin', 'send_input'}
OTHER_TOOL_FUNCS = {'spawn_agent', 'wait_agent', 'close_agent', 'update_plan',
                    'request_user_input', 'view_image', 'list_mcp_resources',
                    'read_mcp_resource', 'list_mcp_resource_templates'}

_CODE_MODE_TOOL_CALL_RE = re.compile(
    r'\btools\.(?:exec_command|execute_command|run_command|shell|shell_command|write_stdin|send_input)\s*\('
)
_CODE_MODE_MCP_CALL_RE = re.compile(r'\btools\.(mcp__[A-Za-z0-9_]+)\s*\(')
_CODE_MODE_ANY_TOOL_RE = re.compile(r'\btools\.([A-Za-z0-9_]+)\s*\(')
_CODE_MODE_COMMAND_RE = re.compile(
    r'''\b(?:cmd|command)\s*:\s*(?:"((?:\\.|[^"\\])*)"|'((?:\\.|[^'\\])*)'|`((?:\\.|[^`\\])*)`)''',
    re.DOTALL,
)

def classify_tool(name: str, namespace: str = '') -> str:
    """Classify a function_call name into a sub-category key."""
    if name in TERMINAL_FUNCS:       return 'terminal_cmd'
    if name.startswith('mcp__'):     return 'mcp_tool'
    if name in OTHER_TOOL_FUNCS:     return 'other_tool'
    if namespace:
        return 'other_tool'
    return 'other_tool'

def _decode_code_mode_string(value: str, quote: str) -> str:
    token = quote + value + quote
    try:
        if quote == '"':
            return json.loads(token)
        if quote == "'":
            return ast.literal_eval(token)
    except (ValueError, SyntaxError, json.JSONDecodeError):
        pass
    if quote == '`':
        return value.replace(r'\`', '`').replace(r'\\', '\\')
    return value

def _extract_code_mode_commands(source: str) -> List[str]:
    if not isinstance(source, str) or not _CODE_MODE_TOOL_CALL_RE.search(source):
        return []
    commands: List[str] = []
    for match in _CODE_MODE_COMMAND_RE.finditer(source):
        groups = match.groups()
        for index, value in enumerate(groups):
            if value is not None:
                quote = ('"', "'", '`')[index]
                decoded = _decode_code_mode_string(value, quote).strip()
                if decoded and decoded not in commands:
                    commands.append(decoded)
                break
    return commands

def _is_code_mode_terminal_wrapper(name: str, source: str) -> bool:
    return name == 'exec' and isinstance(source, str) and bool(_CODE_MODE_TOOL_CALL_RE.search(source))

def _classify_code_mode_exec(source: str) -> Tuple[str, str]:
    """Return (section category, display tool name) for a Code Mode exec wrapper."""
    if not isinstance(source, str):
        return 'other_tool', 'exec'
    if _CODE_MODE_TOOL_CALL_RE.search(source):
        return 'terminal_cmd', 'exec'
    mcp_match = _CODE_MODE_MCP_CALL_RE.search(source)
    if mcp_match:
        return 'mcp_tool', mcp_match.group(1)
    tool_match = _CODE_MODE_ANY_TOOL_RE.search(source)
    if tool_match:
        return 'other_tool', tool_match.group(1)
    return 'other_tool', 'exec'

def _normalize_tool_output(value) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ''.join(_normalize_tool_output(item) for item in value)
    if isinstance(value, dict):
        if isinstance(value.get('text'), str):
            return value['text']
        if 'output' in value:
            return _normalize_tool_output(value.get('output'))
        if 'content' in value:
            return _normalize_tool_output(value.get('content'))
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)
    return str(value)

def _command_execution_command(item: Dict) -> str:
    parsed = item.get('parsed_cmd')
    if isinstance(parsed, list):
        commands = [part.get('cmd', '').strip() for part in parsed
                    if isinstance(part, dict) and isinstance(part.get('cmd'), str) and part.get('cmd', '').strip()]
        if commands:
            return ' ; '.join(commands)
    command = item.get('command', '')
    if isinstance(command, list):
        parts = [str(part) for part in command]
        for flag in ('-Command', '-c', '/c'):
            if flag in parts:
                index = parts.index(flag)
                if index + 1 < len(parts):
                    return parts[index + 1]
        return ' '.join(parts)
    return str(command or '')

def _command_execution_output(item: Dict) -> str:
    aggregated = item.get('aggregated_output')
    if isinstance(aggregated, str) and aggregated:
        return aggregated
    formatted = item.get('formatted_output')
    if isinstance(formatted, str) and formatted:
        return formatted
    stdout = _normalize_tool_output(item.get('stdout'))
    stderr = _normalize_tool_output(item.get('stderr'))
    return stdout + stderr

def _normalized_command(value: str) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip().lower()

def _commands_equivalent(left: str, right: str) -> bool:
    left_n = _normalized_command(left)
    right_n = _normalized_command(right)
    if not left_n or not right_n:
        return False
    if left_n == right_n:
        return True
    return min(len(left_n), len(right_n)) >= 8 and (left_n in right_n or right_n in left_n)

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
    message = strip_user_message_prefix(content)
    if not message:
        return "Empty or Technical Session"

    fallback = ""
    for line in message.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        normalized = normalize_title_candidate(candidate)
        noisy = is_title_noise(candidate)
        if not fallback and not noisy:
            fallback = normalized
        if not noisy:
            return normalized
    return fallback or "Empty or Technical Session"

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

def _text_from_message_content(content) -> str:
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, dict):
                text = part.get('text')
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(part, str):
                parts.append(part)
        return '\n'.join(parts).strip()
    if isinstance(content, str):
        return content.strip()
    if content is None:
        return ""
    return str(content).strip()

def _message_text_from_entry(entry: Dict, role_name: str) -> str:
    payload = entry.get('payload', {})
    if not isinstance(payload, dict):
        return ""

    etype = entry.get('type')
    ptype = payload.get('type')
    if etype == 'event_msg' and ptype == f'{role_name}_message':
        return _text_from_message_content(payload.get('message', ''))
    if etype == 'response_item' and ptype == 'message' and payload.get('role') == role_name:
        return _text_from_message_content(payload.get('content', []))
    return ""

def _rollout_session_id(path: Path) -> Optional[str]:
    matches = ROLLOUT_ID_RE.findall(path.stem)
    return matches[-1] if matches else None

def _nested_subagent_parent_id(source) -> Optional[str]:
    if not isinstance(source, dict):
        return None
    subagent = source.get('subagent')
    if not isinstance(subagent, dict):
        return None
    spawn = subagent.get('thread_spawn')
    if not isinstance(spawn, dict):
        return None
    parent = spawn.get('parent_thread_id')
    return parent if isinstance(parent, str) else None

def session_aliases_from_meta(path: Path, meta: Optional[Dict]) -> Set[str]:
    aliases: Set[str] = set()

    def add(value):
        if isinstance(value, str):
            value = value.strip()
            if value:
                aliases.add(value)

    if isinstance(meta, dict):
        for key in ('id', 'session_id', 'parent_thread_id', 'thread_id'):
            add(meta.get(key))
        add(_nested_subagent_parent_id(meta.get('source')))

    add(_rollout_session_id(path))
    return aliases

def primary_session_id(path: Path, meta: Optional[Dict], aliases: Optional[Set[str]] = None) -> str:
    if isinstance(meta, dict):
        for key in ('id', 'session_id', 'parent_thread_id', 'thread_id'):
            value = meta.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    rollout_id = _rollout_session_id(path)
    if rollout_id:
        return rollout_id
    if aliases:
        return sorted(aliases)[0]
    stem = path.stem
    return stem[len("rollout-"):] if stem.startswith("rollout-") else stem

# ──────────────────────────────────────────────────────────────
# Session Scanning  (fast head-scan for preview list)
# ──────────────────────────────────────────────────────────────
def read_session_summary(filepath: Path, head_limit=10, user_scan_limit=200):
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as fp:
            session_meta = None
            first_user_message = None
            saw_user_message = False
            lines_scanned = 0
            while lines_scanned < head_limit or (
                session_meta is not None and not saw_user_message
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
                message = _message_text_from_entry(entry, 'user')
                if message:
                    saw_user_message = True
                    if first_user_message is None:
                        message = strip_user_message_prefix(message)
                        if message:
                            first_user_message = message
                    if session_meta is not None:
                        break
            return session_meta, first_user_message, saw_user_message
    except Exception:
        return None, None, False

def is_interactive_session_meta(meta: Optional[Dict]) -> bool:
    if not meta: return False
    source = meta.get('source')
    if isinstance(source, str) and source.lower() in INTERACTIVE_SESSION_SOURCES:
        return True
    if _nested_subagent_parent_id(source):
        return True
    thread_source = meta.get('thread_source')
    if isinstance(thread_source, str) and thread_source.lower() in ('user', 'subagent'):
        return True
    originator = meta.get('originator')
    return isinstance(originator, str) and originator.lower().startswith('codex')

def get_session_preview_title(filepath: Path) -> str:
    meta, first_user_message, saw_user_message = read_session_summary(filepath)
    if not isinstance(meta, dict):
        return "Untitled / System Log"
    for thread_id in session_aliases_from_meta(filepath, meta):
        if thread_id in THREAD_NAMES:
            return normalize_title_candidate(THREAD_NAMES[thread_id])
    if not first_user_message:
        if saw_user_message:
            return "[Image]"
        sid = primary_session_id(filepath, meta)
        return f"System Session {sid[:8]}" if sid else "Untitled / System Log"
    title = extract_title_from_content(first_user_message)
    if title == "Empty or Technical Session":
        sid = primary_session_id(filepath, meta)
        return f"System Session {sid[:8]}" if sid else "Untitled / System Log"
    return title

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
        self.latest_input_tokens = 0
        self.model_context_window = 0
        self._call_id_map: Dict[str, str] = {}
        self._load()

    def _append_dialog_message(self, msg_type: str, ts: str, content: str):
        content = _text_from_message_content(content)
        if not content:
            return
        if self.data:
            last = self.data[-1]
            if last.get('type') == msg_type and last.get('content') == content:
                return
        self.data.append({'type': msg_type, 'timestamp': ts, 'content': content})

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

        self._dedupe_code_mode_terminal_fallbacks()

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

    def _append_command_execution(self, ts: str, item: Dict):
        command = _command_execution_command(item)
        call_id = item.get('id') or item.get('call_id')
        self.data.append({
            'type': 'terminal_cmd', 'timestamp': ts,
            'name': 'exec_command', 'arguments': command,
            'call_id': call_id, '_canonical_terminal': True,
        })
        self.data.append({
            'type': 'terminal_output', 'timestamp': ts,
            'output': _command_execution_output(item),
            'call_id': call_id, '_canonical_terminal': True,
        })

    def _dedupe_code_mode_terminal_fallbacks(self):
        canonical_commands = [
            str(item.get('arguments', '')) for item in self.data
            if item.get('type') == 'terminal_cmd' and item.get('_canonical_terminal')
        ]
        if not canonical_commands:
            return
        redundant_call_ids: Set[str] = set()
        for item in self.data:
            if item.get('type') != 'terminal_cmd' or not item.get('_code_mode_fallback'):
                continue
            fallback_commands = item.get('_code_mode_commands') or [item.get('arguments', '')]
            if any(_commands_equivalent(fallback, canonical)
                   for fallback in fallback_commands for canonical in canonical_commands):
                call_id = item.get('call_id')
                if call_id:
                    redundant_call_ids.add(call_id)
        if redundant_call_ids:
            self.data = [
                item for item in self.data
                if not (item.get('_code_mode_fallback') and item.get('call_id') in redundant_call_ids)
            ]

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
            self._append_dialog_message('user_message', ts, msg)
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
            self._append_dialog_message('agent_message', ts, msg)
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
                usage = info.get('last_token_usage') or info.get('total_token_usage') or info
                if 'input_tokens' in usage:
                    self.latest_input_tokens = usage['input_tokens']
                if 'model_context_window' in info:
                    self.model_context_window = info['model_context_window']
                parts.append(f"in={usage.get('input_tokens', '?')}, out={usage.get('output_tokens', '?')}")
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
                ctx = payload.get('model_context_window', 0)
                if ctx:
                    self.model_context_window = ctx
                if model or ctx:
                    detail = f" ({model}, ctx={ctx:,})" if ctx else f" ({model})"
            self.data.append({
                'type': 'task_event', 'timestamp': ts,
                'event': ptype,
                'content': ptype.replace('_', ' ').title() + detail,
            })
            return

        # 7 ─ Canonical current command execution record
        if etype == 'event_msg' and ptype == 'item_completed':
            idata = payload.get('item', {})
            if isinstance(idata, dict) and idata.get('type') == 'CommandExecution':
                self._append_command_execution(ts, idata)
                return

        # 7b ─ Legacy completed command event retained by Codex rollout migration
        if etype == 'event_msg' and ptype == 'exec_command_end':
            self._append_command_execution(ts, payload)
            return

        # 7c ─ Context compacted / thread rolled back / turn aborted / item completed
        if etype == 'event_msg' and ptype in ('context_compacted', 'thread_rolled_back', 'turn_aborted', 'item_completed'):
            detail = ''
            num_turns = 0
            if ptype == 'thread_rolled_back':
                num_turns = payload.get('num_turns', 0)
                detail = f" ({num_turns} turns)"
            elif ptype == 'turn_aborted':
                detail = f" (reason: {payload.get('reason', '?')})"
            elif ptype == 'item_completed':
                idata = payload.get('item', {})
                detail = f" ({idata.get('type', '?')})"
            self.data.append({
                'type': 'session_event', 'timestamp': ts,
                'event': ptype,
                'content': ptype.replace('_', ' ').title() + detail,
                'num_turns': num_turns,
            })
            return

        # 8 ─ Compacted top-level entry
        if etype == 'compacted':
            self.data.append({
                'type': 'session_event', 'timestamp': ts,
                'event': 'context_compacted',
                'content': 'Context Compacted',
                'replacement_history': payload.get('replacement_history', [])
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
                cat = classify_tool(fname, payload.get('namespace', ''))
                call_id = payload.get('call_id')
                self.data.append({
                    'type': cat, 'timestamp': ts,
                    'name': fname,
                    'arguments': payload.get('arguments', ''),
                    'call_id': call_id,
                    '_tool_cat': cat,  # save category for output matching
                })
                # Remember call_id → category for output pairing
                if call_id:
                    self._call_id_map[call_id] = cat
                return

            # 10c ─ Function call output  (matched to its call's category)
            if ptype == 'function_call_output':
                call_id = payload.get('call_id')
                parent_cat = self._call_id_map.get(call_id, 'other_tool')
                out_cat = TOOL_OUTPUT_MAP.get(parent_cat, 'other_tool_output')
                self.data.append({
                    'type': out_cat, 'timestamp': ts,
                    'output': _normalize_tool_output(payload.get('output', '')),
                    'call_id': call_id,
                })
                return

            # 10d ─ Code Mode exec wrapper, or a real custom tool (apply_patch, etc.)
            if ptype == 'custom_tool_call':
                name = payload.get('name', 'custom_tool')
                source = payload.get('input', '')
                call_id = payload.get('call_id')
                if name == 'exec':
                    cat, display_name = _classify_code_mode_exec(source)
                    commands = _extract_code_mode_commands(source) if cat == 'terminal_cmd' else []
                    arguments = commands[0] if len(commands) == 1 else ({'commands': commands} if commands else source)
                    self.data.append({
                        'type': cat, 'timestamp': ts,
                        'name': display_name, 'arguments': arguments,
                        'call_id': call_id,
                        '_code_mode_fallback': cat == 'terminal_cmd',
                        '_code_mode_commands': commands,
                    })
                    if call_id:
                        self._call_id_map[call_id] = cat
                    return
                self.data.append({
                    'type': 'custom_tool_call', 'timestamp': ts,
                    'name': name,
                    'content': source,
                    'call_id': call_id,
                })
                return

            # 10e ─ Custom tool output
            if ptype == 'custom_tool_call_output':
                call_id = payload.get('call_id')
                output = _normalize_tool_output(payload.get('output', ''))
                parent_cat = self._call_id_map.get(call_id)
                if parent_cat in TOOL_OUTPUT_MAP:
                    self.data.append({
                        'type': TOOL_OUTPUT_MAP[parent_cat], 'timestamp': ts,
                        'output': output, 'call_id': call_id,
                        '_code_mode_fallback': parent_cat == 'terminal_cmd',
                    })
                    return
                self.data.append({
                    'type': 'custom_tool_output', 'timestamp': ts,
                    'content': output,
                    'call_id': call_id,
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
                text = _text_from_message_content(payload.get('content', []))
                if text:
                    self.data.append({'type': 'system_message', 'timestamp': ts, 'content': text})
                return

            # 10i ─ Raw assistant / user messages. Some logs have no event_msg mirror.
            if ptype == 'message' and role == 'assistant':
                self._append_dialog_message('agent_message', ts, payload.get('content', []))
                return
            if ptype == 'message' and role == 'user':
                self._append_dialog_message('user_message', ts, payload.get('content', []))
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
            if isinstance(content, list): content = '\n'.join(str(x) for x in content)
            elif not isinstance(content, str): content = str(content)

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
                if isinstance(args, (dict, list)):
                    try: args = json.dumps(args, indent=2)
                    except Exception: args = str(args)
                else:
                    try: args = json.dumps(json.loads(args), indent=2)
                    except Exception: args = str(args)
                lines = args.count('\n') + 5
            elif itype in tool_output_types:
                out = item.get('output', '')
                if isinstance(out, list): out = '\n'.join(str(x) for x in out)
                elif not isinstance(out, str): out = str(out)
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

    # --- turn boundaries ---
    def get_turn_boundaries(self) -> List[int]:
        """Return indices in self.data where each turn starts (at each user_message)."""
        return [i for i, item in enumerate(self.data) if item['type'] == 'user_message']

    def get_turn_count(self) -> int:
        """Return the number of turns (user messages) in this session."""
        return len(self.get_turn_boundaries())

    def trim_to_last_n_turns(self, n: int):
        """Keep only the last N turns worth of data items.
        A turn starts at a user_message and includes everything until the next one."""
        if n <= 0:
            return
        boundaries = self.get_turn_boundaries()
        if not boundaries or n >= len(boundaries):
            return
        cut_index = boundaries[-n]
        self.data = self.data[cut_index:]

    def trim_to_live_context(self):
        """
        Replicate Codex's live context logic.
        - Compactions clear older history and replace it with their replacement_history.
        - Rollbacks drop the last N user turns and everything that follows.
        """
        live_data = []
        for item in self.data:
            if item['type'] == 'session_event' and item.get('event') == 'thread_rolled_back':
                num_turns = item.get('num_turns', 0)
                if num_turns > 0:
                    user_msg_indices = [i for i, x in enumerate(live_data) if x['type'] == 'user_message']
                    if user_msg_indices:
                        cut_idx = user_msg_indices[-num_turns] if num_turns <= len(user_msg_indices) else 0
                        live_data = live_data[:cut_idx]
                live_data.append(item)
            elif item['type'] == 'session_event' and item.get('event') == 'context_compacted':
                repl_history = item.get('replacement_history', [])
                if repl_history:
                    parsed_repl = self._parse_replacement_history(repl_history)
                    # Replace older history with the compacted history
                    live_data = parsed_repl + [item]
                else:
                    # If it's a legacy compaction without history, just append the marker
                    live_data.append(item)
            else:
                live_data.append(item)
        self.data = live_data

    def _parse_replacement_history(self, repl_history) -> List[Dict]:
        parsed = []
        for repl_item in repl_history:
            ptype = repl_item.get('type', '')
            role = repl_item.get('role', '')
            content = repl_item.get('content', [])
            
            # Helper to extract text from content array or string
            def extract_text(c):
                if isinstance(c, list):
                    return '\n'.join(p.get('text', '') for p in c if p.get('type') == 'input_text')
                return str(c)

            if ptype == 'message' and role == 'user':
                msg = extract_text(content)
                if msg: parsed.append({'type': 'user_message', 'timestamp': '', 'content': msg})
            elif ptype == 'message' and role == 'assistant':
                msg = extract_text(content)
                if msg: parsed.append({'type': 'agent_message', 'timestamp': '', 'content': msg})
            elif ptype == 'message' and role == 'developer':
                msg = extract_text(content)
                if msg: parsed.append({'type': 'system_message', 'timestamp': '', 'content': msg})
            elif ptype == 'reasoning':
                parts = repl_item.get('summary', [])
                summary = '\n'.join(p.get('text', '') for p in parts if p.get('text', ''))
                parsed.append({'type': 'reasoning', 'timestamp': '', 'content': summary, 'encrypted': bool(repl_item.get('encrypted_content'))})
        return parsed

    # --- markdown rendering with filter ---
    def to_markdown(self, section_filter: Optional[Dict[str, bool]] = None,
                    clean_content: bool = False, output_cap: int = 0, user_cap: int = 0, agent_cap: int = 0, reasoning_cap: int = 0, internal_cap: int = 0) -> str:
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

        keep_indices = set(range(len(self.data)))
        counts = {'user_message': 0, 'agent_message': 0, 'agent_reasoning': 0, 'reasoning': 0}
        
        for i in range(len(self.data) - 1, -1, -1):
            itype = self.data[i]['type']
            if itype == 'user_message' and user_cap > 0:
                if counts['user_message'] >= user_cap: keep_indices.remove(i)
                counts['user_message'] += 1
            elif itype == 'agent_message' and agent_cap > 0:
                if counts['agent_message'] >= agent_cap: keep_indices.remove(i)
                counts['agent_message'] += 1
            elif itype == 'agent_reasoning' and reasoning_cap > 0:
                if counts['agent_reasoning'] >= reasoning_cap: keep_indices.remove(i)
                counts['agent_reasoning'] += 1
            elif itype == 'reasoning' and internal_cap > 0:
                if counts['reasoning'] >= internal_cap: keep_indices.remove(i)
                counts['reasoning'] += 1

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

        for i, item in enumerate(self.data):
            if i not in keep_indices: continue
            itype = item['type']
            if not section_filter.get(itype, False):
                continue

            content = item.get('content', '')
            if isinstance(content, list): content = '\n'.join(str(x) for x in content)
            elif not isinstance(content, str): content = str(content)

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
                    if isinstance(args, (dict, list)):
                        args = json.dumps(args, indent=2)
                    else:
                        args = json.dumps(json.loads(args), indent=2)
                    lang = "json"
                except Exception:
                    args = str(args)
                    lang = "text"
                emoji_map = {'terminal_cmd': '💻', 'mcp_tool': '🔌', 'other_tool': '🧩'}
                em = emoji_map.get(itype, '🛠️')
                md.append(f"### {em} Tool: `{name}`\n\n```{lang}\n{args}\n```\n")

            elif itype in ('terminal_output', 'mcp_tool_output', 'other_tool_output'):
                out = item.get('output', '')
                if isinstance(out, list): out = '\n'.join(str(x) for x in out)
                elif not isinstance(out, str): out = str(out)
                out = _cap_text(out)
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
def _clear_screen():
    """Cross-platform screen clear."""
    os.system('cls' if _IS_WINDOWS else 'clear')

def read_key() -> str:
    """Read a single keypress cross-platform."""
    if _IS_WINDOWS:
        import msvcrt
        ch = msvcrt.getwch()
        if ch in ('\r', '\n'):
            return 'ENTER'
        if ch == ' ':
            return 'SPACE'
        if ch in ('\b', '\x7f'):
            return 'BACKSPACE'
        if ch == '\x03':
            raise KeyboardInterrupt
        if ch == '\xe0' or ch == '\x00':  # special key prefix on Windows
            ext = msvcrt.getwch()
            return {'H': 'UP', 'P': 'DOWN', 'M': 'RIGHT', 'K': 'LEFT'}.get(ext, '')
        return ch.upper()
    else:
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
            if ch in ('\x7f', '\b'):
                return 'BACKSPACE'
            if ch == '\x03':
                raise KeyboardInterrupt
            return ch.upper()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

# ──────────────────────────────────────────────────────────────
# Interactive Section Filter
# ──────────────────────────────────────────────────────────────

# Output sections whose blocks can be capped
OUTPUT_SECTIONS = {'terminal_output', 'mcp_tool_output', 'other_tool_output', 'custom_tool_output'}
# Cap steps for ◀ ▶ control  (0 = no cap, show all)
CAP_STEPS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20, 30, 50, 100, 200, 500]
MSG_CAP_STEPS = [0, 5] + list(range(10, 101, 10)) + [150, 200, 300, 500]

def interactive_filter(parsers: List[SessionParser], scope_label: str = "") -> Tuple[Dict[str, bool], bool, int, int, int, int]:
    """
    Full-screen interactive filter.
    Returns (section_filter, clean_content, output_cap, user_cap, agent_cap, reasoning_cap, internal_cap).
    """
    _line_cache = {}
    
    def get_lines_for_state(cap_out: int, cap_user: int, cap_agent: int, cap_reason: int, cap_internal: int, cc: bool):
        cache_key = (cap_out, cap_user, cap_agent, cap_reason, cap_internal, cc)
        if cache_key in _line_cache:
            return _line_cache[cache_key]
        
        counts = {s[0]: 0 for s in SECTION_DEFS}
        msg_counts: Dict[str, int] = {}  # actual block/message counts for chat types
        tool_call_types = {'terminal_cmd', 'mcp_tool', 'other_tool'}
        tool_output_types = {'terminal_output', 'mcp_tool_output', 'other_tool_output'}
        
        for parser in parsers:
            keep_indices = set(range(len(parser.data)))
            chat_counts = {'u': 0, 'a': 0, 'r': 0, 'i': 0}
            
            for i in range(len(parser.data) - 1, -1, -1):
                itype = parser.data[i]['type']
                if itype == 'user_message' and cap_user > 0:
                    if chat_counts['u'] >= cap_user: keep_indices.remove(i)
                    chat_counts['u'] += 1
                elif itype == 'agent_message' and cap_agent > 0:
                    if chat_counts['a'] >= cap_agent: keep_indices.remove(i)
                    chat_counts['a'] += 1
                elif itype == 'agent_reasoning' and cap_reason > 0:
                    if chat_counts['r'] >= cap_reason: keep_indices.remove(i)
                    chat_counts['r'] += 1
                elif itype == 'reasoning' and cap_internal > 0:
                    if chat_counts['i'] >= cap_internal: keep_indices.remove(i)
                    chat_counts['i'] += 1
            
            for i, item in enumerate(parser.data):
                if i not in keep_indices: continue
                itype = item['type']
                content = item.get('content', '')
                if isinstance(content, list): content = '\n'.join(str(x) for x in content)
                elif not isinstance(content, str): content = str(content)
                
                # Count actual messages for chat-type sections
                if itype in ('user_message', 'agent_message', 'agent_reasoning', 'reasoning'):
                    msg_counts[itype] = msg_counts.get(itype, 0) + 1
                
                if itype == 'user_message':
                    if cc: content = trim_chat_content(content)
                    lines = content.count('\n') + 4 if content else 0
                elif itype == 'agent_message':
                    if cc: content = trim_chat_content(content)
                    lines = content.count('\n') + 4 if content else 0
                elif itype in ('agent_reasoning', 'reasoning'):
                    lines = 2 if not content else content.count('\n') + 3
                elif itype in tool_call_types:
                    args = item.get('arguments', '')
                    if isinstance(args, (dict, list)):
                        try: args = json.dumps(args, indent=2)
                        except Exception: args = str(args)
                    else:
                        try: args = json.dumps(json.loads(args), indent=2)
                        except Exception: args = str(args)
                    lines = args.count('\n') + 5
                elif itype in tool_output_types:
                    out = item.get('output', '')
                    if isinstance(out, list): out = '\n'.join(str(x) for x in out)
                    elif not isinstance(out, str): out = str(out)
                    total_out = out.count('\n') + 1 if out.strip() else 0
                    if cap_out > 0: lines = min(total_out, cap_out) + 4 if total_out > 0 else 0
                    else: lines = total_out + 4 if total_out > 0 else 0
                elif itype == 'custom_tool_call':
                    lines = content.count('\n') + 5
                elif itype == 'custom_tool_output':
                    total_out = content.count('\n') + 1 if content.strip() else 0
                    if cap_out > 0: lines = min(total_out, cap_out) + 4 if total_out > 0 else 0
                    else: lines = total_out + 4 if total_out > 0 else 0
                elif itype in ('web_search', 'token_count', 'turn_context', 'task_event', 'git_snapshot', 'session_event'):
                    lines = 2
                elif itype == 'system_message':
                    lines = content.count('\n') + 5
                else:
                    lines = 1
                    
                counts[itype] = counts.get(itype, 0) + lines
                
            if parser.metadata:
                counts['session_meta'] = counts.get('session_meta', 0) + 5
                
        _line_cache[cache_key] = (counts, msg_counts)
        return counts, msg_counts

    fstate: Dict[str, bool] = {s[0]: s[3] for s in SECTION_DEFS}
    clean_content = False
    
    output_cap = 8
    cap_idx = CAP_STEPS.index(8)
    
    user_cap = 0
    u_idx = 0
    
    agent_cap = 0
    a_idx = 0
    
    reason_cap = 0
    r_idx = 0
    
    internal_cap = 0
    i_idx = 0

    cursor = 0
    ROW_CLEAN = len(SECTION_DEFS)
    ROW_CAP   = len(SECTION_DEFS) + 1
    ROW_USER  = len(SECTION_DEFS) + 2
    ROW_AGENT = len(SECTION_DEFS) + 3
    ROW_REASON= len(SECTION_DEFS) + 4
    ROW_INTERNAL = len(SECTION_DEFS) + 5
    num_items = len(SECTION_DEFS) + 6

    import shutil as _shutil

    # Alternate screen buffer (htop/vim/less style) — keeps the filter UI off
    # the terminal's scrollback so it can't accumulate frames there. We combine
    # that with an INTERNAL viewport: when the filter has more rows than the
    # terminal can show, we render only the slice around the cursor and surface
    # ▲/▼ markers so you always know there's more.
    sys.stdout.write('\033[?1049h\033[?25l\033[H\033[2J')
    sys.stdout.flush()

    scroll_offset = 0

    try:
        while True:
            agg_lines, agg_msgs = get_lines_for_state(output_cap, user_cap, agent_cap, reason_cap, internal_cap, clean_content)
            total_lines = sum(agg_lines.get(s[0], 0) for s in SECTION_DEFS)
            selected_lines = sum(agg_lines.get(s[0], 0) for s in SECTION_DEFS if fstate.get(s[0], False))
            pct = (selected_lines / total_lines * 100) if total_lines > 0 else 0

            # ── Build the LIST of navigable / decorative middle rows ──
            # Each entry is (cursor_id_or_None, rendered_text).
            mid_rows: List[Tuple[Optional[int], str]] = []

            for i, (key, name, emoji, _default) in enumerate(SECTION_DEFS):
                is_cursor = (i == cursor)
                is_on     = fstate.get(key, False)
                lines     = agg_lines.get(key, 0)
                
                arrow = f'{Style.BOLD}{Style.YELLOW}▸{Style.RESET}' if is_cursor else ' '
                toggle = f'{Style.GREEN}██{Style.RESET}' if is_on else f'{Style.DIM}░░{Style.RESET}'
                
                if is_cursor and is_on: nstyle = f'{Style.BOLD}{Style.GREEN}'
                elif is_cursor and not is_on: nstyle = f'{Style.BOLD}{Style.RED}'
                elif is_on: nstyle = ''
                else: nstyle = Style.DIM

                _MSG_COUNT_TYPES = {'user_message', 'agent_message', 'agent_reasoning', 'reasoning'}
                msg_n = agg_msgs.get(key, 0)
                if key in _MSG_COUNT_TYPES and msg_n > 0:
                    msg_label = f' {Style.DIM}({msg_n:,} Msg){Style.RESET}'
                else:
                    msg_label = ''

                count_str = f'{Style.CYAN}{lines:>6,}{Style.RESET}' if is_on and lines > 0 else f'{Style.DIM}{lines:>6,}{Style.RESET}'
                if lines == 0: count_str = f'{Style.DIM}     0{Style.RESET}'

                visible_name = f'{emoji} {name}'
                pad_len = max(1, 44 - len(visible_name))
                dots = f'{Style.DIM}{"·" * pad_len}{Style.RESET}'

                mid_rows.append((i, f'  {arrow} {toggle} {nstyle}{visible_name}{Style.RESET} {dots} {count_str}{msg_label}'))

            mid_rows.append((None, f'  {Style.DIM}{"─" * 62}{Style.RESET}'))

            # Clean Chat
            cc_on    = clean_content
            cc_cur   = (cursor == ROW_CLEAN)
            cc_arrow = f'{Style.BOLD}{Style.YELLOW}▸{Style.RESET}' if cc_cur else ' '
            cc_tog   = f'{Style.GREEN}██{Style.RESET}' if cc_on else f'{Style.DIM}░░{Style.RESET}'
            cc_st    = f'{Style.BOLD}' if cc_cur else Style.DIM
            cc_val   = f'{Style.GREEN}ON {Style.RESET}' if cc_on else f'{Style.DIM}OFF{Style.RESET}'
            chat_lines = agg_lines.get('user_message', 0) + agg_lines.get('agent_message', 0)
            mid_rows.append((ROW_CLEAN, f'  {cc_arrow} {cc_tog} {cc_st}✂️  Clean Chat{Style.RESET} {Style.DIM}(strips IDE context from 👤🤖){Style.RESET}  {Style.DIM}{chat_lines:,}L{Style.RESET}  {cc_val}'))

            # Output Cap
            cap_cur   = (cursor == ROW_CAP)
            cap_arrow = f'{Style.BOLD}{Style.YELLOW}▸{Style.RESET}' if cap_cur else ' '
            cap_st    = f'{Style.BOLD}' if cap_cur else Style.DIM
            cap_label = f'{Style.DIM}ALL{Style.RESET}' if output_cap == 0 else f'{Style.YELLOW}{output_cap}{Style.RESET}'
            hint = f' {Style.DIM}◀▶{Style.RESET}' if cap_cur else ''
            mid_rows.append((ROW_CAP, f'  {cap_arrow}    {cap_st}💻 Terminal Output Cap{Style.RESET} {Style.DIM}(max lines/block){Style.RESET}  {cap_label}{hint}'))

            # User Cap
            u_cur   = (cursor == ROW_USER)
            u_arrow = f'{Style.BOLD}{Style.YELLOW}▸{Style.RESET}' if u_cur else ' '
            u_st    = f'{Style.BOLD}' if u_cur else Style.DIM
            u_label = f'{Style.DIM}ALL{Style.RESET}' if user_cap == 0 else f'{Style.YELLOW}Last {user_cap}{Style.RESET}'
            u_hint = f' {Style.DIM}◀▶{Style.RESET}' if u_cur else ''
            mid_rows.append((ROW_USER, f'  {u_arrow}    {u_st}👤 User Message Cap{Style.RESET} {Style.DIM}(blocks){Style.RESET}             {u_label}{u_hint}'))

            # Agent Cap
            a_cur   = (cursor == ROW_AGENT)
            a_arrow = f'{Style.BOLD}{Style.YELLOW}▸{Style.RESET}' if a_cur else ' '
            a_st    = f'{Style.BOLD}' if a_cur else Style.DIM
            a_label = f'{Style.DIM}ALL{Style.RESET}' if agent_cap == 0 else f'{Style.YELLOW}Last {agent_cap}{Style.RESET}'
            a_hint = f' {Style.DIM}◀▶{Style.RESET}' if a_cur else ''
            mid_rows.append((ROW_AGENT, f'  {a_arrow}    {a_st}🤖 Agent Message Cap{Style.RESET} {Style.DIM}(blocks){Style.RESET}            {a_label}{a_hint}'))

            # Reason Cap
            r_cur   = (cursor == ROW_REASON)
            r_arrow = f'{Style.BOLD}{Style.YELLOW}▸{Style.RESET}' if r_cur else ' '
            r_st    = f'{Style.BOLD}' if r_cur else Style.DIM
            r_label = f'{Style.DIM}ALL{Style.RESET}' if reason_cap == 0 else f'{Style.YELLOW}Last {reason_cap}{Style.RESET}'
            r_hint = f' {Style.DIM}◀▶{Style.RESET}' if r_cur else ''
            mid_rows.append((ROW_REASON, f'  {r_arrow}    {r_st}🧠 Agent Reasoning Cap{Style.RESET} {Style.DIM}(blocks){Style.RESET}          {r_label}{r_hint}'))

            # Internal Reasoning Cap
            i_cur   = (cursor == ROW_INTERNAL)
            i_arrow = f'{Style.BOLD}{Style.YELLOW}▸{Style.RESET}' if i_cur else ' '
            i_st    = f'{Style.BOLD}' if i_cur else Style.DIM
            i_label = f'{Style.DIM}ALL{Style.RESET}' if internal_cap == 0 else f'{Style.YELLOW}Last {internal_cap}{Style.RESET}'
            i_hint = f' {Style.DIM}◀▶{Style.RESET}' if i_cur else ''
            mid_rows.append((ROW_INTERNAL, f'  {i_arrow}    {i_st}🔒 Internal Reasoning Cap{Style.RESET} {Style.DIM}(blocks){Style.RESET}       {i_label}{i_hint}'))

            # ── Viewport math ──
            term_size = _shutil.get_terminal_size((80, 30))
            term_h = max(10, term_size.lines)
            # Fixed-size header (3 lines: blank + title + separator) +
            # fixed-size footer (separator + progress bar + hint + 1 cushion).
            # Plus 2 lines reserved for ▲/▼ markers so the layout never jumps.
            HEADER_LINES = 3
            FOOTER_LINES = 5
            INDICATOR_LINES = 2
            view_h = max(5, term_h - HEADER_LINES - FOOTER_LINES - INDICATOR_LINES)

            # Find cursor's index in mid_rows and scroll viewport so it's visible
            cur_pos = next((idx for idx, (rid, _) in enumerate(mid_rows) if rid == cursor), 0)
            if cur_pos < scroll_offset:
                scroll_offset = cur_pos
            elif cur_pos >= scroll_offset + view_h:
                scroll_offset = cur_pos - view_h + 1
            max_offset = max(0, len(mid_rows) - view_h)
            scroll_offset = max(0, min(scroll_offset, max_offset))

            # ── Compose frame ──
            out = ['\033[H\033[2J']  # home + clear (alt-screen surface)
            sessions_label = f"{len(parsers)} session{'s' if len(parsers) > 1 else ''}"
            if scope_label:
                sessions_label += f" · {scope_label}"
            out.append(f"  {Style.BOLD}{Style.HEADER}SECTION FILTER{Style.RESET}  {Style.DIM}({sessions_label}){Style.RESET}")
            out.append(f"  {Style.DIM}{'━' * 62}{Style.RESET}")
            out.append('')

            # ▲ indicator (always reserve the line — keeps row positions stable)
            if scroll_offset > 0:
                out.append(f'  {Style.DIM}▲ {scroll_offset} more above{Style.RESET}')
            else:
                out.append('')

            # Viewport slice
            for _rid, text in mid_rows[scroll_offset:scroll_offset + view_h]:
                out.append(text)

            # ▼ indicator
            below = max(0, len(mid_rows) - (scroll_offset + view_h))
            if below > 0:
                out.append(f'  {Style.DIM}▼ {below} more below{Style.RESET}')
            else:
                out.append('')

            # Footer: separator + progress + hint
            out.append(f'  {Style.DIM}{"━" * 62}{Style.RESET}')
            bar_w = 30
            filled = int(bar_w * pct / 100)
            bar = f'{Style.GREEN}{"█" * filled}{Style.DIM}{"░" * (bar_w - filled)}{Style.RESET}'
            sel_c = Style.GREEN if pct > 0 else Style.RED
            out.append(f'  {bar}  {sel_c}{Style.BOLD}{selected_lines:,}{Style.RESET}{Style.DIM}/{Style.RESET}{total_lines:,}  {Style.DIM}({pct:.0f}%){Style.RESET}')
            out.append(f'  {Style.DIM}↑↓ move  ⏎ toggle  ◀▶ cap  Q export  A all  N none  D defaults  1-7 presets{Style.RESET}')

            sys.stdout.write('\n'.join(out))
            sys.stdout.flush()

            key = read_key()

            if key == 'UP': cursor = (cursor - 1) % num_items
            elif key == 'DOWN': cursor = (cursor + 1) % num_items
            elif key in ('ENTER', 'SPACE'):
                if cursor < len(SECTION_DEFS):
                    skey = SECTION_DEFS[cursor][0]
                    fstate[skey] = not fstate[skey]
                elif cursor == ROW_CLEAN:
                    clean_content = not clean_content
            elif key == 'LEFT':
                if cursor == ROW_CAP:
                    cap_idx = max(0, cap_idx - 1)
                    output_cap = CAP_STEPS[cap_idx]
                elif cursor == ROW_USER:
                    u_idx = max(0, u_idx - 1)
                    user_cap = MSG_CAP_STEPS[u_idx]
                elif cursor == ROW_AGENT:
                    a_idx = max(0, a_idx - 1)
                    agent_cap = MSG_CAP_STEPS[a_idx]
                elif cursor == ROW_REASON:
                    r_idx = max(0, r_idx - 1)
                    reason_cap = MSG_CAP_STEPS[r_idx]
                elif cursor == ROW_INTERNAL:
                    i_idx = max(0, i_idx - 1)
                    internal_cap = MSG_CAP_STEPS[i_idx]
            elif key == 'RIGHT':
                if cursor == ROW_CAP:
                    cap_idx = min(len(CAP_STEPS) - 1, cap_idx + 1)
                    output_cap = CAP_STEPS[cap_idx]
                elif cursor == ROW_USER:
                    u_idx = min(len(MSG_CAP_STEPS) - 1, u_idx + 1)
                    user_cap = MSG_CAP_STEPS[u_idx]
                elif cursor == ROW_AGENT:
                    a_idx = min(len(MSG_CAP_STEPS) - 1, a_idx + 1)
                    agent_cap = MSG_CAP_STEPS[a_idx]
                elif cursor == ROW_REASON:
                    r_idx = min(len(MSG_CAP_STEPS) - 1, r_idx + 1)
                    reason_cap = MSG_CAP_STEPS[r_idx]
                elif cursor == ROW_INTERNAL:
                    i_idx = min(len(MSG_CAP_STEPS) - 1, i_idx + 1)
                    internal_cap = MSG_CAP_STEPS[i_idx]
            elif key == 'A':
                for s in SECTION_DEFS: fstate[s[0]] = True
            elif key == 'N':
                for s in SECTION_DEFS: fstate[s[0]] = False
            elif key == 'I':
                for s in SECTION_DEFS: fstate[s[0]] = not fstate[s[0]]
            elif key == 'D':
                for s in SECTION_DEFS: fstate[s[0]] = s[3]
                clean_content = False
                output_cap = 8
                cap_idx = CAP_STEPS.index(8)
                user_cap = 0
                u_idx = 0
                agent_cap = 0
                a_idx = 0
                reason_cap = 0
                r_idx = 0
                internal_cap = 0
                i_idx = 0
            elif key == 'Q' or key == 'ESC':
                break
            elif key.isdigit():
                pi = int(key) - 1
                if 0 <= pi < len(FILTER_PRESETS):
                    _pname, pkeys, pclean = FILTER_PRESETS[pi]
                    if pkeys is None:
                        for s in SECTION_DEFS: fstate[s[0]] = s[3]
                    else:
                        for s in SECTION_DEFS: fstate[s[0]] = s[0] in pkeys
                    clean_content = pclean
    finally:
        # Restore cursor and leave the alt screen. The terminal pops back to
        # whatever was on the main screen before we entered.
        sys.stdout.write('\033[?25h\033[?1049l')
        sys.stdout.flush()

    return fstate, clean_content, output_cap, user_cap, agent_cap, reason_cap, internal_cap

# ──────────────────────────────────────────────────────────────
# Extraction Scope (Last N Turns)
# ──────────────────────────────────────────────────────────────
def select_extraction_scope(parsers: List[SessionParser]) -> Tuple[str, int]:
    """
    Ask user for extraction scope before entering the section filter.
    Returns ('full', 0) for full session, ('last_n', N) for last N turns, or ('live', 0) for Live Context.
    """
    _clear_screen()
    print(f"\n  {Style.BOLD}{Style.HEADER}EXTRACTION SCOPE{Style.RESET}\n")
    print(f"  {Style.DIM}{'━' * 52}{Style.RESET}\n")

    for i, p in enumerate(parsers):
        tc = p.get_turn_count()
        label = f"Session {i+1}" if len(parsers) > 1 else "Session"
        title = p.title
        if len(title) > 42:
            title = title[:39] + "..."
        print(f"  {Style.CYAN}{label}{Style.RESET}  {Style.DIM}{title}{Style.RESET}")
        
        ctx_str = ""
        if p.model_context_window > 0:
            pct = (p.latest_input_tokens / p.model_context_window) * 100
            ctx_str = f"  {Style.DIM}Live Context: {p.latest_input_tokens//1000}k/{p.model_context_window//1000}k tokens ({pct:.1f}%){Style.RESET}"
            
        print(f"           {Style.BOLD}{tc}{Style.RESET} turn{'s' if tc != 1 else ''}{ctx_str}\n")

    print(f"  {Style.DIM}{'━' * 52}{Style.RESET}")
    print(f"  {Style.DIM}A 'turn' = one user message + all agent work that followed.{Style.RESET}\n")
    print(f"    {Style.GREEN}[F]{Style.RESET} Full Session — export every turn  {Style.DIM}(Default){Style.RESET}")
    print(f"    {Style.YELLOW}[L]{Style.RESET} Last N Turns — only the most recent N turns")
    print(f"    {Style.CYAN}[C]{Style.RESET} Live Context — exact context window (resolves rollbacks & compactions)\n")

    choice = input(f"  {Style.BOLD}Select > {Style.RESET}").strip().lower()

    if choice == 'c':
        return 'live', 0
    elif choice == 'l':
        while True:
            n_str = input(f"  {Style.BOLD}How many recent turns? > {Style.RESET}").strip()
            if n_str.isdigit() and int(n_str) > 0:
                return 'last_n', int(n_str)
            print(f"  {Style.error('Enter a positive number.')}")

    return 'full', 0

# ──────────────────────────────────────────────────────────────
# Project grouping helpers  (a "project" = the session's cwd)
# ──────────────────────────────────────────────────────────────
def project_key_from_cwd(cwd: Optional[str]) -> str:
    """Normalized, comparable key for a project directory.

    Handles Windows drive-letter case differences and trailing slashes so
    that e.g. 'C:\\foo\\' and 'c:\\foo' map to the same project.
    """
    if not cwd or not isinstance(cwd, str):
        return ""
    cleaned = cwd.strip().rstrip('\\/')
    if not cleaned:
        return ""
    return os.path.normcase(cleaned)

def project_display_name(cwd: Optional[str]) -> str:
    """Human-friendly project label (the final path component)."""
    if not cwd or not isinstance(cwd, str):
        return "(no project)"
    cleaned = cwd.strip().rstrip('\\/')
    if not cleaned:
        return "(no project)"
    base = os.path.basename(cleaned)
    return base or cleaned


# ──────────────────────────────────────────────────────────────
# Session List & Main Loop
# ──────────────────────────────────────────────────────────────
class SessionRecord:
    """Lightweight metadata for one session, captured during the scan."""
    __slots__ = ('path', 'mtime', 'cwd', 'pkey', 'pname', 'session_id', 'aliases', 'has_user_message')

    def __init__(self, path: Path, mtime: float, cwd: Optional[str],
                 session_id: Optional[str] = None, aliases: Optional[Set[str]] = None,
                 has_user_message: bool = False):
        self.path = path
        self.mtime = mtime
        self.cwd = cwd or ""
        self.pkey = project_key_from_cwd(cwd)
        self.pname = project_display_name(cwd)
        self.session_id = session_id or path.stem.replace("rollout-", "")
        self.aliases = set(aliases or set())
        self.aliases.add(self.session_id)
        self.has_user_message = has_user_message


def scan_sessions() -> List[SessionRecord]:
    """Scan the sessions directory once, returning session records
    (newest first) with their project (cwd) captured.

    Records are intentionally not limited to event_msg-backed interactive
    sessions because Codex can store valid user/agent content as response_item
    entries, and subagent sessions can use object-shaped source metadata."""
    if not SESSIONS_DIR.exists():
        return []
    pattern = str(SESSIONS_DIR / "**" / "rollout-*.jsonl")
    files = glob.glob(pattern, recursive=True)
    records: List[SessionRecord] = []
    for f in files:
        path = Path(f)
        meta, _, saw_user_message = read_session_summary(path)
        if not isinstance(meta, dict) and not saw_user_message:
            continue
        cwd = meta.get('cwd') if isinstance(meta, dict) and isinstance(meta.get('cwd'), str) else None
        aliases = session_aliases_from_meta(path, meta)
        session_id = primary_session_id(path, meta, aliases)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        records.append(SessionRecord(path, mtime, cwd, session_id, aliases, saw_user_message))
    records.sort(key=lambda r: r.mtime, reverse=True)
    return records


def group_by_project(records: List[SessionRecord]) -> List[Dict]:
    """Group session records by project key.

    Returns a list of project dicts {'pkey','pname','cwd','idxs'} ordered by
    most-recent activity. 'idxs' are indices into the *records* list and stay
    in newest-first order (records are pre-sorted)."""
    groups: Dict[str, Dict] = {}
    for i, r in enumerate(records):
        key = r.pkey or "\x00none"
        g = groups.get(key)
        if g is None:
            groups[key] = {
                'pkey': key,
                'pname': r.pname if r.pkey else "(no project)",
                'cwd': r.cwd,
                'idxs': [i],
            }
        else:
            g['idxs'].append(i)
    ordered = sorted(groups.values(),
                     key=lambda g: records[g['idxs'][0]].mtime, reverse=True)
    return ordered

def print_menu_header():
    os.system('cls' if os.name == 'nt' else 'clear')
    print(f"\n{Style.BOLD}CODEX SESSION MANAGER{Style.RESET}  {Style.DIM}v2.6.3{Style.RESET}")
    print(f"{Style.DIM}Directory: {SESSIONS_DIR}{Style.RESET}")
    print(f"{Style.DIM}Default output: {script_directory()}{Style.RESET}\n")

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

_TABLE_WIDTH = 104
_ANSI_RE = re.compile(r'\033\[[0-9;]*m')

def _visible_len(s: str) -> int:
    """Length of a string ignoring ANSI escape sequences."""
    return len(_ANSI_RE.sub('', s))

def _pad_ansi(s: str, width: int) -> str:
    """Right-pad a (possibly ANSI-colored) string to a visible width."""
    pad = width - _visible_len(s)
    return s + (" " * pad if pad > 0 else "")

def _truncate(text: str, width: int) -> str:
    """Truncate plain text to a visible width with an ellipsis."""
    if len(text) > width:
        return text[:max(0, width - 3)] + "..."
    return text

def render_session_table(page_recs: List['SessionRecord'], start_no: int = 1,
                         global_latest: Optional[Path] = None,
                         show_project: bool = False):
    """Render one page of sessions, numbered locally from start_no."""
    if show_project:
        header = f"{'#':<4} {'DATE':<32} {'TITLE':<38} {'PROJECT':<16} {'SIZE'}"
    else:
        header = f"{'#':<4} {'DATE':<32} {'TITLE':<54} {'SIZE'}"
    print(f"{Style.BOLD}{header}{Style.RESET}")
    print(f"{Style.DIM}{'-' * _TABLE_WIDTH}{Style.RESET}")

    for offset, rec in enumerate(page_recs):
        no = start_no + offset
        try:
            size_label = format_size(rec.path.stat().st_size)
        except OSError:
            size_label = "?"
        dt_str = datetime.fromtimestamp(rec.mtime).strftime("%Y-%m-%d %H:%M")
        rel_str = format_relative_time(rec.mtime)
        title = get_session_preview_title(rec.path)

        is_latest = (global_latest is not None and rec.path == global_latest)
        tag = " [LATEST]" if is_latest else ""
        row_color = Style.GREEN if is_latest else (Style.CYAN if no % 2 == 0 else Style.RESET)

        dt_cell = _pad_ansi(f"{dt_str} {Style.DIM}{rel_str}{Style.RESET}", 32)

        if show_project:
            title_cell = _truncate(f"{title}{tag}", 38)
            proj_cell = _truncate(rec.pname if rec.pkey else "(no project)", 16)
            print(f"{row_color}{no:<4} {dt_cell} {title_cell:<38} {proj_cell:<16} {size_label}{Style.RESET}")
        else:
            title_cell = _truncate(f"{title}{tag}", 54)
            print(f"{row_color}{no:<4} {dt_cell} {title_cell:<54} {size_label}{Style.RESET}")
    print(f"{Style.DIM}{'-' * _TABLE_WIDTH}{Style.RESET}")

def render_project_table(page_projs: List[Dict], records: List['SessionRecord'],
                         start_no: int = 1):
    """Render one page of projects, numbered locally from start_no."""
    print(f"{Style.BOLD}{'#':<4} {'PROJECT':<26} {'SESSIONS':<12} {'LAST ACTIVE':<16} {'PATH'}{Style.RESET}")
    print(f"{Style.DIM}{'-' * _TABLE_WIDTH}{Style.RESET}")
    for offset, g in enumerate(page_projs):
        no = start_no + offset
        count = len(g['idxs'])
        newest = records[g['idxs'][0]].mtime
        rel = format_relative_time(newest)            # e.g. "(6h 17m ago)" (no ANSI)
        name = _truncate(g['pname'], 26)
        cwd = _truncate(g['cwd'] or "(unknown)", 42)
        count_str = f"{count} session{'s' if count != 1 else ''}"
        row_color = Style.CYAN if no % 2 == 0 else Style.RESET
        print(f"{row_color}{no:<4} {name:<26} {count_str:<12} {rel:<16} {Style.DIM}{cwd}{Style.RESET}")
    print(f"{Style.DIM}{'-' * _TABLE_WIDTH}{Style.RESET}")

def _print_pager(page_idx: int, total_pages: int, total_items: int, noun: str):
    print(f"\n{Style.DIM}Page {page_idx + 1}/{total_pages}  ·  {total_items} {noun} total{Style.RESET}")

def _parse_row_selection(choice: str, page_paths: List[Path]) -> List[Path]:
    """Map a user's comma list of row numbers (1-based, current page) to Paths."""
    valid: List[Path] = []
    seen: Set[Path] = set()
    for part in (x.strip() for x in choice.split(',')):
        if not part:
            continue
        if not part.isdigit():
            print(Style.warn(f"Ignoring '{part}' — enter row numbers shown on this page."))
            continue
        n = int(part)
        if 1 <= n <= len(page_paths):
            p = page_paths[n - 1]
            if p not in seen:
                seen.add(p)
                valid.append(p)
        else:
            print(Style.warn(f"Row {n} is not on this page (1-{len(page_paths)})."))
    return valid


def _read_seeded_selection_line(seed: str) -> str:
    """Read a browser selection line after the first digit was already pressed."""
    buffer = list(seed)
    sys.stdout.write(seed)
    sys.stdout.flush()

    while True:
        key = read_key()
        if key == 'ENTER':
            print()
            return ''.join(buffer).strip()
        if key == 'ESC':
            print()
            return ""
        if key == 'BACKSPACE':
            if buffer:
                buffer.pop()
                sys.stdout.write('\b \b')
                sys.stdout.flush()
            continue
        if key == 'SPACE':
            buffer.append(' ')
            sys.stdout.write(' ')
            sys.stdout.flush()
            continue
        if key in ('LEFT', 'RIGHT', 'UP', 'DOWN'):
            continue
        if len(key) == 1 and key.isprintable():
            buffer.append(key)
            sys.stdout.write(key)
            sys.stdout.flush()


def _read_browser_action(prompt: str):
    """Render the prompt and read a single keypress for the browser.

    Returns a (kind, value) tuple:
      ('prev',   None)       ← / ↑ / p   — previous page
      ('next',   None)       → / ↓ / n   — next page
      ('select', "1, 3")     a digit was pressed; the full typed line follows
      ('cmd',    'M')        a command letter (uppercased): M / B / A / Q ...
      ('noop',   None)       Enter / Space / unrecognized

    Navigation keys act on a single keypress (no Enter). Pressing a digit drops
    into normal line input so multi-session selections like '1, 3, 5' still work.
    """
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        key = read_key()
    except KeyboardInterrupt:
        print()
        return ('cmd', 'Q')
    if key in ('LEFT', 'UP', 'P'):
        print()
        return ('prev', None)
    if key in ('RIGHT', 'DOWN', 'N'):
        print()
        return ('next', None)
    if key and key.isdigit():
        return ('select', _read_seeded_selection_line(key))
    if key in ('ENTER', 'SPACE', 'ESC', ''):
        print()
        return ('noop', None)
    print()
    return ('cmd', key)


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

def script_directory() -> Path:
    """Directory containing this script; falls back to the current shell cwd."""
    try:
        return Path(__file__).parent.resolve()
    except NameError:
        return Path.cwd().resolve()

def prompt_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        answer = input(f"  {Style.BOLD}{prompt} [{suffix}] > {Style.RESET}").strip().lower()
        if not answer:
            return default
        if answer in ('y', 'yes'):
            return True
        if answer in ('n', 'no'):
            return False
        print(f"  {Style.error('Enter y or n.')}")

def project_dir_for_parser(parser: SessionParser) -> Optional[Path]:
    """Return the session cwd as a real local directory, if available."""
    cwd = parser.metadata.get('cwd') if isinstance(parser.metadata, dict) else None
    if not isinstance(cwd, str) or not cwd.strip():
        return None
    try:
        path = Path(os.path.expandvars(cwd.strip())).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve()
    except (OSError, RuntimeError):
        return None
    return path if path.is_dir() else None

def available_project_dirs(parsers: List[SessionParser]) -> List[Path]:
    dirs: List[Path] = []
    seen: Set[str] = set()
    for parser in parsers:
        project_dir = project_dir_for_parser(parser)
        if project_dir is None:
            continue
        key = os.path.normcase(str(project_dir))
        if key not in seen:
            seen.add(key)
            dirs.append(project_dir)
    return dirs

def prompt_custom_output_dir() -> Optional[Path]:
    while True:
        raw = input(f"  {Style.BOLD}Directory path (blank to cancel) > {Style.RESET}").strip().strip('"')
        if not raw:
            return None
        try:
            path = Path(os.path.expandvars(raw)).expanduser()
            if not path.is_absolute():
                path = Path.cwd() / path
            path = path.resolve()
        except (OSError, RuntimeError) as exc:
            print(f"  {Style.error(f'Invalid path: {exc}')}")
            continue

        if path.exists():
            if path.is_dir():
                return path
            print(f"  {Style.error('That path exists but is not a directory.')}")
            continue

        if prompt_yes_no(f"Create {path}?", default=True):
            try:
                path.mkdir(parents=True, exist_ok=True)
                return path
            except OSError as exc:
                print(f"  {Style.error(f'Could not create directory: {exc}')}")

def select_save_location(parsers: List[SessionParser]) -> Tuple[str, Optional[Path]]:
    """Ask where file exports should be written.

    Returns ('script', path), ('custom', path), or ('project', None). Project
    mode resolves per session so multi-project exports can stay with each cwd.
    """
    default_dir = script_directory()
    project_dirs = available_project_dirs(parsers)

    while True:
        print(f"\n  {Style.BOLD}Save Location:{Style.RESET}")
        print(f"    {Style.YELLOW}[S]{Style.RESET}cript directory       {Style.DIM}{default_dir} [Default]{Style.RESET}")
        if project_dirs:
            if len(project_dirs) == 1:
                print(f"    {Style.YELLOW}[P]{Style.RESET}roject directory      {Style.DIM}{project_dirs[0]}{Style.RESET}")
            else:
                print(f"    {Style.YELLOW}[P]{Style.RESET}roject directories    {Style.DIM}save each session beside its own project ({len(project_dirs)} folders){Style.RESET}")
        else:
            print(f"    {Style.DIM}[P]roject directory      unavailable; no selected session cwd exists locally{Style.RESET}")
        print(f"    {Style.YELLOW}[C]{Style.RESET}ustom directory")

        choice = input(f"\n  {Style.BOLD}Select > {Style.RESET}").strip().lower()
        if not choice:
            choice = 's'
        if choice == 's':
            return 'script', default_dir
        if choice == 'p':
            if project_dirs:
                return 'project', None
            print(f"  {Style.warn('No usable project directory was found for the selected session(s).')}")
            continue
        if choice == 'c':
            custom_dir = prompt_custom_output_dir()
            if custom_dir is not None:
                return 'custom', custom_dir
            continue
        print(f"  {Style.error('Choose S, P, or C.')}")

def output_dir_for_parser(parser: SessionParser, save_mode: str,
                          selected_dir: Optional[Path], fallback_dir: Path) -> Tuple[Path, bool]:
    if save_mode == 'project':
        project_dir = project_dir_for_parser(parser)
        if project_dir is not None:
            return project_dir, False
        return fallback_dir, True
    return selected_dir or fallback_dir, False

def next_available_path(path: Path) -> Path:
    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1

def resolve_output_path_conflict(path: Path) -> Tuple[Optional[Path], str]:
    if not path.exists():
        return path, 'new'

    print(f"\n  {Style.warn(f'File already exists: {path}')}")
    print(f"    {Style.YELLOW}[O]{Style.RESET}verwrite  {Style.DIM}[Default]{Style.RESET}")
    print(f"    {Style.YELLOW}[R]{Style.RESET}ename")
    print(f"    {Style.YELLOW}[S]{Style.RESET}kip")

    while True:
        choice = input(f"\n  {Style.BOLD}Select > {Style.RESET}").strip().lower()
        if not choice:
            choice = 'o'
        if choice == 'o':
            return path, 'overwrite'
        if choice == 'r':
            return next_available_path(path), 'rename'
        if choice == 's':
            return None, 'skip'
        print(f"  {Style.error('Choose O, R, or S.')}")

def file_manager_name() -> str:
    if sys.platform == 'win32':
        return "File Explorer"
    if sys.platform == 'darwin':
        return "Finder"
    return "file manager"

def open_file_default(target: Path) -> bool:
    import shutil
    import subprocess

    try:
        if sys.platform == 'win32':
            os.startfile(str(target))  # type: ignore[attr-defined]
            return True
        if sys.platform == 'darwin':
            subprocess.Popen(['open', str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True

        opener = shutil.which('xdg-open') or shutil.which('gio') or shutil.which('gnome-open') or shutil.which('kde-open')
        if not opener:
            return False
        if Path(opener).name == 'gio':
            subprocess.Popen([opener, 'open', str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen([opener, str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False

def open_in_file_manager(target: Path) -> bool:
    import shutil
    import subprocess

    try:
        if sys.platform == 'win32':
            if target.is_file():
                subprocess.Popen(['explorer', f'/select,{str(target)}'])
            else:
                os.startfile(str(target))  # type: ignore[attr-defined]
            return True
        if sys.platform == 'darwin':
            args = ['open', '-R', str(target)] if target.is_file() else ['open', str(target)]
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True

        directory = target.parent if target.is_file() else target
        opener = shutil.which('xdg-open') or shutil.which('gio') or shutil.which('gnome-open') or shutil.which('kde-open')
        if not opener:
            return False
        if Path(opener).name == 'gio':
            subprocess.Popen([opener, 'open', str(directory)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen([opener, str(directory)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False

def prompt_open_saved_outputs(saved_paths: List[Path]) -> bool:
    if not saved_paths:
        return False

    unique_dirs: List[Path] = []
    seen: Set[str] = set()
    for path in saved_paths:
        directory = path.parent
        key = os.path.normcase(str(directory))
        if key not in seen:
            seen.add(key)
            unique_dirs.append(directory)

    manager = file_manager_name()
    while True:
        print(f"\n  {Style.BOLD}Open Saved Output:{Style.RESET}")
        if len(saved_paths) == 1:
            print(f"    {Style.YELLOW}[F]{Style.RESET}ile       {Style.DIM}open Markdown file in the default app{Style.RESET}")
        else:
            print(f"    {Style.YELLOW}[F]{Style.RESET}irst file {Style.DIM}open the first saved Markdown file{Style.RESET}")
        if len(unique_dirs) == 1:
            print(f"    {Style.YELLOW}[D]{Style.RESET}irectory  {Style.DIM}show the output folder in {manager}{Style.RESET}")
        else:
            print(f"    {Style.YELLOW}[D]{Style.RESET}irectories {Style.DIM}open {len(unique_dirs)} output folders in {manager}{Style.RESET}")
        print(f"    {Style.YELLOW}[N]{Style.RESET}one       {Style.DIM}[Default]{Style.RESET}")
        choice = input(f"\n  {Style.BOLD}Select > {Style.RESET}").strip().lower()
        if not choice:
            choice = 'n'
        if choice == 'n':
            return False
        if choice == 'f':
            target = saved_paths[0]
            if open_file_default(target):
                print(f"  {Style.GREEN}➜{Style.RESET} Opened: {target}")
                return True
            else:
                print(f"  {Style.warn(f'Could not open {target}')}")
                return False
        if choice == 'd':
            opened = 0
            for target in unique_dirs:
                if open_in_file_manager(target):
                    opened += 1
                else:
                    print(f"  {Style.warn(f'Could not open {target}')}")
            if opened:
                print(f"  {Style.GREEN}➜{Style.RESET} Opened {opened} location{'s' if opened != 1 else ''}.")
            return opened > 0
        print(f"  {Style.error('Choose F, D, or N.')}")

def convert_files(valid_files: List[Path]):
    """Run the full extraction/filter/export flow for a set of session files.

    Selection parsing now happens in the browser (interactive_loop); this
    routine receives already-resolved Paths."""
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

    # Extraction scope (Last N Turns / Live Context)
    scope_type, turn_limit = select_extraction_scope(parsers)
    scope_label = ""
    if scope_type == 'last_n':
        for p in parsers:
            p.trim_to_last_n_turns(turn_limit)
        scope_label = f"last {turn_limit} turn{'s' if turn_limit != 1 else ''}"
    elif scope_type == 'live':
        for p in parsers:
            p.trim_to_live_context()
        scope_label = "live context"

    section_filter, clean_content, output_cap, user_cap, agent_cap, reason_cap, internal_cap = interactive_filter(parsers, scope_label=scope_label)

    # Check anything is selected
    if not any(section_filter.values()):
        print(Style.warn("Nothing selected — skipping export."))
        input(f"\n{Style.DIM}Press Enter to return to menu...{Style.RESET}")
        return

    _clear_screen()
    
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

    save_mode = 'clipboard'
    selected_out_dir: Optional[Path] = None
    fallback_out_dir = script_directory()
    if dest_choice in ('f', 'b'):
        save_mode, selected_out_dir = select_save_location(parsers)

    # Export
    print(f"\n{Style.info(f'Processing {len(parsers)} session(s)...')}")

    clipboard_md = []
    saved_paths: List[Path] = []
    project_fallback_reported = False

    for parser in parsers:
        try:
            md_content = parser.to_markdown(
                section_filter=section_filter,
                clean_content=clean_content,
                output_cap=output_cap,
                user_cap=user_cap,
                agent_cap=agent_cap,
                reasoning_cap=reason_cap,
                internal_cap=internal_cap,
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
                out_dir, used_project_fallback = output_dir_for_parser(
                    parser, save_mode, selected_out_dir, fallback_out_dir
                )
                if used_project_fallback and not project_fallback_reported:
                    print(f"  {Style.warn(f'Some selected sessions do not have a usable local project directory; using {fallback_out_dir}.')}")
                    project_fallback_reported = True
                out_path = out_dir / out_filename
                resolved_path, conflict_action = resolve_output_path_conflict(out_path)
                if resolved_path is None:
                    print(f"  {Style.YELLOW}➜{Style.RESET} Skipped existing file: {out_path}")
                    continue
                out_path = resolved_path
                with open(out_path, 'w', encoding='utf-8') as outfile:
                    outfile.write(md_content)
                saved_paths.append(out_path)
                action_label = ""
                if conflict_action == 'overwrite':
                    action_label = f" {Style.DIM}(overwritten){Style.RESET}"
                elif conflict_action == 'rename':
                    action_label = f" {Style.DIM}(renamed){Style.RESET}"
                print(f"  {Style.GREEN}➜{Style.RESET} Saved: {out_path}  "
                      f"{Style.CYAN}({line_count:,} lines){Style.RESET}{action_label}")
                
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

    prompt_open_saved_outputs(saved_paths)

    input(f"\n{Style.DIM}Press Enter to return to menu...{Style.RESET}")

SESSIONS_PER_PAGE = 12

def _search_terms_for_session_query(query: str) -> Set[str]:
    raw = query.strip().strip('"').strip("'").lower()
    terms: Set[str] = set()
    if not raw:
        return terms

    def add(value: str):
        value = value.strip().lower()
        if value:
            terms.add(value)

    add(raw)
    leaf = re.split(r'[\\/]', raw)[-1]
    add(leaf)
    if leaf.endswith('.jsonl'):
        leaf = leaf[:-6]
        add(leaf)
    if leaf.startswith("rollout-"):
        add(leaf[len("rollout-"):])
    for match in ROLLOUT_ID_RE.findall(raw):
        add(match)
    return {term for term in terms if len(term) >= 6}

def find_records_by_session_id(records: List[SessionRecord], query: str) -> List[SessionRecord]:
    needles = _search_terms_for_session_query(query)
    if not needles:
        return []

    exact_ids = {needle for needle in needles if ROLLOUT_ID_RE.fullmatch(needle)}
    if exact_ids:
        primary_matches = [
            rec for rec in records
            if rec.session_id.lower() in exact_ids
        ]
        if primary_matches:
            return primary_matches

    matches: List[SessionRecord] = []
    for rec in records:
        stem = rec.path.stem.lower()
        candidates = {alias.lower() for alias in rec.aliases}
        candidates.update({
            rec.session_id.lower(),
            stem,
            rec.path.name.lower(),
            stem[len("rollout-"):] if stem.startswith("rollout-") else stem,
            str(rec.path).lower(),
        })
        if any(
            candidate == needle or candidate.startswith(needle) or needle in candidate
            for needle in needles
            for candidate in candidates
        ):
            matches.append(rec)
    return matches

def print_session_details(rec: SessionRecord):
    title = get_session_preview_title(rec.path)
    try:
        size_label = format_size(rec.path.stat().st_size)
    except OSError:
        size_label = "?"
    dt_str = datetime.fromtimestamp(rec.mtime).strftime("%Y-%m-%d %H:%M")

    print(f"\n  {Style.BOLD}SESSION FOUND{Style.RESET}")
    print(f"  {Style.DIM}{'━' * 62}{Style.RESET}")
    print(f"  {Style.BOLD}Title:{Style.RESET}      {title}")
    print(f"  {Style.BOLD}Session ID:{Style.RESET} {rec.session_id}")
    related = sorted(
        alias for alias in rec.aliases
        if alias != rec.session_id and ROLLOUT_ID_RE.fullmatch(alias)
    )
    if related:
        print(f"  {Style.BOLD}Related IDs:{Style.RESET} {', '.join(related)}")
    print(f"  {Style.BOLD}Project:{Style.RESET}    {rec.pname}")
    print(f"  {Style.BOLD}Workspace:{Style.RESET}  {rec.cwd or '(unknown)'}")
    print(f"  {Style.BOLD}Date:{Style.RESET}       {dt_str}")
    print(f"  {Style.BOLD}Size:{Style.RESET}       {size_label}")
    print(f"  {Style.BOLD}File:{Style.RESET}       {rec.path}")
    print(f"  {Style.DIM}{'━' * 62}{Style.RESET}")

def find_session_by_id_flow(records: List[SessionRecord]):
    _clear_screen()
    print(f"\n  {Style.BOLD}{Style.HEADER}FIND SESSION BY ID{Style.RESET}\n")
    print(f"  {Style.DIM}Paste the full session ID, rollout file name, or a long ID prefix.{Style.RESET}")
    query = input(f"\n  {Style.BOLD}Session ID > {Style.RESET}").strip()
    if not query:
        return
    matches = find_records_by_session_id(records, query)

    if not matches:
        print(f"\n  {Style.warn('No matching session found. Use at least 6 characters from the session ID.')}")
        input(f"\n{Style.DIM}Press Enter to return to menu...{Style.RESET}")
        return

    if len(matches) == 1:
        rec = matches[0]
        print_session_details(rec)
        if prompt_yes_no("Export this session now?", default=True):
            convert_files([rec.path])
        return

    print(f"\n  {Style.BOLD}{len(matches)} matches found{Style.RESET}  {Style.DIM}(showing newest first){Style.RESET}\n")
    matches = sorted(matches, key=lambda r: r.mtime, reverse=True)
    shown = matches[:SESSIONS_PER_PAGE]
    render_session_table(shown, start_no=1, global_latest=records[0].path if records else None, show_project=True)
    if len(matches) > len(shown):
        print(f"  {Style.DIM}Narrow the ID to see the remaining {len(matches) - len(shown)} matches.{Style.RESET}")
    choice = input(f"\n  {Style.BOLD}Select a session number to export, or Enter to cancel > {Style.RESET}").strip()
    if not choice:
        return
    selected = _parse_row_selection(choice, [r.path for r in shown])
    if selected:
        convert_files(selected)
    else:
        input(f"\n{Style.DIM}Press Enter to return to menu...{Style.RESET}")


def interactive_loop():
    """Paginated browser with two view modes:

      • ALL SESSIONS  — flat, newest-first list (with a project column)
      • PROJECTS      — sessions grouped by working directory; drill into one

    Toggle with [m]. Page with ←/→ (or ↑/↓, or [n]/[p]). Sessions are scanned
    once and reused. Navigation is single-keypress; type a number to select.
    """
    records = scan_sessions()
    if not records:
        print_menu_header()
        print(Style.error(f"No sessions found in {SESSIONS_DIR}"))
        print(Style.info("Check CODEX_HOME environment variable."))
        sys.exit(1)
    projects = group_by_project(records)
    global_latest = records[0].path

    screen = 'all'          # 'all' | 'project_list' | 'project_sessions'
    page = 0
    cur_project: Optional[Dict] = None

    def total_pages_for(n: int) -> int:
        return max(1, (n + SESSIONS_PER_PAGE - 1) // SESSIONS_PER_PAGE)

    def pause(msg: str = "Press Enter..."):
        input(f"{Style.DIM}{msg}{Style.RESET}")

    while True:
        print_menu_header()

        # ───────────────── ALL SESSIONS (flat) ─────────────────
        if screen == 'all':
            total = len(records)
            total_pages = total_pages_for(total)
            page = max(0, min(page, total_pages - 1))
            start = page * SESSIONS_PER_PAGE
            page_recs = records[start:start + SESSIONS_PER_PAGE]
            page_paths = [r.path for r in page_recs]

            print(f"  {Style.BOLD}{Style.HEADER}ALL SESSIONS{Style.RESET}   "
                  f"{Style.DIM}{total} sessions · {len(projects)} projects{Style.RESET}\n")
            render_session_table(page_recs, start_no=1,
                                 global_latest=global_latest, show_project=True)
            _print_pager(page, total_pages, total, "sessions")

            print(f"\n{Style.BOLD}OPTIONS:{Style.RESET}  {Style.DIM}(single keypress; type a number to select){Style.RESET}")
            print(f"  {Style.GREEN}[#, #]{Style.RESET}      Convert sessions on this page (e.g. '1, 3')")
            print(f"  {Style.YELLOW}[a]{Style.RESET}         Convert all sessions on this page")
            print(f"  {Style.YELLOW}[s]{Style.RESET}         Find a session by ID")
            print(f"  {Style.CYAN}[←/→]{Style.RESET} or {Style.CYAN}[n/p]{Style.RESET}  Next / previous page")
            print(f"  {Style.BLUE}[m]{Style.RESET}         Switch to PROJECT view")
            print(f"  {Style.RED}[q]{Style.RESET}         Quit")

            action, value = _read_browser_action(f"\n{Style.BOLD}Select > {Style.RESET}")
            if action == 'cmd' and value == 'Q':
                print("Bye."); sys.exit(0)
            elif action == 'cmd' and value == 'M':
                screen = 'project_list'; page = 0
            elif action == 'cmd' and value == 'S':
                find_session_by_id_flow(records)
            elif action == 'next':
                if page < total_pages - 1:
                    page += 1
            elif action == 'prev':
                if page > 0:
                    page -= 1
            elif action == 'cmd' and value == 'A':
                if page_paths:
                    confirm = input(f"{Style.warn(f'Convert all {len(page_paths)} sessions on this page? (y/n): ')}")
                    if confirm.strip().lower() == 'y':
                        convert_files(page_paths)
            elif action == 'select':
                sel = _parse_row_selection(value, page_paths)
                if sel:
                    convert_files(sel)
                else:
                    pause()
            continue

        # ───────────────── PROJECT LIST ─────────────────
        if screen == 'project_list':
            total = len(projects)
            total_pages = total_pages_for(total)
            page = max(0, min(page, total_pages - 1))
            start = page * SESSIONS_PER_PAGE
            page_projs = projects[start:start + SESSIONS_PER_PAGE]

            print(f"  {Style.BOLD}{Style.HEADER}PROJECTS{Style.RESET}   "
                  f"{Style.DIM}{total} projects · {len(records)} sessions{Style.RESET}\n")
            render_project_table(page_projs, records, start_no=1)
            _print_pager(page, total_pages, total, "projects")

            print(f"\n{Style.BOLD}OPTIONS:{Style.RESET}  {Style.DIM}(single keypress; type a number to open){Style.RESET}")
            print(f"  {Style.GREEN}[#]{Style.RESET}         Open a project to view its sessions")
            print(f"  {Style.YELLOW}[s]{Style.RESET}         Find a session by ID")
            print(f"  {Style.CYAN}[←/→]{Style.RESET} or {Style.CYAN}[n/p]{Style.RESET}  Next / previous page")
            print(f"  {Style.BLUE}[m]{Style.RESET}         Switch to ALL-SESSIONS view")
            print(f"  {Style.RED}[q]{Style.RESET}         Quit")

            action, value = _read_browser_action(f"\n{Style.BOLD}Select > {Style.RESET}")
            if action == 'cmd' and value == 'Q':
                print("Bye."); sys.exit(0)
            elif action == 'cmd' and value == 'M':
                screen = 'all'; page = 0
            elif action == 'cmd' and value == 'S':
                find_session_by_id_flow(records)
            elif action == 'next':
                if page < total_pages - 1:
                    page += 1
            elif action == 'prev':
                if page > 0:
                    page -= 1
            elif action == 'select':
                digits = value.strip()
                if digits.isdigit():
                    n = int(digits)
                    if 1 <= n <= len(page_projs):
                        cur_project = page_projs[n - 1]
                        screen = 'project_sessions'; page = 0
                    else:
                        print(Style.warn(f"Project {n} is not on this page (1-{len(page_projs)})."))
                        pause()
                else:
                    print(Style.warn("Enter a single project number to open it."))
                    pause()
            continue

        # ───────────────── SESSIONS WITHIN A PROJECT ─────────────────
        if screen == 'project_sessions':
            proj_recs = [records[i] for i in cur_project['idxs']]
            total = len(proj_recs)
            total_pages = total_pages_for(total)
            page = max(0, min(page, total_pages - 1))
            start = page * SESSIONS_PER_PAGE
            page_recs = proj_recs[start:start + SESSIONS_PER_PAGE]
            page_paths = [r.path for r in page_recs]

            print(f"  {Style.BOLD}{Style.HEADER}PROJECT:{Style.RESET} {Style.BOLD}{cur_project['pname']}{Style.RESET}   "
                  f"{Style.DIM}{total} session{'s' if total != 1 else ''}{Style.RESET}")
            print(f"  {Style.DIM}{cur_project['cwd'] or '(unknown path)'}{Style.RESET}\n")
            render_session_table(page_recs, start_no=1,
                                 global_latest=global_latest, show_project=False)
            _print_pager(page, total_pages, total, "sessions")

            print(f"\n{Style.BOLD}OPTIONS:{Style.RESET}  {Style.DIM}(single keypress; type a number to select){Style.RESET}")
            print(f"  {Style.GREEN}[#, #]{Style.RESET}      Convert sessions on this page (e.g. '1, 3')")
            print(f"  {Style.YELLOW}[a]{Style.RESET}         Convert all sessions on this page")
            print(f"  {Style.YELLOW}[s]{Style.RESET}         Find a session by ID")
            print(f"  {Style.CYAN}[←/→]{Style.RESET} or {Style.CYAN}[n/p]{Style.RESET}  Next / previous page")
            print(f"  {Style.BLUE}[b]{Style.RESET}         Back to project list")
            print(f"  {Style.RED}[q]{Style.RESET}         Quit")

            action, value = _read_browser_action(f"\n{Style.BOLD}Select > {Style.RESET}")
            if action == 'cmd' and value == 'Q':
                print("Bye."); sys.exit(0)
            elif action == 'cmd' and value == 'B':
                screen = 'project_list'; page = 0; cur_project = None
            elif action == 'cmd' and value == 'M':
                screen = 'all'; page = 0; cur_project = None
            elif action == 'cmd' and value == 'S':
                find_session_by_id_flow(records)
            elif action == 'next':
                if page < total_pages - 1:
                    page += 1
            elif action == 'prev':
                if page > 0:
                    page -= 1
            elif action == 'cmd' and value == 'A':
                if page_paths:
                    confirm = input(f"{Style.warn(f'Convert all {len(page_paths)} sessions on this page? (y/n): ')}")
                    if confirm.strip().lower() == 'y':
                        convert_files(page_paths)
            elif action == 'select':
                sel = _parse_row_selection(value, page_paths)
                if sel:
                    convert_files(sel)
                else:
                    pause()
            continue

if __name__ == "__main__":
    try:
        interactive_loop()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(0)
