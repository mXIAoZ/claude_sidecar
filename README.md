# Claude Code Compact Sidecar

A local, standard-library-only experiment for preserving Claude Code long-session continuity across compaction. The sidecar keeps a project-local rolling summary and injects it through the supported `UserPromptSubmit` hook.

## What It Does

- Stores runtime state under the current project `.memory/` directory by default.
- Reads `.memory/rolling-summary.md` and injects it only when it contains the marker `## Compact 前必须保留`.
- Records `PostCompact` summaries to `compact-history.jsonl` for later review.
- Builds `rolling-summary.draft.md` from recent compact history without overwriting the human-maintained summary.
- Provides daemon maintenance commands and explicit, gated launchd lifecycle commands.

## Safety Boundaries

- No data is uploaded to external services.
- Hook stdout is reserved for Claude Code hook JSON; diagnostics go to `errors.log`.
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

Generate a draft summary from compact history:

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

## Important Files

- `src/userprompt_inject.py`: emits `UserPromptSubmit` hook JSON with `additionalContext`.
- `src/postcompact_record.py`: records `PostCompact` payloads to history.
- `src/merge_compact_history.py`: writes `rolling-summary.draft.md` from recent history.
- `src/daemon.py`: handles run-once, foreground loop, plist artifacts, and gated launchctl lifecycle.
- `src/status.py`: read-only runtime diagnostics.
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
