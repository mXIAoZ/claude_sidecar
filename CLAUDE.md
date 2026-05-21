# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- Run all tests: `python3 -m unittest discover -s tests`
- Run UserPromptSubmit injection tests: `python3 -m unittest tests.test_userprompt_inject`
- Run PostCompact tests: `python3 -m unittest tests.test_postcompact_record`
- Run PostCompact tests and keep generated files: `SIDECAR_TEST_KEEP_DIR=/tmp/sidecar-postcompact-unittest python3 -m unittest tests.test_postcompact_record`
- Run compact history draft tests: `python3 -m unittest tests.test_merge_compact_history`
- Run one test case: `python3 -m unittest tests.test_userprompt_inject.UserPromptInjectTests.test_non_empty_summary_is_injected`
- Validate UserPromptSubmit summary injection:
  `tmp=$(mktemp -d); printf '## Compact 前必须保留\n验证 compact sidecar\n' > "$tmp/rolling-summary.md"; SIDECAR_COMPACT_DIR="$tmp" python3 src/userprompt_inject.py | python3 -m json.tool`
- Validate PostCompact recording:
  `tmp=$(mktemp -d); printf '{"session_id":"test","summary":"compacted"}' | SIDECAR_COMPACT_DIR="$tmp" python3 src/postcompact_record.py; python3 -m json.tool "$tmp/compact-history.jsonl"`
- Validate compact history rotation:
  `tmp=$(mktemp -d); python3 - <<'PY' "$tmp"
from pathlib import Path
import sys
Path(sys.argv[1], "compact-history.jsonl").write_text("x" * 5000001)
PY
printf '{"session_id":"next"}' | SIDECAR_COMPACT_DIR="$tmp" python3 src/postcompact_record.py; ls -lh "$tmp"; python3 -m json.tool "$tmp/compact-history.jsonl"`
- Generate a rolling summary draft from compact history without touching real Claude config:
  `tmp=$(mktemp -d); printf '{"timestamp":"2026-05-21T10:00:00+00:00","payload":{"summary":"compacted"}}\n' > "$tmp/compact-history.jsonl"; SIDECAR_COMPACT_DIR="$tmp" python3 src/merge_compact_history.py; sed -n '1,80p' "$tmp/rolling-summary.draft.md"`

## Architecture

This is a minimal Claude Code sidecar compact validation project. It does not run a daemon, install hooks automatically, or modify `~/.claude/settings.json`. The goal is to test whether injecting a local rolling summary through supported hook context improves long-session continuity.

Source files live in `src/`; tests live in `tests/`. Runtime files are expected under `~/.claude/sidecar-compact/` when installed manually, but tests and smoke checks should use `SIDECAR_COMPACT_DIR` to isolate runtime state in a temporary directory.

Key modules:

- `src/sidecar_paths.py` centralizes runtime path resolution, JSON stdout emission, and error logging. The default runtime directory is `~/.claude/sidecar-compact/`; `SIDECAR_COMPACT_DIR` overrides it.
- `src/userprompt_inject.py` reads `rolling-summary.md` and emits Claude Code `UserPromptSubmit` hook JSON with `additionalContext`. Missing, empty, unreadable, or unmarked summaries produce a valid no-op response. Oversized summaries are truncated as head + notice + tail.
- `src/summary_context.py` centralizes rolling summary reading and truncation.
- `src/postcompact_record.py` reads `PostCompact` hook JSON from stdin and appends the parsed payload to `compact-history.jsonl`. Malformed or non-object payloads are logged to `errors.log` and do not block.
- `src/merge_compact_history.py` reads recent compact history and writes `rolling-summary.draft.md` for manual review. It never overwrites `rolling-summary.md`.

## Runtime Contract

- stdout from hook scripts must be reserved for Claude Code hook JSON only; diagnostics go to `errors.log`.
- Hook failures should degrade to no-op behavior instead of blocking Claude Code compact.
- Use only the Python standard library.
- Do not add background processes, daemon behavior, network access, or automatic settings modification unless the spec is explicitly changed.
- Do not edit `~/.claude/settings.json` as part of this MVP unless the user explicitly requests hook installation.

## Spec Notes

`SPEC.md` is the source of product scope. It explicitly keeps this MVP local-only and experimental: no daemon, no automatic agent summarization, no precise token-threshold tracking, and no unsupported hook output. UserPromptSubmit injection requires the `## Compact 前必须保留` marker unless `SIDECAR_INJECT_ALWAYS=1` is set. The recommended prompt injection size is 12k characters, with truncation preserving both stable background at the start and newest state at the end.
