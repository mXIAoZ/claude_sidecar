# Skill/MCP 产品化任务清单

## 状态说明

- `[ ]` 未开始
- `[~]` 进行中
- `[x]` 已完成

## Phase 0：计划产物

### Task 0：创建可审阅的计划文档

**状态：** `[x]`

**描述：** 把批准后的中文计划保存为 `tasks/plan.md`，并把可执行 checklist 保存为 `tasks/todo.md`。

**依赖：** 无。

**验收标准：**
- [x] `tasks/plan.md` 包含目标、依赖图、架构决策、分阶段任务、检查点、风险和测试策略。
- [x] `tasks/todo.md` 包含任务清单、依赖、验收标准和验证步骤。
- [x] 文档只描述计划，不声称实现已完成。

**验证：**
- [ ] 人工审阅 `tasks/plan.md`。
- [ ] 人工审阅 `tasks/todo.md`。

**可能修改文件：**
- `tasks/plan.md`
- `tasks/todo.md`

**预计范围：** Small。

## Checkpoint：计划审批

- [x] 计划已通过审批。
- [x] `tasks/plan.md` 和 `tasks/todo.md` 已创建。
- [x] 人工确认是否进入 Task 1 实现。

## Phase 1：共享产品契约

### Task 1：增加程序化 Sidecar Facade

**状态：** `[x]`

**描述：** 新增小型 API 层，封装 status、dashboard snapshot、config validation、hook setup preview、daemon status 和受控 mutation entrypoints，降低 CLI、Skill 和 MCP 之间的漂移。

**依赖：** Task 0。

**验收标准：**
- [x] 只读 facade 调用不会创建运行时目录，也不会写文件。
- [x] 写操作 facade 调用必须提供显式目标路径和确认类参数。
- [x] 默认输出已脱敏，不暴露 raw prompt、raw summary 或 API key 值。
- [x] 现有直接脚本用法仍然可用。

**验证：**
- [x] 运行 `python3 -m unittest discover -s tests`。
- [x] 增加并运行 `tests/test_sidecar_api.py`。
- [x] 运行 `python3 src/sidecar.py status --json`。

**可能修改文件：**
- `src/sidecar_api.py`
- `src/sidecar.py`
- `src/status.py`
- `src/dashboard.py`
- `tests/test_sidecar_api.py`

**预计范围：** Medium。

### Task 2：增加 Packaging Metadata 和资源查找

**状态：** `[x]`

**描述：** 增加最小 packaging metadata 和 console entry points，同时确保 `sidecar.config.template.json` 和 Skill asset 在源码与安装包上下文中都能被找到。

**依赖：** Task 0。

**验收标准：**
- [x] 定义 `sidecar` 和 `sidecar-mcp` console entry points。
- [x] `python3 src/sidecar.py ...` 继续可用。
- [x] Config template lookup 在 source、editable install 和 wheel install 中可用。
- [x] Skill 目录被包含为 package asset，或文档明确说明分发方式。

**验证：**
- [x] 运行 `python3 src/sidecar.py --help`。
- [x] 运行 `python3 -m pip wheel . --no-deps -w "$(mktemp -d)"`。
- [x] 如果环境允许，做 package/import smoke test。

**可能修改文件：**
- `pyproject.toml`
- `src/sidecar_config.py`
- `sidecar.config.template.json`
- packaging 相关测试

**预计范围：** Medium。

## Checkpoint：基础层完成

- [x] 现有 CLI 行为保持不变。
- [x] 全量测试通过。
- [x] Wheel 或 packaging smoke check 通过。
- [x] 没有新增默认网络访问或后台生命周期行为。

## Phase 2：Claude Code Skill 产品化

### Task 3：把现有 Skill 强化为操作者工作流

**状态：** `[x]`

**描述：** 将 `sidecar-manager-skill/SKILL.md` 从菜单式命令表升级为可分发 operator skill，包含触发短语、决策树、安全模型、模式选择和可安全复制的命令。

**依赖：** Task 1、Task 2。

**验收标准：**
- [x] Skill 清晰区分只读诊断、演练流程、确认安装、确认卸载、LLM 配置和架构/排障帮助。
- [x] 所有真实写操作都通过 `src/sidecar.py`、`src/daemon.py` 或现有 CLI 门禁执行。
- [x] 真实 settings 写入默认指向项目本地 `.claude/settings.local.json`，除非用户明确要求其他路径。
- [x] Skill 明确提醒 hook stdout 只能输出 hook JSON，raw prompt/summary logging 是 opt-in。
- [x] 不包含直接覆盖 `~/.claude/settings.json` 的临时片段。

**验证：**
- [x] 将 Skill 中每条命令与当前 CLI 对照审阅。
- [x] 运行 `python3 src/sidecar.py status --json`。
- [x] 使用临时 settings 和临时 runtime 运行 setup rehearsal。
- [x] 运行 `python3 -m unittest tests.test_sidecar_cli tests.test_install_hooks`。

**可能修改文件：**
- `sidecar-manager-skill/SKILL.md`
- 可选 `sidecar-manager-skill/README.md`
- 可选 Skill validation tests

**预计范围：** Medium。

## Checkpoint：Skill Ready

- [x] Skill 能指导安全的只读 status、setup rehearsal、hook install/remove、daemon rehearsal 和 uninstall 流程。
- [x] Skill 文档不绕过现有 CLI 安全门禁。
- [x] 分发前人工审阅命令措辞。

## Phase 3：MCP 只读垂直切片

### Task 4：实现 MCP 只读工具

**状态：** `[x]`

**描述：** 新增 `src/mcp_server.py`，实现 MCP stdio 处理和完整只读路径：initialize、list tools、call status/dashboard/config tools，并返回已脱敏 JSON。

**依赖：** Task 1、Task 2。

**验收标准：**
- [x] MCP server 支持 `initialize`、`tools/list` 和 `tools/call`。
- [x] 第一批 tools 包括 `sidecar_status`、`sidecar_dashboard`、`sidecar_config_validate` 和 `sidecar_operation_log` 元数据视图。
- [x] 默认响应隐藏 raw prompt/summary content。
- [x] 永不返回 API key 值。
- [x] 只读调用不会写文件，也不会创建运行时目录。

**验证：**
- [x] 增加并运行 `tests/test_mcp_server.py`。
- [x] 运行 `python3 -m unittest tests.test_status tests.test_dashboard tests.test_operation_log`。
- [x] 如果实现 self-test，运行 `SIDECAR_COMPACT_DIR="$(mktemp -d)/runtime" sidecar-mcp --self-test`。

**可能修改文件：**
- `src/mcp_server.py`
- `src/sidecar_api.py`
- `tests/test_mcp_server.py`

**预计范围：** Medium。

## Checkpoint：MCP Read-Only Ready

- [x] MCP 能报告 sidecar 状态，同时不接触真实 settings、launchctl、tmux、runtime state 或网络。
- [x] 协议级测试覆盖 tool schema、未知工具、参数校验和脱敏错误响应。

## Phase 4：MCP 演练垂直切片

### Task 5：增加安全 MCP 演练工具

**状态：** `[x]`

**描述：** 增加 MCP tools，用临时或调用方显式提供的路径生成/执行安全 preview：setup rehearsal、daemon plist rehearsal、显式 plist 的 daemon status、compact plan preview。

**依赖：** Task 4。

**验收标准：**
- [x] 演练 tools 只写入显式临时路径或调用方提供的路径。
- [x] 演练 tools 永不调用 launchctl。
- [x] 演练 tools 永不调用 tmux。
- [x] 返回 JSON 包含 artifact paths、warnings 和 next-step commands，且不包含 secrets。

**验证：**
- [x] 增加并运行 `tests/test_mcp_rehearsal.py`。
- [x] 使用临时 settings 文件和临时 plist 路径验证。
- [x] 运行 `python3 -m unittest tests.test_daemon tests.test_install_hooks`。

**可能修改文件：**
- `src/mcp_server.py`
- `src/sidecar_api.py`
- `tests/test_mcp_rehearsal.py`

**预计范围：** Medium。

## Checkpoint：MCP Rehearsal Ready

- [x] 用户或 agent 可以通过 MCP 评估 setup/daemon 变更，而不修改真实 Claude settings 或用户 launchd state。
- [x] 测试中的所有 rehearsal artifact 都隔离在临时路径下。

## Phase 5：MCP 显式写操作垂直切片

### Task 6：增加显式门禁 MCP Mutation Tools

**状态：** `[x]`

**描述：** 增加可选写操作 MCP tools：hook install/uninstall、plist write/remove、bounded daemon run-once、显式 launchctl lifecycle、显式 tmux compact send。

**依赖：** Task 5。

**验收标准：**
- [x] 每个真实写操作都必须提供显式目标路径和 `confirm: true` 或等价参数。
- [x] Global settings 路径需要额外显式 opt-in。
- [x] Launchctl tools 调用前校验 plist shape，并在测试中使用 fake `SIDECAR_LAUNCHCTL_PATH`。
- [x] Tmux tools 必须提供显式 pane 或 no-send 模式，并在测试中使用 fake tmux。
- [x] Raw prompt/summary logging 默认仍关闭，只有显式 raw flags 才启用。
- [x] 默认 MCP tools 不暴露 unbounded daemon loop。

**验证：**
- [x] 增加并运行 `tests/test_mcp_mutations.py`。
- [x] 使用临时 runtime/settings/plist 路径运行 mutation tests。
- [x] 运行 fake launchctl 和 fake tmux 测试。
- [x] 运行 `python3 -m unittest tests.test_sidecar_cli tests.test_daemon tests.test_auto_compact_controller`。

**可能修改文件：**
- `src/mcp_server.py`
- `src/sidecar_api.py`
- `tests/test_mcp_mutations.py`

**预计范围：** Medium。

## Checkpoint：MCP Mutations Ready

- [x] 写操作 MCP tools 通过协议级测试和现有 CLI 安全回归测试。
- [x] 没有测试接触真实 `~/.claude/settings.json`、真实 launchctl state、真实 tmux panes 或真实 API endpoints。
- [x] 人工确认写操作 MCP tools 默认随包启用；安全性依赖每个 mutation tool 的 `confirm: true`、显式路径和额外 opt-in gate。

## Phase 6：文档、发布和回归矩阵

### Task 7：记录安装、配置和回滚流程

**状态：** `[x]`

**描述：** 更新产品文档，说明源码用法、打包用法、Skill 安装、MCP client 配置、安全模型、rollback/uninstall 和 test/release 流程。

**依赖：** Task 1 至 Task 6。

**验收标准：**
- [x] 文档同时展示 source checkout 和 packaged entry-point 用法。
- [x] MCP client 配置示例使用显式本地路径且不包含 secrets。
- [x] 真实写操作示例包含 confirmation/safety gates。
- [x] 隐私模型覆盖 raw prompt/summary content、LLM endpoint 行为、API key env names 和 operation-log metadata。
- [x] 回滚步骤覆盖 Skill 移除、hook uninstall、plist removal、launchctl bootout 和 runtime cleanup guidance。

**验证：**
- [x] 运行 `python3 -m unittest discover -s tests`。
- [x] 运行 `python3 src/sidecar.py status --json`。
- [x] 运行 package smoke checks。
- [x] 运行 MCP self-test。
- [x] 人工审阅文档，确认没有不安全的直接 settings 编辑或 secret 示例。

**可能修改文件：**
- `README.md`
- `README.zh-CN.md`
- `SPEC.md`
- `sidecar-manager-skill/SKILL.md`
- `tasks/plan.md`
- `tasks/todo.md`

**预计范围：** Medium。

## Final Checkpoint：Release Candidate

- [x] 全量单元测试通过：`python3 -m unittest discover -s tests`。
- [x] Skill 工作流命令完成人工审阅。
- [x] MCP 只读/演练/写操作测试通过。
- [x] Package build smoke test 通过。
- [x] 隐私回归测试确认默认不泄露 raw prompt、raw summary 或 API key。
- [x] 安全回归测试确认默认不会写真实 Claude settings、launchctl、tmux 或网络。
