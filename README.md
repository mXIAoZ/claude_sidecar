# Claude Code Compact Sidecar

A local, standard-library-only experiment for preserving Claude Code long-session continuity across compaction. The sidecar keeps a project-local rolling summary and injects it through the supported `UserPromptSubmit` hook.

## What It Does

- Stores runtime state under the current project `.memory/` directory by default.
- Reads `.memory/rolling-summary.md` and injects it only when it contains the marker `## Compact 前必须保留`.
- Adds a best-effort compact-readiness advisory before very large prompts when the local heuristic reaches the high threshold.
- Records `PostCompact` summaries to `compact-history.jsonl` for later review.
- Builds `rolling-summary.draft.md` from recent unique compact history summaries without overwriting the human-maintained summary.
- Provides daemon maintenance commands and explicit, gated launchd lifecycle commands.

## Safety Boundaries

- No data is uploaded to external services.
- Hook stdout is reserved for Claude Code hook JSON; diagnostics go to `errors.log`.
- Compact-readiness advisory is approximate and cannot run `/compact` automatically; compact manually, then resend the prompt if the advisory appears.
- UserPromptSubmit prompt text is used only in memory for the current hook run and is not written to `.memory/` or `errors.log`.
- `rolling-summary.md` is never overwritten automatically.
- Tests and smoke checks should use `SIDECAR_COMPACT_DIR` to avoid touching real project state.
- Real `~/.claude/settings.json` is modified only if you explicitly run the installer without `--dry-run` or a temporary `--settings` path.
- Artifact commands do not call `launchctl`; only `--launchctl-* --confirm-launchctl` modes can affect user-level launchd state.

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

## Doctor / Status

Run a read-only runtime status check:

```bash
SIDECAR_COMPACT_DIR=/path/to/runtime python3 src/status.py
```

`status.py` reports known runtime files, injection readiness, and a `compact-readiness` line. Compact readiness is approximate: it uses local runtime file sizes as a deterministic proxy for context growth, does not read transcripts or source code, and does not trigger compact automatically.

Run a read-only doctor check for an explicit plist:

```bash
python3 src/daemon.py --doctor --plist-path /path/to/sidecar.plist
```

`--doctor` checks whether the plist exists, whether it is a valid generated sidecar plist, and whether `launchctl print` can find the user-level service. It does not bootstrap, kickstart, bootout, remove files, write daemon state, or edit Claude Code settings.

## Auto Compact Controller Design

The current hook-based sidecar can detect compact pressure and inject advisory context, but it cannot safely run `/compact` or replay the user's current prompt from inside a `UserPromptSubmit` hook. A practical automatic compact flow should therefore live outside the hook as an explicit controller that targets a known Claude Code session.

Recommended integration:

```text
user prompt
   |
   v
auto_compact_controller.py
   |
   v
estimate readiness with local runtime files + prompt size
   |
   +-- low/medium --> send prompt to Claude Code pane
   |
   +-- high -------> send /compact to Claude Code pane
                     wait for PostCompact hook output
                     run merge_compact_history.py
                     send original prompt to Claude Code pane
```

Responsibilities stay split:

- `src/userprompt_inject.py` keeps injecting `rolling-summary.md` and best-effort compact-readiness advisory context.
- `src/postcompact_record.py` keeps recording `PostCompact` summaries to `compact-history.jsonl`.
- `src/merge_compact_history.py` keeps generating `rolling-summary.draft.md` without overwriting `rolling-summary.md`.
- `src/status.py` and `src/readiness.py` keep providing read-only approximate readiness signals.
- A future `src/auto_compact_controller.py` would orchestrate session control outside the hook.

The safest first implementation should use a tmux pane target instead of trying to control Claude Code from inside a hook:

```bash
python3 src/auto_compact_controller.py \
  --pane %1 \
  --prompt-file /path/to/prompt.txt \
  --runtime-dir .memory \
  --dry-run
```

To actually send keys, require an explicit confirmation flag:

```bash
python3 src/auto_compact_controller.py \
  --pane %1 \
  --prompt-file /path/to/prompt.txt \
  --runtime-dir .memory \
  --confirm-send
```

Suggested safety boundaries for the controller:

- Do not guess or auto-discover the target pane; require `--pane`.
- Default to `--dry-run`; require `--confirm-send` before sending `/compact` or prompt text.
- Do not persist prompt text. If `--prompt-file` is used, treat that as user-provided input and do not make extra copies.
- Do not modify real Claude Code settings.
- Do not overwrite `rolling-summary.md` automatically; keep `rolling-summary.draft.md` as a manual review artifact unless an explicit future promote command is added.
- Use only local files and standard-library Python. The only external command in the minimal controller should be `tmux send-keys` for the explicitly selected pane.

This controller complements the hook flow rather than replacing it. The hook path remains safe and passive; the controller is the only component allowed to affect a live Claude Code session, and only after the user names the pane and confirms sending.

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

- `src/userprompt_inject.py`: emits `UserPromptSubmit` hook JSON with `additionalContext`.
- `src/postcompact_record.py`: records `PostCompact` payloads to history.
- `src/merge_compact_history.py`: writes `rolling-summary.draft.md` from recent history.
- `src/daemon.py`: handles run-once, foreground loop, plist artifacts, and gated launchctl lifecycle.
- `src/status.py`: read-only runtime diagnostics and approximate compact-readiness reporting.
- `src/install_hooks.py`: safely merges hook commands into Claude Code settings.
- `SPEC.md`: product scope and detailed behavior contract.
- `CLAUDE.md`: development commands and repository-specific agent guidance.

## Development

Focused tests:

```bash
python3 -m unittest tests.test_userprompt_inject
python3 -m unittest tests.test_postcompact_record
python3 -m unittest tests.test_merge_compact_history
python3 -m unittest tests.test_daemon
python3 -m unittest tests.test_status
```

Full suite:

```bash
python3 -m unittest discover -s tests
```

Use only the Python standard library unless the project scope changes.
