# Claude Code Compact Sidecar

A local, standard-library-only sidecar for preserving Claude Code long-session continuity across compaction. It keeps project-local runtime state, injects a reviewed rolling summary through the supported `UserPromptSubmit` hook, records `PostCompact` summaries, and provides optional maintenance/automation commands.

## What It Does

- Stores runtime state under the current project `.memory/` directory by default.
- Injects `.memory/rolling-summary.md` only when it contains `## Compact 前必须保留`, unless `SIDECAR_INJECT_ALWAYS=1` is set.
- Adds an approximate compact-readiness advisory before very large prompts.
- Records `PostCompact` hook payloads to `compact-history.jsonl`.
- Builds `rolling-summary.draft.md` from recent unique compact summaries without overwriting `rolling-summary.md`.
- Provides read-only status/doctor commands, bounded daemon maintenance, safe launchd artifact/lifecycle commands, and an explicit tmux auto compact controller.

## Safety Boundaries

- No data is uploaded to external services.
- Python standard library only.
- Hook stdout is reserved for Claude Code hook JSON; diagnostics go to `errors.log`.
- `UserPromptSubmit` prompt text is used only in memory for the current hook run and is not written to `.memory/` or `errors.log`.
- Compact-readiness is approximate local metadata, not exact Claude Code token accounting.
- Hooks do not run `/compact`; only the explicit outer controller can send `/compact`, and only with `--pane --no-dry-run --confirm-send`.
- `rolling-summary.md` is never overwritten automatically.
- Tests and smoke checks should use `SIDECAR_COMPACT_DIR` to avoid touching real project state.
- Real `~/.claude/settings.json` is modified only if you explicitly run the installer without `--dry-run` or a temporary `--settings` path.
- Artifact commands do not call `launchctl`; only `--launchctl-* --confirm-launchctl` modes can affect user-level launchd state.

## Runtime Files

Default runtime directory: `.memory/` in the current project. Override it with `SIDECAR_COMPACT_DIR`.

```text
.memory/
  rolling-summary.md          # human-maintained continuity summary
  rolling-summary.draft.md    # generated draft from compact history
  compact-history.jsonl       # current PostCompact history
  compact-history.jsonl.1     # rotated PostCompact history
  daemon-state.json           # daemon metadata only
  errors.log                  # local diagnostics
```

Recommended `rolling-summary.md` shape:

```markdown
# Rolling Summary

## 当前目标

## 已确认决策

## 活动任务

## 重要约束

## 未解决问题

## Compact 前必须保留
```

Only keep continuity-critical facts. Do not store full transcripts, secrets, tokens, credentials, stale plans, or temporary reasoning.

## Quick Start

Run the test suite:

```bash
python3 -m unittest discover -s tests
```

Create and validate a rolling summary in an isolated runtime:

```bash
tmp=$(mktemp -d)
printf '## Compact 前必须保留\nKeep this across compaction.\n' > "$tmp/rolling-summary.md"
SIDECAR_COMPACT_DIR="$tmp" python3 src/userprompt_inject.py | python3 -m json.tool
```

Record a compact summary payload:

```bash
tmp=$(mktemp -d)
printf '{"session_id":"test","summary":"compacted"}' \
  | SIDECAR_COMPACT_DIR="$tmp" python3 src/postcompact_record.py
python3 -m json.tool "$tmp/compact-history.jsonl"
```

Generate a draft summary from recent unique compact history summaries:

```bash
tmp=$(mktemp -d)
printf '{"timestamp":"2026-05-21T10:00:00+00:00","payload":{"summary":"compacted"}}\n' \
  > "$tmp/compact-history.jsonl"
SIDECAR_COMPACT_DIR="$tmp" python3 src/merge_compact_history.py
sed -n '1,80p' "$tmp/rolling-summary.draft.md"
```

Check runtime status:

```bash
SIDECAR_COMPACT_DIR="$tmp" python3 src/status.py
```

## Hook Installation

Preview hook installation:

```bash
python3 src/install_hooks.py --dry-run
```

Install into a temporary settings file for testing:

```bash
tmp=$(mktemp -d)
python3 src/install_hooks.py --settings "$tmp/settings.json"
python3 -m json.tool "$tmp/settings.json"
```

Only run `python3 src/install_hooks.py` without `--dry-run` when you intentionally want to update real Claude Code settings.

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

`src/auto_compact_controller.py` is an explicit outer controller for tmux-based Claude Code sessions. It is not a hook. It estimates local compact pressure, then either sends the prompt directly or sends `/compact` first, optionally waits for `PostCompact` history, optionally writes `rolling-summary.draft.md`, and finally sends the prompt.

Dry-run is the default and is read-only:

```bash
tmp=$(mktemp -d)
printf 'Explain the current sidecar state.\n' > "$tmp/prompt.txt"
SIDECAR_COMPACT_DIR="$tmp/runtime" \
  python3 src/auto_compact_controller.py \
  --pane session:window.pane \
  --prompt-file "$tmp/prompt.txt"
```

Confirmed sending requires both `--no-dry-run` and `--confirm-send`, plus an explicit tmux pane:

```bash
SIDECAR_COMPACT_DIR="$PWD/.memory" \
  python3 src/auto_compact_controller.py \
  --pane session:window.pane \
  --prompt-file /path/to/prompt.txt \
  --wait-postcompact \
  --merge-after \
  --no-dry-run \
  --confirm-send
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
                           optionally write rolling-summary.draft.md
                           send prompt
```

Useful flags:

- `--pane <target>`: required for confirmed sending; the controller never guesses the active pane.
- `--prompt-file <path>` or `--prompt-stdin`: explicit prompt source; the two options are mutually exclusive.
- `--min-readiness low|medium|high|attention`: compact trigger threshold, default `high`.
- `--wait-postcompact`: wait for `compact-history.jsonl` metadata to change after sending `/compact`.
- `--wait-timeout-seconds <n>` and `--poll-interval-seconds <n>`: bound the wait loop.
- `--merge-after`: write `rolling-summary.draft.md` from compact history after compact; never overwrites `rolling-summary.md`.
- `--tmux-path <path>`: tmux binary override, used by tests with fake tmux.

Controller safety boundaries:

- Dry-run does not call `tmux`, create runtime directories, or write draft files.
- Confirmed sends require `--pane --no-dry-run --confirm-send`.
- Prompt text is never printed, logged, copied into `.memory/`, or written to controller state.
- The controller uses `tmux send-keys` with argument lists, not shell command strings.
- The controller does not modify real Claude Code settings and does not auto-promote `rolling-summary.draft.md` to `rolling-summary.md`.
- The controller cannot prove `/compact` succeeded; it can only observe tmux return code and optional `PostCompact` history metadata.

## Daemon Maintenance

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

Preview plist XML without writing:

```bash
tmp=$(mktemp -d)
SIDECAR_COMPACT_DIR="$tmp/runtime" \
  python3 src/daemon.py --install-agent --dry-run --plist-path "$tmp/sidecar.plist"
```

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

Real launchctl lifecycle commands are explicit and gated:

```bash
python3 src/daemon.py --launchctl-bootstrap --confirm-launchctl --plist-path /path/to/sidecar.plist
python3 src/daemon.py --launchctl-kickstart --confirm-launchctl --plist-path /path/to/sidecar.plist
python3 src/daemon.py --launchctl-status --confirm-launchctl --plist-path /path/to/sidecar.plist
python3 src/daemon.py --launchctl-bootout --confirm-launchctl --plist-path /path/to/sidecar.plist
```

Before invoking `launchctl`, these commands require the plist to exist and pass full sidecar validation. Unit tests use `SIDECAR_LAUNCHCTL_PATH` with a fake launchctl binary; they do not call the real system `launchctl`.

## Persistent Daemon Install

Use this flow only when you intentionally want a user-level launchd agent. It writes one explicit plist artifact under `~/Library/LaunchAgents`, starts it through gated launchctl commands, and keeps runtime state in this project's `.memory/` directory unless you set `SIDECAR_COMPACT_DIR`.

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
  python3 src/daemon.py --launchctl-bootstrap --confirm-launchctl --plist-path "$plist"
SIDECAR_COMPACT_DIR="$runtime" \
  python3 src/daemon.py --launchctl-kickstart --confirm-launchctl --plist-path "$plist"
SIDECAR_COMPACT_DIR="$runtime" \
  python3 src/daemon.py --launchctl-status --confirm-launchctl --plist-path "$plist"
SIDECAR_COMPACT_DIR="$runtime" \
  python3 src/daemon.py --doctor --plist-path "$plist"
```

Stop and remove it explicitly:

```bash
SIDECAR_COMPACT_DIR="$runtime" \
  python3 src/daemon.py --launchctl-bootout --confirm-launchctl --plist-path "$plist"
SIDECAR_COMPACT_DIR="$runtime" \
  python3 src/daemon.py --remove-agent --plist-path "$plist"
```

`--launchctl-bootout` unloads the launchd service but does not delete the plist; `--remove-agent` deletes only a valid generated sidecar plist and does not call `launchctl`. Run bootout before removal when the service may be loaded.

## Important Files

- `src/userprompt_inject.py`: emits `UserPromptSubmit` hook JSON with rolling summary context and compact-readiness advisory.
- `src/postcompact_record.py`: records `PostCompact` payloads to history.
- `src/merge_compact_history.py`: writes `rolling-summary.draft.md` from recent unique history summaries.
- `src/memory_candidates.py`: extracts, dedupes, and limits compact summary candidates.
- `src/daemon.py`: handles run-once, foreground loop, plist artifacts, doctor checks, and gated launchctl lifecycle.
- `src/auto_compact_controller.py`: explicit tmux controller that can send `/compact` and prompts after confirmed readiness checks.
- `src/status.py`: read-only runtime diagnostics and approximate compact-readiness reporting.
- `src/readiness.py`: shared approximate readiness thresholds and advisory text.
- `src/install_hooks.py`: safely merges hook commands into Claude Code settings.
- `src/sidecar_paths.py`: runtime path resolution, JSON stdout helpers, and error logging.
- `src/summary_context.py`: rolling summary reading, marker handling, and head/tail truncation.
- `SPEC.md`: product scope and detailed behavior contract.
- `CLAUDE.md`: development commands and repository-specific agent guidance.

## Development

Focused tests:

```bash
python3 -m unittest tests.test_userprompt_inject
python3 -m unittest tests.test_postcompact_record
python3 -m unittest tests.test_merge_compact_history
python3 -m unittest tests.test_memory_candidates
python3 -m unittest tests.test_daemon
python3 -m unittest tests.test_auto_compact_controller
python3 -m unittest tests.test_status
python3 -m unittest tests.test_install_hooks
python3 -m unittest tests.test_sidecar_paths
python3 -m unittest tests.test_manual_smoke_flow
```

Full suite:

```bash
python3 -m unittest discover -s tests
```

Diff hygiene:

```bash
git diff --check
```

Use only the Python standard library unless the project scope changes.
