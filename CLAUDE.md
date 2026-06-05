# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- Run all tests: `python3 -m unittest discover -s tests`
- Run UserPromptSubmit injection tests: `python3 -m unittest tests.test_userprompt_inject`
- Run operation log tests: `python3 -m unittest tests.test_operation_log`
- Run dashboard tests: `python3 -m unittest tests.test_dashboard`
- Run LLM summarizer request tests: `python3 -m unittest tests.test_llm_summarizer`
- Run rolling summary writer tests: `python3 -m unittest tests.test_rolling_summary_writer`
- Run PostCompact tests: `python3 -m unittest tests.test_postcompact_record`
- Run PostCompact tests and keep generated files: `SIDECAR_TEST_KEEP_DIR=/tmp/sidecar-postcompact-unittest python3 -m unittest tests.test_postcompact_record`
- Run compact history draft tests: `python3 -m unittest tests.test_merge_compact_history`
- Run runtime path tests: `python3 -m unittest tests.test_sidecar_paths`
- Run daemon tests: `python3 -m unittest tests.test_daemon`
- Run auto compact controller tests: `python3 -m unittest tests.test_auto_compact_controller`
- Run hook installer tests: `python3 -m unittest tests.test_install_hooks`
- Run unified sidecar CLI tests: `python3 -m unittest tests.test_sidecar_cli`
- Run centralized config tests: `python3 -m unittest tests.test_sidecar_config`
- Run daemon once with no history in an isolated runtime: `tmp=$(mktemp -d); SIDECAR_COMPACT_DIR="$tmp" python3 src/daemon.py --run-once`
- Run daemon loop twice with no history in an isolated runtime: `tmp=$(mktemp -d); SIDECAR_COMPACT_DIR="$tmp" python3 src/daemon.py --loop --interval-seconds 1 --max-runs 2`
- Inspect launchd plist artifact: `tmp=$(mktemp -d); SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/daemon.py --install-agent --plist-path "$tmp/sidecar.plist"; SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/daemon.py --agent-status --plist-path "$tmp/sidecar.plist"`
- Remove launchd plist artifact safely: `tmp=$(mktemp -d); SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/daemon.py --install-agent --plist-path "$tmp/sidecar.plist"; SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/daemon.py --remove-agent --plist-path "$tmp/sidecar.plist"`
- Test launchctl lifecycle and doctor checks with fake launchctl only: `python3 -m unittest tests.test_daemon`
- Run read-only daemon doctor on an explicit plist: `python3 src/daemon.py --doctor --plist-path /path/to/sidecar.plist`
- Run read-only dashboard: `tmp=$(mktemp -d); SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/dashboard.py`
- Run unified read-only status: `tmp=$(mktemp -d); SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/sidecar.py status --json`
- Run dashboard JSON snapshot: `tmp=$(mktemp -d); SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/dashboard.py --json`
- Test auto compact controller with fake tmux only: `python3 -m unittest tests.test_auto_compact_controller`
- Persistent daemon install flow: use explicit `plist="$HOME/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist"` and `runtime="$PWD/.memory"`, then run install/status/bootstrap/kickstart/status/bootout/remove exactly as documented in `README.md`.
- Install hooks into a temporary settings file: `tmp=$(mktemp -d); python3 src/install_hooks.py --settings "$tmp/settings.json"; python3 -m json.tool "$tmp/settings.json"`
- Validate unified setup without touching real settings: `tmp=$(mktemp -d); SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/sidecar.py setup --settings "$tmp/settings.json" --plist-path "$tmp/sidecar.plist"; python3 -m json.tool "$tmp/settings.json"`
- Validate unified uninstall without touching real settings or launchctl: `tmp=$(mktemp -d); SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/sidecar.py setup --settings "$tmp/settings.json" --plist-path "$tmp/sidecar.plist"; SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/sidecar.py uninstall --settings "$tmp/settings.json" --remove-daemon --plist-path "$tmp/sidecar.plist" --no-launchctl`
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

This is a minimal Claude Code sidecar compact validation project. It does not run a daemon or modify `~/.claude/settings.json` unless the user explicitly runs the installer or unified CLI with confirmation. The goal is to test whether injecting a local rolling summary through supported hook context improves long-session continuity.

Source files live in `src/`; tests live in `tests/`. Runtime files are expected under the current project `.memory/` directory by default. Tests and smoke checks should use `SIDECAR_COMPACT_DIR` to isolate runtime state in a temporary directory.

Configuration defaults live in `sidecar.config.template.json` and are loaded by `src/sidecar_config.py`. Precedence is template defaults, optional `--config` / `SIDECAR_CONFIG_PATH` JSON, existing environment variables, then explicit CLI flags. Config files must not contain actual API key values, raw prompt/summary content, tokens, timestamps, or runtime state; store only the LLM API key environment variable name.

Key modules:

- `src/sidecar_paths.py` centralizes runtime path resolution, JSON stdout emission, and error logging. The default runtime directory is the current project `.memory/`; `SIDECAR_COMPACT_DIR` overrides it.
- `src/userprompt_inject.py` reads `rolling-summary.md` and emits Claude Code `UserPromptSubmit` hook JSON with `additionalContext`. Missing, empty, unreadable, or unmarked summaries produce a valid no-op response. Oversized summaries are truncated as head + notice + tail. Hook stdin prompt parsing is best-effort, bounded, and must not persist prompt text; compact-readiness advisories are approximate and cannot trigger compact automatically.
- `src/summary_context.py` centralizes rolling summary reading and truncation.
- `src/readiness.py` centralizes approximate compact-readiness thresholds and advisory text shared by status and UserPromptSubmit injection.
- `src/postcompact_record.py` reads `PostCompact` hook JSON from stdin and appends the parsed payload to `compact-history.jsonl`. Malformed or non-object payloads are logged to `errors.log` with `service=postcompact` and do not block. `SIDECAR_OPERATION_LOG=1` records metadata-only operations; `SIDECAR_LOG_RAW_SUMMARY=1` explicitly records bounded raw summary content.
- `src/merge_compact_history.py` reads recent unique compact history summaries and writes `rolling-summary.draft.md` for manual review. It never overwrites `rolling-summary.md`. `--operation-log` records metadata-only draft generation; `--log-raw-summary` explicitly records bounded generated draft text.
- `src/memory_candidates.py` dedupes compact summaries with normalized exact matching before draft generation; newest duplicate wins and limits apply after dedupe.
- `src/operation_log.py` appends, rotates, reads, and inspects `operation-log.jsonl` / `.1`. Logging is best-effort and metadata-only by default.
- `src/dashboard.py` renders a read-only terminal Dashboard for runtime files, compact-readiness, latest LLM summary token usage, operation timeline, and warnings. It hides raw prompt/summary content unless `--show-content` is passed.
- `src/llm_summarizer.py` implements the standard-library OpenAI-compatible streaming chat completions request layer, usage-token parsing, env config, and secret-safe error handling.
- `src/rolling_summary_writer.py` validates the required rolling-summary structure and writes `rolling-summary.md` with backup-first atomic replacement.
- `src/daemon.py` supports `--run-once`, bounded foreground `--loop`, default LLM-backed rolling-summary writes from compact history, launchd plist generation with `--install-agent`, read-only plist artifact inspection with `--agent-status`, read-only launchd registration diagnostics with `--doctor`, explicit safe artifact removal with `--remove-agent`, and explicit `--launchctl-*` lifecycle commands. Artifact modes do not call `launchctl`; `--doctor` only calls read-only `launchctl print`; only explicit launchctl lifecycle modes can change user-level launchd state. `--operation-log` records metadata-only daemon operations, including LLM token usage when summarization runs.
- `src/sidecar.py` is the unified CLI for setup, uninstall, daemon startup, explicit auto compact, and status; it delegates to existing modules and preserves their safety gates.
- `src/auto_compact_controller.py` is an explicit outer tmux controller for auto compact flows. It is not a hook: sending requires `--pane` unless `--no-send` is used, prompt text must not be printed or persisted unless `--operation-log --log-raw-prompt` is explicitly used, and `--merge-after` writes a new `rolling-summary.md` only after saving the existing file as `rolling-summary.backup.<date>.md`. Auto compact and daemon may be connected by setup/start configuration, but runtime responsibilities stay separate.
- `src/status.py` reports read-only runtime diagnostics plus approximate compact-readiness from local runtime metadata; it includes operation-log metadata/flags but never raw prompt/summary content, and it does not scan transcripts/source or trigger compact automatically.
- `src/install_hooks.py` safely merges or removes the sidecar `UserPromptSubmit` and `PostCompact` hooks in Claude Code settings. Tests must use `--settings` with a temporary file; do not target real `~/.claude/settings.json` unless the user explicitly asks.

## Runtime Contract

- stdout from hook scripts must be reserved for Claude Code hook JSON only; diagnostics go to `errors.log`.
- Operation logging is metadata-only by default. Raw prompt/summary content requires explicit opt-in (`--log-raw-prompt`, `--log-raw-summary`, or `SIDECAR_LOG_RAW_SUMMARY=1`) and Dashboard must hide it unless `--show-content` is provided. LLM prompt/output text and API key values must never be written to daemon state or operation logs.
- Hook failures should degrade to no-op behavior instead of blocking Claude Code compact. Hooks must not call LLMs or perform network requests.
- Use only the Python standard library.
- Do not add background process lifecycle, network access, or automatic settings modification unless the spec is explicitly changed. The spec now allows daemon-only OpenAI-compatible streaming LLM requests configured through environment variables.
- `src/daemon.py --run-once` and bounded `--loop --max-runs` are allowed to write draft/state files and, when compact history exists and LLM env is configured, backup-first rewrite `rolling-summary.md` under `.memory` or `SIDECAR_COMPACT_DIR`; they may write daemon-scoped parse/read/LLM failures to `errors.log` or metadata state with `service=daemon`. If no compact history exists, they skip LLM and do not write `rolling-summary.md`; if LLM configuration/request/validation fails, they must not overwrite the existing summary. `--install-agent --plist-path <path>` only writes a plist/state and carries non-secret `SIDECAR_LLM_*` settings plus the configured API key env name, never the resolved API key value; `--agent-status --plist-path <path>` is read-only and launchctl-free, `--doctor --plist-path <path>` is read-only and may only call `launchctl print`, and `--remove-agent --plist-path <path>` only removes a valid generated sidecar plist artifact with matching label, daemon loop arguments, runtime env, and safe launch flags. These artifact commands do not invoke `launchctl`. Only explicit `--launchctl-bootstrap`, `--launchctl-kickstart`, `--launchctl-status`, and `--launchctl-bootout` may invoke `launchctl`; tests must use `SIDECAR_LAUNCHCTL_PATH` with a fake binary. For persistent install docs, keep install/status/start/query/stop/remove as separate commands and set `SIDECAR_COMPACT_DIR` explicitly.
- Do not edit `~/.claude/settings.json` directly. `src/install_hooks.py` and `src/sidecar.py setup` may update it by default; use `--settings` with a temporary file for tests or validation.

## Spec Notes

`SPEC.md` is the source of product scope. The current implementation is still a hook-based local foundation with daemon-backed LLM summary writing, while the staged goals include a distributable plugin, better agent dedup/summarization, and approximate compact-readiness tracking. UserPromptSubmit injection requires the `## Compact 前必须保留` marker unless `SIDECAR_INJECT_ALWAYS=1` is set. The recommended prompt injection size is 12k characters, with truncation preserving both stable background at the start and newest state at the end. Claude Code `sessions/*.jsonl` files are reference material only and must not become runtime LLM input.
