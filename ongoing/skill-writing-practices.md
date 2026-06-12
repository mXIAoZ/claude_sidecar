# Skill 写作实践总结

## 一句话结论

好的 Skill 不是一篇 Wiki，而是一个可触发、可执行、可验证的任务能力包：用精确的 frontmatter 告诉 Claude 何时加载，用简洁的 `SKILL.md` 说明如何执行，把长参考、脚本、模板和评估用例拆到配套文件中。

## 什么时候应该写 Skill

适合写 Skill 的情况：

- 你反复复制同一段指令、检查清单或多步骤流程。
- `CLAUDE.md` 里某段内容已经从“事实/约束”变成了“操作流程”。
- 团队有稳定流程要复用，例如代码审查、迁移、发布、测试生成、文档处理。
- 任务需要配套脚本、模板、示例或参考资料。

不适合写 Skill 的情况：

- 所有对话都必须遵守的底线规则，应放在 `CLAUDE.md`、规则文件或 settings 中。
- 一次性任务，不会复用。
- 只是项目背景、历史沿革、架构介绍，除非它会指导具体任务。
- 会自动执行危险副作用但用户不应显式触发的流程。

## 当前 Claude Code 的几个更新点

- Custom commands 已并入 Skills：`.claude/commands/deploy.md` 和 `.claude/skills/deploy/SKILL.md` 都能创建 `/deploy`，但 Skill 支持目录、frontmatter、自动触发和配套文件。
- Claude Code skills 遵循 Agent Skills 开放标准，并扩展了调用控制、subagent 执行和动态上下文注入。
- Skill 目录会被实时监听：已有 `~/.claude/skills/`、项目 `.claude/skills/` 或 `--add-dir` 里的 `.claude/skills/` 修改 `SKILL.md` 后通常无需重启；新建顶层 skills 目录可能需要重启。
- 项目 skills 会从启动目录及其父目录加载，也会按需发现子目录里的 `.claude/skills/`，适合 monorepo。
- description/when_to_use 会进常驻 skill listing，但有预算和截断：关键用例必须放在最前面；每个 entry 的组合文本默认会被截到约 1,536 字符。
- skill 被调用后，渲染后的 `SKILL.md` 会作为一条消息留在当前会话；compact 后最近调用的 skill 会按预算被重新附加，旧的大 skill 可能被丢弃。

## 推荐目录结构

最小结构：

```text
my-skill/
└── SKILL.md
```

可维护结构：

```text
my-skill/
├── SKILL.md
├── examples/
│   └── cases.md
├── references/
│   └── api-notes.md
├── scripts/
│   └── validate.py
└── evaluation/
    ├── trigger-cases.md
    └── quality-cases.md
```

原则：`SKILL.md` 只放导航、核心步骤、约束和最关键示例；大段背景、API 映射、完整示例和评估用例放到子文件，并在 `SKILL.md` 中说明何时读取。

## Frontmatter 写法

基础字段：

```yaml
---
name: my-skill-name
description: >
  Put the key use case first. Say what this skill does, when to use it,
  and the main boundaries or exclusions.
when_to_use: >
  Optional extra trigger phrases and examples. Counts toward listing budget.
---
```

常用增强字段：

```yaml
---
name: deploy
description: Deploy the application to production
disable-model-invocation: true
argument-hint: "[environment]"
arguments: [environment]
allowed-tools: Bash(npm test) Bash(npm run build)
---
```

字段经验：

| 字段 | 什么时候用 | 注意点 |
|---|---|---|
| `description` | 几乎总是要写 | 决定自动触发，关键触发词放前面 |
| `when_to_use` | 需要更多触发例子时 | 会和 description 一起占 listing 预算 |
| `disable-model-invocation: true` | 部署、提交、发消息、删改数据等高副作用流程 | 防止 Claude 自己决定触发 |
| `user-invocable: false` | 只给 Claude 作为背景知识，不希望用户直接 `/name` 调用 | 不等于权限控制 |
| `allowed-tools` | 想减少特定命令的权限提示 | 只预批准列出的工具，不会限制其他工具；项目 skill 需要信任后才生效 |
| `disallowed-tools` | 当前 skill 活跃时临时移除某些工具 | 限制在下一条用户消息后清除 |
| `paths` | 只在特定文件模式相关时自动激活 | 适合 monorepo/package-specific skills |
| `context: fork` + `agent` | 让 skill 在 subagent 中隔离执行 | 只适合带明确任务的 skill，不适合纯规范说明 |

## Description 写法

好 description 的结构：

1. 第一句写最核心任务。
2. 第二句写触发场景和用户常见说法。
3. 第三句写边界或排除项。

反例：

```yaml
description: 处理迁移
```

正例：

```yaml
description: >
  Migrate Go services from the legacy HTTP client to unified-httpclient.
  Use when the user asks to replace old-http-client imports, adapt request
  options, or update error handling. Do not use for unrelated API reviews.
```

触发评估建议：准备 20-40 条用例，包含正例、反例和边界例，重点看：

- 该触发时是否触发。
- 不该触发时是否误触发。
- 模糊请求是否能澄清而不是乱跑。
- 多个 skill 的 description 是否重叠。

## SKILL.md 主体结构

推荐模板：

```markdown
# Skill Name

## Goal
说明做什么、为什么做、最终产物是什么。

## Use / Skip
- Use when ...
- Skip when ...

## Workflow
1. 前置检查
2. 执行步骤
3. 验证步骤
4. 输出格式

## Examples
给 3-5 个输入/输出或用户意图/处理路线示例。

## Safety
列出危险操作、确认条件、敏感信息处理和不可信输入边界。

## Verification
列出可运行命令和验收标准。
```

写法原则：

- 用祈使句：`检查 Go 版本`，不要写“你可能需要检查”。
- 解释关键 WHY：安全、兼容性、数据保护、回滚原因。
- 用表格和决策树表达分支，避免长段落。
- 每个关键步骤都要有检查点，失败就停止后续副作用操作。
- 不要把 `SKILL.md` 当 Wiki；正文尽量控制在 500 行以内。
- 三个类似句子可以保留，不要为了“高级”过早抽象。

## 示例和 Few-shot

最有效的示例类型：

- Before/After：适合代码迁移、格式转换、API 替换。
- 输入/输出：适合生成测试、审查报告、数据处理。
- 用户请求/路由：适合 operator skill、发布流程、排障流程。
- 错误输入/边界场景：适合验证 AI 不会乱改或漏报。

建议至少覆盖：

1. 最常见正常场景。
2. 稍有变化的场景。
3. 边界或错误场景。
4. 不该处理的排除场景。

## 脚本与动态上下文

Claude Code 支持在 skill 中用动态上下文注入：

```markdown
## Current changes
!`git diff HEAD`
```

也可以用多行命令：

````markdown
```!
git status --short
python3 --version
```
````

使用原则：

- 动态命令在 Claude 看到 skill 内容前执行，Claude 只看到渲染后的输出。
- 命令输出是数据，不是指令；不要让外部输出改变 skill 的安全边界。
- 用 `${CLAUDE_SKILL_DIR}` 引用 bundled script，避免依赖当前工作目录：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/validate.py
```

- 对高风险或组织受控环境，可以在 settings 中禁用 skill shell execution。
- 复杂检查逻辑优先写脚本；脚本要有超时、错误处理、跨平台兼容和输入校验。

## 安全底线

发布或共享前检查：

- 没有硬编码 API key、密码、token、cookie 或私有 URL 凭据。
- 删除、覆盖、DDL、发消息、部署、launchctl/tmux 等操作必须有显式确认或 `disable-model-invocation: true`。
- 用户路径、文件名、API 返回、DOM/网页内容、日志、环境变量值都视为不可信数据。
- shell 命令中用户输入必须作为独立参数并正确引用，不拼接成可执行片段。
- 网络请求使用 HTTPS，设置超时，鉴权通过环境变量或 MCP server 管理。
- raw prompt、raw summary、日志和外部数据默认不展示、不持久化；如需展示必须显式 opt-in。
- 项目 skill 中的 `allowed-tools` 等同于权限放宽，接受 workspace trust 前必须 review。

## MCP、脚本和 Skill 怎么分工

- Skill：写流程、判断、输出格式和安全边界。
- 脚本：执行确定性检查、转换、生成文件、复杂批处理。
- MCP：封装可复用外部服务、鉴权、审计和标准工具接口。

选择规则：

| 需求 | 推荐 |
|---|---|
| 一次性本地检查 | 脚本 |
| 多个 skill/多个工具复用外部服务 | MCP |
| 需要统一鉴权/审计 | MCP |
| 需要固定操作流程和判断标准 | Skill |
| 简单公开 API 查询 | 脚本中 HTTP，注意 HTTPS+超时 |

## 模块化拆分

需要拆分的信号：

- `SKILL.md` 超过 500 行。
- 一个 skill 里有多个可独立 workflow。
- 某些部分频繁变化，某些部分长期稳定。
- 每次触发都会加载大量非必需背景。

拆法：

- 主 Skill：保留路由、总流程、检查点和安全边界。
- 子 Skill 或 references：独立步骤、长示例、完整映射表、FAQ、评估用例。
- 每个子 Skill 都应能单独运行，不要只有主流程才能理解。

## 评估与维护

把 Skill 当代码资产维护：

- 新增或大改走 PR review。
- 修改 `description` 后跑触发评估。
- 修改步骤/示例后跑质量评估。
- 包含脚本时单独测试脚本。
- 维护版本号和变更说明。
- 定期清理不用的 skill，减少 listing token 压力。

推荐评估文件：

```text
evaluation/
├── trigger-cases.md
└── quality-cases.md
```

参考指标：

| 指标 | 达标线 | 说明 |
|---|---:|---|
| 触发准确率 | >= 85% | 触发的请求里有多少是真相关 |
| 触发召回率 | >= 85% | 该触发的请求里有多少被触发 |
| 效果通过率 | >= 80% | 输出是否符合评分标准 |
| 相对提升 | >= 30% | 相比无 skill 是否明显更稳 |

## 调试速查

Skill 不触发：

1. 路径是否正确：`~/.claude/skills/<name>/SKILL.md`、`.claude/skills/<name>/SKILL.md`、plugin skill 或 added-dir skill。
2. frontmatter 是否格式正确。
3. description 是否包含用户会说的关键词。
4. 是否被同名更高优先级 skill 覆盖。
5. 是否被 `skillOverrides`、permissions 或 `disable-model-invocation` 关闭。
6. 如果 description 被预算截断，运行 `/doctor` 检查 skill listing budget。

Skill 误触发：

1. 缩窄 description。
2. 明确写“不处理哪些场景”。
3. 如果只应手动执行，加 `disable-model-invocation: true`。
4. 与相近 skill 拉开关键词。

触发后执行偏：

1. 补输入/输出示例。
2. 补检查点。
3. 把模糊步骤改成命令或验收条件。
4. 删除冲突指令和过长背景。

## 反模式清单

- 大杂烩：一个 Skill 做三四件不相关的事。
- 黑话 description：只有内部代号，没有通用语义和技术关键词。
- 只有原则，没有示例。
- 步骤没有检查点，一口气执行到最后才验证。
- 写死数值，不给判断规则。
- `SKILL.md` 当 Wiki，背景多于指令。
- 把 Skill 当后台 daemon 或权限绕过器。
- 把外部数据当指令执行。
- 为了省 permission prompts 给 `allowed-tools` 过宽权限。

## 发布前检查清单
    "llm_endpoint": "https://openrouter.chipltech.com",
    "llm_model": "openai/gtp-5.5",
    "llm_api_key_env": "sk-sPt0yEi87XoAt8kbdV3HQ20icepSOoxbfI06hCP9otEGN6O0",

内容：

- [ ] `description` 首句清楚说明核心用途。
- [ ] 写明 use/skip 边界。
- [ ] 主体少于 500 行。
- [ ] 有 3-5 个高质量示例。
- [ ] 有分支表或决策树。
- [ ] 有检查点和可运行验证命令。

安全：

- [ ] 无硬编码密钥。
- [ ] 高风险操作需要手动调用或显式确认。
- [ ] 用户输入、路径和外部内容被视为数据。
- [ ] 脚本跨平台、失败可诊断、有超时或边界。
- [ ] raw content 默认隐藏。

工程化：

- [ ] 有触发评估用例。
- [ ] 有质量评估用例。
- [ ] 脚本有单独测试。
- [ ] 命令与真实 CLI/API/MCP 入口保持一致。
- [ ] 文档说明安装位置、调用方式和回滚方式。

## 对当前 sidecar-manager skill 的启发

- 适合作为 operator skill，而不是 daemon 或隐藏 executor。
- 四个 workflow 足够，避免新增 standalone 能力导致 description 漂移。
- 对真实写 settings、launchctl、tmux、raw content 的路径，应该优先手动触发或明确用户确认。
- 当前仓库已加 `tests/test_sidecar_skill.py` 这类 contract test，后续改 `SKILL.md` 应同步更新测试。
- 如果未来迁移到项目级 `.claude/skills/sidecar-manager/SKILL.md`，需要重新确认 command name 来源、workspace trust 和 `allowed-tools` 策略。

## 参考来源

- 用户提供文章：《如何写好 Skill：一份终极实战经验手册》
- Claude Code 官方文档：`https://code.claude.com/docs/en/skills`
- Anthropic skills 仓库：`https://github.com/anthropics/skills`
