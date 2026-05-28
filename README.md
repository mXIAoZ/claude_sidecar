# Claude Code Compact Sidecar

A local, standard-library-only sidecar for keeping Claude Code long-session context alive across `/compact`. It stores a reviewed rolling summary in the project, injects it through the supported `UserPromptSubmit` hook, records `PostCompact` summaries, and gives you one CLI for setup, status, daemon startup, and explicit auto compact control.

中文文档见 [`README.zh-CN.md`](README.zh-CN.md).

## Start Here

If you only remember four commands, use these:

```bash
# 1. Install hooks into your Claude Code settings.
python3 src/sidecar.py setup

# 2. Check everything the sidecar knows, without writing runtime files.
python3 src/sidecar.py status --json

# 3. Optional: start daemon maintenance with a launchd plist.
plist="$HOME/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist"
python3 src/sidecar.py start daemon --plist-path "$plist"

# 4. Optional: uninstall hooks and stop/remove the daemon.
python3 src/sidecar.py uninstall --remove-daemon --plist-path "$plist"
```

For a no-risk rehearsal that does not touch real Claude Code settings or launchd state:

```bash
tmp=$(mktemp -d)
SIDECAR_COMPACT_DIR="$tmp/runtime" \
  python3 src/sidecar.py setup \
  --settings "$tmp/settings.json" \
  --plist-path "$tmp/sidecar.plist" \
  --no-launchctl

SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/sidecar.py status --json
python3 -m json.tool "$tmp/settings.json" >/dev/null
```

The default commands are intentionally short. Add opt-out flags only when you want to avoid a real action:

- `--settings <path>` writes hooks to a temporary settings file instead of real `~/.claude/settings.json`.
- `--no-launchctl` writes setup/plist files or removes the plist without calling `launchctl`.
- `--no-send` prints the compact plan but does not send tmux keys.
- `--keep-hooks` keeps hook settings during uninstall.
- `--ignore-bootout-failure` continues plist removal if launchctl bootout fails.
- `--show-content` is required before Dashboard/status output shows raw logged prompt or summary content.

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
  rolling-summary.backup.*.md     dated backups created by auto compact --merge-after
  rolling-summary.draft.md        generated draft from compact history for manual review
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

## Common Workflows

### Basic continuity, no daemon

```bash
python3 src/sidecar.py setup
mkdir -p .memory
$EDITOR .memory/rolling-summary.md
python3 src/sidecar.py status
```

Then use Claude Code normally. When you run `/compact`, `PostCompact` appends compact summaries to `.memory/compact-history.jsonl`. Later, generate a draft:

```bash
python3 src/merge_compact_history.py
$EDITOR .memory/rolling-summary.draft.md
```

Copy only still-accurate facts from the draft into `.memory/rolling-summary.md`.

### One-command setup plus daemon

```bash
plist="$HOME/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist"
SIDECAR_COMPACT_DIR="$PWD/.memory" \
  python3 src/sidecar.py setup \
  --plist-path "$plist" \
  --start-daemon
```

This installs hooks, writes the plist, bootstraps/kickstarts the daemon, and prints launchctl status. Add `--no-launchctl` if you only want the files written.

### Uninstall hooks and daemon

```bash
python3 src/sidecar.py uninstall --remove-daemon --plist-path "$plist"
```

This boots out the launchd service, removes the generated plist, and removes sidecar hook entries from Claude Code settings. Add `--no-launchctl` if the daemon is already stopped or you only want to delete the plist.

### Read the current state

```bash
python3 src/sidecar.py status
python3 src/sidecar.py status --json
python3 src/sidecar.py status --plist-path "$plist" --doctor
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
  python3 src/sidecar.py start compact \
  --pane %2 \
  --prompt-file /path/to/prompt.txt \
  --wait-postcompact \
  --merge-after
```

With `--merge-after`, auto compact writes a fresh `.memory/rolling-summary.md` from compact history and saves the previous file as `rolling-summary.backup.<date>.md` first. Use `--no-send` to preview the plan without touching tmux.

## What Each Component Does

- `src/sidecar.py`: unified CLI for setup, status, daemon startup, hook installation, and compact control.
- `src/userprompt_inject.py`: emits `UserPromptSubmit` hook JSON with rolling summary context and compact-readiness advisory.
- `src/postcompact_record.py`: records `PostCompact` payloads to compact history.
- `src/merge_compact_history.py`: deduplicates compact summaries and writes `rolling-summary.draft.md` for manual review.
- `src/daemon.py`: runs maintenance once or in a loop, manages launchd plist artifacts, and executes explicit launchctl lifecycle commands.
- `src/auto_compact_controller.py`: controls a known tmux pane, can send `/compact`, wait for `PostCompact`, update summary with backup, and send the prompt.
- `src/dashboard.py` / `src/status.py`: show read-only runtime health, compact readiness, operation log metadata, and daemon/plist status.
- `src/operation_log.py`: stores metadata-only operation records and rotates them.

## Safety Boundaries

- No data is uploaded to external services.
- Only the Python standard library is used.
- Hooks never send `/compact` and never start background processes.
- Hook stdout is reserved for Claude Code hook JSON; diagnostics go to `errors.log`.
- Prompt text is used in memory for the current hook/controller run and is not printed or persisted by default.
- Operation logs are metadata-only by default; raw prompt/summary logging requires explicit opt-in flags or environment variables.
- Dashboard/status hide raw content unless `--show-content` is passed.
- Compact-readiness is approximate local metadata, not exact Claude Code token accounting.
- Manual merge and daemon maintenance never overwrite `rolling-summary.md`.
- Auto compact only writes `rolling-summary.md` when you explicitly run the controller with `--merge-after`, and it keeps a dated backup first.
- Tests and smoke checks should use `SIDECAR_COMPACT_DIR` plus temporary `--settings` / `--plist-path` paths.

## Current Project Snapshot

This repository currently provides a complete local validation stack for compact continuity:

- `UserPromptSubmit` injection reads `.memory/rolling-summary.md` and adds it as supported hook `additionalContext` when the required marker is present.
- `PostCompact` recording stores compact payloads in `.memory/compact-history.jsonl` with bounded reads, rotation, and non-blocking error handling.
- `merge_compact_history.py` deduplicates recent compact summaries and writes `rolling-summary.draft.md` without overwriting `rolling-summary.md`.
- `daemon.py` supports one-shot and bounded loop maintenance, launchd plist write/status/remove, read-only doctor checks, and explicit launchctl lifecycle actions.
- `operation_log.py`, `dashboard.py`, and status commands expose a local metadata-only operation timeline and health view.
- `auto_compact_controller.py` provides explicit tmux auto compact control and can write a new summary with a dated backup.
- `sidecar.py` is the unified CLI entrypoint for normal use.

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
python3 src/install_hooks.py --settings "$tmp/settings.json"
python3 -m json.tool "$tmp/settings.json"
```

Run `python3 src/install_hooks.py` when you intentionally want to update real `~/.claude/settings.json`; use `--settings <path>` for temporary validation.

Installed hooks:

- `UserPromptSubmit`: runs `src/userprompt_inject.py` to inject `rolling-summary.md` and compact-readiness advisory context.
- `PostCompact`: runs `src/postcompact_record.py` for both `auto` and `manual` compact events.

## Manual Continuity Flow

1. Keep `.memory/rolling-summary.md` short and accurate.
2. Ensure it contains `## Compact 前必须保留` before expecting injection.
3. Use `/compact` normally in Claude Code.
4. Let `PostCompact` append official compact summaries to `compact-history.jsonl`.
5. Run `merge_compact_history.py` or the daemon to create `rolling-summary.draft.md`.
6. Review the draft manually and copy only still-accurate facts into `rolling-summary.md`.

## Dashboard And Operation Log

Use the Dashboard when you want to answer “what has the sidecar done recently?” without reading each runtime file manually.

`src/dashboard.py` renders a read-only terminal view of runtime files, compact readiness, recent operation records, and health warnings. It never creates the runtime directory and never displays raw prompt/summary content unless you explicitly pass `--show-content`.

```bash
SIDECAR_COMPACT_DIR=/path/to/runtime python3 src/dashboard.py
SIDECAR_COMPACT_DIR=/path/to/runtime python3 src/dashboard.py --watch --interval-seconds 2
SIDECAR_COMPACT_DIR=/path/to/runtime python3 src/dashboard.py --json
```

Operation timeline records live in `operation-log.jsonl` and rotate to `operation-log.jsonl.1`. Records contain `service`, `operation`, `status`, safe metadata, and `content_policy` flags. Raw content is opt-in only:

```bash
printf '{"session_id":"test","summary":"compacted"}' \
  | SIDECAR_OPERATION_LOG=1 SIDECAR_COMPACT_DIR="$tmp" python3 src/postcompact_record.py

SIDECAR_COMPACT_DIR="$tmp" python3 src/merge_compact_history.py --operation-log
SIDECAR_COMPACT_DIR="$tmp" python3 src/daemon.py --run-once --operation-log
SIDECAR_COMPACT_DIR="$tmp" python3 src/auto_compact_controller.py --pane session:window.pane --operation-log
```

Sensitive raw logging requires explicit opt-in and should only be used in trusted local runtimes:

```bash
printf '{"summary":"raw compact summary"}' \
  | SIDECAR_LOG_RAW_SUMMARY=1 SIDECAR_COMPACT_DIR="$tmp" python3 src/postcompact_record.py

SIDECAR_COMPACT_DIR="$tmp" python3 src/merge_compact_history.py --operation-log --log-raw-summary
SIDECAR_COMPACT_DIR="$tmp" python3 src/auto_compact_controller.py --pane session:window.pane --prompt-file prompt.txt --operation-log --log-raw-prompt
SIDECAR_COMPACT_DIR="$tmp" python3 src/dashboard.py --show-content
```

`status.py` reports only operation-log metadata such as records, latest timestamp, malformed counts, and raw-content flags; it never prints raw prompt or summary text.

## Doctor / Status

Run a read-only runtime status check:

```bash
SIDECAR_COMPACT_DIR=/path/to/runtime python3 src/status.py
```

`status.py` reports known runtime files, injection readiness, and a `compact-readiness` line. It does not create directories, write `errors.log`, modify `rolling-summary.md`, scan transcripts/source code, or trigger compact.

Run a read-only daemon doctor check for an explicit plist:

```bash
python3 src/daemon.py --doctor --plist-path /path/to/sidecar.plist
```

`--doctor` checks whether the plist exists, whether it is a valid generated sidecar plist, and whether `launchctl print` can find the user-level service. It does not bootstrap, kickstart, bootout, remove files, write daemon state, or edit Claude Code settings.

## Auto Compact Controller

Use this only when you want an explicit external command to control a known tmux pane. Hooks never call this controller automatically.

`src/auto_compact_controller.py` is an explicit outer controller for tmux-based Claude Code sessions. It is not a hook. It estimates local compact pressure, then either sends the prompt directly or sends `/compact` first, optionally waits for `PostCompact` history, optionally writes `rolling-summary.md` after saving the old summary as a dated backup, and finally sends the prompt.

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
  python3 src/auto_compact_controller.py \
  --pane %2 \
  --prompt-file /path/to/prompt.txt
```

To inspect the plan without sending tmux keys:

```bash
SIDECAR_COMPACT_DIR="$PWD/.memory" \
  python3 src/auto_compact_controller.py \
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
auto_compact_controller.py
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

Daemon maintenance generates draft summaries from compact history on demand or on a bounded foreground loop. It does not overwrite `rolling-summary.md`; a human still decides what becomes durable continuity context.

Run one local maintenance pass:

```bash
tmp=$(mktemp -d)
SIDECAR_COMPACT_DIR="$tmp" python3 src/daemon.py --run-once
```

Run a bounded foreground loop:

```bash
tmp=$(mktemp -d)
SIDECAR_COMPACT_DIR="$tmp" python3 src/daemon.py --loop --interval-seconds 1 --max-runs 2
```

These commands write draft/state files only. They do not overwrite `rolling-summary.md` and do not call `launchctl`.

## Launchd Artifact Commands

Write, inspect, and remove an explicit plist artifact:

```bash
tmp=$(mktemp -d)
SIDECAR_COMPACT_DIR="$tmp/runtime" \
  python3 src/daemon.py --install-agent --plist-path "$tmp/sidecar.plist"
SIDECAR_COMPACT_DIR="$tmp/runtime" \
  python3 src/daemon.py --agent-status --plist-path "$tmp/sidecar.plist"
SIDECAR_COMPACT_DIR="$tmp/runtime" \
  python3 src/daemon.py --remove-agent --plist-path "$tmp/sidecar.plist"
```

`--remove-agent` only deletes a valid generated sidecar plist artifact. Malformed, non-sidecar, or same-label-but-invalid plist files are preserved.

## Launchctl Lifecycle

Real launchctl lifecycle commands are explicit; selecting a `--launchctl-*` mode is the confirmation to change user-level launchd state:

```bash
python3 src/daemon.py --launchctl-bootstrap --plist-path /path/to/sidecar.plist
python3 src/daemon.py --launchctl-kickstart --plist-path /path/to/sidecar.plist
python3 src/daemon.py --launchctl-status --plist-path /path/to/sidecar.plist
python3 src/daemon.py --launchctl-bootout --plist-path /path/to/sidecar.plist
```

Before invoking `launchctl`, these commands require the plist to exist and pass full sidecar validation. `--confirm-launchctl` is accepted for compatibility but is no longer required. Unit tests use `SIDECAR_LAUNCHCTL_PATH` with a fake launchctl binary; they do not call the real system `launchctl`.

## Persistent Daemon Install

Use this flow only when you intentionally want a user-level launchd agent. It writes one explicit plist artifact under `~/Library/LaunchAgents`, starts it through explicit launchctl commands, and keeps runtime state in this project's `.memory/` directory unless you set `SIDECAR_COMPACT_DIR`.

Set paths once:

```bash
plist="$HOME/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist"
runtime="$PWD/.memory"
```

Install and inspect the plist without starting anything:

```bash
SIDECAR_COMPACT_DIR="$runtime" \
  python3 src/daemon.py --install-agent --plist-path "$plist"
SIDECAR_COMPACT_DIR="$runtime" \
  python3 src/daemon.py --agent-status --plist-path "$plist"
SIDECAR_COMPACT_DIR="$runtime" \
  python3 src/daemon.py --doctor --plist-path "$plist"
```

Start and query the daemon explicitly:

```bash
SIDECAR_COMPACT_DIR="$runtime" \
  python3 src/daemon.py --launchctl-bootstrap --plist-path "$plist"
SIDECAR_COMPACT_DIR="$runtime" \
  python3 src/daemon.py --launchctl-kickstart --plist-path "$plist"
SIDECAR_COMPACT_DIR="$runtime" \
  python3 src/daemon.py --launchctl-status --plist-path "$plist"
SIDECAR_COMPACT_DIR="$runtime" \
  python3 src/daemon.py --doctor --plist-path "$plist"
```

Stop and remove it explicitly:

```bash
SIDECAR_COMPACT_DIR="$runtime" \
  python3 src/daemon.py --launchctl-bootout --plist-path "$plist"
SIDECAR_COMPACT_DIR="$runtime" \
  python3 src/daemon.py --remove-agent --plist-path "$plist"
```

`--launchctl-bootout` unloads the launchd service but does not delete the plist; `--remove-agent` deletes only a valid generated sidecar plist and does not call `launchctl`. Run bootout before removal when the service may be loaded.

## Important Files

- `src/userprompt_inject.py`: emits `UserPromptSubmit` hook JSON with rolling summary context and compact-readiness advisory.
- `src/postcompact_record.py`: records `PostCompact` payloads to history.
- `src/merge_compact_history.py`: writes `rolling-summary.draft.md` from recent unique history summaries.
- `src/memory_candidates.py`: extracts, dedupes, and limits compact summary candidates.
- `src/operation_log.py`: appends, rotates, reads, and inspects the project-local operation timeline.
- `src/dashboard.py`: read-only terminal Dashboard for runtime health and operation timeline visualization.
- `src/daemon.py`: handles run-once, foreground loop, plist artifacts, doctor checks, and explicit launchctl lifecycle.
- `src/auto_compact_controller.py`: explicit tmux controller that can send `/compact` and prompts after readiness checks.
- `src/status.py`: read-only runtime diagnostics and approximate compact-readiness reporting.
- `src/readiness.py`: shared approximate readiness thresholds and advisory text.
- `src/install_hooks.py`: safely merges or removes hook commands in Claude Code settings.
- `src/sidecar_paths.py`: runtime path resolution, JSON stdout helpers, and error logging.
- `src/summary_context.py`: rolling summary reading, marker handling, and head/tail truncation.
- `SPEC.md`: product scope and detailed behavior contract.
- `CLAUDE.md`: development commands and repository-specific agent guidance.

## Troubleshooting

- Dashboard shows `status: empty`: the runtime directory has no known sidecar files yet, or `SIDECAR_COMPACT_DIR` points somewhere else.
- Summary is not injected: ensure `.memory/rolling-summary.md` exists, is non-empty, and contains `## Compact 前必须保留`, or set `SIDECAR_INJECT_ALWAYS=1` for experiments.
- `PostCompact` history is missing: confirm the hook is installed and that hook stdout is not polluted by diagnostics.
- Auto compact does nothing: confirm that `--pane` points at the Claude Code tmux pane and that `--no-send` was not passed.
- Raw prompt/summary is not visible in Dashboard: this is expected unless raw logging was explicitly enabled and `--show-content` is passed.

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
python3 -m unittest tests.test_postcompact_record
python3 -m unittest tests.test_merge_compact_history
python3 -m unittest tests.test_memory_candidates
python3 -m unittest tests.test_daemon
python3 -m unittest tests.test_auto_compact_controller
python3 -m unittest tests.test_status
python3 -m unittest tests.test_install_hooks
python3 -m unittest tests.test_sidecar_cli
python3 -m unittest tests.test_sidecar_paths
python3 -m unittest tests.test_manual_smoke_flow
```

Run isolated smoke checks:

```bash
tmp=$(mktemp -d)
printf '## Compact 前必须保留
Keep this across compaction.
' > "$tmp/rolling-summary.md"
SIDECAR_COMPACT_DIR="$tmp" python3 src/userprompt_inject.py | python3 -m json.tool
```

```bash
tmp=$(mktemp -d)
printf '{"session_id":"test","summary":"compacted"}' \
  | SIDECAR_COMPACT_DIR="$tmp" python3 src/postcompact_record.py
python3 -m json.tool "$tmp/compact-history.jsonl"
```

```bash
tmp=$(mktemp -d)
printf '{"timestamp":"2026-05-21T10:00:00+00:00","payload":{"summary":"compacted"}}\n' \
  > "$tmp/compact-history.jsonl"
SIDECAR_COMPACT_DIR="$tmp" python3 src/merge_compact_history.py
sed -n '1,80p' "$tmp/rolling-summary.draft.md"
```

```bash
tmp=$(mktemp -d)
SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/status.py
SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/dashboard.py --json
```

```bash
tmp=$(mktemp -d)
python3 src/install_hooks.py --settings "$tmp/settings.json"
python3 -m json.tool "$tmp/settings.json"
```

```bash
tmp=$(mktemp -d)
SIDECAR_COMPACT_DIR="$tmp/runtime" \
  python3 src/daemon.py --install-agent --plist-path "$tmp/sidecar.plist"
SIDECAR_COMPACT_DIR="$tmp/runtime" \
  python3 src/daemon.py --agent-status --plist-path "$tmp/sidecar.plist"
```

Check diff hygiene:

```bash
git diff --check
```

Use only the Python standard library unless the project scope changes.
