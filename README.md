<div align="center">
  <img src="banner.png" alt="Codex Session Export Preview" width="100%" style="max-width: 800px; border-radius: 12px; margin-bottom: 20px;">
</div>

# Codex Session Export ⚡️

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

- **20 Filterable Sections:** Toggle everything from User/Agent messages to hidden agent reasoning, terminal commands, MCP tool calls, git snapshots, and more.
- **Parallel Exports:** Select and process multiple sessions at the same time. The filter will show you the combined line counts and will export them simultaneously in one batch.
- **Dynamic Output Capping:** Terminal payloads can be hundreds of thousands of lines long. Instantly cap output blocks to exactly 1, 5, 8, 10, or up to 500 lines to keep your context windows lean.
- **Granular Message Filtering *(New in v2.1)*:** Independently control how many of the last N blocks you want from each message type — 👤 User, 🤖 Agent, 🧠 Agent Reasoning, and 🔒 Internal Reasoning — all separately adjustable with `◀`/`▶`. Each defaults to showing all, so you only cut what you need.
- **"Clean Chat" Mode:** Instantly strips messy IDE background data, active-file streams, and open-tab XML that the agent silently attaches to your prompt, leaving just your actual words.
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

1. **Select a session:** The script automatically scans `~/.codex/sessions` and presents a chronological list of your recent threads. Type the ID of the session (e.g., `1`) or select multiple (e.g., `1,2,3`).
2. **Filter & Refine:** 
   * `↑` / `↓` - Navigate the filter list
   * `Enter` / `Space` - Toggle a section ON/OFF
   * `◀` / `▶` on **💻 Terminal Output Cap** - Adjust max lines per terminal/tool output block
   * `◀` / `▶` on **👤 User Message Cap** - Keep only the last N user messages
   * `◀` / `▶` on **🤖 Agent Message Cap** - Keep only the last N agent responses
   * `◀` / `▶` on **🧠 Agent Reasoning Cap** - Keep only the last N agent reasoning blocks
   * `◀` / `▶` on **🔒 Internal Reasoning Cap** - Keep only the last N internal reasoning blocks
   * `1`-`7` - Load presets
3. **Export Destination:** Press `Q` when you're ready, and you will be asked where to send the output:
   * **[F]ile:** Save directly to a `.md` file in the current directory (Default).
   * **[C]lipboard:** Instantly copy the raw Markdown so you can paste it straight into ChatGPT or Claude.
   * **[B]oth:** Save to disk *and* copy to clipboard simultaneously.

---

## 📈 Activity & Growth

[![Star History Chart](https://api.star-history.com/svg?repos=mexenon/codex-session-export&type=Date&theme=dark)](https://star-history.com/#mexenon/codex-session-export&Date)

---

### Why build this?

When pushing AI to its limits, the conversation log becomes your most valuable codebase asset. This tool guarantees you have complete ownership, visibility, and control over that data.

*If this tool saved your context window (or your sanity), **please give it a ⭐️!***
