<div align="center">
  <img src="banner.png" alt="Codex Session Export Preview" width="100%" style="max-width: 800px; border-radius: 12px; margin-bottom: 20px;">
</div>

# Codex (ChatGPT) Session Export ⚡️

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.x](https://img.shields.io/badge/python-3.x-blue.svg)](https://www.python.org/)
[![Visitors](https://komarev.com/ghpvc/?username=mexenon-codexsessionexport&label=visitors&color=1d70b8&style=flat)](https://github.com/MeXenon/codex-session-export)

**The ultimate, terminal-native export pipeline for Codex.**

If you find this tool useful, **please consider leaving a ⭐️ on this repository!** It helps others find the project.

---

## 🛑 The Problem

When working with advanced agentic AI models (like Codex, ChatGPT, or Claude), they sometimes hallucinate, make mistakes, or lose track of their context. When things go wrong—or when you pull off an incredibly complex workflow—you need to analyze *exactly* what happened.

You might need to:
- Feed the session into another AI model for debugging or code review.
- Extract just the terminal commands to create an automated script.
- Read through the agent's hidden internal reasoning.
- Strip away thousands of lines of verbose tool outputs to see the actual conversation.

Currently, there’s no official feature built for this granular level of control. If you just export raw logs, you get a massive, unreadable wall of text. 

## 💡 The Solution

**Codex Session Manager** (`codex-md.py`) is designed for maximum flexibility. It parses your local `.jsonl` session logs and provides a beautiful, interactive, fullscreen terminal UI (TUI) to dynamically filter exactly what you want to export into clean Markdown. 

Because it’s a pure Python CLI tool, it's completely **portable**. You can use it locally on your laptop, or run it headlessly on a remote VPS. It is 100% compatible with the Codex CLI extension and app.

---

## ✨ Features

- **Current Codex Tool Compatibility *(New in v2.6.4)*:** Terminal commands and outputs are recognized across legacy `function_call` rollouts, Code Mode `exec` wrappers, legacy `exec_command_end` events, and current `item_completed → CommandExecution` records. Code Mode MCP and other tool wrappers are also routed to their correct sections instead of being mislabeled as patches. Canonical command records automatically replace terminal wrapper fallbacks so exports stay complete without duplicates.
- **Project View & Pagination *(New in v2.6)*:** Just like Codex Desktop, your sessions are now grouped by **project** (their working directory). Toggle between a flat **All Sessions** list and a **Projects** view with a single key, drill into any project to see only its threads, and page through large histories cleanly with `n`/`p` — no more giant unreadable walls of sessions.
- **Find by Session ID:** Paste a full session ID, rollout filename, or long ID prefix to jump straight to the matching session and see its title, workspace, date, size, and source file before exporting.
- **20 Filterable Sections:** Toggle everything from User/Agent messages to hidden agent reasoning, terminal commands, MCP tool calls, git snapshots, and more.
- **Parallel Exports:** Select and process multiple sessions at the same time. The filter will show you the combined line counts and will export them simultaneously in one batch.
- **Dynamic Output Capping:** Terminal payloads can be hundreds of thousands of lines long. Instantly cap output blocks to exactly 1, 5, 8, 10, or up to 500 lines to keep your context windows lean.
- **Granular Message Filtering:** Independently control how many of the last N blocks you want from each message type — 👤 User, 🤖 Agent, 🧠 Agent Reasoning, and 🔒 Internal Reasoning — all separately adjustable with `◀`/`▶`. Each defaults to showing all, so you only cut what you need.
- **Last N Turns *(New in v2.4)*:** Don't need the full session? Select only the most recent N turns to export. A "turn" is one user message plus all the agent work that followed (reasoning, tool calls, outputs, responses). Perfect for extracting just the last few interactions without wading through the entire history.
- **Live Context *(New in v2.5)*:** Want to see *exactly* what the LLM is seeing right now? The "Live Context" option parses the raw JSON logs to reverse-engineer Codex's memory state. It dynamically tracks the model's exact context limit (e.g. 258k), displays real-time token usage, resolves all your `Undo`/`Rollback` actions, and processes session compactions so you export the strict active memory window.
- **"Clean Chat" Mode:** Instantly strips messy IDE background data, active-file streams, and open-tab XML that the agent silently attaches to your prompt, leaving just your actual words.
- **Save Location Prompt:** When exporting to a file, choose the script directory, the selected session's project directory, or a custom folder, then optionally reveal the saved output in File Explorer, Finder, or your Linux file manager.
- **Overwrite Protection:** If an output file already exists, choose overwrite, rename, or skip. Overwrite is the default.
- **7 Built-in Presets:** Jump straight to "Chat Only", "Terminal Only", "Outputs Only", or "Full Export" with a single keystroke.
- **Real-Time Context Math:** See exactly how many lines you are selecting *before* you export, complete with a live progress bar.

---

## 🛠 Usage

No complex dependencies. Just download and run the script using Python.

### 🚀 Quick Start (One-Liner)
Run the software instantly without manually cloning the repo:

**🐧 Linux / 🍎 macOS:**
```bash
curl -sO https://raw.githubusercontent.com/MeXenon/codex-session-export/main/codex-md.py && python3 codex-md.py
```

**🪟 Windows — Command Prompt:**
```cmd
curl -sO https://raw.githubusercontent.com/MeXenon/codex-session-export/main/codex-md.py && python codex-md.py
```

**🪟 Windows — PowerShell 7:**
```powershell
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/MeXenon/codex-session-export/main/codex-md.py" -OutFile "codex-md.py"; python codex-md.py
```

### 💻 Manual Run
If you prefer to download or clone the file manually:

**Linux / macOS:**
```bash
python3 codex-md.py
```

**Windows:**
```cmd
python codex-md.py
```

### The Interface

1. **Browse & select a session:** The script scans `~/.codex/sessions` once and opens a paginated browser with two view modes:
   * **All Sessions** *(default)* — a flat, newest-first list of every thread, with a **Project** column so you can see at a glance which working directory each one belongs to.
   * **Projects** — your sessions grouped by **project** (working directory), just like Codex Desktop. Each row shows the project name, how many sessions it has, and when it was last active. Open a project (type its number) to drill into just that project's threads.

   Navigation (single keypress — no Enter needed, except when typing numbers):
   * `←` / `→` (or `↑` / `↓`, or `n` / `p`) — previous / next page.
   * **Type a number** — convert the session(s) on the **current page** (e.g. `1`, or `1, 3, 5`; numbers are page-local, press Enter to confirm). In the **Projects** view a number instead opens that project.
   * `a` — convert every session shown on the current page.
   * `s` — find a session by session ID, rollout filename, or long ID prefix.
   * `m` — toggle between **All Sessions** and **Projects** views.
   * `b` — back to the project list (when you're inside a project).
   * `q` — quit.
2. **Choose extraction scope:**
   * **[F] Full Session** — Export all turns (default).
   * **[L] Last N Turns** — Enter a number and only the most recent N turns are included.
   * **[C] Live Context** — Parses the log to extract the exact active context window (automatically resolving undo rollbacks and session compactions). Token usage relative to the model's limit (e.g. `197k/258k`) is displayed dynamically in the UI.
3. **Filter & Refine:** 
   * `↑` / `↓` - Navigate the filter list
   * `Enter` / `Space` - Toggle a section ON/OFF
   * `◀` / `▶` on **💻 Terminal Output Cap** - Adjust max lines per terminal/tool output block
   * `◀` / `▶` on **👤 User Message Cap** - Keep only the last N user messages
   * `◀` / `▶` on **🤖 Agent Message Cap** - Keep only the last N agent responses
   * `◀` / `▶` on **🧠 Agent Reasoning Cap** - Keep only the last N agent reasoning blocks
   * `◀` / `▶` on **🔒 Internal Reasoning Cap** - Keep only the last N internal reasoning blocks
   * `1`-`7` - Load presets
4. **Export Destination:** Press `Q` when you're ready, and you will be asked where to send the output:
   * **[F]ile:** Save directly to a `.md` file.
   * **[C]lipboard:** Instantly copy the raw Markdown so you can paste it straight into ChatGPT or Claude.
   * **[B]oth:** Save to disk *and* copy to clipboard simultaneously.
5. **Save Location:** If you choose **File** or **Both**, choose where the `.md` file should go:
   * **[S]cript directory:** Save beside `codex-md.py` (Default).
   * **[P]roject directory:** Save into the selected session's recorded project `cwd`, when that folder exists locally. Multi-project exports save each session beside its own project.
   * **[C]ustom directory:** Enter any folder path and create it if needed.
6. **Existing Files:** If the file already exists, choose:
   * **[O]verwrite:** Replace the existing file (Default).
   * **[R]ename:** Save with a suffix like `-2.md`.
   * **[S]kip:** Leave the existing file unchanged.
7. **Open Output:** After saving, choose whether to open the Markdown file in the default app, show the output folder in File Explorer/Finder/Linux file manager, or do nothing.

---

## 📈 Activity & Growth

[![Star History Chart](https://api.star-history.com/svg?repos=mexenon/codex-session-export&type=Date&theme=dark)](https://star-history.com/#mexenon/codex-session-export&Date)

---

### Why build this?

When pushing AI to its limits, the conversation log becomes your most valuable codebase asset. This tool guarantees you have complete ownership, visibility, and control over that data.

*Note: In previous versions, selecting 'Full Session' could sometimes cause the program to crash on very large JSON logs. This has been fully resolved.*

*If this tool saved your context window (or your sanity), **please give it a ⭐️!***
