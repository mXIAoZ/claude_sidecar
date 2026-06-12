# Sidecar Capability Plan

## Goal

Add three new capability areas while keeping the existing safety model intact:

1. Repository indexing and read strategy.
2. Explicit raw prompt audit/viewing.
3. Metadata-only Claude process and activity monitoring.

The CLI remains the only real executor. MCP exposes the same capabilities through explicit, safety-gated tools. The Skill remains limited to install, monitor/status, configure, and uninstall workflows; it must not add standalone capability menu items or bypass CLI/MCP safety gates.

## Safety Principles

- Default to metadata-only output.
- Do not persist raw prompt content unless explicitly enabled.
- Do not inspect unrelated projects or processes beyond metadata needed for status.
- Do not read full repository files when an index summary is enough.
- Keep summaries under 10% of the original content length.
- Remove context unrelated to the current project from summaries.
- Store project-specific rules in project files; store global user preferences only when explicitly requested.

## Capability 1: Repository Index And Read Strategy

### Purpose

For complex repositories, maintain a local index so agents can understand file purpose before reading entire files. This reduces token use and avoids carrying unrelated context.

### Runtime Files

```text
.memory/repo-index.json
.memory/repo-index-state.json
.memory/repo-index-errors.log
```

### Index Entry Shape

```json
{
  "path": "src/compact_sidecar/cli.py",
  "mtime_ns": 123456789,
  "size": 12000,
  "sha256": "...",
  "summary": "This file primarily implements the unified sidecar CLI. Read it fully if changing setup/uninstall/start/status behavior.",
  "summary_chars": 180,
  "source_chars": 12000,
  "summary_ratio": 0.015,
  "tags": ["cli", "setup", "status"],
  "updated_at": "2026-06-08T00:00:00+00:00"
}
```

### CLI Surface

```bash
sidecar index build
sidecar index update
sidecar index status --json
sidecar index query <keyword> --json
sidecar index explain <path>
sidecar read-plan <path-or-topic>
```

Source checkout equivalents use:

```bash
PYTHONPATH=src python3 -m compact_sidecar.cli index ...
PYTHONPATH=src python3 -m compact_sidecar.cli read-plan ...
```

### MCP Tools

- `sidecar_repo_index_status`: read-only index metadata.
- `sidecar_repo_index_query`: read-only search over index summaries and tags.
- `sidecar_repo_read_plan`: suggests which files to read fully.
- `sidecar_repo_index_update`: mutation; requires `confirm=true` and explicit runtime path.

### Skill Workflow

Do not add repository index menu items to `sidecar-manager-skill/SKILL.md`. If the user asks about repository indexing from the Skill, route it through the Monitor workflow as read-only status/query output or the Configure workflow for confirmed setup changes.

The Skill should say: "This mainly covers X; read full Y only if deeper changes are needed." It should not summarize unrelated repository context.

### Implementation Notes

- Add `compact_sidecar.runtime.repo_index`.
- Use standard library only.
- Ignore `.git/`, `.memory/`, `.claude/worktrees/`, caches, build output, and binary files.
- Prefer incremental update by mtime/size/hash.
- Recompute summary when file hash changes.
- Enforce `summary_chars <= max(1, source_chars // 10)`.
- Keep summaries factual and project-local.

## Capability 2: Explicit Raw Prompt Audit

### Purpose

Allow users to inspect original prompts when they explicitly opt in. This helps debugging hook behavior and reviewing prompt continuity, but raw prompt content is sensitive and must never be captured by default.

### Runtime Files

```text
.memory/prompt-log.jsonl
.memory/prompt-log.jsonl.1
```

### Prompt Record Shape

```json
{
  "timestamp": "2026-06-08T00:00:00+00:00",
  "session_id": "...",
  "prompt_sha256": "...",
  "prompt_chars": 1200,
  "stored_chars": 1200,
  "truncated": false,
  "raw_prompt": "only present when explicitly enabled"
}
```

### CLI Surface

```bash
sidecar prompts status --json
sidecar prompts list --json
sidecar prompts show <id-or-hash> --show-content
sidecar prompts purge --confirm
```

### MCP Tools

- `sidecar_prompt_log_status`: read-only metadata.
- `sidecar_prompt_log_list`: read-only metadata list.
- `sidecar_prompt_log_show`: content hidden by default; `show_content=true` requires `confirm=true`.
- `sidecar_prompt_log_purge`: mutation; requires `confirm=true`.

### Skill Workflow

Do not add raw prompt audit menu items to `sidecar-manager-skill/SKILL.md`. If the user asks about prompt audit from the Skill, route metadata checks through Monitor and opt-in settings changes through Configure.

Skill responses must warn that prompts may contain secrets, private text, or unrelated data.

### Implementation Notes

- Extend `compact_sidecar.hooks.userprompt` only at explicit boundary.
- Default behavior remains no raw prompt persistence.
- Enable persistence only with `SIDECAR_LOG_RAW_PROMPT=1` or a future explicit CLI setup flag.
- Dashboard and status show metadata only unless `--show-content` is passed.
- Apply bounded read limits and truncation.
- Do not send prompt content to LLMs.

## Capability 3: Claude Process And Activity Monitor

### Purpose

Monitor local Claude Code activity at a metadata level so users can see which sidecar-enabled sessions appear active, idle, stale, blocked, or unknown.

### Runtime Files

```text
.memory/claude-process-snapshot.json
```

### Status Model

Use neutral labels:

```text
active
idle
stale
blocked
unknown
```

Avoid subjective labels in code or output. If the user asks "which one is slacking," present that as an `idle_score` or `staleness_score`.

### CLI Surface

```bash
sidecar monitor claude --json
sidecar monitor claude --watch
sidecar monitor claude --project <path> --json
```

### MCP Tools

- `sidecar_claude_processes`: read-only process metadata.
- `sidecar_claude_activity`: read-only sidecar activity correlation.
- `sidecar_claude_monitor_snapshot`: optional snapshot write; requires `confirm=true`.

### Skill Workflow

Do not add Claude process monitor menu items to `sidecar-manager-skill/SKILL.md`. If the user asks about Claude process activity from the Skill, route it through the Monitor workflow as metadata-only status output.

### Implementation Notes

- Add `compact_sidecar.services.claude_monitor` or `compact_sidecar.runtime.claude_monitor` depending on whether it remains read-only.
- Use platform-safe process inspection where possible.
- Metadata only: pid, command basename, cwd when available, start time, CPU hints, runtime dir match, last operation-log timestamp.
- Do not capture screen content.
- Do not read raw prompts unless Capability 2 has explicit opt-in and the user passes `--show-content`.
- Correlate with `.memory/operation-log.jsonl`, daemon state, and prompt metadata.

## Cross-Layer Architecture

```text
compact_sidecar.runtime.repo_index
compact_sidecar.runtime.prompt_log
compact_sidecar.services.claude_monitor
        |
        v
compact_sidecar.api
        |
        +--> compact_sidecar.cli
        +--> compact_sidecar.mcp.server
        +--> sidecar-manager-skill/SKILL.md four workflow routes
```

The CLI should call runtime/service modules directly. MCP should call `compact_sidecar.api` wrappers. The Skill should call CLI/MCP commands only through documented safety gates.

## Suggested Phases

### Phase 1: Repo Index

- Add repo index runtime module.
- Add CLI `index` and `read-plan` commands.
- Add MCP read-only index tools and gated update tool.
- Keep Skill routing inside the existing Monitor/Configure workflows; do not add new Skill menu entries.
- Test index build/update/query and 10% summary cap.

### Phase 2: Prompt Audit

- Add prompt log runtime module.
- Extend UserPromptSubmit hook with explicit raw prompt opt-in.
- Add CLI `prompts` commands.
- Add MCP prompt metadata tools and confirmed content reveal.
- Keep Skill routing inside the existing Monitor/Configure workflows; do not add new Skill menu entries.
- Test default no raw persistence, opt-in persistence, truncation, and hidden dashboard/status behavior.

### Phase 3: Claude Monitor

- Add metadata-only monitor module.
- Add CLI `monitor claude` command.
- Add MCP monitor tools.
- Keep Skill routing inside the existing Monitor workflow; do not add new Skill menu entries.
- Test process parsing with fake process data and operation-log correlation.

## Verification Plan

Run after each phase:

```bash
python3 -m unittest discover -s tests
python3 -m pip wheel . --no-deps -w "$(mktemp -d)"
tmp=$(mktemp -d); SIDECAR_COMPACT_DIR="$tmp/runtime" PYTHONPATH=src python3 -m compact_sidecar.cli status --json
PYTHONPATH=src python3 -m compact_sidecar.mcp.server --self-test
```

Additional phase-specific checks:

```bash
tmp=$(mktemp -d); SIDECAR_COMPACT_DIR="$tmp/runtime" PYTHONPATH=src python3 -m compact_sidecar.cli index build --json
tmp=$(mktemp -d); SIDECAR_LOG_RAW_PROMPT=1 SIDECAR_COMPACT_DIR="$tmp/runtime" PYTHONPATH=src python3 -m compact_sidecar.hooks.userprompt < /tmp/prompt.json
tmp=$(mktemp -d); SIDECAR_COMPACT_DIR="$tmp/runtime" PYTHONPATH=src python3 -m compact_sidecar.cli monitor claude --json
```

## Open Questions

- Should repo index summaries be generated purely by local heuristics, or may a future daemon-only LLM path summarize files when explicitly configured?
- Should raw prompt logging be configurable only by environment variable, or also by project-local settings setup?
- What minimum process metadata is available and reliable across macOS, Linux, and remote environments?
- Should manual LLM environment test helpers in `tests/manual/` remain ignored local files, or should sanitized versions become tracked manual smoke tests?
