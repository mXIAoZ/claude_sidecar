# Claude Code Compact Sidecar 中文文档

这是一个只使用 Python 标准库的 Claude Code compact sidecar。它的目标是在长会话发生 `/compact` 后，尽量保留项目当前目标、重要约束、已确认决策和下一步操作。

默认运行时文件都保存在当前项目的 `.memory/` 目录下，可以用 `SIDECAR_COMPACT_DIR` 覆盖到临时目录。hook、status、dashboard、manual merge 和 auto compact 不上传数据；daemon 的默认 LLM summary 路径会把 compact-history 派生文本发送到你配置的 OpenAI-compatible endpoint，并在成功校验后自动改写 `rolling-summary.md`。

## 当前项目概览

当前仓库已经提供一套完整的本地 compact continuity 验证栈：

- `UserPromptSubmit` 注入会读取 `.memory/rolling-summary.md`，在存在必需 marker 时通过受支持的 hook `additionalContext` 注入上下文。
- `PostCompact` 记录会把 compact payload 写入 `.memory/compact-history.jsonl`，并支持有界读取、轮转和非阻塞错误处理。
- `merge_compact_history.py` 会对最近 compact summaries 做本地去重，并生成 `rolling-summary.draft.md` 供人工审查，不会覆盖 `rolling-summary.md`。
- `daemon.py` 支持 run-once、有界 loop、默认 LLM 生成并写入 `rolling-summary.md`、launchd plist artifact 生成/读取/移除、只读 doctor，以及显式 `--launchctl-*` lifecycle。
- `operation_log.py`、`dashboard.py` 和 status 命令提供本地 metadata-only 操作时间线、LLM token 用量和健康视图；raw content 默认隐藏，只有显式请求才显示。
- `auto_compact_controller.py` 是显式 tmux controller，可以发送 `/compact`，可选等待 `PostCompact`，可选在备份旧 summary 后写入新的 `rolling-summary.md`，然后发送 prompt，让这次 prompt submit 触发 `UserPromptSubmit` 连续性注入。
- `sidecar.py` 是统一 CLI，覆盖 setup、uninstall、daemon 启动、compact 控制、hook 安装和只读 status，同时保留底层所有安全 gate。

项目仍然保持 local-first：不引入非标准库依赖，hook 不触发 `/compact`、hook 不写 summary、hook 不访问网络。daemon 的 LLM summary 是唯一会访问外部 endpoint 的路径，且只读取 sidecar 自己的 compact history。默认 setup 会写真实用户 settings；如需安全演练请传 `--settings` 指向临时文件。launchctl 状态只会在显式 daemon 启动或 `--launchctl-*` lifecycle 中改变；如需只写 plist 不启动请传 `--no-launchctl`。daemon 成功写 summary 和 auto compact `--merge-after` 都会先保存日期备份，再写入新的 `rolling-summary.md`。

## 适用场景

- 长会话经常 compact，compact 后模型容易忘记当前目标。
- 希望把 continuity-critical 信息维护在项目本地，而不是依赖完整聊天记录。
- 希望记录 `PostCompact` 官方 summary，之后人工整理成新的 rolling summary。
- 希望用终端 Dashboard 查看 sidecar 做过什么、当前是否健康、是否接近 compact 压力阈值。
- 希望实验显式 auto compact controller，但不希望 hook 自动发送 `/compact`。

## 核心工作流

推荐从最保守的手动流程开始：

```text
1. 手动维护或由 daemon 维护 .memory/rolling-summary.md
2. 预览并安装 UserPromptSubmit / PostCompact hooks
3. 正常使用 Claude Code，按需手动 /compact
4. PostCompact 把 compact payload 追加到 compact-history.jsonl
5. merge_compact_history.py 生成人工 draft，或 daemon 默认调用 LLM 写 rolling-summary.md
6. daemon 成功写入前会保存旧 summary 日期备份；失败时不覆盖旧文件
7. 用 status.py 或 dashboard.py 检查本地状态和 LLM token 用量
```

## 安全边界

- hook、status、dashboard、manual merge 和 auto compact 不上传 summary、history、prompt、日志或代码；daemon LLM summary 只会把 compact-history 派生文本发送到配置的 `SIDECAR_LLM_ENDPOINT`。
- 只使用 Python 标准库。
- hook stdout 只输出 Claude Code hook JSON；诊断写入 `errors.log`。
- `UserPromptSubmit` 中读取到的 prompt 只用于当次近似估算，不写入 `.memory/`。
- operation log 默认只记录 metadata；raw prompt / raw summary 必须显式 opt-in。
- Dashboard 默认隐藏 raw prompt / raw summary，只有 `--show-content` 才显示。
- hook 不会自动执行 `/compact`；只有显式外层 controller 在提供 `--pane` 时才会发送，或用 `--no-send` 只查看计划。
- 手动 merge 不会覆盖 `rolling-summary.md`；daemon 成功生成并校验 LLM summary 后会先把旧文件保存为 `rolling-summary.backup.<date>.md`，再写入新的 `rolling-summary.md`；auto compact `--merge-after` 也遵循同样的 backup-first 写入。
- daemon state 和 operation log 不写入 LLM prompt 原文、LLM 输出原文或 API key value。
- 默认 setup 会修改真实 `~/.claude/settings.json`；安全演练请使用临时 `--settings`。
- launchd artifact 命令不调用 `launchctl`；显式 `--launchctl-*` 或统一 daemon 启动会改变用户级 launchd 状态，除非传入 `--no-launchctl`。

## 运行时文件

默认目录：当前项目 `.memory/`。

```text
.memory/
  rolling-summary.md          # 人工维护或 daemon 自动写入的连续性摘要
  rolling-summary.backup.*.md # 自动写入前保存的日期备份
  rolling-summary.draft.md    # 从 compact history 生成的兼容草稿
  compact-history.jsonl       # 当前 PostCompact history
  compact-history.jsonl.1     # 轮转后的 PostCompact history
  operation-log.jsonl         # metadata-only 操作时间线
  operation-log.jsonl.1       # 轮转后的操作时间线
  daemon-state.json           # daemon metadata 状态
  errors.log                  # 本地诊断日志
```

建议的 `rolling-summary.md`：

```markdown
# Rolling Summary

## 当前目标

## 已确认决策

## 活动任务

## 重要约束

## 未解决问题

## Compact 前必须保留
```

只保存 compact 后继续工作真正需要的信息。不要保存完整聊天记录、secrets、tokens、credentials、临时推理过程或已经失效的计划。

## LLM Summary 默认行为

daemon maintenance 是自动写 memory 的默认路径。当 compact history 中存在 summary candidates 时，`src/daemon.py --run-once` 和 daemon loop 会读取 `.memory/compact-history.jsonl` / `.memory/compact-history.jsonl.1`，构造请求，默认用 streaming SSE 调用 OpenAI-compatible chat completions endpoint（`stream: true` 并请求 usage chunks），校验响应必须包含 `# Rolling Summary` 和 `## Compact 前必须保留`，然后写入 `.memory/rolling-summary.md`。如果旧 summary 已存在，会先保存为 `rolling-summary.backup.<date>.md`。

不需要新增 LLM CLI 参数。运行 daemon 或安装 launchd plist 前，用环境变量配置 provider：

请求路径始终使用 streaming chat completions，因此不需要额外 streaming 开关。这兼容要求 SSE streaming 的 OpenAI-compatible provider，包括 OpenRouter 风格 endpoint。provider 没返回 streaming usage chunks 时，Dashboard/status 会把 token 字段显示为 `unknown`，不会自行估算。

```bash
export SIDECAR_LLM_ENDPOINT="https://api.openai.com/v1/chat/completions"
export SIDECAR_LLM_MODEL="gpt-4.1-mini"
export SIDECAR_LLM_API_KEY_ENV="OPENAI_API_KEY"
export OPENAI_API_KEY="..."
export SIDECAR_LLM_TIMEOUT_SECONDS="30"
export SIDECAR_LLM_MAX_INPUT_CHARS="40000"
export SIDECAR_LLM_MAX_OUTPUT_CHARS="12000"
```

`SIDECAR_LLM_MAX_INPUT_CHARS` 默认是 `40000`，最高可调到 `200000`；`SIDECAR_LLM_MAX_OUTPUT_CHARS` 默认是 `12000`，最高可调到 `50000`。超过这些硬上限会在发送 LLM 请求前配置失败。

没有 compact history 时，daemon 会跳过 LLM 调用，并保持 `rolling-summary.md` 不变。配置缺失、HTTP/JSON 错误或 summary 校验失败时，daemon fail closed：不覆盖旧 summary，把错误 metadata 写入 `daemon-state.json`；启用 `--operation-log` 时写入 metadata-only 的 `daemon | llm-summary` 记录。`--run-once` 在这些 LLM 失败场景返回非零；loop 会记录失败并继续下一轮。

Dashboard、status 和 operation log 会展示最近一次 LLM summary 状态、provider/model、prompt tokens、completion tokens、total tokens、elapsed time、summary path、backup path 和 error kind。不会把 LLM prompt 原文、LLM 输出原文或 API key value 写进 daemon state / operation log。provider 没返回 usage 时，token 字段显示为 `unknown`。

daemon 只读取 sidecar 自己的 compact history。Claude Code `sessions/*.jsonl` 或其他 agent 对话记录只能作为参考材料，不会被作为 runtime 输入读取。启用真实 provider 会把 compact-history 派生 summary 文本发送到 `SIDECAR_LLM_ENDPOINT`，请把该 endpoint 视为可信环境的一部分。

## 快速开始

进入项目根目录后即可运行本地脚本，不需要安装步骤。

测试和隔离 smoke checks 见 [测试与开发](#测试与开发)。

## 统一 CLI

如果你希望用一个入口完成 setup、daemon 启动、auto compact、状态查看和卸载，使用 `src/sidecar.py`。

用临时 settings 和 plist 做安全验证：

```bash
tmp=$(mktemp -d)
SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/sidecar.py setup --settings "$tmp/settings.json" --plist-path "$tmp/sidecar.plist"
SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/sidecar.py status --json
```

真实写入用户 settings 并启动用户级 daemon：

```bash
plist="$HOME/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist"
SIDECAR_COMPACT_DIR="$PWD/.memory" \
  python3 src/sidecar.py setup \
  --plist-path "$plist" \
  --start-daemon
```

卸载 hooks 并停止/删除 daemon：

```bash
python3 src/sidecar.py uninstall --remove-daemon --plist-path "$plist"
```

这会先 bootout launchd service，再删除通过校验的 generated plist，并从 Claude Code settings 中移除 sidecar hook entries。daemon 已经停止或只想删除 plist 时可以加 `--no-launchctl`；只想删除 daemon、不移除 hooks 时可以加 `--keep-hooks`。如果要让后台 daemon 使用 LLM，请先 export `SIDECAR_LLM_*` 和对应 API key 变量；生成的 plist 会携带这些值，其中可能包含 secret。

通过统一 CLI 运行受支持的 auto compact flow：

```bash
SIDECAR_COMPACT_DIR="$PWD/.memory" \
  python3 src/sidecar.py start compact \
  --pane %2 \
  --prompt-file /path/to/prompt.txt \
  --wait-postcompact \
  --merge-after
```

`UserPromptSubmit` 注入发生在 prompt submit 时。上面的 compact flow 会发送 `/compact`，可选等待 `PostCompact`，可选在把旧 summary 保存为 `rolling-summary.backup.<date>.md` 后写入新的 `rolling-summary.md`，然后发送 prompt；这次 prompt submit 会触发已有 `UserPromptSubmit` hook 注入。CLI 不使用不受支持的 compact-time context injection。

安全控制以 opt-out/显式路径为主：用 `--settings` 写临时 settings，用 `--no-launchctl` 只写配置不启动 launchd，用 `--no-send` 只查看 compact 计划不发送 tmux keys。raw prompt / summary logging 仍然必须显式 opt-in。

Skill 和 agent 是开发辅助，不是真实 runtime 的替代品。它们适合做 review、代码检索、测试策略、诊断和文档一致性检查；真实 hook 配置、daemon lifecycle、compact 控制和 status 查看仍应使用 `src/sidecar.py`、`src/daemon.py` 和 `src/auto_compact_controller.py`，因为这些命令会强制执行项目的安全 gate。

## 安装 Hooks

先用临时 settings 文件安全测试：

```bash
tmp=$(mktemp -d)
python3 src/install_hooks.py --settings "$tmp/settings.json"
python3 -m json.tool "$tmp/settings.json"
```

确认无误后，如果你确实要写入真实 Claude Code settings：

```bash
python3 src/install_hooks.py
```

安装后的 hooks：

- `UserPromptSubmit`：运行 `src/userprompt_inject.py`，注入 `rolling-summary.md` 和 compact-readiness advisory。
- `PostCompact`：运行 `src/postcompact_record.py`，记录 `auto` 和 `manual` compact events。

## UserPromptSubmit 注入逻辑

`src/userprompt_inject.py` 会读取 runtime 里的 `rolling-summary.md`。

默认只有满足以下条件才注入：

- 文件存在；
- 文件非空；
- 包含 marker：`## Compact 前必须保留`。

如果你想实验每轮都注入，可以设置：

```bash
SIDECAR_INJECT_ALWAYS=1
```

注入失败或 summary 不满足条件时，脚本输出有效 no-op JSON `{}`，不会阻塞 Claude Code。

## PostCompact History

`src/postcompact_record.py` 从 stdin 读取 `PostCompact` hook payload，并追加到：

```text
.memory/compact-history.jsonl
```

行为：

- stdin 最多读取 200k 字符；超限会写 `errors.log` 并安全返回。
- malformed JSON 或非 object payload 会写 `errors.log`，不会阻塞 compact。
- history 超过大小限制时轮转到 `compact-history.jsonl.1`。
- 默认不记录 raw summary 到 operation log。

启用 metadata-only operation log：

```bash
printf '{"session_id":"test","summary":"compacted"}' \
  | SIDECAR_OPERATION_LOG=1 SIDECAR_COMPACT_DIR="$tmp" python3 src/postcompact_record.py
```

显式记录 raw summary：

```bash
printf '{"summary":"raw compact summary"}' \
  | SIDECAR_LOG_RAW_SUMMARY=1 SIDECAR_COMPACT_DIR="$tmp" python3 src/postcompact_record.py
```

## 生成 rolling-summary.draft.md

`src/merge_compact_history.py` 会读取：

- `compact-history.jsonl`
- `compact-history.jsonl.1`

然后提取最近的 unique summary，生成：

```text
.memory/rolling-summary.draft.md
```

它不会覆盖 `rolling-summary.md`。你需要人工审查 draft，只复制仍然准确且值得长期保留的信息。

常用命令：

```bash
SIDECAR_COMPACT_DIR="$tmp" python3 src/merge_compact_history.py
```

记录 operation log：

```bash
SIDECAR_COMPACT_DIR="$tmp" python3 src/merge_compact_history.py --operation-log
```

显式把生成的 draft 作为 raw summary 写入 operation log：

```bash
SIDECAR_COMPACT_DIR="$tmp" python3 src/merge_compact_history.py --operation-log --log-raw-summary
```

## Dashboard 和 Operation Log

Dashboard 用来回答：“sidecar 最近做过什么？”

```bash
SIDECAR_COMPACT_DIR=/path/to/runtime python3 src/dashboard.py
SIDECAR_COMPACT_DIR=/path/to/runtime python3 src/dashboard.py --watch --interval-seconds 2
SIDECAR_COMPACT_DIR=/path/to/runtime python3 src/dashboard.py --json
```

Dashboard 展示：

- runtime dir；
- overall status；
- compact-readiness；
- runtime files；
- latest LLM summary token usage；
- recent operations；
- health warnings。

operation log 文件：

```text
operation-log.jsonl
operation-log.jsonl.1
```

每条记录包含：

- `schema_version`
- `timestamp`
- `service`
- `operation`
- `status`
- `metadata`
- `content_policy`
- 可选 `raw`

raw prompt / raw summary 默认不记录，也不会显示。raw 内容必须显式 opt-in 后才可能存在：

```bash
printf '{"summary":"raw compact summary"}' \
  | SIDECAR_LOG_RAW_SUMMARY=1 SIDECAR_COMPACT_DIR="$tmp" python3 src/postcompact_record.py

SIDECAR_COMPACT_DIR="$tmp" python3 src/merge_compact_history.py --operation-log --log-raw-summary
SIDECAR_COMPACT_DIR="$tmp" python3 src/auto_compact_controller.py --pane session:window.pane --prompt-file prompt.txt --operation-log --log-raw-prompt
```

Dashboard 仍然默认隐藏 raw 内容；只有传入 `--show-content` 才会显示：

```bash
SIDECAR_COMPACT_DIR="$tmp" python3 src/dashboard.py --show-content
```

`status.py` 只显示 operation-log metadata、daemon LLM token metadata 和 raw-content flags，不打印 raw 内容。

## Status 和 Doctor

只读 runtime 状态：

```bash
SIDECAR_COMPACT_DIR=/path/to/runtime python3 src/status.py
```

`status.py` 不创建目录、不写 `errors.log`、不修改 `rolling-summary.md`、不扫描 transcript/source，也不会触发 compact。

只读 launchd doctor：

```bash
python3 src/daemon.py --doctor --plist-path /path/to/sidecar.plist
```

`--doctor` 会检查 plist 是否存在、是否是有效 sidecar plist，以及 `launchctl print` 是否能找到服务。它不会 bootstrap、kickstart、bootout、删除文件或写 daemon state。

## Daemon Maintenance

daemon maintenance 是默认 LLM 写入路径。它仍会写 `rolling-summary.draft.md` 作为兼容草稿；当 compact history 有候选 summary 时，它还会调用配置好的 LLM，在校验输出后写入 `rolling-summary.md`，并先把旧文件保存为日期备份。

运行一次：

```bash
export SIDECAR_LLM_ENDPOINT="https://api.openai.com/v1/chat/completions"
export SIDECAR_LLM_MODEL="gpt-4.1-mini"
export OPENAI_API_KEY="..."
SIDECAR_COMPACT_DIR="$PWD/.memory" python3 src/daemon.py --run-once --operation-log
```

运行有界前台 loop：

```bash
SIDECAR_COMPACT_DIR="$PWD/.memory" python3 src/daemon.py --loop --interval-seconds 1 --max-runs 2 --operation-log
```

没有 compact summary candidates 时，daemon 跳过 LLM 并保持 `rolling-summary.md` 不变。LLM 路径失败时，旧 summary 保留，`daemon-state.json` 记录 `llm_summary_status=error`，`--run-once` 返回非零。这些命令不会调用 `launchctl`；只有显式 lifecycle 命令或统一 daemon 启动会调用 launchctl。

记录 daemon operation log：

```bash
SIDECAR_COMPACT_DIR="$tmp" python3 src/daemon.py --run-once --operation-log
```

## Launchd Artifact 和 Lifecycle

写入、检查、移除显式 plist artifact：

```bash
tmp=$(mktemp -d)
SIDECAR_COMPACT_DIR="$tmp/runtime"   python3 src/daemon.py --install-agent --plist-path "$tmp/sidecar.plist"
SIDECAR_COMPACT_DIR="$tmp/runtime"   python3 src/daemon.py --agent-status --plist-path "$tmp/sidecar.plist"
SIDECAR_COMPACT_DIR="$tmp/runtime"   python3 src/daemon.py --remove-agent --plist-path "$tmp/sidecar.plist"
```

`--remove-agent` 只删除通过完整 sidecar 校验的 plist。malformed、非 sidecar、同 label 但结构无效的 plist 都会被保留。

真正调用 launchctl 的命令必须显式选择 `--launchctl-*` mode：

```bash
python3 src/daemon.py --launchctl-bootstrap --plist-path /path/to/sidecar.plist
python3 src/daemon.py --launchctl-kickstart --plist-path /path/to/sidecar.plist
python3 src/daemon.py --launchctl-status --plist-path /path/to/sidecar.plist
python3 src/daemon.py --launchctl-bootout --plist-path /path/to/sidecar.plist
```

这些命令调用前会要求 plist 存在并通过完整 sidecar 校验。`--confirm-launchctl` 仍可传入，但现在只是兼容 no-op；单独的 plist artifact 命令不会调用真实 `launchctl`。

## 持久化 Daemon 安装

只有当你明确希望创建用户级 launchd agent 时才使用这个流程。它会把 plist 写到 `~/Library/LaunchAgents`，通过显式 launchctl 命令启动，并把运行时状态保存在当前项目 `.memory/`，除非你设置 `SIDECAR_COMPACT_DIR`。如果需要 LLM summary，请在安装 plist 前 export `SIDECAR_LLM_*` 和对应 API key 变量；launchd 后台进程只能看到写入 plist 的环境变量。生成的 plist 会让 daemon loop 默认带 metadata-only `--operation-log`，因此每轮 summary 的 token usage 会写入 operation log。

先设置路径：

```bash
plist="$HOME/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist"
runtime="$PWD/.memory"
```

安装并检查 plist，不启动：

```bash
SIDECAR_COMPACT_DIR="$runtime" \
  python3 src/daemon.py --install-agent --plist-path "$plist"
SIDECAR_COMPACT_DIR="$runtime" \
  python3 src/daemon.py --agent-status --plist-path "$plist"
SIDECAR_COMPACT_DIR="$runtime" \
  python3 src/daemon.py --doctor --plist-path "$plist"
```

显式启动并查询 daemon：

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

显式停止并移除：

```bash
SIDECAR_COMPACT_DIR="$runtime" \
  python3 src/daemon.py --launchctl-bootout --plist-path "$plist"
SIDECAR_COMPACT_DIR="$runtime" \
  python3 src/daemon.py --remove-agent --plist-path "$plist"
```

`--launchctl-bootout` 会 unload launchd service，但不会删除 plist；`--remove-agent` 只删除通过校验的 generated sidecar plist，且不会调用 `launchctl`。service 可能已加载时，先 bootout 再 remove。

## Auto Compact Controller

`src/auto_compact_controller.py` 是 hook 外部的显式 tmux controller。它不会被 hook 自动调用。

### tmux 使用方式

tmux 会给 Claude Code 所在终端一个稳定 target pane，例如 `sidecar:0.0` 或 `%2`。没有 tmux 时，controller 没有安全的 pane target 来发送 `/compact` 或 prompt。

安装 tmux，创建 session，并在 session 里运行 Claude Code：

```bash
brew install tmux
tmux new -s sidecar
claude
```

常用 tmux 快捷键：

```text
Ctrl-b %      左右分屏
Ctrl-b "      上下分屏
Ctrl-b q      显示 pane 编号
Ctrl-b d      detach，离开 session 但保持进程运行
tmux attach -t sidecar    之后重新进入
```

在 Claude Code 所在 pane 里查看当前 target：

```bash
tmux display-message -p '#S:#I.#P'
```

如果已经分屏，用下面命令列出所有 pane，找到 Claude Code 所在 pane：

```bash
tmux list-panes -a -F '#S:#I.#P #{pane_id} active=#{pane_active} cmd=#{pane_current_command} title=#{pane_title}'
```

如果输出里有 `%2` 这种 `pane_id`，可以直接传给 `--pane`。controller 不会猜测当前 pane，所以真正发送前先确认目标：

```bash
SIDECAR_COMPACT_DIR="$PWD/.memory" \
  python3 src/auto_compact_controller.py \
  --pane %2 \
  --prompt-file /path/to/prompt.txt
```

真正发送示例：

```bash
SIDECAR_COMPACT_DIR="$PWD/.memory" \
  python3 src/auto_compact_controller.py \
  --pane %2 \
  --prompt-file /path/to/prompt.txt \
  --wait-postcompact \
  --merge-after
```

daemon 不需要 tmux；只有 auto compact controller 自动发送 `/compact` 或 prompt 时才需要 tmux。

真正发送必须提供 `--pane`；如果只想查看计划不发送，额外传入 `--no-send`。

示例：

```bash
SIDECAR_COMPACT_DIR="$PWD/.memory"   python3 src/auto_compact_controller.py   --pane session:window.pane   --prompt-file /path/to/prompt.txt   --wait-postcompact   --merge-after
```

常用 flag：

- `--pane <target>`：tmux target pane，发送时必填。
- `--prompt-file <path>` / `--prompt-stdin`：显式 prompt source，互斥。
- `--min-readiness low|medium|high|attention`：compact 触发阈值，默认 `high`。
- `--wait-postcompact`：发送 `/compact` 后等待 `compact-history.jsonl` metadata 变化。
- `--wait-timeout-seconds <n>` / `--poll-interval-seconds <n>`：限制等待总时长和轮询间隔。
- `--merge-after`：compact 后从 history 写入新的 `rolling-summary.md`，并把旧 summary 保存为 `rolling-summary.backup.<date>.md`。
- `--tmux-path <path>`：覆盖 tmux binary，测试 fake tmux 时使用。
- `--operation-log`：记录 metadata-only controller operations。
- `--no-send`：只输出计划，不发送 tmux keys。
- `--log-raw-prompt`：和 `--operation-log` 一起显式记录 bounded raw prompt；敏感。

## Compact Readiness

compact-readiness 是本地近似值，只基于 runtime 文件字符数/字节数 metadata。

它不是 Claude Code 内部精确 token 使用率，也不能精确判断 80% 阈值。它只能用于提示你“可能该 compact 了”。

## 测试与开发

运行全部测试：

```bash
python3 -m unittest discover -s tests
```

运行 focused tests：

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
python3 -m unittest tests.test_manual_smoke_flow
```

运行隔离 smoke checks：

```bash
tmp=$(mktemp -d)
printf '## Compact 前必须保留
Keep this across compaction.
' > "$tmp/rolling-summary.md"
SIDECAR_COMPACT_DIR="$tmp" python3 src/userprompt_inject.py | python3 -m json.tool
```

```bash
tmp=$(mktemp -d)
printf '{"session_id":"test","summary":"compacted"}'   | SIDECAR_COMPACT_DIR="$tmp" python3 src/postcompact_record.py
python3 -m json.tool "$tmp/compact-history.jsonl"
```

```bash
tmp=$(mktemp -d)
printf '{"timestamp":"2026-05-21T10:00:00+00:00","payload":{"summary":"compacted"}}
'   > "$tmp/compact-history.jsonl"
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
SIDECAR_COMPACT_DIR="$tmp/runtime"   python3 src/daemon.py --install-agent --plist-path "$tmp/sidecar.plist"
python3 src/daemon.py --agent-status --plist-path "$tmp/sidecar.plist"
```

检查 diff 空白问题：

```bash
git diff --check
```

所有测试和 smoke check 都应使用 `SIDECAR_COMPACT_DIR` 指向临时目录，避免污染真实 `.memory/`。

## 故障排查

- Dashboard 显示 `status: empty`：runtime 目录没有 sidecar 文件，或 `SIDECAR_COMPACT_DIR` 指错了。
- summary 没有注入：确认 `rolling-summary.md` 非空且包含 `## Compact 前必须保留`。
- `compact-history.jsonl` 没有生成：确认 `PostCompact` hook 已安装，并且 hook stdout 没被诊断信息污染。
- auto compact 没有发送：确认 `--pane` 指向 Claude Code 所在 tmux pane，并且没有传入 `--no-send`。
- Dashboard 看不到 raw 内容：这是默认安全行为；需要显式启用 raw logging，并传 `--show-content`。
- launchd 操作没有生效：artifact 命令不调用 `launchctl`；显式 `--launchctl-*` 或统一 daemon 启动才会改变 lifecycle state，`--no-launchctl` 会跳过启动。
- daemon 有 compact history 时返回非零：检查 `SIDECAR_LLM_ENDPOINT`、`SIDECAR_LLM_MODEL`、`SIDECAR_LLM_API_KEY_ENV`，以及 `SIDECAR_LLM_API_KEY_ENV` 指向的真实 API key 变量。

## 重要文件

- `src/sidecar.py`：统一 CLI，覆盖 setup、uninstall、daemon 启动、compact 控制、hook 安装和只读 status。
- `src/userprompt_inject.py`：输出 `UserPromptSubmit` hook JSON。
- `src/postcompact_record.py`：记录 `PostCompact` payload。
- `src/merge_compact_history.py`：从 compact history 生成 draft。
- `src/llm_summarizer.py`：发送 OpenAI-compatible streaming chat completions 请求并解析 token usage。
- `src/rolling_summary_writer.py`：校验 rolling summary 结构并 backup-first 写入。
- `src/memory_candidates.py`：提取、去重、限制 compact summary candidates。
- `src/sidecar_paths.py`：集中处理 runtime path、JSON stdout 和错误日志。
- `src/summary_context.py`：集中读取、截断 rolling summary。
- `src/readiness.py`：集中维护近似 compact-readiness 阈值和 advisory 文本。
- `src/operation_log.py`：写入、轮转、读取、检查 operation timeline。
- `src/dashboard.py`：只读终端 Dashboard。
- `src/daemon.py`：run-once、foreground loop、plist artifact、doctor、launchctl lifecycle。
- `src/auto_compact_controller.py`：显式 tmux auto compact controller。
- `src/status.py`：只读 runtime diagnostics 和 compact-readiness。
- `src/install_hooks.py`：安全合并或移除 Claude Code hooks。
- `SPEC.md`：产品范围和行为契约。
- `README.md`：英文使用文档。
- `CLAUDE.md`：仓库内 agent 指令和开发命令。
