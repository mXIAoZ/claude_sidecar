# 把 Compact Sidecar 产品化为 Claude Code Skill 和 MCP Server 的实施计划

## 背景

当前仓库是一个本地 Claude Code compact sidecar：它记录 compact 事件，在 `.memory` 或 `SIDECAR_COMPACT_DIR` 下维护滚动摘要，通过受支持的 `UserPromptSubmit` hook context 注入该摘要，提供只读 status/dashboard 视图，并可选运行 daemon 驱动的 LLM 摘要生成器。

下一步是把项目产品化为两个互补形态：

- Claude Code Skill：为操作者提供安全的 setup、status、uninstall、排障、LLM 配置等工作流。
- MCP stdio server：把部分 sidecar 能力作为结构化工具暴露给 Claude，同时保留当前安全和隐私边界。

推荐原则：继续让现有 CLI 和模块作为权威执行入口。Skill 和 MCP 应该只是现有安全门禁之上的薄编排层，而不是另一套运行时。

## 当前依赖图

```text
sidecar.config.template.json
    -> src/sidecar_config.py
        -> src/sidecar.py 统一 CLI
        -> src/daemon.py launchd/LLM/run-once 流程
        -> src/install_hooks.py settings 合并/移除
        -> src/auto_compact_controller.py 显式 tmux 流程
        -> src/status.py 和 src/dashboard.py 只读诊断

src/sidecar_paths.py
    -> .memory 或 SIDECAR_COMPACT_DIR 下的运行时文件
    -> summary/history/errors/operation-log 路径

src/operation_log.py
    -> dashboard/status 元数据时间线
    -> daemon/merge/compact 操作元数据

src/status.py + src/dashboard.py
    -> 最适合作为 MCP 只读能力的表面
    -> sidecar status --json

src/install_hooks.py + src/daemon.py + src/auto_compact_controller.py
    -> 会产生写入或外部影响的操作
    -> 必须保持显式门禁

sidecar-manager-skill/SKILL.md
    -> 现有菜单式操作文档
    -> 应调用 src/sidecar.py 和相关 CLI 门禁

未来的 src/mcp_server.py
    -> 优先复用 status/dashboard/config 的只读能力
    -> 只有在确认和测试门禁完善后才暴露写操作
```

## 架构决策

- 除非产品 spec 明确改变，否则继续只使用 Python 标准库。
- 第一版 MCP 使用 `src/mcp_server.py` 实现 stdio JSON-RPC，不先引入 SDK 依赖。
- 增加一个小型程序化门面，例如 `src/sidecar_api.py`，让 CLI、Skill 文档和 MCP tools 共用同一套安全契约。
- 将 `sidecar-manager-skill/SKILL.md` 定位为操作者工作流层，不是 daemon、隐藏执行器，也不是 CLI 门禁替代品。
- MCP tools 分成三组：只读、演练、显式写操作。
- 默认输出只包含元数据：不返回 raw prompt、raw summary、API key 值或未脱敏的 LLM 错误，除非已有显式 opt-in 参数允许。
- 真实 settings 写入、launchctl 生命周期、tmux 发送、raw logging 都必须继续放在显式参数和现有 CLI/module 校验之后。

## 阶段 0：计划产物

### Task 0：创建可审阅的计划文档

**描述：** 在计划获得批准后，把最终实施计划保存到 `tasks/plan.md`，把可执行任务清单保存到 `tasks/todo.md`。

**验收标准：**
- `tasks/plan.md` 包含目标、依赖图、架构决策、分阶段任务、检查点、风险和测试策略。
- `tasks/todo.md` 包含按垂直切片拆分的任务，每个任务都有依赖、验收标准和验证步骤。
- 文档不会声称实现已经完成。

**验证：**
- 人工审阅 `tasks/plan.md` 和 `tasks/todo.md`。
- 确认这个 planning-only 任务没有改动源码或运行时行为。

**依赖：** 无。

**可能修改文件：**
- `tasks/plan.md`
- `tasks/todo.md`

**预计范围：** Small。

## 检查点：计划审批

- 人类确认计划后，再开始任何源码实现。
- `tasks/plan.md` 和 `tasks/todo.md` 成为后续实现 checklist。

## 阶段 1：共享产品契约

### Task 1：增加程序化 Sidecar Facade

**描述：** 引入一个小型 API 层，封装当前 status、dashboard snapshot、config validation、hook setup preview、daemon status 和受控 mutation entrypoint。这样可以减少 CLI、Skill 文档和 MCP tools 之间的漂移。

**验收标准：**
- 只读 facade 调用不会创建运行时目录，也不会写文件。
- 写操作 facade 调用必须提供显式目标路径和确认类参数。
- 默认输出已脱敏，不暴露 raw prompt、raw summary 或 API key 值。
- 现有直接脚本用法仍然可用。

**验证：**
- 运行 `python3 -m unittest discover -s tests`。
- 增加针对只读 facade 调用的聚焦测试，并使用临时 `SIDECAR_COMPACT_DIR`。
- 验证 `python3 src/sidecar.py status --json` 仍然可用。

**依赖：** Task 0。

**可能修改文件：**
- `src/sidecar_api.py`
- `src/sidecar.py`
- `src/status.py`
- `src/dashboard.py`
- `tests/test_sidecar_api.py`

**预计范围：** Medium。

### Task 2：增加 Packaging Metadata 和资源查找

**描述：** 增加最小 packaging metadata 和 console entry points，同时保留直接脚本执行方式。确保 `sidecar.config.template.json` 和 Skill asset 在源码 checkout 与安装包上下文中都能被找到。

**验收标准：**
- 定义 `sidecar` 和 `sidecar-mcp` console entry points。
- 现有 `python3 src/sidecar.py ...` 命令继续可用。
- Config template lookup 在源码、editable install 和 wheel install 中都可用。
- Skill 目录被包含为 package asset，或在文档中明确说明分发方式。

**验证：**
- 运行 `python3 src/sidecar.py --help`。
- 运行 `python3 -m pip wheel . --no-deps -w "$(mktemp -d)"` 构建 wheel。
- 如果环境允许，在临时环境中做 package/import smoke test。

**依赖：** Task 0。

**可能修改文件：**
- `pyproject.toml`
- `src/sidecar_config.py`
- `sidecar.config.template.json`
- packaging 相关测试

**预计范围：** Medium。

## 检查点：基础层完成

- 现有 CLI 行为保持不变。
- 全量测试通过。
- Wheel 或 packaging smoke check 通过。
- 没有新增默认网络访问或后台生命周期行为。

## 阶段 2：Claude Code Skill 产品化

### Task 3：把现有 Skill 强化为操作者工作流

**描述：** 将 `sidecar-manager-skill/SKILL.md` 从菜单式命令表升级为可分发的 operator skill，包含触发短语、决策树、安全模型、模式选择和可安全复制的命令。

**验收标准：**
- Skill 清晰区分只读诊断、演练流程、确认安装、确认卸载、LLM 配置和架构/排障帮助。
- 所有真实写操作都通过 `src/sidecar.py`、`src/daemon.py` 或现有 CLI 门禁执行。
- 真实 settings 写入默认指向项目本地 `.claude/settings.local.json`，除非用户明确要求其他路径。
- Skill 明确提醒：hook stdout 只能输出 hook JSON；raw prompt/summary logging 是 opt-in。
- 不包含直接覆盖 `~/.claude/settings.json` 的临时片段。

**验证：**
- 将 Skill 中每条命令与当前 CLI 对照审阅。
- 运行 `python3 src/sidecar.py status --json`。
- 使用临时 settings 和临时 runtime 运行 setup rehearsal。
- 运行 `python3 -m unittest tests.test_sidecar_cli tests.test_install_hooks`。

**依赖：** Task 1 和 Task 2。

**可能修改文件：**
- `sidecar-manager-skill/SKILL.md`
- 如后续明确需要，可选 `sidecar-manager-skill/README.md`
- 可选 Skill validation tests

**预计范围：** Medium。

## 检查点：Skill Ready

- Skill 能指导安全的只读 status、setup rehearsal、hook install/remove、daemon rehearsal 和 uninstall 流程。
- Skill 文档不绕过现有 CLI 安全门禁。
- 分发前人工审阅命令措辞。

## 阶段 3：MCP 只读垂直切片

### Task 4：实现 MCP 只读工具

**描述：** 增加 `src/mcp_server.py`，实现 MCP stdio 处理和完整只读路径：initialize、list tools、call status/dashboard/config tools，并返回已脱敏 JSON。

**验收标准：**
- MCP server 支持 `initialize`、`tools/list` 和 `tools/call`。
- 第一批 tools 包括 `sidecar_status`、`sidecar_dashboard`、`sidecar_config_validate` 和 `sidecar_operation_log` 元数据视图。
- 默认响应隐藏 raw prompt/summary content。
- 永不返回 API key 值。
- 只读调用不会写文件，也不会创建运行时目录。

**验证：**
- 增加 `tests/test_mcp_server.py`，覆盖 JSON-RPC initialize/list/call。
- 运行 `python3 -m unittest tests.test_mcp_server`。
- 运行 `python3 -m unittest tests.test_status tests.test_dashboard tests.test_operation_log`。
- 如果实现 self-test，运行例如 `SIDECAR_COMPACT_DIR="$(mktemp -d)/runtime" sidecar-mcp --self-test`。

**依赖：** Task 1 和 Task 2。

**可能修改文件：**
- `src/mcp_server.py`
- `src/sidecar_api.py`
- `tests/test_mcp_server.py`

**预计范围：** Medium。

## 检查点：MCP Read-Only Ready

- MCP 能报告 sidecar 状态，同时不接触真实 settings、launchctl、tmux、runtime state 或网络。
- 协议级测试覆盖 tool schema、未知工具、参数校验和脱敏错误响应。

## 阶段 4：MCP 演练垂直切片

### Task 5：增加安全 MCP 演练工具

**描述：** 增加 MCP tools，用临时或调用方显式提供的路径生成/执行安全 preview：setup rehearsal、daemon plist rehearsal、显式 plist 的 daemon status、compact plan preview。

**验收标准：**
- 演练 tools 只写入显式临时路径或调用方提供的路径。
- 演练 tools 永不调用 launchctl。
- 演练 tools 永不调用 tmux。
- 返回 JSON 包含 artifact paths、warnings 和 next-step commands，且不包含 secrets。

**验证：**
- 增加 `tests/test_mcp_rehearsal.py`。
- 使用临时 settings 文件和临时 plist 路径。
- 运行 `python3 -m unittest tests.test_mcp_rehearsal tests.test_daemon tests.test_install_hooks`。

**依赖：** Task 4。

**可能修改文件：**
- `src/mcp_server.py`
- `src/sidecar_api.py`
- `tests/test_mcp_rehearsal.py`

**预计范围：** Medium。

## 检查点：MCP Rehearsal Ready

- 用户或 agent 可以通过 MCP 评估 setup/daemon 变更，而不修改真实 Claude settings 或用户 launchd state。
- 测试中的所有 rehearsal artifact 都隔离在临时路径下。

## 阶段 5：MCP 显式写操作垂直切片

### Task 6：增加显式门禁 MCP Mutation Tools

**描述：** 增加可选的写操作 MCP tools：hook install/uninstall、plist write/remove、bounded daemon run-once、显式 launchctl lifecycle、显式 tmux compact send。它们不属于默认 happy path，必须要求显式确认参数。

**验收标准：**
- 每个真实写操作都必须提供显式目标路径和 `confirm: true` 或等价参数。
- Global settings 路径需要额外显式 opt-in。
- Launchctl tools 在调用前校验生成的 plist shape，并在测试中使用 fake `SIDECAR_LAUNCHCTL_PATH`。
- Tmux tools 必须提供显式 pane 或 no-send 模式，并在测试中使用 fake tmux。
- Raw prompt/summary logging 默认仍关闭，只有传入已有显式 raw flags 才启用。
- 默认 MCP tools 不暴露 unbounded daemon loop。

**验证：**
- 增加 `tests/test_mcp_mutations.py`。
- 使用临时 runtime/settings/plist 路径运行 mutation tests。
- 运行 fake launchctl 和 fake tmux 测试。
- 运行 `python3 -m unittest tests.test_mcp_mutations tests.test_sidecar_cli tests.test_daemon tests.test_auto_compact_controller`。

**依赖：** Task 5。

**可能修改文件：**
- `src/mcp_server.py`
- `src/sidecar_api.py`
- `tests/test_mcp_mutations.py`

**预计范围：** Medium。

## 检查点：MCP Mutations Ready

- 写操作 MCP tools 通过协议级测试和现有 CLI 安全回归测试。
- 没有测试接触真实 `~/.claude/settings.json`、真实 launchctl state、真实 tmux panes 或真实 API endpoints。
- 人工确认写操作 MCP tools 是否应该默认随包启用。

## 阶段 6：文档、发布和回归矩阵

### Task 7：记录安装、配置和回滚流程

**描述：** 更新产品文档，说明源码用法、打包用法、Skill 安装、MCP client 配置、安全模型、rollback/uninstall 和 test/release 流程。

**验收标准：**
- 文档同时展示 source checkout 和 packaged entry-point 用法。
- MCP client 配置示例使用显式本地路径且不包含 secrets。
- 真实写操作示例包含 confirmation/safety gates。
- 隐私模型覆盖 raw prompt/summary content、LLM endpoint 行为、API key env names 和 operation-log metadata。
- 回滚步骤覆盖 Skill 移除、hook uninstall、plist removal、launchctl bootout 和 runtime cleanup guidance。

**验证：**
- 运行 `python3 -m unittest discover -s tests`。
- 运行 `python3 src/sidecar.py status --json`。
- 运行 package smoke checks。
- 运行 MCP self-test。
- 人工审阅文档，确认没有不安全的直接 settings 编辑或 secret 示例。

**依赖：** Task 1 至 Task 6。

**可能修改文件：**
- `README.md`
- `README.zh-CN.md`
- `SPEC.md`
- `sidecar-manager-skill/SKILL.md`
- `tasks/plan.md`
- `tasks/todo.md`

**预计范围：** Medium。

## 最终检查点：Release Candidate

- 全量单元测试通过：`python3 -m unittest discover -s tests`。
- Skill 工作流命令完成人工审阅。
- MCP 只读/演练/写操作测试通过。
- Package build smoke test 通过。
- 隐私回归测试确认默认不泄露 raw prompt、raw summary 或 API key。
- 安全回归测试确认默认不会写真实 Claude settings、launchctl、tmux 或网络。

## 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---:|---|
| MCP 协议漂移或 JSON-RPC 行为不完整 | Medium | 将 MCP transport 隔离在 `src/mcp_server.py`；增加 fixture-based protocol tests 和可选 inspector smoke checks。 |
| MCP 意外产生副作用 | High | 分离只读、演练和写操作 tools；写操作要求显式路径和 confirmation flags。 |
| raw prompt/summary/API key 泄露 | High | 默认 `show_content=false`；脱敏 env values 和 errors；增加 raw fields 与 secret-like values 测试。 |
| Packaging 破坏 template/runtime lookup | Medium | 测试源码、editable、wheel 上下文；增加 installed data files 的 fallback lookup。 |
| Skill/MCP/CLI 行为漂移 | Medium | 增加 `src/sidecar_api.py`，让 Skill 文档指向 CLI/API entrypoints，而不是内嵌自定义写逻辑。 |
| 写操作 MCP tools 分发风险过高 | Medium | 先发布只读/演练 tools；写操作 tools 设为可选或默认禁用，等人工审阅后再启用。 |
| LLM 摘要导致隐藏网络行为 | High | hooks 永不调用 LLM；daemon/LLM 行为只通过显式配置和现有 daemon gates 暴露。 |

## 需要人工确认的问题

- 第一版 MCP 是否只包含只读/演练 tools，还是同时发布 gated mutation tools？
- Packaging 第一阶段是只支持本地源码 checkout，还是立即加入 wheel/entry-point 支持？
- `sidecar-manager-skill` 是否继续留在当前 repo，还是后续拆成独立可分发 skill package？
- MCP 是否允许在 `show_content` 后返回 raw `rolling-summary.md` 内容，还是完全不通过 MCP 暴露 raw content？

## 下一步

先审阅 `tasks/plan.md` 和 `tasks/todo.md`。确认后从 Task 1 开始实现共享 facade，再进入 packaging、Skill 和 MCP 的分阶段实现。
