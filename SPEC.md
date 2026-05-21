# SPEC：Claude Code 极简 Sidecar Compact 验证版

## 1. 目标

构建一个极简的 Claude Code sidecar compact 验证方案，用来判断“旁路 rolling summary 通过受支持 hook 注入”是否真的能改善长会话的上下文连续性。

这个版本不是完整插件，也不包含后台 daemon。它只维护一个本地 `rolling-summary.md` 文件，并通过 Claude Code `UserPromptSubmit` hook 注入这份摘要。`PreCompact` 当前不支持 `additionalContext` 输出，因此不用于注入。目标是在 1-2 周内用最低复杂度验证这个机制是否值得继续投入。

MVP 目标：

- 使用一个本地 rolling summary 文件保存 continuity-critical 信息。
- 默认只在 `rolling-summary.md` 包含 `## Compact 前必须保留` 时注入；如需实验性每轮注入，可设置 `SIDECAR_INJECT_ALWAYS=1`。
- 可选在 `PostCompact` 阶段把 Claude Code compact 后的摘要追加到日志或 summary 草稿中。
- 不启动 daemon，不做长期监听，不重写 Claude Code compact。
- 尽量保持本地、简单、可回滚。

MVP 非目标：

- 不做可分发插件。
- 不做后台 daemon。
- 不做自动 agent 去重。
- 不精确追踪 80% token 阈值。
- 不把 summary 注入不支持 `additionalContext` 的 hook。
- 不上传摘要、日志、转录或代码到任何外部服务。

成功判断标准：

- compact 后更容易恢复当前目标、约束、决策和下一步。
- rolling summary 没有明显引入过期信息或误导模型。
- 维护成本足够低，不干扰正常 Claude Code 使用。

如果这个验证版在 1-2 周内效果明显，再考虑升级为 daemon 或可分发插件。

## 2. 命令

核心脚本：

```bash
python3 ~/.claude/sidecar-compact/userprompt_inject.py
```

读取 `rolling-summary.md`，并输出 Claude Code `UserPromptSubmit` hook JSON，把摘要注入 `additionalContext`。

可选脚本：

```bash
python3 ~/.claude/sidecar-compact/postcompact_record.py
```

从 stdin 读取 `PostCompact` hook payload，把 compact 后的摘要记录到 `compact-history.jsonl`，或追加到 `rolling-summary.md` 的待整理区域。

可选手动维护命令：

```bash
$EDITOR ~/.claude/sidecar-compact/rolling-summary.md
```

用户可以手动维护 rolling summary，只保留真正需要跨 compact 保存的信息。

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
            "command": "python3 ~/.claude/sidecar-compact/userprompt_inject.py",
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
            "command": "python3 ~/.claude/sidecar-compact/postcompact_record.py",
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
            "command": "python3 ~/.claude/sidecar-compact/postcompact_record.py",
            "timeout": 5,
            "statusMessage": "Recording compact summary manual..."
          }
        ]
      }
    ]
  }
}
```

安装到已有 `settings.json` 时，必须合并到现有 hooks 中。绝不能覆盖已有配置文件语法检查 hook、代码 review hook、HUD statusLine、permissions、enabled plugins 或 autoCompact 设置。MVP 可以先手动安装；如果后续增加安装脚本，安装脚本必须检测并跳过已存在的 sidecar hook，不能重复追加同一条 hook。

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
  tests/
    test_userprompt_inject.py
    test_postcompact_record.py
    test_merge_compact_history.py
```

安装后的本地运行时目录：

```text
~/.claude/sidecar-compact/
  userprompt_inject.py
  summary_context.py
  postcompact_record.py
  merge_compact_history.py
  rolling-summary.md
  rolling-summary.draft.md
  compact-history.jsonl
  compact-history.jsonl.1
  errors.log
```

源码目录用于开发和测试；`~/.claude/sidecar-compact/` 只作为安装目标和运行时数据目录，避免把项目源码、用户 summary 和 hook 日志混在一起。

文件职责：

- `userprompt_inject.py`：读取 rolling summary，输出 `UserPromptSubmit` hook JSON，通过 `additionalContext` 注入。
- `summary_context.py`：共享 rolling summary 读取、空值处理和 head/tail 截断逻辑。
- `postcompact_record.py`：可选，记录 `PostCompact` payload，便于用户之后整理 summary。
- `merge_compact_history.py`：从 compact history 生成 `rolling-summary.draft.md`，供用户手动审查。
- `rolling-summary.md`：人工或半自动维护的 continuity-critical 摘要。
- `rolling-summary.draft.md`：从 compact history 生成的草稿，不会自动注入。
- `compact-history.jsonl`：可选，保存 compact 后的官方 summary 历史。
- `compact-history.jsonl.1`：history 轮转文件。
- `errors.log`：记录 hook 输入解析失败或文件读取失败。

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
- 如果无法解析输入，记录错误但不阻塞 Claude Code。
- 不自动覆盖 `rolling-summary.md`，除非未来明确启用。

`merge_compact_history.py` 行为：

- 读取 `compact-history.jsonl` 和 `compact-history.jsonl.1`。
- 提取最近的 `payload.summary`，生成 `rolling-summary.draft.md`。
- 不自动覆盖 `rolling-summary.md`；用户必须手动审查 draft，只复制仍然准确且值得长期保留的信息。
- 如果 history 缺失或没有 summary，仍生成一个空 draft 模板。

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
- `merge_compact_history.py` 能从 compact history 生成 `rolling-summary.draft.md`，且不覆盖 `rolling-summary.md`。

建议命令：

脚本级测试全部使用临时 `SIDECAR_COMPACT_DIR`，不会触碰真实 `~/.claude/sidecar-compact/` 或 `~/.claude/settings.json`。

```bash
python3 -m unittest discover -s tests
```

只跑某一类测试：

```bash
python3 -m unittest tests.test_userprompt_inject
python3 -m unittest tests.test_postcompact_record
python3 -m unittest tests.test_merge_compact_history
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

手动验证：

1. 创建 `rolling-summary.md`，写入一段带唯一 marker 的测试摘要，例如 `SIDE_CAR_TEST_MARKER_12345`。
2. 运行 `userprompt_inject.py`，确认输出 JSON 有 `additionalContext`，并且包含该 marker。
3. 把 `UserPromptSubmit` hook 合并到 `~/.claude/settings.json`；最小验证可以先不启用 `PostCompact`。
4. 发送一条普通 prompt。
5. 询问模型：“刚才 sidecar summary 里要求保留的测试 marker 是什么？”
6. 如果模型能回答 `SIDE_CAR_TEST_MARKER_12345`，说明 `UserPromptSubmit` 注入链路生效。
7. 如果启用了 `PostCompact` hook，触发 compact 后检查 `compact-history.jsonl` 是否记录了 compact payload。
8. 观察 1-2 周，判断是否明显改善长会话连续性。

端到端 compact 有效性测试：

```bash
mkdir -p ~/.claude/sidecar-compact
cp src/userprompt_inject.py src/summary_context.py src/postcompact_record.py src/merge_compact_history.py src/sidecar_paths.py ~/.claude/sidecar-compact/
cat > ~/.claude/sidecar-compact/rolling-summary.md <<'EOF'
# Rolling Summary

## 当前目标
验证 sidecar compact 是否能在 compact 后保留这句话：SIDE_CAR_TEST_MARKER_12345

## Compact 前必须保留
如果 compact 后还能看到 SIDE_CAR_TEST_MARKER_12345，说明注入成功。
EOF
python3 ~/.claude/sidecar-compact/userprompt_inject.py | python3 -m json.tool
```

然后手动安装 `UserPromptSubmit` hook，发送普通 prompt，并询问模型是否记得 `SIDE_CAR_TEST_MARKER_12345`。这个测试是 MVP 最重要的有效性判断：脚本测试只能证明 JSON 输出正确，marker 测试才能证明 hook 流程确实吸收了 sidecar summary。

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

- 修改 `~/.claude/settings.json`。
- 增加 daemon 或后台进程。
- 引入 agent 自动总结。
- 引入非标准库依赖。
- 删除历史 summary 或 compact history。
- 把这个方案升级成可分发插件。

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
