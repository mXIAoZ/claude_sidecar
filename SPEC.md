# SPEC：Claude Code 极简 Sidecar Compact 验证版

## 1. 目标

构建一个 Claude Code sidecar compact 验证方案，用来判断“旁路 rolling summary 通过受支持 hook 注入”是否真的能改善长会话的上下文连续性。

当前阶段先保留轻量 hook 脚本作为最小可用路径：维护本地 `rolling-summary.md` 文件，并通过 Claude Code `UserPromptSubmit` hook 注入这份摘要。`PreCompact` 当前不支持 `additionalContext` 输出，因此不用于注入。后续按阶段升级为可分发插件、可选 daemon、自动 agent 去重/摘要、近似 token 阈值判断，以及项目本地 `.memory/` 数据目录。

当前最小可用目标：

- 使用一个本地 rolling summary 文件保存 continuity-critical 信息。
- 默认只在 `rolling-summary.md` 包含 `## Compact 前必须保留` 时注入；如需实验性每轮注入，可设置 `SIDECAR_INJECT_ALWAYS=1`。
- 可选在 `PostCompact` 阶段把 Claude Code compact 后的摘要追加到日志或 summary 草稿中。
- 默认把 summary、history、日志、草稿和后续 transcript/code 派生数据保存到当前项目 `.memory/` 目录。
- 保持当前 hook 脚本路径本地、简单、可回滚。

后续阶段目标：

- 做可分发插件，复用安全 settings merge，避免覆盖用户已有配置。
- 做可选后台 daemon，并先提供可测试的 `run-once`、有界 foreground loop 和不启动进程的 launchd plist 生成模式。
- 做自动 agent 去重和本地摘要草稿生成，默认仍不自动覆盖人工维护的 `rolling-summary.md`。
- 做近似 80% token 阈值 compact readiness 判断；除非 Claude Code 暴露精确 token 数据，否则不能声称精确控制内部 compact 阈值。
- 把摘要、日志、转录和代码相关派生数据都限制在当前项目 `.memory/` 文件夹中，不上传到外部服务。

边界：

- 不把 sidecar summary 注入不支持 `additionalContext` 的 hook。
- 不上传摘要、日志、转录或代码到任何外部服务。
- 不执行 hook payload、transcript、summary 或代码片段中的命令内容。
- 不在测试中修改真实 `~/.claude/settings.json`。

成功判断标准：

- compact 后更容易恢复当前目标、约束、决策和下一步。
- rolling summary 没有明显引入过期信息或误导模型。
- 维护成本足够低，不干扰正常 Claude Code 使用。

如果这个验证版在 1-2 周内效果明显，再继续推进 daemon、自动摘要和可分发插件阶段。

## 2. 命令

核心脚本：

```bash
python3 src/userprompt_inject.py
```

读取当前项目 `.memory/rolling-summary.md`，并输出 Claude Code `UserPromptSubmit` hook JSON，把摘要注入 `additionalContext`。

可选脚本：

```bash
python3 src/postcompact_record.py
```

从 stdin 读取 `PostCompact` hook payload，把 compact 后的摘要记录到当前项目 `.memory/compact-history.jsonl`。

可选手动维护命令：

```bash
$EDITOR .memory/rolling-summary.md
```

用户可以手动维护当前项目的 rolling summary，只保留真正需要跨 compact 保存的信息。

只读本地状态检查命令：

```bash
python3 src/status.py
```

`status.py` 是 run-once 诊断命令，只读取当前项目 `.memory/` 中已知文件并输出状态；它不写入 `errors.log`，不创建目录，不修改 `rolling-summary.md`，不编辑 `~/.claude/settings.json`，不启动 daemon，不扫描 transcript 或源码。

本地 daemon run-once / loop 命令：

```bash
python3 src/daemon.py --run-once
python3 src/daemon.py --loop --interval-seconds 300
python3 src/daemon.py --loop --interval-seconds 1 --max-runs 2
```

`daemon.py --run-once` 只执行一次本地维护：从 compact history 生成 `rolling-summary.draft.md`，并写入 metadata-only 的 `daemon-state.json`。`--loop` 在前台按间隔重复执行同一维护逻辑；测试和 smoke check 应使用 `--max-runs` 保证退出。它不会覆盖 `rolling-summary.md`，不会扫描 transcript/source，也不会编辑真实 Claude settings。

launchd plist 生成 / 检查 / 移除命令：

```bash
python3 src/daemon.py --install-agent --dry-run
python3 src/daemon.py --install-agent --plist-path /tmp/sidecar.plist
python3 src/daemon.py --agent-status --plist-path /tmp/sidecar.plist
python3 src/daemon.py --remove-agent --plist-path /tmp/sidecar.plist
```

`--install-agent --dry-run` 只打印 launchd plist XML，不写文件；`--install-agent --plist-path <path>` 只写 plist 文件和 metadata-only daemon state，不调用 `launchctl`，不 bootstrap/kickstart，不启动持久后台进程。非 dry-run 写 plist 必须显式提供 `--plist-path`，避免意外写入真实 `~/Library/LaunchAgents`。生成的 plist 固定 `WorkingDirectory` 为当前项目根，并通过 `EnvironmentVariables` 固定 `SIDECAR_COMPACT_DIR`，避免 launchd 启动时 runtime 目录漂移。

`--agent-status --plist-path <path>` 只读取显式 plist artifact 并报告 label、ProgramArguments、runtime env 和 safe flags；它不创建 runtime 目录，不写 `errors.log`，不调用 `launchctl`。`--remove-agent --plist-path <path>` 只删除显式路径中通过完整 sidecar plist 校验的 artifact：label 必须匹配，ProgramArguments 必须指向 `daemon.py --loop --interval-seconds`，runtime env 必须存在，且 `RunAtLoad` / `KeepAlive` 必须保持关闭；缺失文件安全退出，malformed、非 sidecar 或同 label 但结构无效的 plist 都不会被删除，也不会 unload/stop 任何进程。

安装 hook 脚本：

```bash
python3 src/install_hooks.py --dry-run
python3 src/install_hooks.py
```

安装脚本会合并到 `~/.claude/settings.json`，保留已有 hooks、permissions、statusLine、enabled plugins、autoCompact 和未知配置，并跳过已存在的 sidecar hook，避免重复追加。

推荐 Claude Code settings 结构：

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /absolute/path/to/claude_code_compact_sidecar/src/userprompt_inject.py",
            "timeout": 5,
            "statusMessage": "Injecting sidecar rolling summary..."
          }
        ]
      }
    ],
    "PostCompact": [
      {
        "matcher": "auto",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /absolute/path/to/claude_code_compact_sidecar/src/postcompact_record.py",
            "timeout": 5,
            "statusMessage": "Recording compact summary auto..."
          }
        ]
      },
      {
        "matcher": "manual",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /absolute/path/to/claude_code_compact_sidecar/src/postcompact_record.py",
            "timeout": 5,
            "statusMessage": "Recording compact summary manual..."
          }
        ]
      }
    ]
  }
}
```

安装到已有 `settings.json` 时，必须合并到现有 hooks 中。绝不能覆盖已有配置文件语法检查 hook、代码 review hook、HUD statusLine、permissions、enabled plugins 或 autoCompact 设置。安装脚本必须检测并跳过已存在的 sidecar hook，不能重复追加同一条 hook。默认运行时数据位于每个项目自己的 `.memory/`，测试和调试仍可用 `SIDECAR_COMPACT_DIR` 覆盖。

## 3. 项目结构

极简验证版推荐源码结构：

```text
claude_code_compact_sidecar/
  SPEC.md
  src/
    userprompt_inject.py
    summary_context.py
    postcompact_record.py
    merge_compact_history.py
    daemon.py
    install_hooks.py
    status.py
  tests/
    test_userprompt_inject.py
    test_postcompact_record.py
    test_merge_compact_history.py
    test_daemon.py
    test_sidecar_paths.py
    test_install_hooks.py
    test_status.py
```

安装后的项目运行时目录：

```text
claude_code_compact_sidecar/
  .memory/
    rolling-summary.md
    rolling-summary.draft.md
    daemon-state.json
    compact-history.jsonl
    compact-history.jsonl.1
    errors.log
```

源码目录用于开发、测试和 hook 脚本执行；`.memory/` 只保存当前项目的用户 summary、compact history 和 hook 日志，避免把运行时数据写入全局目录。

文件职责：

- `userprompt_inject.py`：读取 rolling summary，输出 `UserPromptSubmit` hook JSON，通过 `additionalContext` 注入。
- `summary_context.py`：共享 rolling summary 读取、空值处理和 head/tail 截断逻辑。
- `postcompact_record.py`：可选，记录 `PostCompact` payload，便于用户之后整理 summary。
- `merge_compact_history.py`：从 compact history 生成 `rolling-summary.draft.md`，供用户手动审查。
- `daemon.py`：支持 `--run-once`、有界 foreground `--loop`、launchd plist 生成、plist artifact 只读检查和显式安全移除；写入 draft/state 文件，或按显式路径写/删匹配的 sidecar plist，但不调用 `launchctl`，不启动/停止持久后台进程。
- `install_hooks.py`：把所需 Claude Code hooks 安全合并到 `settings.json`，保留既有配置并避免重复安装。
- `status.py` 是 run-once 诊断命令，只读取当前项目 `.memory/` 中已知文件并输出状态；它不写入 `errors.log`，不创建目录，不修改 `rolling-summary.md`，不编辑 `~/.claude/settings.json`，不启动 daemon，不扫描 transcript 或源码。
- `rolling-summary.md`：人工或半自动维护的 continuity-critical 摘要。
- `rolling-summary.draft.md`：从 compact history 生成的草稿，不会自动注入。
- `compact-history.jsonl`：可选，保存 compact 后的官方 summary 历史。
- `daemon-state.json`：`daemon.py` 写入的本地状态文件，只包含时间、模式、候选数量、draft 路径、plist path、launchctl_invoked、loop interval/run count/shutdown reason 等 metadata，不保存 summary 原文。
- `compact-history.jsonl.1`：history 轮转文件。
- `errors.log`：记录 hook/daemon 输入解析失败或文件读取失败；每条记录包含 `service` 字段，用于区分 `postcompact`、`daemon` 或其他本地维护服务。

建议 `rolling-summary.md` 格式：

```markdown
# Rolling Summary

## 当前目标

## 已确认决策

## 活动任务

## 重要约束

## 未解决问题

## Compact 前必须保留
```

写入原则：

- 只保存 compact 后继续工作真正需要的信息。
- 不保存完整聊天记录。
- 不保存临时推理过程。
- 不保存已经失效的计划。
- 不保存 secrets、tokens、credentials。
- MVP 建议把 `rolling-summary.md` 控制在 20k-40k 字符内；`UserPromptSubmit` 实际注入上限为 12k 字符，超过时保留开头的稳定背景和结尾的最新状态，中间用截断提示替代，并提示用户整理。

## 4. 代码风格

使用 Python 标准库。

通用要求：

- 使用 `pathlib.Path` 处理路径。
- 使用 `json` 读取 hook payload 和输出 hook response。
- 不引入第三方依赖。
- 脚本必须快速返回，避免拖慢 compact。
- 错误写入 `errors.log`，不要污染 stdout。
- stdout 只输出 Claude Code 需要读取的 JSON。

`userprompt_inject.py` 行为：

- 如果 `rolling-summary.md` 存在、非空且包含 `## Compact 前必须保留`，输出包含 `additionalContext` 的 `UserPromptSubmit` hook JSON。
- 如果设置 `SIDECAR_INJECT_ALWAYS=1`，允许没有 marker 的 summary 也被注入。
- 如果文件不存在、为空或没有 marker，输出有效 no-op JSON `{}`。
- 如果读取失败，记录错误并输出 no-op JSON `{}`。
- 不因 sidecar 失败阻塞用户 prompt。

示例输出：

```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "Sidecar rolling summary for continuity preservation:\\n..."
  }
}
```

注意：上面的 `\\n` 表示 JSON 字符串中的转义换行；脚本实际输出必须能通过 `python3 -m json.tool` 校验。

`postcompact_record.py` 行为：

- 从 stdin 最多读取 200k 字符的 hook payload。
- 把原始 payload 或提取出的 summary 追加到 `compact-history.jsonl`。
- 如果无法解析输入，记录 `service=postcompact` 的错误但不阻塞 Claude Code。
- 不自动覆盖 `rolling-summary.md`，除非未来明确启用。

`merge_compact_history.py` 行为：

- 读取 `compact-history.jsonl` 和 `compact-history.jsonl.1`。
- 提取最近的 `payload.summary`，生成 `rolling-summary.draft.md`。
- draft 可以包含从 compact summary 文本中提取的 path-like review hints，例如 `src/foo.py` 或 `tests/test_foo.py`；这些 hints 只来自 summary 文本，不扫描 transcript 或源码，不验证文件是否存在，也不代表文件一定相关。
- 不自动覆盖 `rolling-summary.md`；用户必须手动审查 draft，只复制仍然准确且值得长期保留的信息。
- 如果 history 缺失或没有 summary，仍生成一个空 draft 模板。

`daemon.py --run-once` / `--loop` / `--install-agent` / `--agent-status` / `--remove-agent` 行为：

- `--run-once` 从 compact history 收集最近 summary 候选，复用 `merge_compact_history.py` 的 draft 格式。
- `--run-once` 写入或更新 `rolling-summary.draft.md` 和 metadata-only 的 `daemon-state.json`；history 解析/读取失败时允许写入 `errors.log`，并标记 `service=daemon`。
- `--loop --interval-seconds N` 在前台重复生成 draft/state；`--max-runs N` 用于测试和 smoke check，保证不会留下持久进程。
- loop state 记录 `mode`、`interval_seconds`、`run_count` 和 `shutdown_reason`，但不保存 summary 原文。
- `--install-agent --dry-run` 输出有效 launchd plist XML，不写文件。
- `--install-agent --plist-path <path>` 只写 plist 文件和 metadata-only daemon state；ProgramArguments 指向当前 `daemon.py --loop --interval-seconds N`，WorkingDirectory 固定为当前项目根，EnvironmentVariables 固定 `SIDECAR_COMPACT_DIR`，stdout/stderr 日志路径位于 runtime dir。
- `--agent-status --plist-path <path>` 只读检查 plist artifact；缺失文件安全退出，malformed plist 报 invalid 且不 traceback。
- `--remove-agent --plist-path <path>` 只移除 label 匹配 sidecar 的显式 plist artifact；缺失文件安全退出，malformed 或非 sidecar plist 保留不删。
- 即使没有 history，也生成空 draft 模板并退出 0。
- 不覆盖 `rolling-summary.md`。
- 不扫描 transcript、源码或任意项目文件。
- 不调用 `launchctl`，不 bootstrap/kickstart，不启动、不停止、不 fork，不编辑真实 `~/.claude/settings.json`。

`status.py` 行为：

- 只读检查当前项目 `.memory/` 中的已知文件：`rolling-summary.md`、`rolling-summary.draft.md`、`compact-history.jsonl`、`compact-history.jsonl.1`、`errors.log` 和 `daemon-state.json`。
- 输出文件是否存在、大小、summary marker / injectable 状态、history / errors 记录数、daemon last_run 和 loop metadata。
- 不输出 summary 或 history 的原文内容。
- 不创建目录，不写入 `errors.log`，不修改 `rolling-summary.md`，不读取 transcript 或源码。

## 5. 测试策略

测试不依赖网络，也不依赖真实 Claude Code 会话。

最小测试：

- `rolling-summary.md` 不存在时，`userprompt_inject.py` 输出有效 no-op JSON `{}`。
- `rolling-summary.md` 为空时，`userprompt_inject.py` 输出有效 no-op JSON `{}`。
- `rolling-summary.md` 有内容但没有 `## Compact 前必须保留` marker 时，输出有效 no-op JSON `{}`。
- 有 marker 或设置 `SIDECAR_INJECT_ALWAYS=1` 时，`userprompt_inject.py` 输出包含 `additionalContext` 的有效 JSON。
- `rolling-summary.md` 超过大小上限时，输出“开头 + 截断提示 + 结尾”的内容和整理提示。
- 输出可以通过 `python3 -m json.tool` 校验。
- `postcompact_record.py` 能接收合成 JSON，拒绝超过 200k 字符的 payload，并写入 `compact-history.jsonl`。
- `daemon.py --run-once` 能从 compact history 生成 draft 和 metadata-only daemon state，且不覆盖 `rolling-summary.md`。
- `daemon.py --loop --max-runs` 能有界退出，更新 metadata-only daemon state，且不覆盖 `rolling-summary.md`。
- `daemon.py --install-agent --dry-run` 能输出可解析 plist 且不写文件；`--plist-path` 只写 plist，不调用 `launchctl`；非 dry-run 缺少 `--plist-path` 时安全失败。
- `daemon.py --agent-status --plist-path` 能只读检查 plist artifact，缺失/损坏文件不会创建 runtime 或 traceback。
- `daemon.py --remove-agent --plist-path` 只删除匹配 sidecar label 的显式 plist artifact，保留 malformed 或非 sidecar plist。

建议命令：

- 脚本级测试全部使用临时 `SIDECAR_COMPACT_DIR`，不会触碰真实 `.memory/` 或 `~/.claude/settings.json`。
- `install_hooks.py` 测试必须通过 `--settings` 指向临时 `settings.json`，不能修改真实 Claude Code 设置。

```bash
python3 -m unittest discover -s tests
```

只跑某一类测试：

```bash
python3 -m unittest tests.test_userprompt_inject
python3 -m unittest tests.test_postcompact_record
python3 -m unittest tests.test_merge_compact_history
python3 -m unittest tests.test_daemon
python3 -m unittest tests.test_sidecar_paths
python3 -m unittest tests.test_install_hooks
python3 -m unittest tests.test_status
```

如果想保留 `test_postcompact_record` 生成的 `compact-history.jsonl` / `errors.log`，指定 `SIDECAR_TEST_KEEP_DIR`：

```bash
SIDECAR_TEST_KEEP_DIR=/tmp/sidecar-postcompact-unittest python3 -m unittest tests.test_postcompact_record
find /tmp/sidecar-postcompact-unittest -maxdepth 2 -type f | sort
```

手动 smoke test `UserPromptSubmit` 注入 marker：

```bash
tmp=$(mktemp -d)
printf '## Compact 前必须保留\n验证 compact sidecar\nSIDE_CAR_TEST_MARKER_12345\n' > "$tmp/rolling-summary.md"
SIDECAR_COMPACT_DIR="$tmp" python3 src/userprompt_inject.py | python3 -m json.tool
```

手动 smoke test `PostCompact` 记录：

```bash
tmp=$(mktemp -d)
printf '{"session_id":"test","summary":"compacted"}' | SIDECAR_COMPACT_DIR="$tmp" python3 src/postcompact_record.py
python3 -m json.tool "$tmp/compact-history.jsonl"
```

手动 smoke test `compact-history.jsonl` 轮转：

```bash
tmp=$(mktemp -d)
python3 - <<'PY' "$tmp"
from pathlib import Path
import sys
Path(sys.argv[1], "compact-history.jsonl").write_text("x" * 5000001)
PY
printf '{"session_id":"next"}' | SIDECAR_COMPACT_DIR="$tmp" python3 src/postcompact_record.py
ls -lh "$tmp"
python3 -m json.tool "$tmp/compact-history.jsonl"
```

手动 smoke test 从 compact history 生成 draft：

```bash
tmp=$(mktemp -d)
printf '{"timestamp":"2026-05-21T10:00:00+00:00","payload":{"summary":"compacted"}}\n' > "$tmp/compact-history.jsonl"
SIDECAR_COMPACT_DIR="$tmp" python3 src/merge_compact_history.py
sed -n '1,80p' "$tmp/rolling-summary.draft.md"
```

手动 smoke test daemon run-once：

```bash
tmp=$(mktemp -d)
printf '{"timestamp":"2026-05-21T10:00:00+00:00","payload":{"summary":"daemon compact summary"}}\n' > "$tmp/compact-history.jsonl"
SIDECAR_COMPACT_DIR="$tmp" python3 src/daemon.py --run-once
python3 -m json.tool "$tmp/daemon-state.json"
sed -n '1,80p' "$tmp/rolling-summary.draft.md"
test ! -f "$tmp/rolling-summary.md"
```

手动 smoke test daemon loop：

```bash
tmp=$(mktemp -d)
printf '{"timestamp":"2026-05-21T10:00:00+00:00","payload":{"summary":"loop compact summary"}}\n' > "$tmp/compact-history.jsonl"
SIDECAR_COMPACT_DIR="$tmp" python3 src/daemon.py --loop --interval-seconds 1 --max-runs 2
python3 -m json.tool "$tmp/daemon-state.json"
test ! -f "$tmp/rolling-summary.md"
```

手动 smoke test launchd plist dry-run：

```bash
tmp=$(mktemp -d)
SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/daemon.py --install-agent --dry-run --plist-path "$tmp/sidecar.plist"
test ! -e "$tmp/sidecar.plist"
```

手动 smoke test launchd plist 写入显式路径：

```bash
tmp=$(mktemp -d)
SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/daemon.py --install-agent --plist-path "$tmp/sidecar.plist"
python3 - <<'PY' "$tmp/sidecar.plist"
import plistlib, sys
with open(sys.argv[1], 'rb') as handle:
    plistlib.load(handle)
PY
```

手动 smoke test launchd plist 检查：

```bash
tmp=$(mktemp -d)
SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/daemon.py --install-agent --plist-path "$tmp/sidecar.plist"
SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/daemon.py --agent-status --plist-path "$tmp/sidecar.plist"
test -f "$tmp/sidecar.plist"
```

手动 smoke test launchd plist 安全移除：

```bash
tmp=$(mktemp -d)
SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/daemon.py --install-agent --plist-path "$tmp/sidecar.plist"
SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/daemon.py --remove-agent --plist-path "$tmp/sidecar.plist"
test ! -e "$tmp/sidecar.plist"
python3 -m json.tool "$tmp/runtime/daemon-state.json"
```

手动 smoke test 非 sidecar plist 不移除：

```bash
tmp=$(mktemp -d)
python3 - <<'PY' "$tmp/not-sidecar.plist"
import plistlib, sys
with open(sys.argv[1], 'wb') as handle:
    plistlib.dump({"Label": "not.sidecar"}, handle)
PY
SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/daemon.py --remove-agent --plist-path "$tmp/not-sidecar.plist" || true
test -f "$tmp/not-sidecar.plist"
```

2. 运行 `userprompt_inject.py`，确认输出 JSON 有 `additionalContext`，并且包含该 marker。
3. 把 `UserPromptSubmit` hook 合并到 `~/.claude/settings.json`；最小验证可以先不启用 `PostCompact`。
4. 发送一条普通 prompt。
5. 询问模型：“刚才 sidecar summary 里要求保留的测试 marker 是什么？”
6. 如果模型能回答 `SIDE_CAR_TEST_MARKER_12345`，说明 `UserPromptSubmit` 注入链路生效。
7. 如果启用了 `PostCompact` hook，触发 compact 后检查 `compact-history.jsonl` 是否记录了 compact payload。
8. 观察 1-2 周，判断是否明显改善长会话连续性。

端到端 compact 有效性测试：

```bash
mkdir -p .memory
cat > .memory/rolling-summary.md <<'EOF'
# Rolling Summary

## 当前目标
验证 sidecar compact 是否能在 compact 后保留这句话：SIDE_CAR_TEST_MARKER_12345

## Compact 前必须保留
如果 compact 后还能看到 SIDE_CAR_TEST_MARKER_12345，说明注入成功。
EOF
python3 src/userprompt_inject.py | python3 -m json.tool
```

然后运行 `python3 src/install_hooks.py --dry-run` 检查 settings 合并结果；确认无误后再运行 `python3 src/install_hooks.py` 安装 `UserPromptSubmit` / `PostCompact` hooks，发送普通 prompt，并询问模型是否记得 `SIDE_CAR_TEST_MARKER_12345`。这个测试是 MVP 最重要的有效性判断：脚本测试只能证明 JSON 输出正确，marker 测试才能证明 hook 流程确实吸收了 sidecar summary。

## 6. 边界

默认边界：极简验证，优先低复杂度和可回滚。

始终要做：

- 保留现有 Claude Code settings，不覆盖无关配置。
- 通过受支持的 `UserPromptSubmit` hook 注入 sidecar summary。
- 保持实现本地-only。
- 保持脚本快速、失败安全。
- 让失败变成 no-op，而不是阻塞 compact。
- 控制 `rolling-summary.md` 的内容质量和大小，MVP 建议文件大小为 20k-40k 字符；`UserPromptSubmit` 实际注入上限为 12k 字符，截断时保留开头和结尾。

操作前先询问：

- 真实写入 `~/.claude/settings.json`。
- 启动、停止或安装 daemon / 后台进程。
- 启用自动覆盖 `rolling-summary.md` 的 agent 摘要流程。
- 引入非标准库依赖。
- 删除历史 summary 或 compact history。
- 发布或安装可分发插件。

永远不要做：

- 替换或删除用户已有 hooks。
- 不把 sidecar summary 注入不支持 `additionalContext` 的 hook。
- 把 secrets 写入 rolling summary。
- 执行 hook payload 中的命令或内容。
- 依赖网络访问。
- 声称该方案可以精确控制 Claude Code 内部 compact 阈值。

MVP 验收标准：

- 有 summary 时，`UserPromptSubmit` 能注入该 summary。
- 无 summary 或脚本失败时，prompt / compact 继续正常进行。
- 可选 `postcompact_record.py` 能记录 compact 后 payload。
- 现有 Claude Code 设置不被破坏。
- 如果实现安装脚本，重复安装不会重复追加同一个 hook。
- 经过 1-2 周使用后，能判断是否值得继续升级。


扩展候选目标：

1. 会话完成后发送本地可配置通知，例如钉钉 webhook；默认不启用，且不得上传 summary/history/code，除非用户明确配置通知内容。
2. 支持可视化当前编码流程、工具调用流程和关键决策流程，降低黑盒感；默认读取本地 `.memory/` 派生状态。通过前端展示，支持通过钉钉查询。
