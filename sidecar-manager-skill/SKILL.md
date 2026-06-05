---
name: sidecar-manager
description: Menu-driven management for this compact sidecar project, including confirmed auto-install and LLM environment configuration.
---

# Sidecar Manager

Use this skill to manage the Claude Code compact sidecar project in this repository.

Show this menu when the user asks what this skill can do or how to choose an operation:

```text
Sidecar Manager Menu
1. Status check             Read current sidecar/runtime status
2. Dashboard snapshot       Generate a read-only dashboard JSON snapshot
3. Run tests                Run the project test suite
4. Setup rehearsal          Preview generated hooks/plist using temporary files
5. Install project hooks    Install sidecar hooks into project-local settings
6. Remove project hooks     Remove sidecar hooks from project-local settings
7. Daemon rehearsal         Generate and inspect a temporary launchd plist
8. LLM config menu          Show LLM configuration choices
9. Write LLM env config     Write SIDECAR_LLM_* env values into project-local settings
10. LLM config check        Validate current LLM environment/config
11. Auto install sidecar    Confirmed install: hooks, optional LLM env, optional daemon plist
12. Uninstall sidecar       Confirmed uninstall: hooks and optional daemon artifact
13. Explain architecture    Summarize key modules and runtime contract
```

Users may choose by number or by natural language, for example:

```text
/sidecar-manager 11
/sidecar-manager auto install sidecar with llm env
/sidecar-manager write llm env config
/sidecar-manager remove project hooks
```

## Confirmation Model

- Read-only or temporary operations can run directly when requested: options 1, 2, 3, 4, 7, 8, 10, and 13.
- Change operations can run automatically after the user clearly chooses them: options 5, 6, 9, 11, and 12.
- If a change operation is ambiguous, ask for the missing values first, then run it after the user confirms the choices.
- Before option 11 or 12, present a one-line action summary and ask for confirmation if the user has not already said to proceed.
- Do not require extra confirmation after the user explicitly says things like `yes`, `confirm`, `proceed`, `run option 11`, or `auto install`.

## Config Targets

Default to project-local configuration:

- Claude Code settings: `.claude/settings.local.json`
- Runtime directory: `.memory`
- Optional launchd plist: `~/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist`

Use global `~/.claude/settings.json` only if the user explicitly asks for global installation.

## Option 1: Status Check

```bash
python3 src/sidecar.py status --json
```

## Option 2: Dashboard Snapshot

```bash
tmp=$(mktemp -d); SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/dashboard.py --json
```

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
```

## Option 4: Setup Rehearsal

Preview setup with temporary files:

```bash
tmp=$(mktemp -d); SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/sidecar.py setup --settings "$tmp/settings.json" --plist-path "$tmp/sidecar.plist" --no-launchctl; python3 -m json.tool "$tmp/settings.json"
```

## Option 5: Install Project Hooks

Install hooks into `.claude/settings.local.json`:

```bash
SIDECAR_COMPACT_DIR="$PWD/.memory" python3 src/sidecar.py setup --settings "$PWD/.claude/settings.local.json" --no-launchctl
```

Verify hook counts:

```bash
python3 - <<'PY'
import json
from pathlib import Path
settings = json.loads(Path('.claude/settings.local.json').read_text(encoding='utf-8'))
hooks = settings.get('hooks', {})
print('UserPromptSubmit:', len(hooks.get('UserPromptSubmit', [])))
print('PostCompact:', len(hooks.get('PostCompact', [])))
PY
```

## Option 6: Remove Project Hooks

```bash
python3 src/sidecar.py uninstall --settings "$PWD/.claude/settings.local.json"
```

## Option 7: Daemon Rehearsal

```bash
tmp=$(mktemp -d); SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/daemon.py --install-agent --plist-path "$tmp/sidecar.plist"; python3 src/daemon.py --agent-status --plist-path "$tmp/sidecar.plist"
```

## Option 8: LLM Config Menu

Show this submenu:

```text
LLM Config Menu
8a. Explain variables       Describe SIDECAR_LLM_* variables
8b. Print export template   Show shell export commands
8c. Write env config        Write env values to project-local settings
8d. Validate config         Validate env/config without printing API key values
8e. Foreground run-once     Run daemon once with current config
8f. Include in daemon plist Include env values when installing persistent daemon
```

LLM summary runs in the daemon path. Hooks, status, dashboard, manual merge, and auto compact do not call the LLM.

## Option 9: Write LLM Env Config

Collect these values from the user:

```text
SIDECAR_LLM_ENDPOINT
SIDECAR_LLM_MODEL
SIDECAR_LLM_API_KEY_ENV
<the API key variable named by SIDECAR_LLM_API_KEY_ENV>
SIDECAR_LLM_TIMEOUT_SECONDS
SIDECAR_LLM_MAX_INPUT_CHARS
SIDECAR_LLM_MAX_OUTPUT_CHARS
```

Defaults:

```text
SIDECAR_LLM_ENDPOINT=https://api.openai.com/v1/chat/completions
SIDECAR_LLM_MODEL=gpt-4.1-mini
SIDECAR_LLM_API_KEY_ENV=OPENAI_API_KEY
SIDECAR_LLM_TIMEOUT_SECONDS=30
SIDECAR_LLM_MAX_INPUT_CHARS=40000
SIDECAR_LLM_MAX_OUTPUT_CHARS=12000
```

Write them into `.claude/settings.local.json` under `env`, preserving existing settings. Do not replace the full settings file. If the API key value is provided, store it under the variable named by `SIDECAR_LLM_API_KEY_ENV`.

Use Python to merge safely:

```bash
python3 - <<'PY'
import json
from pathlib import Path
path = Path('.claude/settings.local.json')
settings = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
env = settings.setdefault('env', {})
updates = {
    'SIDECAR_LLM_ENDPOINT': '<endpoint>',
    'SIDECAR_LLM_MODEL': '<model>',
    'SIDECAR_LLM_API_KEY_ENV': '<api_key_env>',
    '<api_key_env>': '<api_key_value>',
    'SIDECAR_LLM_TIMEOUT_SECONDS': '<timeout_seconds>',
    'SIDECAR_LLM_MAX_INPUT_CHARS': '<max_input_chars>',
    'SIDECAR_LLM_MAX_OUTPUT_CHARS': '<max_output_chars>',
}
env.update({key: value for key, value in updates.items() if value})
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
PY
```

After writing, validate JSON and show variable names only. Do not print the API key value.

```bash
python3 -m json.tool .claude/settings.local.json >/dev/null
python3 - <<'PY'
import json
from pathlib import Path
settings = json.loads(Path('.claude/settings.local.json').read_text(encoding='utf-8'))
env = settings.get('env', {})
for key in sorted(k for k in env if k.startswith('SIDECAR_LLM_') or k == env.get('SIDECAR_LLM_API_KEY_ENV')):
    value = '<set>' if key == env.get('SIDECAR_LLM_API_KEY_ENV') else env.get(key)
    print(f'{key}={value}')
PY
```

## Option 10: LLM Config Check

Validate current process environment:

```bash
python3 - <<'PY'
import os
from src.llm_summarizer import LLMSummaryConfig, LLMSummaryConfigError
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

To validate values stored in `.claude/settings.local.json`, run with that env loaded:

```bash
python3 - <<'PY'
import json, os
from pathlib import Path
from src.llm_summarizer import LLMSummaryConfig, LLMSummaryConfigError
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

For hooks only:

```bash
SIDECAR_COMPACT_DIR="$PWD/.memory" python3 src/sidecar.py setup --settings "$PWD/.claude/settings.local.json" --no-launchctl
```

For hooks plus LLM env config, run option 9 first, then install hooks.

For daemon plist without launchctl:

```bash
plist="$HOME/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist"; SIDECAR_COMPACT_DIR="$PWD/.memory" python3 src/sidecar.py setup --settings "$PWD/.claude/settings.local.json" --plist-path "$plist" --no-launchctl
```

For persistent launchctl bootstrap/kickstart, use explicit separate commands after writing the plist:

```bash
plist="$HOME/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist"; SIDECAR_COMPACT_DIR="$PWD/.memory" python3 src/sidecar.py setup --settings "$PWD/.claude/settings.local.json" --plist-path "$plist"
```

After install, run:

```bash
python3 src/sidecar.py status --json --plist-path "$HOME/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist"
```

## Option 12: Uninstall Sidecar

Remove project hooks only:

```bash
python3 src/sidecar.py uninstall --settings "$PWD/.claude/settings.local.json"
```

Remove hooks and daemon artifact without launchctl:

```bash
plist="$HOME/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist"; python3 src/sidecar.py uninstall --settings "$PWD/.claude/settings.local.json" --remove-daemon --plist-path "$plist" --no-launchctl
```

Remove hooks and boot out daemon first:

```bash
plist="$HOME/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist"; python3 src/sidecar.py uninstall --settings "$PWD/.claude/settings.local.json" --remove-daemon --plist-path "$plist"
```

## Option 13: Explain Architecture

Summarize these modules and runtime contract:

- `src/install_hooks.py` owns hook specs and settings merging.
- `src/sidecar.py` is the unified CLI for setup, uninstall, daemon startup, explicit auto compact, and status.
- `src/daemon.py` handles launchd plist artifacts and explicit lifecycle commands.
- `src/llm_summarizer.py` parses `SIDECAR_LLM_*` environment variables and performs OpenAI-compatible streaming chat completions requests.
- `src/userprompt_inject.py` and `src/postcompact_record.py` are hook targets.
- Tests should isolate runtime files with `SIDECAR_COMPACT_DIR`.
- Hook stdout must remain hook JSON only; diagnostics go to `errors.log`.
