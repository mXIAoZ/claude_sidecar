# SPEC：Claude Code 极简 Sidecar Compact 验证版

## 1. 目标

构建一个 Claude Code sidecar compact 验证方案，用来判断“旁路 rolling summary 通过受支持 hook 注入”是否真的能改善长会话的上下文连续性。

当前阶段先保留轻量 hook 脚本作为最小可用路径：维护本地 `rolling-summary.md` 文件，并通过 Claude Code `UserPromptSubmit` hook 注入这份摘要。`PreCompact` 当前不支持 `additionalContext` 输出，因此不用于注入。后续按阶段升级为可分发插件、可选 daemon、自动 agent 去重/摘要、近似 token 阈值判断，以及项目本地 `.memory/` 数据目录。

当前实现已经覆盖本地验证闭环：

- `UserPromptSubmit` 读取 `.memory/rolling-summary.md` 并通过受支持的 `additionalContext` 注入 reviewed rolling summary。
- `PostCompact` 把 compact payload 追加到 `.memory/compact-history.jsonl`，并支持有界读取、错误降级、文件轮转和 metadata-only operation log。
- `merge_compact_history.py` 从最近 history 中提取、去重 summary，生成 `rolling-summary.draft.md`，但不自动覆盖人工维护的 `rolling-summary.md`。
- `daemon.py` 支持 run-once、bounded foreground loop、默认 LLM 生成并写入 `rolling-summary.md`、launchd plist artifact 管理、read-only doctor 和显式确认的 launchctl lifecycle。
- `dashboard.py` / status 路径提供只读健康视图、compact-readiness 近似信号、LLM token usage 和 operation timeline，默认隐藏 raw prompt/summary。
- `auto_compact_controller.py` 作为 hook 外层显式 tmux controller，提供 `--pane` 时才会发送 `/compact` 或 prompt，`--no-send` 只输出计划；`--merge-after` 会先备份旧 `rolling-summary.md`，再直接写入新的 `rolling-summary.md`。
- `sidecar.py` 提供统一 CLI，把 hook setup、daemon startup、auto compact 和状态查看聚合到一个入口，同时保留原有安全 gate。

保留的目标和边界：

- 使用一个本地 rolling summary 文件保存 continuity-critical 信息。
- 默认只在 `rolling-summary.md` 包含 `## Compact 前必须保留` 时注入；如需实验性每轮注入，可设置 `SIDECAR_INJECT_ALWAYS=1`。
- 可选在 `PostCompact` 阶段把 Claude Code compact 后的摘要追加到日志或 summary 草稿中。
- 默认把 summary、history、日志、草稿和后续 transcript/code 派生数据保存到当前项目 `.memory/` 目录。
- 保持当前 hook 脚本路径本地、简单、可回滚。

后续阶段目标：

- 做可分发插件，复用安全 settings merge，避免覆盖用户已有配置。
- 持续完善可选后台 daemon：`run-once`、有界 foreground loop 和 launchd plist 生成必须可测试；daemon 默认用配置好的 LLM 从 compact history 写入 `rolling-summary.md`，并 backup-first。
- 做自动 agent 去重和摘要生成；手动 merge 仍只写 draft，daemon LLM 路径负责自动写 memory。
- 做近似 80% token 阈值 compact readiness 判断；除非 Claude Code 暴露精确 token 数据，否则不能声称精确控制内部 compact 阈值。UserPromptSubmit 只能做 best-effort advisory，提示用户手动 `/compact` 后重发输入，不能自动执行 compact；真正发送 `/compact` 必须由显式外层 controller 针对用户指定 tmux pane 执行。
- 做显式 auto compact controller：只在用户提供 `--pane` 且未传入 `--no-send` 时通过 tmux 发送 `/compact` 和 prompt，可选等待 `PostCompact` history 更新；启用 `--merge-after` 时先把旧 `rolling-summary.md` 保存为日期副本，再直接写入新的 `rolling-summary.md`。
- 做终端 Dashboard 和 operation log，把本地 hook/controller/daemon 操作可视化；默认只记录 metadata，raw prompt/summary 必须显式 opt-in。
- 把摘要、日志、转录和代码相关派生数据都限制在当前项目 `.memory/` 文件夹中；唯一外部传输例外是 daemon LLM summary 路径把 compact-history 派生文本发送到用户配置的 `SIDECAR_LLM_ENDPOINT`。

边界：

- 所有配置默认值必须集中在项目根的 `sidecar.config.template.json`，并按环境变量名、路径、hooks、summary、readiness、history、operation log、LLM、daemon/launchd、controller、dashboard/status、CLI defaults 和测试诊断分类。运行时配置优先级固定为：模板默认值 < `--config` / `SIDECAR_CONFIG_PATH` 指定的 JSON 文件 < 现有环境变量 < 显式 CLI flags。配置文件不能包含 API key value、raw prompt、raw summary、tokens、timestamps 或 runtime state；LLM secret 只能通过 `api_key_env` 指向的环境变量读取。生成的 hook 命令和 launchd plist 必须传播 `SIDECAR_CONFIG_PATH`，让所有入口使用同一 resolved configuration。
- 不把 sidecar summary 注入不支持 `additionalContext` 的 hook。
- 除 daemon LLM summary 发送 compact-history 派生文本到用户配置的 endpoint 外，不上传摘要、日志、转录或代码到外部服务。
- 不执行 hook payload、transcript、summary 或代码片段中的命令内容。
- 不在测试中修改真实 `~/.claude/settings.json`。

成功判断标准：

- compact 后更容易恢复当前目标、约束、决策和下一步。
- rolling summary 没有明显引入过期信息或误导模型。
- 维护成本足够低，不干扰正常 Claude Code 使用。

如果这个验证版在 1-2 周内效果明显，再继续推进 daemon、自动摘要和可分发插件阶段。

## 2. 命令

统一 CLI 命令：

```bash
python3 src/sidecar.py setup --settings /tmp/sidecar-settings.json --plist-path /tmp/sidecar.plist
python3 src/sidecar.py status --json
python3 src/sidecar.py start daemon --plist-path /tmp/sidecar.plist
python3 src/sidecar.py start compact --pane session:window.pane --prompt-file /path/to/prompt.txt --wait-postcompact --merge-after
```

`sidecar.py` 是现有脚本的统一入口。它不会改变 hook 能力边界：`UserPromptSubmit` 仍然只在 prompt submit 时注入 `additionalContext`。自动 compact flow 的受支持语义是 controller 发送 `/compact`，可选等待 `PostCompact`，可选先备份旧 `rolling-summary.md` 再写入新的 `rolling-summary.md`，然后发送 prompt，由这次 prompt submit 触发 `UserPromptSubmit` 注入。`sidecar.py setup` 默认可以写真实 `~/.claude/settings.json`；用 `--settings` 写临时文件，用 `--no-launchctl` 跳过 launchd 启动，用 `--no-send` 跳过 tmux 发送。tmux 发送仍必须显式提供 `--pane`。

Skill / Agent 职责边界：

- Skill 适合封装固定开发流程，例如 code review、测试清单、文档检查或安全审查输出格式；它可以指导如何分析和呈现结果，但不应作为后台 daemon 或隐式 compact 执行器。
- Agent 适合并行执行独立的开发辅助任务，例如代码检索、二次 review、测试覆盖分析或文档一致性检查；它可以帮助实现和验证，但不应替代真实 runtime controller。
- 真实 sidecar 操作必须由项目 CLI / daemon / controller 执行：hook 配置走 `sidecar.py setup` 或 `install_hooks.py`，daemon lifecycle 走 `sidecar.py start daemon` 或 `daemon.py`，auto compact 走 `sidecar.py start compact` 或 `auto_compact_controller.py`。
- 不能让 skill/agent 隐式执行真实 runtime 操作：真实 settings 写入、launchctl lifecycle、tmux 发送都必须走项目 CLI；需要保守运行时使用 `--settings`、`--no-launchctl` 或 `--no-send`。
- 推荐组合是：skill/agent 用于开发、review、测试、诊断和文档维护；项目 CLI 用于真实 hook 配置、daemon 启动、compact 控制和状态查看。

核心脚本：

```bash
python3 src/userprompt_inject.py
```

读取当前项目 `.memory/rolling-summary.md`，并输出 Claude Code `UserPromptSubmit` hook JSON，把摘要注入 `additionalContext`。如果 hook stdin 提供当前 prompt，脚本会用有界读取做本地近似估算；当 prompt + 注入摘要 + runtime metadata 达到 high readiness 阈值时，只通过 `additionalContext` 注入 compact-readiness advisory，建议用户先手动运行 `/compact` 再重发输入。该路径不保存 prompt 文本，不写 `errors.log`，不阻断 prompt，也不自动执行 compact。

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

`status.py` 是 run-once 诊断命令，只读取当前项目 `.memory/` 中已知文件并输出状态；它不写入 `errors.log`，不创建目录，不修改 `rolling-summary.md`，不编辑 `~/.claude/settings.json`，不启动 daemon，不扫描 transcript 或源码。它还输出 `compact-readiness` 近似信号：该信号只基于本地 runtime 文件的字符数/字节数 metadata，不能代表 Claude Code 内部精确 token 使用率，也不会自动触发 compact。

本地 daemon run-once / loop 命令：

```bash
python3 src/daemon.py --run-once
python3 src/daemon.py --loop --interval-seconds 300
python3 src/daemon.py --loop --interval-seconds 1 --max-runs 2
```

`daemon.py --run-once` 只执行一次维护：从 compact history 生成 `rolling-summary.draft.md`，并在有候选 summary 时默认调用环境变量配置的 OpenAI-compatible LLM，校验输出后 backup-first 写入 `rolling-summary.md`，同时写入 metadata-only 的 `daemon-state.json`。`--loop` 在前台按间隔重复执行同一维护逻辑；测试和 smoke check 应使用 `--max-runs` 保证退出。它不会扫描 transcript/source，也不会编辑真实 Claude settings。

LLM 配置不通过 CLI 参数传入，只读取环境变量：`SIDECAR_LLM_ENDPOINT`、`SIDECAR_LLM_MODEL`、`SIDECAR_LLM_API_KEY_ENV`、该变量指向的 API key、`SIDECAR_LLM_TIMEOUT_SECONDS`、`SIDECAR_LLM_MAX_INPUT_CHARS`、`SIDECAR_LLM_MAX_OUTPUT_CHARS`。`SIDECAR_LLM_MAX_INPUT_CHARS` 默认 `40000`、硬上限 `200000`；`SIDECAR_LLM_MAX_OUTPUT_CHARS` 默认 `12000`、硬上限 `50000`，超过上限时必须在发送 LLM 请求前配置失败。没有 compact history 时跳过 LLM；有 history 但配置/请求/校验失败时 fail closed，不覆盖旧 summary，`--run-once` 返回非零，loop 记录失败后继续。daemon state 和 operation log 只能保存 token usage、provider/model、路径、耗时、候选数量和 error kind 等 metadata，不能保存 LLM prompt 原文、LLM output 原文或 API key value。

本地 auto compact controller 命令：

```bash
python3 src/auto_compact_controller.py --pane session:window.pane --prompt-file /path/to/prompt.txt
python3 src/auto_compact_controller.py --pane session:window.pane --prompt-file /path/to/prompt.txt --wait-postcompact --merge-after
```

`auto_compact_controller.py` 是 hook 外层的显式会话控制器。它必须提供 `--pane` 且未传入 `--no-send` 才会通过 tmux 发送内容；controller 不猜测当前 tmux pane，不读取 shell history，不通过 shell 字符串执行命令。它用近似 readiness 判断是否先发送 `/compact`，可选用 `--wait-postcompact` 等待 `compact-history.jsonl` metadata 变化；启用 `--merge-after` 时会从 compact history 生成新的 `rolling-summary.md`，并先把旧 summary 保存为 `rolling-summary.backup.<date>.md`。随后 controller 发送原 prompt。prompt 文本只能来自 `--prompt-file` 或 `--prompt-stdin`，不会写入 `.memory/`、日志、stdout/stderr 或 state。

launchd plist 生成 / 检查 / 移除命令：

```bash
python3 src/daemon.py --install-agent --plist-path /tmp/sidecar.plist
python3 src/daemon.py --agent-status --plist-path /tmp/sidecar.plist
python3 src/daemon.py --doctor --plist-path /tmp/sidecar.plist
python3 src/daemon.py --remove-agent --plist-path /tmp/sidecar.plist
python3 src/daemon.py --launchctl-bootstrap --plist-path /tmp/sidecar.plist
python3 src/daemon.py --launchctl-kickstart --plist-path /tmp/sidecar.plist
python3 src/daemon.py --launchctl-status --plist-path /tmp/sidecar.plist
python3 src/daemon.py --launchctl-bootout --plist-path /tmp/sidecar.plist
```

`--install-agent --plist-path <path>` 只写 plist 文件和 metadata-only daemon state，不调用 `launchctl`，不 bootstrap/kickstart，不启动持久后台进程。写 plist 必须显式提供 `--plist-path`，避免意外写入真实 `~/Library/LaunchAgents`。生成的 plist 固定 `WorkingDirectory` 为当前项目根，并通过 `EnvironmentVariables` 固定 `SIDECAR_COMPACT_DIR`，避免 launchd 启动时 runtime 目录漂移。

`--agent-status --plist-path <path>` 只读取显式 plist artifact 并报告 label、ProgramArguments、runtime env 和 safe flags；它不创建 runtime 目录，不写 `errors.log`，不调用 `launchctl`。`--doctor --plist-path <path>` 是只读诊断命令：它先检查 plist 是否存在并通过完整 sidecar 校验，只有校验通过且平台支持时才调用只读 `launchctl print gui/<uid>/<label>` 检查服务是否已注册；它不 bootstrap、kickstart、bootout、删除文件、写 `daemon-state.json` 或编辑真实配置。`--remove-agent --plist-path <path>` 只删除显式路径中通过完整 sidecar plist 校验的 artifact：label 必须匹配，ProgramArguments 必须指向 `daemon.py --loop --interval-seconds`，runtime env 必须存在，且 `RunAtLoad` / `KeepAlive` 必须保持关闭；缺失文件安全退出，malformed、非 sidecar 或同 label 但结构无效的 plist 都不会被删除，也不会 unload/stop 任何进程。

显式 `--launchctl-bootstrap`、`--launchctl-kickstart`、`--launchctl-status`、`--launchctl-bootout` 会调用对应的 launchctl lifecycle 命令。这些命令在调用前要求显式 `--plist-path` 存在并通过完整 sidecar plist 校验；它们只写 metadata-only `daemon-state.json`，不保存 summary 原文，不删除 plist，不编辑真实 `~/.claude/settings.json`。自动测试必须通过 `SIDECAR_LAUNCHCTL_PATH` 指向 fake launchctl，不能调用真实系统 `launchctl`。

持久化安装流程必须保持分步显式：先用 `--install-agent --plist-path "$HOME/Library/LaunchAgents/com.claude-code-compact-sidecar.daemon.plist"` 写入用户指定 plist，再用 `--agent-status` 只读校验，然后才允许用户手动执行 `--launchctl-bootstrap` / `--launchctl-kickstart` / `--launchctl-status`。卸载流程必须先 `--launchctl-bootout`，再 `--remove-agent` 删除 plist artifact；`bootout` 不删除文件，`remove-agent` 不调用 launchctl。文档中的真实安装示例必须显式设置 `SIDECAR_COMPACT_DIR`，避免 launchd runtime 目录漂移。

安装 hook 脚本：

```bash
python3 src/install_hooks.py --settings /tmp/sidecar-settings.json
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


## 2.1 Skill、MCP 和 Packaging 产品契约

产品化后的仓库同时支持三种入口：source checkout 脚本、packaged console entry points、Claude Code Skill/MCP 辅助层。

Source checkout 必须继续可用：

```bash
python3 src/sidecar.py status --json
python3 src/mcp_server.py --self-test
```

Packaging metadata 必须提供：

```bash
sidecar --help
sidecar status --json
sidecar-mcp --self-test
```

`sidecar` 映射到统一 CLI；`sidecar-mcp` 启动 stdio MCP server。`sidecar.config.template.json` 和 `sidecar-manager-skill/SKILL.md` 必须能在源码 checkout、editable install 和 wheel install 上下文中被找到或作为分发 asset 明确记录。

Skill 契约：

- `sidecar-manager-skill/SKILL.md` 是 operator workflow，负责诊断菜单、演练流程、确认安装/卸载、LLM 配置说明和排障。
- Skill 不作为 daemon、隐藏 executor 或 CLI safety gate 替代品。
- Skill 中的真实写操作必须调用 `src/sidecar.py`、`src/daemon.py`、`src/install_hooks.py` 或 `src/auto_compact_controller.py` 的现有门禁。
- 默认 settings target 是项目本地 `.claude/settings.local.json`；全局 `~/.claude/settings.json` 必须由用户明确要求。

MCP 契约：

- `src/mcp_server.py` 使用标准库实现 stdio JSON-RPC，支持 `initialize`、`tools/list` 和 `tools/call`。
- 只读 tools：`sidecar_status`、`sidecar_dashboard`、`sidecar_config_validate`、`sidecar_operation_log`。这些调用不得创建 runtime 目录、写文件、调用 launchctl/tmux 或访问网络。
- 演练 tools：`sidecar_setup_rehearsal`、`sidecar_daemon_plist_rehearsal`、`sidecar_daemon_status`、`sidecar_compact_plan_preview`。这些工具只写调用方显式提供的 settings/runtime/plist artifact 或读取显式路径；永不调用 launchctl/tmux。
- 写操作 tools：`sidecar_hook_install`、`sidecar_hook_uninstall`、`sidecar_daemon_plist_write`、`sidecar_daemon_plist_remove`、`sidecar_daemon_run_once`、`sidecar_launchctl_lifecycle`、`sidecar_tmux_compact` 默认随 `sidecar-mcp` 暴露。每个真实写操作都必须要求 `confirm: true` 和显式目标路径；全局 settings 写入还必须要求 `allow_global_settings: true`；tmux 真发送必须要求显式 pane 和 `no_send: false`。
- MCP 默认响应必须隐藏 raw prompt、raw summary 和 API key value。`show_content` 类参数只能显示已经通过显式 raw logging opt-in 保存过的内容。
- MCP client 配置示例只能包含显式本地路径和非 secret 环境变量；不得包含 API key value。

回滚契约：

- hook uninstall 通过 `sidecar.py uninstall` 或 `install_hooks.py --uninstall` 类入口执行，不能直接覆盖 settings 文件。
- daemon 卸载先显式 `launchctl bootout`，再 `--remove-agent` 删除通过校验的 plist；`bootout` 不删除文件，`remove-agent` 不调用 launchctl。
- Skill 移除是删除已安装的 `sidecar-manager` skill asset；packaged 命令移除走 `pip uninstall claude-code-compact-sidecar`。
- `.memory/` runtime cleanup 必须保持人工决定，因为 summary/history/logs 可能有上下文或审计价值。

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
    auto_compact_controller.py
    operation_log.py
    dashboard.py
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
- `daemon.py`：支持 `--run-once`、有界 foreground `--loop`、默认 LLM 写入 rolling summary、launchd plist 生成、plist artifact 只读检查和显式安全移除；artifact 命令不调用 `launchctl`。显式 `--launchctl-*` 命令可在通过 plist 校验后调用 launchctl 管理用户级 launchd state。
- `llm_summarizer.py`：只用标准库发送 OpenAI-compatible streaming chat completions 请求，解析 SSE chunks 中的 `choices[].delta.content` 和 usage token，并保证错误消息不泄漏 API key value。
- `rolling_summary_writer.py`：集中校验 `# Rolling Summary` 和 `## Compact 前必须保留`，并用 backup-first + atomic replace 写入 `rolling-summary.md`。
- `auto_compact_controller.py`：hook 外层的显式 tmux controller；提供 `--pane` 时才会发送 `/compact` 或 prompt，`--no-send` 只输出计划，可选等待 `PostCompact` history 变化；启用 `--merge-after` 时先备份旧 `rolling-summary.md`，再写入新的 `rolling-summary.md`。默认不保存 prompt 文本。
- `operation_log.py`：写入、读取、轮转和检查 project-local operation timeline；logging failure 必须 best-effort，不阻断 hook/controller/daemon。
- `dashboard.py`：只读终端 Dashboard，展示 runtime health、compact-readiness、known files、recent operations 和 warnings；默认隐藏 raw content。
- `sidecar.py`：统一 CLI，封装 hook 安装、daemon 启动、auto compact controller 和 read-only status；只复用现有安全 gate，不绕过确认。
- `install_hooks.py`：把所需 Claude Code hooks 安全合并到 `settings.json`，保留既有配置并避免重复安装。
- `status.py` 是 run-once 诊断命令，只读取当前项目 `.memory/` 中已知文件并输出状态；它不写入 `errors.log`，不创建目录，不修改 `rolling-summary.md`，不编辑 `~/.claude/settings.json`，不启动 daemon，不扫描 transcript 或源码。它还输出基于本地 runtime 文件大小/字符数 metadata 的近似 `compact-readiness`，不保存 prompt/transcript 内容，不代表精确 token 使用率，也不自动触发 compact。
- `rolling-summary.md`：人工或半自动维护的 continuity-critical 摘要。
- `rolling-summary.draft.md`：从 compact history 生成的草稿，不会自动注入。
- `compact-history.jsonl`：可选，保存 compact 后的官方 summary 历史。
- `daemon-state.json`：`daemon.py` 写入的本地状态文件，只包含时间、模式、候选数量、draft 路径、LLM summary status、provider/model、token usage、耗时、summary/backup path、plist path、launchctl_invoked、launchctl_action、launchctl_target、launchctl_returncode、launchctl_status、plist_validated、error_kind、loop interval/run count/shutdown reason 等 metadata，不保存 summary 原文、LLM prompt/output 原文、API key value、plist XML 或 launchctl stdout/stderr 原文。
- `compact-history.jsonl.1`：history 轮转文件。
- `operation-log.jsonl`：operation timeline 当前文件；默认只保存 service、operation、status、metadata 和 raw-content policy flags。
- `operation-log.jsonl.1`：operation timeline 轮转文件。
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


`operation_log.py` / `dashboard.py` 行为：

- `operation-log.jsonl` 和 `operation-log.jsonl.1` 只保存在当前 runtime dir。
- operation record 默认 metadata-only，包含 `schema_version`、`timestamp`、`service`、`operation`、`status`、`metadata` 和 `content_policy`。
- raw prompt 只允许 `auto_compact_controller.py --operation-log --log-raw-prompt` 显式记录。
- raw summary 只允许 `SIDECAR_LOG_RAW_SUMMARY=1` 或 `merge_compact_history.py --operation-log --log-raw-summary` 显式记录。
- `dashboard.py` 默认一次性只读渲染；`--watch` 循环刷新；`--json` 输出 machine-readable snapshot；`--show-content` 是显示 raw prompt/summary 的唯一开关；LLM token metadata 默认可显示，因为它不包含 raw prompt/summary。
- `status.py` 和 dashboard 默认输出都不能打印 raw prompt/summary；只能显示 raw-content flags 和 hidden marker。
- malformed operation log 只能影响 read-only status/dashboard warnings，不能写 `errors.log`。

`postcompact_record.py` 行为：

- 从 stdin 最多读取 200k 字符的 hook payload。
- 把原始 payload 或提取出的 summary 追加到 `compact-history.jsonl`。
- 如果无法解析输入，记录 `service=postcompact` 的错误但不阻塞 Claude Code。
- auto compact controller 不会 hook 自动触发；只有用户显式提供 `--pane --merge-after` 且未传入 `--no-send` 时，才会在备份旧 summary 后写入新的 `rolling-summary.md`。

`merge_compact_history.py` 行为：

- 读取 `compact-history.jsonl` 和 `compact-history.jsonl.1`。
- 提取最近的 `payload.summary`，生成 `rolling-summary.draft.md`。
- draft 可以包含从 compact summary 文本中提取的 path-like review hints，例如 `src/foo.py` 或 `tests/test_foo.py`；这些 hints 只来自 summary 文本，不扫描 transcript 或源码，不验证文件是否存在，也不代表文件一定相关。
- 手动 `merge_compact_history.py` 不自动覆盖 `rolling-summary.md`；用户必须手动审查 draft，只复制仍然准确且值得长期保留的信息。
- 如果 history 缺失或没有 summary，仍生成一个空 draft 模板。

`daemon.py --run-once` / `--loop` / `--install-agent` / `--agent-status` / `--remove-agent` 行为：

- `--run-once` 从 compact history 收集最近 summary 候选，复用 `merge_compact_history.py` 的 draft 格式写入兼容 draft。
- 如果存在候选 summary，`--run-once` 默认读取 LLM 环境变量、用 streaming SSE 调用 OpenAI-compatible endpoint、校验返回 markdown，并 backup-first 写入 `rolling-summary.md`。
- 如果没有 history candidates，仍生成空 draft 模板，记录 `llm_summary_status=skipped`，不调用 LLM，不覆盖 `rolling-summary.md`，退出 0。
- 如果 LLM 配置缺失、请求失败、响应 shape 错误或 summary 缺少 required marker，旧 `rolling-summary.md` 保留，state 记录 `llm_summary_status=error` 和 `error_kind`，`--run-once` 返回非零。
- `--loop --interval-seconds N` 在前台重复相同维护逻辑；单轮 LLM 失败记录 metadata 后继续，`--max-runs N` 用于测试和 smoke check，保证不会留下持久进程。
- loop state 记录 `mode`、`interval_seconds`、`run_count`、`shutdown_reason` 和 LLM metadata，但不保存 summary 原文。
- `--operation-log` 会记录 metadata-only 的 `daemon | llm-summary` operation，包含 provider/model、candidate_count、input/output chars、token usage、elapsed_ms、summary_written、summary_backup、error_kind，不包含 raw prompt/output。
- `--install-agent --plist-path <path>` 只写 plist 文件和 metadata-only daemon state；ProgramArguments 指向当前 `daemon.py --loop --interval-seconds N --operation-log`，WorkingDirectory 固定为当前项目根，EnvironmentVariables 固定 `SIDECAR_COMPACT_DIR`，并携带非 secret 的 `SIDECAR_LLM_*` 设置和 API key 环境变量名，不携带解析后的 API key value，stdout/stderr 日志路径位于 runtime dir。
- `--agent-status --plist-path <path>` 只读检查 plist artifact；缺失文件安全退出，malformed plist 报 invalid 且不 traceback。
- `--remove-agent --plist-path <path>` 只移除 label 匹配 sidecar 的显式 plist artifact；缺失文件安全退出，malformed 或非 sidecar plist 保留不删。
- 不扫描 transcript、源码、Claude Code `sessions/*.jsonl` 或任意项目文件作为 runtime LLM 输入。
- artifact 命令不调用 `launchctl`，不 bootstrap/kickstart，不启动、不停止、不 fork，不编辑真实 `~/.claude/settings.json`。只有显式 `--launchctl-*` lifecycle 命令允许调用 launchctl。

`status.py` 行为：

- 只读检查当前项目 `.memory/` 中的已知文件：`rolling-summary.md`、`rolling-summary.draft.md`、`compact-history.jsonl`、`compact-history.jsonl.1`、`errors.log` 和 `daemon-state.json`。
- 输出文件是否存在、大小、summary marker / injectable 状态、history / errors 记录数、daemon last_run、loop metadata 和最近 LLM summary token metadata。
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
- `llm_summarizer.py` request 测试使用 fake streaming `urllib.request.urlopen`，验证 request body/header、usage token 解析、无 usage 时 token unknown、错误不泄漏 API key value，禁止真实网络。
- `rolling_summary_writer.py` 测试验证 required heading/marker、backup-first 写入和新文件写入。
- `daemon.py --run-once` 无 history 时跳过 LLM 且不覆盖 `rolling-summary.md`；有 history 和 fake LLM 时写入 `rolling-summary.md`、创建旧 summary backup、记录 token metadata。
- `daemon.py --run-once` 在 LLM 失败时不覆盖旧 `rolling-summary.md`，并返回非零。
- `daemon.py --loop --max-runs` 能有界退出，更新 metadata-only daemon state，并在单轮 LLM 失败后继续。
- `daemon.py --install-agent --plist-path <path>` 只写 plist，不调用 `launchctl`；缺少 `--plist-path` 时安全失败。
- `daemon.py --agent-status --plist-path` 能只读检查 plist artifact，缺失/损坏文件不会创建 runtime 或 traceback。
- `daemon.py --remove-agent --plist-path` 只删除匹配 sidecar label 的显式 plist artifact，保留 malformed 或非 sidecar plist。
- `sidecar_api.py` facade 测试覆盖只读调用不写 runtime、默认脱敏、hook setup preview、daemon status 和受控 mutation entrypoints。
- `mcp_server.py` 协议测试覆盖 initialize、tools/list、tools/call、未知工具、参数错误、默认脱敏和 API key value 不泄漏。
- MCP 演练测试必须使用临时 settings/runtime/plist 路径，并验证 fake launchctl/tmux 没有被调用。
- MCP mutation 测试必须验证 `confirm=true` gate、global settings opt-in、fake `SIDECAR_LAUNCHCTL_PATH`、fake tmux、bounded daemon run-once、raw logging 默认关闭，以及未授权路径不会创建 runtime。
- Packaging smoke test 必须构建 wheel，并验证 `sidecar` / `sidecar-mcp` entry points 或对应 import/self-test。

建议命令：

- 脚本级测试全部使用临时 `SIDECAR_COMPACT_DIR`，不会触碰真实 `.memory/` 或 `~/.claude/settings.json`。
- `install_hooks.py` 测试必须通过 `--settings` 指向临时 `settings.json`，不能修改真实 Claude Code 设置。

```bash
python3 -m unittest discover -s tests
```

只跑某一类测试：

```bash
python3 -m unittest tests.test_userprompt_inject
python3 -m unittest tests.test_operation_log
python3 -m unittest tests.test_dashboard
python3 -m unittest tests.test_llm_summarizer
python3 -m unittest tests.test_rolling_summary_writer
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

手动 smoke test daemon run-once 无 history skip：

```bash
tmp=$(mktemp -d)
SIDECAR_COMPACT_DIR="$tmp" python3 src/daemon.py --run-once
python3 -m json.tool "$tmp/daemon-state.json"
test ! -f "$tmp/rolling-summary.md"
```

手动 smoke test daemon loop 无 history skip：

```bash
tmp=$(mktemp -d)
SIDECAR_COMPACT_DIR="$tmp" python3 src/daemon.py --loop --interval-seconds 1 --max-runs 2
python3 -m json.tool "$tmp/daemon-state.json"
test ! -f "$tmp/rolling-summary.md"
```

手动 smoke test launchd plist artifact：

```bash
tmp=$(mktemp -d)
SIDECAR_COMPACT_DIR="$tmp/runtime" python3 src/daemon.py --install-agent --plist-path "$tmp/sidecar.plist"
test -f "$tmp/sidecar.plist"
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

然后运行 `python3 src/install_hooks.py --settings "$tmp/settings.json"` 检查 settings 合并结果；确认无误后再运行 `python3 src/install_hooks.py` 安装 `UserPromptSubmit` / `PostCompact` hooks，发送普通 prompt，并询问模型是否记得 `SIDE_CAR_TEST_MARKER_12345`。这个测试是 MVP 最重要的有效性判断：脚本测试只能证明 JSON 输出正确，marker 测试才能证明 hook 流程确实吸收了 sidecar summary。

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
