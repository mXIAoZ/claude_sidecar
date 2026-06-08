# Claude Code Compact Sidecar

A local, standard-library-only sidecar for keeping Claude Code long-session context alive across `/compact`. It stores a rolling summary in the project, injects it through the supported `UserPromptSubmit` hook, records `PostCompact` summaries, and can let the daemon call a configured OpenAI-compatible LLM to keep `.memory/rolling-summary.md` updated by default.

中文文档见 [`README.zh-CN.md`](README.zh-CN.md).

## Start Here

If you only remember four commands, use these:

```bash
# 1. Install hooks into your Claude Code settings.
PYTHONPATH=src python3 -m compact_sidecar.cli setup

# 2. Check everything the sidecar knows, without writing runtime files.
PYTHONPATH=src python3 -m compact_sidecar.cli status --json

# 3. Optional: start daemon maintenance with a launchd plist.
plist="$HOME/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist"
PYTHONPATH=src python3 -m compact_sidecar.cli start daemon --plist-path "$plist"

# 4. Optional: uninstall hooks and stop/remove the daemon.
PYTHONPATH=src python3 -m compact_sidecar.cli uninstall --remove-daemon --plist-path "$plist"
```

For a no-risk rehearsal that does not touch real Claude Code settings or launchd state:

```bash
tmp=$(mktemp -d)
SIDECAR_COMPACT_DIR="$tmp/runtime" \
  PYTHONPATH=src python3 -m compact_sidecar.cli setup \
  --settings "$tmp/settings.json" \
  --plist-path "$tmp/sidecar.plist" \
  --no-launchctl

SIDECAR_COMPACT_DIR="$tmp/runtime" PYTHONPATH=src python3 -m compact_sidecar.cli status --json
python3 -m json.tool "$tmp/settings.json" >/dev/null
```

The default commands are intentionally short. Add opt-out flags only when you want to avoid a real action:

- `--settings <path>` writes hooks to a temporary settings file instead of real `~/.claude/settings.json`.
- `--no-launchctl` writes setup/plist files or removes the plist without calling `launchctl`.
- `--no-send` prints the compact plan but does not send tmux keys.
- `--keep-hooks` keeps hook settings during uninstall.
- `--ignore-bootout-failure` continues plist removal if launchctl bootout fails.
- `--show-content` is required before Dashboard/status output shows raw logged prompt or summary content.


## Source And Packaged Usage

Use the source checkout when developing or auditing exact scripts:

```bash
PYTHONPATH=src python3 -m compact_sidecar.cli --help
PYTHONPATH=src python3 -m compact_sidecar.cli status --json
PYTHONPATH=src python3 -m compact_sidecar.mcp.server --self-test
```

Use the packaged entry points when installing this repository as a local tool:

```bash
python3 -m pip install .
sidecar --help
sidecar status --json
sidecar-mcp --self-test
```

Build a wheel before release or distribution checks:

```bash
python3 -m pip wheel . --no-deps -w "$(mktemp -d)"
```

The packaged `sidecar` command maps to the unified CLI. The packaged `sidecar-mcp` command runs the stdio MCP server and exposes the same safety-gated facade used by tests. Direct source commands remain supported for checkout-based use.

## Source Layout

The implementation lives in the `compact_sidecar` package under `src/`, grouped by responsibility:

```text
src/compact_sidecar/
  config.py, paths.py          configuration and runtime path helpers
  hooks/                       UserPromptSubmit, PostCompact, settings installer
  runtime/                     summaries, history candidates, operation log, readiness
  services/                    daemon, LLM summarizer, auto compact controller
  ui/                          status and dashboard renderers
  mcp/                         stdio MCP server
  api.py, cli.py               MCP facade and unified CLI
```

Source-checkout commands use package modules with `PYTHONPATH=src python3 -m compact_sidecar...`; packaged installs use the `sidecar` and `sidecar-mcp` console scripts. The former top-level `src/*.py` wrappers have been removed.

## Skill And MCP Distribution

The Skill asset lives at `sidecar-manager-skill/SKILL.md`. Install or copy that directory into the Claude Code skill location your environment uses, then invoke it as `sidecar-manager`. The Skill is an operator workflow: it chooses safe commands and explains tradeoffs, but it does not replace the CLI safety gates.

The MCP server is read from stdio. Configure clients with explicit local paths and non-secret environment variables. Source-checkout example:

```json
{
  "mcpServers": {
    "compact-sidecar": {
      "command": "python3",
      "args": [
        "-m",
        "compact_sidecar.mcp.server"
      ],
      "env": {
        "PYTHONPATH": "/absolute/path/to/claude_code_compact_sidecar/src",
        "SIDECAR_COMPACT_DIR": "/absolute/path/to/project/.memory"
      }
    }
  }
}
```

Packaged entry-point example:

```json
{
  "mcpServers": {
    "compact-sidecar": {
      "command": "sidecar-mcp",
      "args": [],
      "env": {
        "SIDECAR_COMPACT_DIR": "/absolute/path/to/project/.memory"
      }
    }
  }
}
```

Do not put API key values in MCP config. If daemon LLM summaries are enabled, set `SIDECAR_LLM_API_KEY_ENV` to the environment variable name and provide the secret through the process environment managed by your shell or launcher.

MCP tools are grouped by side effect level:

- Read-only: `sidecar_status`, `sidecar_dashboard`, `sidecar_config_validate`, and `sidecar_operation_log` do not write runtime files or call launchctl/tmux.
- Rehearsal: `sidecar_setup_rehearsal`, `sidecar_daemon_plist_rehearsal`, `sidecar_daemon_status`, and `sidecar_compact_plan_preview` write only explicit caller-provided artifacts or inspect explicit paths; they never call launchctl or tmux.
- Mutations: `sidecar_hook_install`, `sidecar_hook_uninstall`, `sidecar_daemon_plist_write`, `sidecar_daemon_plist_remove`, `sidecar_daemon_run_once`, `sidecar_launchctl_lifecycle`, and `sidecar_tmux_compact` are enabled by default in `sidecar-mcp`, but require `confirm: true` plus explicit paths. Global `~/.claude/settings.json` writes also require `allow_global_settings: true`; tmux sending requires an explicit pane and `no_send: false`.

## What Problem This Solves

Claude Code `/compact` is useful, but long sessions can still lose project-specific continuity: current goals, recent decisions, active constraints, and the exact details you want carried forward. This sidecar keeps those facts in a small project-local file and injects them on the next prompt submission using Claude Code's supported hook context.

The important distinction:

```text
/compact itself
   |
   v
Claude Code compacts the session
   |
   v
PostCompact hook records the compact summary
   |
   v
Your next prompt submission triggers UserPromptSubmit
   |
   v
rolling-summary.md is injected as additionalContext
```

The sidecar does not rely on unsupported compact-time context injection. Auto compact works by sending `/compact`, optionally waiting for `PostCompact`, optionally updating `rolling-summary.md`, and then sending the prompt so `UserPromptSubmit` injects continuity context on that prompt.

## The Runtime Files

By default, runtime state is stored under `.memory/` in the current project. Override it with `SIDECAR_COMPACT_DIR`.

```text
.memory/
  rolling-summary.md              reviewed continuity summary injected into prompts
  rolling-summary.backup.*.md     dated backups created before automatic summary rewrites
  rolling-summary.draft.md        generated draft from compact history for compatibility/manual review
  compact-history.jsonl           current PostCompact history
  compact-history.jsonl.1         rotated PostCompact history
  operation-log.jsonl             metadata-only operation timeline
  operation-log.jsonl.1           rotated operation timeline
  daemon-state.json               daemon/plist/launchctl metadata
  errors.log                      local diagnostics from hooks and scripts
```

A good `rolling-summary.md` is short and boring. Keep only facts that must survive compaction:

```markdown
# Rolling Summary

## 当前目标

## 已确认决策

## 活动任务

## 重要约束

## 未解决问题

## Compact 前必须保留
```

The `## Compact 前必须保留` marker is required by default before injection happens. You can set `SIDECAR_INJECT_ALWAYS=1` for experiments, but the marker keeps accidental files from being injected.

## Configuration Template

All built-in defaults live in [`sidecar.config.template.json`](sidecar.config.template.json). The template is categorized by runtime paths, hook specs, summary injection, readiness thresholds, compact history, operation logging, LLM limits, launchd daemon settings, controller defaults, dashboard/status defaults, CLI relationships, and test diagnostics.

Configuration precedence is:

1. Built-in defaults from `sidecar.config.template.json`.
2. A JSON config file passed with `--config <path>` or `SIDECAR_CONFIG_PATH`.
3. Existing environment variables such as `SIDECAR_COMPACT_DIR`, `SIDECAR_INJECT_ALWAYS`, `SIDECAR_OPERATION_LOG`, `SIDECAR_LOG_RAW_SUMMARY`, `SIDECAR_LAUNCHCTL_PATH`, and `SIDECAR_LLM_*`.
4. Explicit CLI flags.

Example:

```bash
cp sidecar.config.template.json /tmp/sidecar.config.json
PYTHONPATH=src python3 -m compact_sidecar.cli --config /tmp/sidecar.config.json status --json
SIDECAR_COMPACT_DIR=/tmp/sidecar-runtime \
  PYTHONPATH=src python3 -m compact_sidecar.cli --config /tmp/sidecar.config.json setup \
  --settings /tmp/sidecar-settings.json \
  --plist-path /tmp/sidecar.plist \
  --no-launchctl
```

Do not put actual API keys in the config file. The config stores only the LLM `api_key_env` name; the secret value must come from that environment variable. Generated hook commands and launchd plist environments propagate `SIDECAR_CONFIG_PATH` so hook, daemon, dashboard, status, setup, uninstall, merge, and compact controller flows resolve the same defaults.

## LLM Summary Defaults

Daemon maintenance is the automatic writer path. When compact history has summary candidates, `PYTHONPATH=src python3 -m compact_sidecar.services.daemon --run-once` and daemon loops build a prompt from `.memory/compact-history.jsonl` / `.memory/compact-history.jsonl.1`, call the configured OpenAI-compatible chat completions endpoint with streaming SSE by default (`stream: true` plus usage chunks), validate that the response includes `# Rolling Summary` and `## Compact 前必须保留`, then write `.memory/rolling-summary.md`. If an older summary exists, it is first saved to `rolling-summary.backup.<date>.md`.

No LLM-specific CLI flags are required. Configure the provider with environment variables before running the daemon or installing the launchd plist:

The request path always uses streaming chat completions, so there is no separate streaming toggle to set. This matches OpenAI-compatible providers that require SSE streaming, including OpenRouter-style endpoints. If the provider omits streaming usage chunks, Dashboard/status show token counts as `unknown` instead of estimating them.

```bash
export SIDECAR_LLM_ENDPOINT="https://api.openai.com/v1/chat/completions"
export SIDECAR_LLM_MODEL="gpt-4.1-mini"
export SIDECAR_LLM_API_KEY_ENV="OPENAI_API_KEY"
export OPENAI_API_KEY="<set in shell; do not commit>"
export SIDECAR_LLM_TIMEOUT_SECONDS="30"
export SIDECAR_LLM_MAX_INPUT_CHARS="40000"
export SIDECAR_LLM_MAX_OUTPUT_CHARS="12000"
```

`SIDECAR_LLM_MAX_INPUT_CHARS` defaults to `40000` and may be raised up to `200000`; `SIDECAR_LLM_MAX_OUTPUT_CHARS` defaults to `12000` and may be raised up to `50000`. Values above those hard limits fail configuration before any LLM request is sent.

If there is no compact history, the daemon skips the LLM call and leaves `rolling-summary.md` unchanged. If configuration, HTTP, JSON parsing, or summary validation fails, the daemon fails closed: it does not overwrite the existing summary, records metadata in `daemon-state.json`, and records a metadata-only `daemon | llm-summary` operation when `--operation-log` is enabled. `--run-once` returns non-zero on those LLM failures; loops record the failed pass and continue.

Dashboard, status, and operation logs show the latest LLM summary status, provider/model, prompt tokens, completion tokens, total tokens, elapsed time, summary path, backup path, and error kind. Raw LLM prompt/output text and API key values are not written to daemon state or operation logs. If the provider omits usage, token counts display as `unknown`.

The daemon only uses sidecar-owned compact history as input. Claude Code `sessions/*.jsonl` transcripts or logs from other agents are reference material only; they are not read as runtime input. Enabling a real provider sends compact-history-derived summary text to `SIDECAR_LLM_ENDPOINT`, so treat that endpoint as part of your trusted environment.

## Common Workflows

### Basic continuity, no daemon

```bash
PYTHONPATH=src python3 -m compact_sidecar.cli setup
mkdir -p .memory
$EDITOR .memory/rolling-summary.md
PYTHONPATH=src python3 -m compact_sidecar.cli status
```

Then use Claude Code normally. When you run `/compact`, `PostCompact` appends compact summaries to `.memory/compact-history.jsonl`. Later, generate a draft:

```bash
PYTHONPATH=src python3 -m compact_sidecar.runtime.merge_compact_history
$EDITOR .memory/rolling-summary.draft.md
```

Copy only still-accurate facts from the draft into `.memory/rolling-summary.md`.

### One-command setup plus daemon

```bash
plist="$HOME/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist"
SIDECAR_COMPACT_DIR="$PWD/.memory" \
  PYTHONPATH=src python3 -m compact_sidecar.cli setup \
  --plist-path "$plist" \
  --start-daemon
```

This installs hooks, writes the plist, bootstraps/kickstarts the daemon, and prints launchctl status. Export the non-secret `SIDECAR_LLM_*` variables first if you want the background daemon to summarize with an LLM. The generated plist carries the LLM endpoint/model/limits and the API key environment variable name, but not the resolved API key value. Add `--no-launchctl` if you only want the files written.

### Uninstall hooks and daemon

```bash
PYTHONPATH=src python3 -m compact_sidecar.cli uninstall --remove-daemon --plist-path "$plist"
```

This boots out the launchd service, removes the generated plist, and removes sidecar hook entries from Claude Code settings. Add `--no-launchctl` if the daemon is already stopped or you only want to delete the plist.

### Read the current state

```bash
PYTHONPATH=src python3 -m compact_sidecar.cli status
PYTHONPATH=src python3 -m compact_sidecar.cli status --json
PYTHONPATH=src python3 -m compact_sidecar.cli status --plist-path "$plist" --doctor
```

Status is read-only. It does not create `.memory/`, trigger compact, edit settings, or start/stop launchd.

### Explicit auto compact through tmux

Auto compact needs a real tmux pane because the controller must know where to send `/compact` and the prompt.

```bash
# Run inside the tmux pane where Claude Code is open.
tmux display-message -p '#S:#I.#P'
```

Then use that target:

```bash
SIDECAR_COMPACT_DIR="$PWD/.memory" \
  PYTHONPATH=src python3 -m compact_sidecar.cli start compact \
  --pane %2 \
  --prompt-file /path/to/prompt.txt \
  --wait-postcompact \
  --merge-after
```

With `--merge-after`, auto compact writes a fresh `.memory/rolling-summary.md` from compact history and saves the previous file as `rolling-summary.backup.<date>.md` first. Use `--no-send` to preview the plan without touching tmux.

## What Each Component Does

- `src/compact_sidecar/cli.py`: unified CLI for setup, status, daemon startup, hook installation, and compact control.
- `src/compact_sidecar/api.py`: shared programmatic facade used by CLI-adjacent product surfaces and MCP tools.
- `src/compact_sidecar/mcp/server.py`: stdio MCP server exposing read-only, rehearsal, and gated mutation tools.
- `src/compact_sidecar/hooks/userprompt.py`: emits `UserPromptSubmit` hook JSON with rolling summary context and compact-readiness advisory.
- `src/compact_sidecar/hooks/postcompact.py`: records `PostCompact` payloads to compact history.
- `src/compact_sidecar/runtime/merge_compact_history.py`: deduplicates compact summaries and writes `rolling-summary.draft.md` for manual review.
- `src/compact_sidecar/services/daemon.py`: runs maintenance once or in a loop, calls the configured LLM to update `rolling-summary.md` by default when compact history exists, manages launchd plist artifacts, and executes explicit launchctl lifecycle commands.
- `src/compact_sidecar/services/llm_summarizer.py`: standard-library OpenAI-compatible streaming request layer with usage-token parsing and secret-safe errors.
- `src/compact_sidecar/runtime/rolling_summary_writer.py`: validates required rolling-summary structure and performs backup-first atomic writes.
- `src/compact_sidecar/services/auto_compact_controller.py`: controls a known tmux pane, can send `/compact`, wait for `PostCompact`, update summary with backup, and send the prompt.
- `src/compact_sidecar/ui/dashboard.py` / `src/compact_sidecar/ui/status.py`: show read-only runtime health, compact readiness, operation log metadata, and daemon/plist status.
- `src/compact_sidecar/runtime/operation_log.py`: stores metadata-only operation records and rotates them.

## Safety Boundaries

- Hooks, status, dashboard, manual merge, and auto compact do not upload data to external services. The daemon LLM summary path sends compact-history-derived text only to the configured `SIDECAR_LLM_ENDPOINT`.
- Only the Python standard library is used.
- Hooks never send `/compact` and never start background processes.
- Hook stdout is reserved for Claude Code hook JSON; diagnostics go to `errors.log`.
- Prompt text is used in memory for the current hook/controller run and is not printed or persisted by default.
- Operation logs are metadata-only by default; raw prompt/summary logging requires explicit opt-in flags or environment variables.
- Dashboard/status hide raw content unless `--show-content` is passed.
- Compact-readiness is approximate local metadata, not exact Claude Code token accounting.
- Manual merge never overwrites `rolling-summary.md`. The daemon and auto compact can rewrite it only after keeping a dated backup first: daemon does this after a successful validated LLM summary, and auto compact does this with `--merge-after`.
- LLM prompt text, LLM output text, and API key values must not be written to daemon state or operation logs.
- Tests and smoke checks should use `SIDECAR_COMPACT_DIR` plus temporary `--settings` / `--plist-path` paths.

## Current Project Snapshot

This repository currently provides a complete local validation stack for compact continuity:

- `UserPromptSubmit` injection reads `.memory/rolling-summary.md` and adds it as supported hook `additionalContext` when the required marker is present.
- `PostCompact` recording stores compact payloads in `.memory/compact-history.jsonl` with bounded reads, rotation, and non-blocking error handling.
- `compact_sidecar.runtime.merge_compact_history` deduplicates recent compact summaries and writes `rolling-summary.draft.md` without overwriting `rolling-summary.md`.
- `compact_sidecar.services.daemon` supports one-shot and bounded loop maintenance, default LLM-backed rolling-summary writes, launchd plist write/status/remove, read-only doctor checks, and explicit launchctl lifecycle actions.
- `compact_sidecar.runtime.operation_log`, `compact_sidecar.ui.dashboard`, and status commands expose a local metadata-only operation timeline, LLM token usage, and health view.
- `compact_sidecar.services.auto_compact_controller` provides explicit tmux auto compact control and can write a new summary with a dated backup.
- `compact_sidecar.cli` is the source-checkout unified CLI; installed packages expose it as `sidecar`.

## Use Cases

- Keep a small, reviewed continuity summary available after `/compact`.
- Capture official `PostCompact` summaries for later consolidation.
- Inspect sidecar health, compact pressure, daemon state, and operation timelines locally.
- Run an explicit tmux-based auto compact flow without letting hooks send `/compact`.
- Keep all runtime artifacts project-local and easy to delete.

## Hook Installation

The installer merges hook entries into Claude Code settings and preserves existing hooks, permissions, statusLine, enabled plugins, `autoCompact`, and unknown fields. Validate with a temporary settings file before updating real Claude Code settings.

```bash
tmp=$(mktemp -d)
PYTHONPATH=src python3 -m compact_sidecar.hooks.install --settings "$tmp/settings.json"
python3 -m json.tool "$tmp/settings.json"
```

Run `PYTHONPATH=src python3 -m compact_sidecar.hooks.install` when you intentionally want to update real `~/.claude/settings.json`; use `--settings <path>` for temporary validation.

Installed hooks:

- `UserPromptSubmit`: runs `python -m compact_sidecar.hooks.userprompt` in source checkouts to inject `rolling-summary.md` and compact-readiness advisory context.
- `PostCompact`: runs `python -m compact_sidecar.hooks.postcompact` in source checkouts for both `auto` and `manual` compact events.

## Manual Continuity Flow

1. Keep `.memory/rolling-summary.md` short and accurate.
2. Ensure it contains `## Compact 前必须保留` before expecting injection.
3. Use `/compact` normally in Claude Code.
4. Let `PostCompact` append official compact summaries to `compact-history.jsonl`.
5. Run `PYTHONPATH=src python3 -m compact_sidecar.runtime.merge_compact_history` to create `rolling-summary.draft.md` for manual review, or run the configured daemon to rewrite `rolling-summary.md` automatically from compact history.
6. If using the manual draft path, review the draft and copy only still-accurate facts into `rolling-summary.md`.

## Dashboard And Operation Log

Use the Dashboard when you want to answer “what has the sidecar done recently?” without reading each runtime file manually.

`src/compact_sidecar/ui/dashboard.py` renders a read-only terminal view of runtime files, compact readiness, latest LLM summary token usage, recent operation records, and health warnings. It never creates the runtime directory and never displays raw prompt/summary content unless you explicitly pass `--show-content`.

```bash
SIDECAR_COMPACT_DIR=/path/to/runtime PYTHONPATH=src python3 -m compact_sidecar.ui.dashboard
SIDECAR_COMPACT_DIR=/path/to/runtime PYTHONPATH=src python3 -m compact_sidecar.ui.dashboard --watch --interval-seconds 2
SIDECAR_COMPACT_DIR=/path/to/runtime PYTHONPATH=src python3 -m compact_sidecar.ui.dashboard --json
```

Operation timeline records live in `operation-log.jsonl` and rotate to `operation-log.jsonl.1`. Records contain `service`, `operation`, `status`, safe metadata, and `content_policy` flags. Raw content is opt-in only:

```bash
printf '{"session_id":"test","summary":"compacted"}' \
  | SIDECAR_OPERATION_LOG=1 SIDECAR_COMPACT_DIR="$tmp" PYTHONPATH=src python3 -m compact_sidecar.hooks.postcompact

SIDECAR_COMPACT_DIR="$tmp" PYTHONPATH=src python3 -m compact_sidecar.runtime.merge_compact_history --operation-log
SIDECAR_COMPACT_DIR="$tmp" PYTHONPATH=src python3 -m compact_sidecar.services.daemon --run-once --operation-log
SIDECAR_COMPACT_DIR="$tmp" PYTHONPATH=src python3 -m compact_sidecar.services.auto_compact_controller --pane session:window.pane --operation-log
```

Sensitive raw logging requires explicit opt-in and should only be used in trusted local runtimes:

```bash
printf '{"summary":"raw compact summary"}' \
  | SIDECAR_LOG_RAW_SUMMARY=1 SIDECAR_COMPACT_DIR="$tmp" PYTHONPATH=src python3 -m compact_sidecar.hooks.postcompact

SIDECAR_COMPACT_DIR="$tmp" PYTHONPATH=src python3 -m compact_sidecar.runtime.merge_compact_history --operation-log --log-raw-summary
SIDECAR_COMPACT_DIR="$tmp" PYTHONPATH=src python3 -m compact_sidecar.services.auto_compact_controller --pane session:window.pane --prompt-file prompt.txt --operation-log --log-raw-prompt
SIDECAR_COMPACT_DIR="$tmp" PYTHONPATH=src python3 -m compact_sidecar.ui.dashboard --show-content
```

`compact_sidecar.ui.status` reports only operation-log metadata, daemon LLM token metadata, malformed counts, and raw-content flags; it never prints raw prompt or summary text.

## Doctor / Status

Run a read-only runtime status check:

```bash
SIDECAR_COMPACT_DIR=/path/to/runtime PYTHONPATH=src python3 -m compact_sidecar.ui.status
```

`compact_sidecar.ui.status` reports known runtime files, injection readiness, and a `compact-readiness` line. It does not create directories, write `errors.log`, modify `rolling-summary.md`, scan transcripts/source code, or trigger compact.

Run a read-only daemon doctor check for an explicit plist:

```bash
PYTHONPATH=src python3 -m compact_sidecar.services.daemon --doctor --plist-path /path/to/sidecar.plist
```

`--doctor` checks whether the plist exists, whether it is a valid generated sidecar plist, and whether `launchctl print` can find the user-level service. It does not bootstrap, kickstart, bootout, remove files, write daemon state, or edit Claude Code settings.

## Auto Compact Controller

Use this only when you want an explicit external command to control a known tmux pane. Hooks never call this controller automatically.

`src/compact_sidecar/services/auto_compact_controller.py` is an explicit outer controller for tmux-based Claude Code sessions. It is not a hook. It estimates local compact pressure, then either sends the prompt directly or sends `/compact` first, optionally waits for `PostCompact` history, optionally writes `rolling-summary.md` after saving the old summary as a dated backup, and finally sends the prompt.

### Tmux Usage

tmux gives the controller a stable target pane such as `sidecar:0.0` or `%2`. Without tmux, the controller has no safe pane target for sending `/compact` or prompt text.

Install tmux, start a named session, and run Claude Code inside it:

```bash
brew install tmux
tmux new -s sidecar
claude
```

Useful tmux shortcuts:

```text
Ctrl-b %      split left/right
Ctrl-b "      split top/bottom
Ctrl-b q      show pane numbers
Ctrl-b d      detach from the session
tmux attach -t sidecar    reattach later
```

Get the current pane target from inside the pane running Claude Code:

```bash
tmux display-message -p '#S:#I.#P'
```

List every pane when you need to find where Claude Code is running:

```bash
tmux list-panes -a -F '#S:#I.#P #{pane_id} active=#{pane_active} cmd=#{pane_current_command} title=#{pane_title}'
```

If the output includes a `pane_id` such as `%2`, you can pass that directly to `--pane`. The controller never guesses the active pane, so verify the target before sending:

```bash
SIDECAR_COMPACT_DIR="$PWD/.memory" \
  PYTHONPATH=src python3 -m compact_sidecar.services.auto_compact_controller \
  --pane %2 \
  --prompt-file /path/to/prompt.txt
```

To inspect the plan without sending tmux keys:

```bash
SIDECAR_COMPACT_DIR="$PWD/.memory" \
  PYTHONPATH=src python3 -m compact_sidecar.services.auto_compact_controller \
  --pane %2 \
  --prompt-file /path/to/prompt.txt \
  --wait-postcompact \
  --merge-after \
  --no-send
```

Behavior:

```text
prompt source
   |
   v
compact_sidecar.services.auto_compact_controller
   |
   v
estimate runtime metadata + prompt size
   |
   +-- below threshold --> send prompt only
   |
   +-- at threshold ----> send /compact
                           optionally wait for compact-history.jsonl to change
                           optionally write rolling-summary.md with dated backup
                           send prompt
```

Useful flags:

- `--pane <target>`: required for sending; the controller never guesses the active pane.
- `--prompt-file <path>` or `--prompt-stdin`: explicit prompt source; the two options are mutually exclusive.
- `--min-readiness low|medium|high|attention`: compact trigger threshold, default `high`.
- `--wait-postcompact`: wait for `compact-history.jsonl` metadata to change after sending `/compact`.
- `--wait-timeout-seconds <n>` and `--poll-interval-seconds <n>`: bound the wait loop.
- `--merge-after`: after compact, write `rolling-summary.md` from compact history and save the previous summary as `rolling-summary.backup.<date>.md`.
- `--tmux-path <path>`: tmux binary override, used by tests with fake tmux.
- `--operation-log`: append metadata-only controller operations to `operation-log.jsonl`.
- `--log-raw-prompt`: with `--operation-log`, store bounded raw prompt text; sensitive.

Controller safety boundaries:

- Sending requires `--pane`; use `--no-send` to print the plan without touching tmux.
- Prompt text is never printed, logged, copied into `.memory/`, or written to controller state.
- The controller uses `tmux send-keys` with argument lists, not shell command strings.
- The controller does not modify real Claude Code settings. `--merge-after` only changes project-local runtime summary files and keeps the previous summary as a dated backup.
- The controller cannot prove `/compact` succeeded; it can only observe tmux return code and optional `PostCompact` history metadata.

## Daemon Maintenance

Daemon maintenance is the default LLM-backed writer. It still writes `rolling-summary.draft.md` for compatibility, but when compact history has candidates it also calls the configured LLM and writes `rolling-summary.md` after saving the previous file as a dated backup.

Run one local maintenance pass with LLM configuration inherited from the environment:

```bash
export SIDECAR_LLM_ENDPOINT="https://api.openai.com/v1/chat/completions"
export SIDECAR_LLM_MODEL="gpt-4.1-mini"
export OPENAI_API_KEY="<set in shell; do not commit>"
SIDECAR_COMPACT_DIR="$PWD/.memory" PYTHONPATH=src python3 -m compact_sidecar.services.daemon --run-once --operation-log
```

Run a bounded foreground loop:

```bash
SIDECAR_COMPACT_DIR="$PWD/.memory" PYTHONPATH=src python3 -m compact_sidecar.services.daemon --loop --interval-seconds 1 --max-runs 2 --operation-log
```

If there are no compact summary candidates, daemon maintenance skips the LLM and leaves `rolling-summary.md` unchanged. If the LLM path fails, the old summary stays in place, `daemon-state.json` records `llm_summary_status=error`, and `--run-once` exits non-zero. These commands do not call `launchctl`; launchctl is only used by explicit lifecycle commands or unified daemon startup.

## Launchd Artifact Commands

Write, inspect, and remove an explicit plist artifact:

```bash
tmp=$(mktemp -d)
SIDECAR_COMPACT_DIR="$tmp/runtime" \
  PYTHONPATH=src python3 -m compact_sidecar.services.daemon --install-agent --plist-path "$tmp/sidecar.plist"
SIDECAR_COMPACT_DIR="$tmp/runtime" \
  PYTHONPATH=src python3 -m compact_sidecar.services.daemon --agent-status --plist-path "$tmp/sidecar.plist"
SIDECAR_COMPACT_DIR="$tmp/runtime" \
  PYTHONPATH=src python3 -m compact_sidecar.services.daemon --remove-agent --plist-path "$tmp/sidecar.plist"
```

`--remove-agent` only deletes a valid generated sidecar plist artifact. Malformed, non-sidecar, or same-label-but-invalid plist files are preserved.

## Launchctl Lifecycle

Real launchctl lifecycle commands are explicit; selecting a `--launchctl-*` mode is the confirmation to change user-level launchd state:

```bash
PYTHONPATH=src python3 -m compact_sidecar.services.daemon --launchctl-bootstrap --plist-path /path/to/sidecar.plist
PYTHONPATH=src python3 -m compact_sidecar.services.daemon --launchctl-kickstart --plist-path /path/to/sidecar.plist
PYTHONPATH=src python3 -m compact_sidecar.services.daemon --launchctl-status --plist-path /path/to/sidecar.plist
PYTHONPATH=src python3 -m compact_sidecar.services.daemon --launchctl-bootout --plist-path /path/to/sidecar.plist
```

Before invoking `launchctl`, these commands require the plist to exist and pass full sidecar validation. `--confirm-launchctl` is accepted for compatibility but is no longer required. Unit tests use `SIDECAR_LAUNCHCTL_PATH` with a fake launchctl binary; they do not call the real system `launchctl`.

## Persistent Daemon Install

Use this flow only when you intentionally want a user-level launchd agent. It writes one explicit plist artifact under `~/Library/LaunchAgents`, starts it through explicit launchctl commands, and keeps runtime state in this project's `.memory/` directory unless you set `SIDECAR_COMPACT_DIR`. For LLM summaries, export the non-secret `SIDECAR_LLM_*` settings before installing the plist; the generated plist includes endpoint/model/limit settings and the API key environment variable name, but not the resolved API key value, and runs the daemon loop with metadata-only `--operation-log` so token usage is recorded per pass.

Set paths once:

```bash
plist="$HOME/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist"
runtime="$PWD/.memory"
```

Install and inspect the plist without starting anything:

```bash
SIDECAR_COMPACT_DIR="$runtime" \
  PYTHONPATH=src python3 -m compact_sidecar.services.daemon --install-agent --plist-path "$plist"
SIDECAR_COMPACT_DIR="$runtime" \
  PYTHONPATH=src python3 -m compact_sidecar.services.daemon --agent-status --plist-path "$plist"
SIDECAR_COMPACT_DIR="$runtime" \
  PYTHONPATH=src python3 -m compact_sidecar.services.daemon --doctor --plist-path "$plist"
```

Start and query the daemon explicitly:

```bash
SIDECAR_COMPACT_DIR="$runtime" \
  PYTHONPATH=src python3 -m compact_sidecar.services.daemon --launchctl-bootstrap --plist-path "$plist"
SIDECAR_COMPACT_DIR="$runtime" \
  PYTHONPATH=src python3 -m compact_sidecar.services.daemon --launchctl-kickstart --plist-path "$plist"
SIDECAR_COMPACT_DIR="$runtime" \
  PYTHONPATH=src python3 -m compact_sidecar.services.daemon --launchctl-status --plist-path "$plist"
SIDECAR_COMPACT_DIR="$runtime" \
  PYTHONPATH=src python3 -m compact_sidecar.services.daemon --doctor --plist-path "$plist"
```

Stop and remove it explicitly:

```bash
SIDECAR_COMPACT_DIR="$runtime" \
  PYTHONPATH=src python3 -m compact_sidecar.services.daemon --launchctl-bootout --plist-path "$plist"
SIDECAR_COMPACT_DIR="$runtime" \
  PYTHONPATH=src python3 -m compact_sidecar.services.daemon --remove-agent --plist-path "$plist"
```

`--launchctl-bootout` unloads the launchd service but does not delete the plist; `--remove-agent` deletes only a valid generated sidecar plist and does not call `launchctl`. Run bootout before removal when the service may be loaded.

## Important Files

- `src/compact_sidecar/api.py`: shared facade for status, dashboard/config snapshots, rehearsals, and gated mutations.
- `src/compact_sidecar/mcp/server.py`: stdio MCP entry point for read-only, rehearsal, and confirmed mutation tools.
- `src/compact_sidecar/hooks/userprompt.py`: emits `UserPromptSubmit` hook JSON with rolling summary context and compact-readiness advisory.
- `src/compact_sidecar/hooks/postcompact.py`: records `PostCompact` payloads to history.
- `src/compact_sidecar/runtime/merge_compact_history.py`: writes `rolling-summary.draft.md` from recent unique history summaries.
- `src/compact_sidecar/services/llm_summarizer.py`: sends OpenAI-compatible streaming chat completions requests and parses token usage.
- `src/compact_sidecar/runtime/rolling_summary_writer.py`: validates and writes `rolling-summary.md` with dated backups.
- `src/compact_sidecar/runtime/memory_candidates.py`: extracts, dedupes, and limits compact summary candidates.
- `src/compact_sidecar/runtime/operation_log.py`: appends, rotates, reads, and inspects the project-local operation timeline.
- `src/compact_sidecar/ui/dashboard.py`: read-only terminal Dashboard for runtime health and operation timeline visualization.
- `src/compact_sidecar/services/daemon.py`: handles run-once, foreground loop, plist artifacts, doctor checks, and explicit launchctl lifecycle.
- `src/compact_sidecar/services/auto_compact_controller.py`: explicit tmux controller that can send `/compact` and prompts after readiness checks.
- `src/compact_sidecar/ui/status.py`: read-only runtime diagnostics and approximate compact-readiness reporting.
- `src/compact_sidecar/runtime/readiness.py`: shared approximate readiness thresholds and advisory text.
- `src/compact_sidecar/hooks/install.py`: safely merges or removes hook commands in Claude Code settings.
- `src/compact_sidecar/paths.py`: runtime path resolution, JSON stdout helpers, and error logging.
- `src/compact_sidecar/runtime/summary_context.py`: rolling summary reading, marker handling, and head/tail truncation.
- `SPEC.md`: product scope and detailed behavior contract.
- `CLAUDE.md`: development commands and repository-specific agent guidance.


## Rollback And Uninstall Matrix

Use the smallest rollback that matches the change you made:

| Change to undo | Command |
|---|---|
| Remove project-local hooks | `PYTHONPATH=src python3 -m compact_sidecar.cli uninstall --settings "$PWD/.claude/settings.local.json"` |
| Remove global hooks intentionally installed by default setup | `PYTHONPATH=src python3 -m compact_sidecar.cli uninstall` |
| Stop a loaded launchd daemon | `SIDECAR_COMPACT_DIR="$PWD/.memory" PYTHONPATH=src python3 -m compact_sidecar.services.daemon --launchctl-bootout --plist-path "$plist"` |
| Remove a generated plist without launchctl | `SIDECAR_COMPACT_DIR="$PWD/.memory" PYTHONPATH=src python3 -m compact_sidecar.services.daemon --remove-agent --plist-path "$plist"` |
| Remove hooks and generated daemon artifact together | `PYTHONPATH=src python3 -m compact_sidecar.cli uninstall --remove-daemon --plist-path "$plist"` |
| Keep hooks but remove daemon artifact | `PYTHONPATH=src python3 -m compact_sidecar.cli uninstall --keep-hooks --remove-daemon --plist-path "$plist" --no-launchctl` |
| Remove Skill distribution | Delete the installed `sidecar-manager` skill directory from your Claude Code skill location. |
| Remove packaged commands | `python3 -m pip uninstall claude-code-compact-sidecar` |
| Clean project runtime files | Move or delete `.memory/` only after reviewing whether `rolling-summary.md`, compact history, or operation logs should be kept. |

For a loaded launchd service, bootout before removing the plist. `--launchctl-bootout` changes launchd state but does not delete files; `--remove-agent` deletes only a valid generated sidecar plist and never calls launchctl. Runtime cleanup is intentionally manual because `.memory/rolling-summary.md` and compact history may be useful audit/context artifacts.

## Privacy Model

- Raw prompt and raw summary content are hidden by default in CLI, Dashboard, status, MCP, and operation-log views.
- Raw prompt logging requires explicit controller flags such as `--operation-log --log-raw-prompt`; raw summary logging requires `SIDECAR_LOG_RAW_SUMMARY=1` or `--log-raw-summary` where supported.
- API key values must not appear in config files, generated plists, daemon state, operation logs, MCP responses, or documentation examples. Store only the API key environment variable name such as `SIDECAR_LLM_API_KEY_ENV=OPENAI_API_KEY`.
- The daemon LLM path is the only default network-capable path. It sends compact-history-derived text to `SIDECAR_LLM_ENDPOINT`; hooks, status, dashboard, manual merge, auto compact, read-only MCP tools, and rehearsal MCP tools do not call LLMs.
- Operation logs are metadata-only by default: service, operation, status, bounded metadata, and raw-content policy flags. Dashboard/status/MCP show raw content only when an explicit show-content style option is used and raw content was explicitly logged earlier.

## Release Checklist

Before treating a change as a release candidate, run:

```bash
python3 -m unittest discover -s tests
PYTHONPATH=src python3 -m compact_sidecar.cli status --json
PYTHONPATH=src python3 -m compact_sidecar.mcp.server --self-test
python3 -m pip wheel . --no-deps -w "$(mktemp -d)"
```

Also run focused MCP and packaging checks after touching the MCP server, facade, or metadata:

```bash
python3 -m unittest tests.test_mcp_server tests.test_mcp_rehearsal tests.test_mcp_mutations
python3 -m unittest tests.test_sidecar_api tests.test_sidecar_config tests.test_sidecar_cli
```

Manual release review:

- Verify Skill commands still match the CLI and do not bypass safety gates.
- Verify docs contain no API key values, secret-looking placeholders, or unsafe direct settings overwrite snippets.
- Verify mutation examples include `confirm: true` or an explicit CLI mode, explicit target paths, and global settings opt-in when relevant.
- Verify read-only and rehearsal checks do not write real Claude settings, call real launchctl, send tmux keys, or require network.

## Troubleshooting

- Dashboard shows `status: empty`: the runtime directory has no known sidecar files yet, or `SIDECAR_COMPACT_DIR` points somewhere else.
- Summary is not injected: ensure `.memory/rolling-summary.md` exists, is non-empty, and contains `## Compact 前必须保留`, or set `SIDECAR_INJECT_ALWAYS=1` for experiments.
- `PostCompact` history is missing: confirm the hook is installed and that hook stdout is not polluted by diagnostics.
- Auto compact does nothing: confirm that `--pane` points at the Claude Code tmux pane and that `--no-send` was not passed.
- Raw prompt/summary is not visible in Dashboard: this is expected unless raw logging was explicitly enabled and `--show-content` is passed.
- Daemon returns non-zero with compact history present: check `SIDECAR_LLM_ENDPOINT`, `SIDECAR_LLM_MODEL`, `SIDECAR_LLM_API_KEY_ENV`, and the API key variable named by `SIDECAR_LLM_API_KEY_ENV`.

## Testing And Development

Run the full suite:

```bash
python3 -m unittest discover -s tests
```

Run focused test modules:

```bash
python3 -m unittest tests.test_userprompt_inject
python3 -m unittest tests.test_operation_log
python3 -m unittest tests.test_dashboard
python3 -m unittest tests.test_llm_summarizer
python3 -m unittest tests.test_rolling_summary_writer
python3 -m unittest tests.test_postcompact_record
python3 -m unittest tests.test_merge_compact_history
python3 -m unittest tests.test_memory_candidates
python3 -m unittest tests.test_daemon
python3 -m unittest tests.test_auto_compact_controller
python3 -m unittest tests.test_status
python3 -m unittest tests.test_install_hooks
python3 -m unittest tests.test_sidecar_cli
python3 -m unittest tests.test_sidecar_paths
python3 -m unittest tests.test_sidecar_config
python3 -m unittest tests.test_sidecar_api
python3 -m unittest tests.test_mcp_server
python3 -m unittest tests.test_mcp_rehearsal
python3 -m unittest tests.test_mcp_mutations
python3 -m unittest tests.test_manual_smoke_flow
```

Run isolated smoke checks:

```bash
tmp=$(mktemp -d)
printf '## Compact 前必须保留
Keep this across compaction.
' > "$tmp/rolling-summary.md"
SIDECAR_COMPACT_DIR="$tmp" PYTHONPATH=src python3 -m compact_sidecar.hooks.userprompt | python3 -m json.tool
```

```bash
tmp=$(mktemp -d)
printf '{"session_id":"test","summary":"compacted"}' \
  | SIDECAR_COMPACT_DIR="$tmp" PYTHONPATH=src python3 -m compact_sidecar.hooks.postcompact
python3 -m json.tool "$tmp/compact-history.jsonl"
```

```bash
tmp=$(mktemp -d)
printf '{"timestamp":"2026-05-21T10:00:00+00:00","payload":{"summary":"compacted"}}\n' \
  > "$tmp/compact-history.jsonl"
SIDECAR_COMPACT_DIR="$tmp" PYTHONPATH=src python3 -m compact_sidecar.runtime.merge_compact_history
sed -n '1,80p' "$tmp/rolling-summary.draft.md"
```

```bash
tmp=$(mktemp -d)
SIDECAR_COMPACT_DIR="$tmp/runtime" PYTHONPATH=src python3 -m compact_sidecar.ui.status
SIDECAR_COMPACT_DIR="$tmp/runtime" PYTHONPATH=src python3 -m compact_sidecar.ui.dashboard --json
```

```bash
tmp=$(mktemp -d)
PYTHONPATH=src python3 -m compact_sidecar.hooks.install --settings "$tmp/settings.json"
python3 -m json.tool "$tmp/settings.json"
```

```bash
tmp=$(mktemp -d)
SIDECAR_COMPACT_DIR="$tmp/runtime" \
  PYTHONPATH=src python3 -m compact_sidecar.services.daemon --install-agent --plist-path "$tmp/sidecar.plist"
SIDECAR_COMPACT_DIR="$tmp/runtime" \
  PYTHONPATH=src python3 -m compact_sidecar.services.daemon --agent-status --plist-path "$tmp/sidecar.plist"
```

Check diff hygiene:

```bash
git diff --check
```

Use only the Python standard library unless the project scope changes.
