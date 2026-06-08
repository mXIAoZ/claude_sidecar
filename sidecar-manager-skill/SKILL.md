---
name: sidecar-manager
description: Safe operator workflow for this Claude Code compact sidecar: diagnostics, rehearsals, confirmed setup/uninstall, LLM config, and troubleshooting.
---

# Sidecar Manager

Use this skill to manage the Claude Code compact sidecar in this repository. Treat the existing CLI as the only runtime executor: this skill chooses and explains commands, but real writes must go through the `compact_sidecar.cli`, `compact_sidecar.services.daemon`, `compact_sidecar.hooks.install`, or `compact_sidecar.services.auto_compact_controller` safety gates. In a source checkout, run them with `PYTHONPATH=src python3 -m ...`; installed packages expose `sidecar` and `sidecar-mcp`.

## Trigger Phrases

Use this skill when the user asks to:

- check sidecar status, dashboard, compact readiness, or operation logs
- rehearse setup, daemon plist generation, hook installation, or uninstall
- install/remove sidecar hooks for this project
- configure daemon LLM summary environment variables
- explain sidecar architecture, safety boundaries, or troubleshooting steps
- package or prepare the sidecar Skill/MCP workflow

## Operator Menu

```text
Sidecar Manager Menu
1. Status check             Read current sidecar/runtime status
2. Dashboard snapshot       Generate a read-only dashboard JSON snapshot
3. Run tests                Run the project test suite
4. Setup rehearsal          Preview generated hooks/plist using temporary files
5. Install project hooks    Confirmed write to project-local settings
6. Remove project hooks     Confirmed removal from project-local settings
7. Daemon rehearsal         Generate and inspect a temporary launchd plist
8. LLM config menu          Explain and validate LLM configuration
9. Prepare LLM env config   Produce a redacted env/settings plan for review
10. LLM config check        Validate current LLM environment/config
11. Auto install sidecar    Confirmed install: hooks, optional LLM env, optional daemon plist
12. Uninstall sidecar       Confirmed uninstall: hooks and optional daemon artifact
13. Explain architecture    Summarize modules and runtime contract
```

Users may choose by number or natural language, for example:

```text
/sidecar-manager 1
/sidecar-manager setup rehearsal
/sidecar-manager install project hooks
/sidecar-manager auto install sidecar with daemon plist but no launchctl
```

## Safety Model

- Read-only operations can run directly when requested: options 1, 2, 3, 8, 10, and 13.
- Rehearsal operations can run directly because they use temporary paths: options 4 and 7.
- Change operations require a clear user request or confirmation: options 5, 6, 9, 11, and 12.
- Before option 11 or 12, present a one-line summary of the settings path, runtime dir, plist path, and whether launchctl/tmux/network will be used.
- Do not edit `~/.claude/settings.json` unless the user explicitly asks for a global install. Default to `.claude/settings.local.json`.
- Do not print API key values, raw prompts, or raw summaries. Show variable names and `<set>` markers only.
- Hook stdout must remain Claude Code hook JSON only; diagnostics belong in `errors.log`.
- Hooks must not call LLMs, start background processes, invoke launchctl, or send tmux keys.
- Raw prompt/summary logging is sensitive and opt-in only through existing flags such as `--show-content`, `--log-raw-prompt`, or `SIDECAR_LOG_RAW_SUMMARY=1`.

## Default Targets

Use these project-local defaults unless the user says otherwise:

```text
settings: .claude/settings.local.json
runtime:  .memory
plist:    ~/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist
```

Use temporary paths for rehearsals:

```bash
tmp=$(mktemp -d)
```

## Decision Tree

1. If the user asks "is it working?" or "what is the status?", run option 1.
2. If the user asks for a safer preview before setup/uninstall, run option 4 or 7.
3. If the user asks to install only hooks, run option 5 after confirming project-local settings.
4. If the user asks for persistent daemon behavior, write/inspect the plist first, then ask before launchctl lifecycle commands.
5. If the user asks for LLM summaries, explain that only daemon summary generation calls the LLM; hooks/status/dashboard do not.
6. If the user asks to uninstall, prefer project-local hook removal first; remove daemon artifacts only with an explicit plist path.

## Option 1: Status Check

Read-only, no settings writes, no launchctl, no tmux, no network:

```bash
PYTHONPATH=src python3 -m compact_sidecar.cli status --json
```

With explicit daemon plist artifact inspection:

```bash
plist="$HOME/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist"; PYTHONPATH=src python3 -m compact_sidecar.cli status --json --plist-path "$plist"
```

## Option 2: Dashboard Snapshot

Read-only isolated snapshot:

```bash
tmp=$(mktemp -d); SIDECAR_COMPACT_DIR="$tmp/runtime" PYTHONPATH=src python3 -m compact_sidecar.ui.dashboard --json
```

For real project runtime, omit the temporary override:

```bash
PYTHONPATH=src python3 -m compact_sidecar.ui.dashboard --json
```

Do not add `--show-content` unless the user explicitly asks to reveal raw logged prompt/summary content.

## Option 3: Run Tests

```bash
python3 -m unittest discover -s tests
```

Focused checks:

```bash
python3 -m unittest tests.test_install_hooks
python3 -m unittest tests.test_sidecar_cli
python3 -m unittest tests.test_daemon
python3 -m unittest tests.test_llm_summarizer
python3 -m unittest tests.test_sidecar_config
python3 -m unittest tests.test_sidecar_api
python3 -m unittest tests.test_mcp_server tests.test_mcp_rehearsal tests.test_mcp_mutations
```

## Option 4: Setup Rehearsal

Use temporary settings, runtime, and plist paths. This writes only inside the temporary directory and does not call launchctl:

```bash
tmp=$(mktemp -d); SIDECAR_COMPACT_DIR="$tmp/runtime" PYTHONPATH=src python3 -m compact_sidecar.cli setup --settings "$tmp/settings.json" --plist-path "$tmp/sidecar.plist" --no-launchctl; python3 -m json.tool "$tmp/settings.json"
```

Inspect the generated plist without launchctl:

```bash
tmp=$(mktemp -d); SIDECAR_COMPACT_DIR="$tmp/runtime" PYTHONPATH=src python3 -m compact_sidecar.cli setup --settings "$tmp/settings.json" --plist-path "$tmp/sidecar.plist" --no-launchctl; SIDECAR_COMPACT_DIR="$tmp/runtime" PYTHONPATH=src python3 -m compact_sidecar.cli status --json --plist-path "$tmp/sidecar.plist"
```

## Option 5: Install Project Hooks

Confirmed write to `.claude/settings.local.json`; no launchctl:

```bash
SIDECAR_COMPACT_DIR="$PWD/.memory" PYTHONPATH=src python3 -m compact_sidecar.cli setup --settings "$PWD/.claude/settings.local.json" --no-launchctl
```

Verify hook JSON without printing raw prompt/summary content:

```bash
python3 -m json.tool .claude/settings.local.json >/dev/null
```

## Option 6: Remove Project Hooks

Confirmed removal from `.claude/settings.local.json`; daemon artifacts are untouched:

```bash
PYTHONPATH=src python3 -m compact_sidecar.cli uninstall --settings "$PWD/.claude/settings.local.json"
```

## Option 7: Daemon Rehearsal

Temporary plist generation and inspection; no launchctl:

```bash
tmp=$(mktemp -d); SIDECAR_COMPACT_DIR="$tmp/runtime" PYTHONPATH=src python3 -m compact_sidecar.services.daemon --install-agent --plist-path "$tmp/sidecar.plist"; SIDECAR_COMPACT_DIR="$tmp/runtime" PYTHONPATH=src python3 -m compact_sidecar.services.daemon --agent-status --plist-path "$tmp/sidecar.plist"
```

Read-only doctor on an explicit plist may call `launchctl print` only:

```bash
PYTHONPATH=src python3 -m compact_sidecar.services.daemon --doctor --plist-path /path/to/sidecar.plist
```

## Option 8: LLM Config Menu

Show this submenu:

```text
LLM Config Menu
8a. Explain variables       Describe SIDECAR_LLM_* variables
8b. Print export template   Show shell export commands without secrets
8c. Prepare settings env    Draft project-local env updates for review
8d. Validate config         Validate env/config without printing API key values
8e. Foreground run-once     Run daemon once with current config
8f. Include in daemon plist Include env names/values when writing daemon plist
```

LLM summary runs only in the daemon path. Hooks, status, dashboard, manual merge, and auto compact do not call the LLM.

## Option 9: Prepare LLM Env Config

Collect these values from the user, but do not print the API key value back:

```text
SIDECAR_LLM_ENDPOINT
SIDECAR_LLM_MODEL
SIDECAR_LLM_API_KEY_ENV
the API key variable named by SIDECAR_LLM_API_KEY_ENV
SIDECAR_LLM_TIMEOUT_SECONDS
SIDECAR_LLM_MAX_INPUT_CHARS
SIDECAR_LLM_MAX_OUTPUT_CHARS
```

Recommended defaults:

```text
SIDECAR_LLM_ENDPOINT=https://api.openai.com/v1/chat/completions
SIDECAR_LLM_MODEL=gpt-4.1-mini
SIDECAR_LLM_API_KEY_ENV=OPENAI_API_KEY
SIDECAR_LLM_TIMEOUT_SECONDS=30
SIDECAR_LLM_MAX_INPUT_CHARS=40000
SIDECAR_LLM_MAX_OUTPUT_CHARS=12000
```

Before writing settings, show a redacted plan:

```text
settings_path=.claude/settings.local.json
SIDECAR_LLM_ENDPOINT=<endpoint>
SIDECAR_LLM_MODEL=<model>
SIDECAR_LLM_API_KEY_ENV=<api_key_env>
<api_key_env>=<set>
SIDECAR_LLM_TIMEOUT_SECONDS=<timeout_seconds>
SIDECAR_LLM_MAX_INPUT_CHARS=<max_input_chars>
SIDECAR_LLM_MAX_OUTPUT_CHARS=<max_output_chars>
```

After the user confirms the exact settings path and values, merge only the `env` object in `.claude/settings.local.json`; never replace the full settings file. If using a script for the merge, it must preserve all existing keys and must not echo the API key.

Validate JSON after writing:

```bash
python3 -m json.tool .claude/settings.local.json >/dev/null
```

## Option 10: LLM Config Check

Validate current process environment without printing API key values:

```bash
PYTHONPATH=src python3 - <<'PY'
import os
from compact_sidecar.services.llm_summarizer import LLMSummaryConfig, LLMSummaryConfigError
try:
    config = LLMSummaryConfig.from_env(os.environ)
except LLMSummaryConfigError as exc:
    print(f"LLM config invalid: {exc}")
else:
    print("LLM config valid")
    print(f"endpoint={config.endpoint}")
    print(f"model={config.model}")
    print(f"api_key_env={config.api_key_env}")
    print(f"timeout_seconds={config.timeout_seconds}")
    print(f"max_input_chars={config.max_input_chars}")
    print(f"max_output_chars={config.max_output_chars}")
PY
```

Validate values stored in `.claude/settings.local.json` by loading only its `env` object:

```bash
PYTHONPATH=src python3 - <<'PY'
import json, os
from pathlib import Path
from compact_sidecar.services.llm_summarizer import LLMSummaryConfig, LLMSummaryConfigError
settings = json.loads(Path('.claude/settings.local.json').read_text(encoding='utf-8'))
env = dict(os.environ)
env.update(settings.get('env', {}))
try:
    config = LLMSummaryConfig.from_env(env)
except LLMSummaryConfigError as exc:
    print(f"LLM config invalid: {exc}")
else:
    print("LLM config valid")
    print(f"endpoint={config.endpoint}")
    print(f"model={config.model}")
    print(f"api_key_env={config.api_key_env}")
PY
```

## Option 11: Auto Install Sidecar

Use this after the user chooses auto install. Support these modes:

```text
11a. Hooks only
11b. Hooks + LLM env config
11c. Hooks + daemon plist, no launchctl
11d. Hooks + LLM env config + daemon plist, no launchctl
11e. Hooks + LLM env config + persistent launchctl bootstrap/kickstart
```

Default mode is `11b` if the user asks for LLM configuration, otherwise `11a`.

Hooks only, no launchctl:

```bash
SIDECAR_COMPACT_DIR="$PWD/.memory" PYTHONPATH=src python3 -m compact_sidecar.cli setup --settings "$PWD/.claude/settings.local.json" --no-launchctl
```

Hooks plus daemon plist without launchctl:

```bash
plist="$HOME/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist"; SIDECAR_COMPACT_DIR="$PWD/.memory" PYTHONPATH=src python3 -m compact_sidecar.cli setup --settings "$PWD/.claude/settings.local.json" --plist-path "$plist" --no-launchctl
```

Persistent launchctl bootstrap/kickstart uses the same CLI gate but must be explicitly requested:

```bash
plist="$HOME/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist"; SIDECAR_COMPACT_DIR="$PWD/.memory" PYTHONPATH=src python3 -m compact_sidecar.cli setup --settings "$PWD/.claude/settings.local.json" --plist-path "$plist" --start-daemon
```

After install, verify:

```bash
PYTHONPATH=src python3 -m compact_sidecar.cli status --json --plist-path "$HOME/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist"
```

## Option 12: Uninstall Sidecar

Remove project hooks only:

```bash
PYTHONPATH=src python3 -m compact_sidecar.cli uninstall --settings "$PWD/.claude/settings.local.json"
```

Remove hooks and daemon artifact without launchctl:

```bash
plist="$HOME/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist"; PYTHONPATH=src python3 -m compact_sidecar.cli uninstall --settings "$PWD/.claude/settings.local.json" --remove-daemon --plist-path "$plist" --no-launchctl
```

Remove hooks and boot out daemon first only when explicitly requested:

```bash
plist="$HOME/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist"; PYTHONPATH=src python3 -m compact_sidecar.cli uninstall --settings "$PWD/.claude/settings.local.json" --remove-daemon --plist-path "$plist"
```

## Option 13: Explain Architecture

Summarize these modules and runtime contract:

- `compact_sidecar.cli` is the unified CLI for setup, uninstall, daemon startup, explicit auto compact, and status.
- `src/compact_sidecar/api.py` is the shared programmatic facade for Skill/MCP layers.
- `src/compact_sidecar/hooks/install.py` owns hook specs and settings merging/removal.
- `compact_sidecar.services.daemon` handles daemon run-once, launchd plist artifacts, and explicit launchctl lifecycle commands.
- `compact_sidecar.services.llm_summarizer` parses `SIDECAR_LLM_*` environment variables and performs OpenAI-compatible streaming chat completions requests.
- `src/compact_sidecar/hooks/userprompt.py` and `src/compact_sidecar/hooks/postcompact.py` are hook targets.
- `compact_sidecar.ui.status` and `compact_sidecar.ui.dashboard` are read-only diagnostics.
- Tests should isolate runtime files with `SIDECAR_COMPACT_DIR`.
- Runtime files default to project `.memory` and must not contain API key values.
- Hook stdout must remain hook JSON only; diagnostics go to `errors.log`.

## Distribution Notes

- Source checkout usage remains supported: `PYTHONPATH=src python3 -m compact_sidecar.cli ...` and `PYTHONPATH=src python3 -m compact_sidecar.mcp.server --self-test`.
- The package entry point `sidecar` maps to the unified CLI.
- The package entry point `sidecar-mcp` runs the stdio MCP server and exposes read-only, rehearsal, and gated mutation tools.
- MCP client configs must use explicit local paths and must not include API key values; provide only env names such as `SIDECAR_LLM_API_KEY_ENV`.
- Mutation MCP tools are enabled by default in `sidecar-mcp`, but require `confirm=true`; global settings writes require `allow_global_settings=true`; tmux sends require an explicit pane and `no_send=false`.
- The Skill asset is included in packaging as `sidecar-manager-skill/SKILL.md`.
