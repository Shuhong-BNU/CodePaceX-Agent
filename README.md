# CodePaceX Agent

<p align="center">
  <a href="./README.md"><strong>中文文档</strong></a>
  ·
  <a href="./README.en.md"><strong>English README</strong></a>
</p>

<p align="center"><strong>面向真实代码库的终端 AI 编程 Agent</strong></p>

<p align="center">
  <img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-blue">
  <img alt="Textual TUI" src="https://img.shields.io/badge/Textual-TUI-purple">
  <img alt="MCP" src="https://img.shields.io/badge/MCP-enabled-green">
  <img alt="Multi-Agent" src="https://img.shields.io/badge/Multi--Agent-worktree-orange">
  <img alt="Agent Eval" src="https://img.shields.io/badge/Agent%20Eval-baseline%20v1-success">
</p>

CodePaceX Agent 是一个使用 Python 构建的终端 AI 编程助手。它围绕真实代码库中的阅读、计划、编辑、验证和权限控制展开，把 ReAct 工具循环、Plan Mode、多模型协议、MCP/Skill 扩展、会话压缩、多 Agent 协作与轻量级 Agent Eval 收拢到一套可运行、可回归、可扩展的开发者工作流中。

名称中的 **Pace** 表示稳定推进、持续验证和迭代修复；**X** 表示 extensible，强调模型协议、工具、Skill、记忆与多 Agent 协作的扩展能力。

> CodePaceX is a terminal coding agent built around iterative tool use, plan-first workflows, extensible model protocols, durable sessions, and multi-agent collaboration.

## ✨ 核心亮点

- **渐进式 Agent Loop**：以模型—工具—结果循环完成代码阅读、定位、编辑和验证，支持流式文本、thinking 和工具调用事件。
- **Plan Mode 与审批流**：在动手改代码前先读取、提问、探索和维护计划文件，退出计划模式后进入审批流程。
- **多模型协议与 fallback**：支持 Anthropic Messages、OpenAI Responses 和 OpenAI-compatible Chat Completions，并提供模型测试、发现和 fallback 链。
- **MCP 延迟工具加载**：连接 stdio / Streamable HTTP MCP Server，仅在需要时暴露外部工具 Schema，降低上下文占用。
- **Skill 与自定义 Agent**：支持 Markdown Skill、目录型 Skill、inline/fork 执行、自定义 Python 工具和项目/用户级 Agent。
- **会话恢复、上下文压缩和记忆**：JSONL 会话持久化、大型工具结果落盘、上下文摘要、恢复附件和用户/项目记忆。
- **权限模式与安全边界**：危险命令检测、路径沙箱、权限规则、会话级放行、人工确认和多种 permission mode。
- **Git worktree / 多 Agent 协作**：支持子 Agent、后台 Agent、Agent Team、Mailbox、共享任务、调用追踪和 worktree 隔离。
- **Lightweight Agent Eval Harness**：内置 6-task 轻量 Eval Suite，Baseline v1 已达到 6/6 PASS、0 FAIL、0 ERROR、0 WARNING。

## 🧭 执行流程

```mermaid
flowchart TD
    U["用户任务"] --> C["构建系统提示与项目上下文"]
    C --> L["调用 LLM"]
    L --> D{"是否产生工具调用"}
    D -- 否 --> R["返回最终结果"]
    D -- 是 --> P["权限与安全检查"]
    P --> E["执行内置工具、MCP、Skill 或 Agent"]
    E --> O["把工具结果写回对话"]
    O --> X{"接近上下文上限"}
    X -- 否 --> L
    X -- 是 --> S["持久化大结果并压缩早期对话"]
    S --> L
```

## 🏗️ 架构分层

| 层次 | 主要模块 | 职责 |
| --- | --- | --- |
| 交互层 | `app.py`、`remote.py`、`commands/` | TUI、远程界面、命令与用户审批 |
| Agent 引擎层 | `agent.py`、`client.py`、`conversation.py` | 模型调用、事件统一、工具循环与状态维护 |
| 工具扩展层 | `tools/`、`mcp/`、`skills/`、`hooks/` | 本地工具、外部协议、技能包与生命周期扩展 |
| 上下文与记忆层 | `context/`、`memory/`、`filehistory/` | Token 预算、会话恢复、项目指令与历史快照 |
| 安全与协作层 | `permissions/`、`agents/`、`teams/`、`worktree/` | 权限控制、任务委派、团队通信与文件隔离 |

这些层是职责上的逻辑分层，并非独立进程或强制的依赖隔离。

## ⚡ 快速开始

```bash
# 1. 安装 uv 与 Python 3.12
brew install uv
uv python install 3.12

# 2. 安装开发依赖
uv sync --group dev

# 3. 配置至少一个 provider 的 API Key
export DASHSCOPE_API_KEY="..."

# 4. 启动终端界面
uv run codepacex

# 5. 非交互执行一次代码分析
uv run codepacex -p "分析这个项目的入口和核心调用链"

# 6. 运行轻量级 Agent Eval Suite
./.venv/bin/python evals/run_eval.py --keep-failed
```

更多 provider、fallback、权限和 MCP 配置见下方配置章节。真实 API Key 建议写入 shell 环境或本机安全配置，不要提交到 Git。

## 📁 项目结构

```text
README.md             # 中文文档
README.en.md          # English README
CODEPACEX.md          # 项目级 Agent 指令
CODE_CHANGE_PROPOSALS.md
codepacex/
  __main__.py          # CLI 入口与运行模式分发
  app.py               # Textual TUI 入口
  remote.py            # 远程浏览器模式
  agent.py             # Agent Loop、工具调度与事件流
  client.py            # Anthropic、OpenAI、OpenAI-compatible 客户端适配
  conversation.py      # 对话历史、工具调用和工具结果消息
  commands/            # TUI 斜杠命令与命令处理器
  tools/               # ReadFile、WriteFile、EditFile、Bash、Grep 等内置工具
  permissions/         # 权限模式、路径边界、危险命令检测和规则引擎
  context/             # 上下文预算、压缩和大结果落盘
  memory/              # 项目指令、长期记忆和会话记忆
  mcp/                 # MCP 客户端、连接管理和工具封装
  skills/              # Skill 加载、解析、执行和内置 Skill
  hooks/               # 生命周期 Hook 配置与执行
  agents/              # 子 Agent、后台任务、Agent 配置和调用追踪
  teams/               # Agent Team、Mailbox、共享任务和多后端 teammate
  worktree/            # Git worktree 隔离、清理和会话集成
  filehistory/         # 文件历史快照
evals/
  run_eval.py          # 轻量级 Agent Eval Runner
  graders.py           # deterministic grader 与 metrics helper
  tasks/               # 6 个 Eval Task 的 YAML 定义
  fixtures/            # Eval 使用的最小项目 fixture
tests/                 # pytest 单元测试与 Eval Harness 测试
```

## 🧭 非交互模式调用链

下面是 `uv run codepacex -p ...` 的主要文件级落点。TUI 和 remote 模式会在入口处分流到 `app.py` 或 `remote.py`，因此不完全复用这条初始化路径。

```text
pyproject.toml
-> codepacex.__main__:main
-> _run_prompt(...)
-> create_client(...)
-> create_default_registry(...)
-> PermissionChecker(...)
-> Agent(...)
-> ConversationManager(...)
-> Agent.run(...)
-> client.stream(...)
-> ToolRegistry.get(...)
-> PermissionChecker.check(...)
-> Tool.execute(...)
-> ConversationManager.add_tool_results_message(...)
```

一次典型 Agent Loop 可以理解为：

```text
用户：分析项目入口
Agent：ReadFile pyproject.toml
ToolResult：发现命令入口 codepacex.__main__:main
Agent：ReadFile codepacex/__main__.py
ToolResult：发现入口创建 client、registry、checker、agent、conversation
Agent：总结主调用链
```

## 🧪 Lightweight Agent Eval

仓库包含一套轻量级、确定性的 Agent Eval Harness，用于在固定 fixture 上回归验证 CodePaceX 的非交互 Agent 行为。它会复制 fixture 到临时 workspace，运行当前 checkout 的 `codepacex -p`，采集 `stream-json` trace，在 grader 执行前计算 Agent 文件 diff，并输出 Markdown 与 JSON 报告。

运行单个任务：

```bash
./.venv/bin/python evals/run_eval.py --task codepacex_001_config_bugfix --keep-failed
```

运行完整 suite：

```bash
./.venv/bin/python evals/run_eval.py --keep-failed
```

Eval 产物默认写入 `evals/.runs/`，该目录是本地 artifact 并被 Git 忽略。Baseline v1 已在正常 Mac Terminal 中完成完整 suite：6/6 PASS，0 FAIL，0 ERROR，0 WARNING，Task Success Rate 100%。详细任务、状态分类和边界见 [`evals/README.md`](evals/README.md) / [`evals/README.en.md`](evals/README.en.md)。

除既有 deterministic Eval 外，Goal 2 已加入冻结 Qwen Pilot、真实 runtime-mapped `ExperimentProfile`、分阶段预算 reservation/逐请求 ledger、Stage B 成对最小 Pilot scope、MCP/Retention/Permission/Multi-Agent/Hook/长会话研究 runner、Microsoft SWE-bench-Live `python-only` 官方适配，以及从真实 Artifact 自动生成 Claims 的路径。CI 与 dry-run 不访问模型；SWE empty/gold evaluator smoke 已完成，但真实 paid Pilot、SWE Agent inference、正式 A/B 和长会话仍尚未执行。完整顺序、预算风险和发布边界见 [`evals/GOAL2_RUNBOOK.md`](evals/GOAL2_RUNBOOK.md)。

当前工程基线来自已合并的 PR #13（`e44f3a1`）及其 correctness closure。Goal 2 分支在该稳定基线上构建可验证实验设施；`8fd4b19` 已执行首个真实 Stage A Pilot，产生 6 次 Provider request 和 terminal `task_failure`，并暴露出随后修复的证据/计费工程缺口。该 Run 不构成效果 Claim；真实 SWE Agent inference、AgentRouter、正式付费 A/B 和长会话仍未运行。

## 🧰 环境要求

- macOS 或 Linux
- Python 3.11 以上；开发环境固定使用 Python 3.12
- [uv](https://docs.astral.sh/uv/)
- 使用 worktree 或多 pane teammate 时需要 Git，以及可选的 tmux/iTerm2
- 至少一个可用的 Anthropic、OpenAI 或兼容服务 API

## ⚙️ 安装

macOS：

```bash
brew install uv
uv python install 3.12
uv sync --group dev
```

其他平台可先按 uv 官方文档安装 uv，再执行：

```bash
uv python install 3.12
uv sync --group dev
```

验证安装：

```bash
uv run python --version
uv run codepacex --help
```

如果希望在任意代码仓库中直接使用当前源码版本，可以安装为用户级工具：

```bash
uv tool install --editable .
codepacex --help
```

editable 安装会跟随当前源码目录中的代码变化；依赖声明发生变化后，重新执行上述安装命令。

## ⚙️ 配置

CodePaceX 按以下顺序加载并合并配置：

1. `~/.codepacex/config.yaml`
2. `<project>/.codepacex/config.yaml`
3. `<project>/.codepacex/config.local.yaml`

后加载的项目配置用于覆盖或补充用户配置。建议通过环境变量提供密钥，不要提交真实 API Key。
首次使用时建议只保留一个 Provider；非交互模式默认使用列表中的第一个 Provider。

```yaml
providers:
  - name: anthropic
    protocol: anthropic
    base_url: https://api.anthropic.com
    api_key_env: ANTHROPIC_API_KEY
    default_model: claude-sonnet-4-6
    models:
      - claude-sonnet-4-6
      - claude-haiku-4-5
    thinking: true
    context_window: 200000
    max_output_tokens: 16000

  - name: openai
    protocol: openai
    base_url: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY
    default_model: gpt-5.5
    models:
      - gpt-5.5
      - gpt-5.4-mini

  - name: aliyun
    protocol: openai-compat
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
    api_key_env: DASHSCOPE_API_KEY
    default_model: qwen-plus
    models:
      - qwen-plus
      - qwen-turbo
      - qwen-max

  - name: deepseek
    protocol: openai-compat
    base_url: https://api.deepseek.com/v1
    api_key_env: DEEPSEEK_API_KEY
    default_model: deepseek-chat
    models:
      - deepseek-chat
      - deepseek-reasoner

  - name: openrouter
    protocol: openai-compat
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY
    default_model: openai/gpt-4o-mini
    models:
      - openai/gpt-4o-mini
      - anthropic/claude-sonnet-4
      - deepseek/deepseek-chat

  - name: moonshot
    protocol: openai-compat
    base_url: https://api.moonshot.ai/v1
    api_key_env: MOONSHOT_API_KEY
    default_model: kimi-k2.6
    models:
      - kimi-k2.6

  - name: zhipu
    protocol: openai-compat
    base_url: https://open.bigmodel.cn/api/paas/v4/
    api_key_env: ZAI_API_KEY
    default_model: glm-5.2
    models:
      - glm-5.2

  - name: xiaomi-mimo
    protocol: openai-compat
    base_url: https://api.xiaomimimo.com/v1
    api_key_env: MIMO_API_KEY
    default_model: mimo-v2.5-pro
    models:
      - mimo-v2.5-pro
      - mimo-v2.5-pro-ultraspeed
      - mimo-v2.5

  - name: ollama-local
    protocol: openai-compat
    base_url: http://localhost:11434/v1
    api_key: ollama
    default_model: qwen3:8b
    models:
      - qwen3:8b
      - llama3.1:8b
      - gemma3:4b

  - name: lmstudio-local
    protocol: openai-compat
    base_url: http://localhost:1234/v1
    api_key: lm-studio
    default_model: local-model
    models:
      - local-model

  - name: vllm-local
    protocol: openai-compat
    base_url: http://localhost:8000/v1
    api_key: token-abc123
    default_model: local-vllm-model
    models:
      - local-vllm-model

fallback:
  - aliyun/qwen-max
  - aliyun/qwen-plus
  - deepseek/deepseek-chat
  - openrouter/openai/gpt-4o-mini

permission_mode: default
enable_fork: true
enable_verification_agent: true
teammate_mode: in-process
enable_coordinator_mode: false

worktree:
  symlink_directories:
    - node_modules
    - .venv
  stale_cleanup_interval: 3600
  stale_cutoff_hours: 24

mcp_servers:
  - name: local-tools
    command: uvx
    args: [example-mcp-server]
    env:
      EXAMPLE_TOKEN: ${EXAMPLE_TOKEN}

  - name: remote-tools
    url: https://example.com/mcp
    headers:
      Authorization: Bearer ${EXAMPLE_TOKEN}
```

Provider 的 API key 解析优先级为：`api_key` 明文值、`api_key_env` 指定的
环境变量、协议默认环境变量。协议默认环境变量为 `ANTHROPIC_API_KEY` 或
`OPENAI_API_KEY`；OpenAI-compatible provider 建议显式设置 `api_key_env`，例如
`DASHSCOPE_API_KEY`、`DEEPSEEK_API_KEY` 或 `OPENROUTER_API_KEY`。当前版本不会展开
`api_key` 字段中的 `${...}` 占位符，因此不要把环境变量占位符直接写在该字段中。
MCP 的 `env` 和 `headers` 配置仍支持 `${...}` 环境变量展开。

旧的 `model` 字段仍然可用；推荐的新写法是 `default_model` + `models`。
`models` 是候选模型列表，不保证账号一定有权限调用，实际可用模型以各平台控制台
或模型列表 API 为准。本地 provider 需要先启动 Ollama、LM Studio 或 vLLM 等服务；
本地 OpenAI-compatible 服务通常不校验 key，但 OpenAI SDK 仍要求 key 非空，因此可
使用 `api_key: ollama`、`api_key: lm-studio` 这类占位值。

`fallback` 是全局备用模型链，条目格式为 `provider/model`，按第一个 `/` 分割，
因此兼容 `openrouter/openai/gpt-4o-mini` 这类模型名。建议优先配置与主模型相同
protocol 的备用模型：

```yaml
fallback:
  - aliyun/qwen-plus
  - aliyun/qwen-turbo
  - deepseek/deepseek-chat
```

fallback 只是一轮请求内的临时恢复机制，不等同于 `/model use`。fallback 成功后
不会修改当前 active provider/model，不会更新 title/status 中显示的 active model，
也不会自动修改配置文件。下一轮请求仍会先使用当前 active model，再按 fallback 链
处理可恢复错误。

fallback 只会在尚未产生可见 streaming 输出前尝试备用模型；如果模型已经输出了部分
内容，本轮不会继续切换，以避免同一条 assistant 回复混用多个模型。切到备用模型前，
CodePaceX 会按备用模型的 protocol 和 context_window 重新 compact / rebuild prompt，
不会复用主模型 runtime 下预先构造的 prompt。为了避免不同协议的
thinking/reasoning/tool 历史不兼容，已有 conversation history 时默认跳过危险的
cross-protocol fallback。

会触发 fallback 的错误包括 rate limit、网络错误、timeout、服务端错误和 overloaded。
不会触发 fallback 的错误包括 missing key、认证失败、权限不足、模型不存在、配置错误、
无效 provider/model、用户取消和工具执行错误。fallback 不提供健康缓存、自动模型发现、
测速排行或 ModelRouter。

协议取值：

- `anthropic`：Anthropic Messages API。
- `openai`：OpenAI Responses API。
- `openai-compat`：兼容 OpenAI Chat Completions 的服务。

## 🚀 运行方式

启动终端界面：

```bash
uv run codepacex
```

非交互执行：

```bash
uv run codepacex -p "分析这个项目的入口和核心调用链"
```

这个最小 Demo 会让 Agent 先读取项目配置和入口文件，定位 `pyproject.toml` 中的脚本入口，再沿着 `__main__.py` 追踪 client、registry、checker、agent 和 conversation 的创建过程，最后总结核心调用链。

输出 NDJSON 事件：

```bash
uv run codepacex -p "运行测试并总结失败原因" --output-format stream-json
```

远程模式：

```bash
uv run codepacex --remote
```

服务默认监听 `0.0.0.0:18888`。该模式会暴露本地 Agent 能力，只应在可信网络中使用。

权限模式可以通过 `--mode` 临时覆盖配置：

```bash
uv run codepacex --mode plan
uv run codepacex --mode acceptEdits
```

## 💬 常用斜杠命令

TUI 会话中可以使用 `/model` 管理当前会话的模型选择：

- `/model` 或 `/model current`：显示当前 provider、protocol、model 和 base URL。
- `/model list`：列出配置中的 provider 和候选模型，并标记当前 active 模型。
- `/model discover` 或 `/model discover <provider>`：只读列出 openai-compatible provider 的 `/models` 返回结果。
- `/model test` 或 `/model test <provider>/<model>`：对当前或指定 provider/model 发起一次最小连通性测试。
- `/model test --all`：串行测试配置中的所有 provider/models。
- `/model test --provider <provider>`：只测试指定 provider 配置中的 models。
- `/model test --fallback`：按 fallback 链顺序测试备用模型。
- `/model use <provider>/<model>`：切换当前会话后续请求使用的 provider/model。

`/model current` 会显示 fallback 链摘要；`/model list` 会标注 fallback 链中的模型。
这些展示不会联网探测健康状态，也不会显示 API Key。fallback 链只在请求失败时按配置
临时尝试备用模型，不提供自动测速、在线模型发现或 ModelRouter。

`/model discover` 是只读模型发现命令，不会修改配置文件，也不会自动覆盖 `models`。
当前最小实现只支持 `openai-compat` provider 的 `/models` 风格发现，适用于
DashScope compatible mode、DeepSeek、OpenRouter、Ollama、LM Studio 和 vLLM 等
兼容端点。发现结果表示 provider 的模型列表接口当前可见，不等于 health check，
也不代表账号一定有权限或该模型一定能 chat 调用。要验证真实可调用性，请使用
`/model test <provider>/<model>` 或批量健康检查命令。

`/model test --all`、`/model test --provider <provider>` 和 `/model test --fallback`
会复用单模型测试逻辑，对配置中已有的模型发起最小真实请求。批量检查默认串行执行，
以降低触发 rate limit 的概率；missing key 会被归为 skipped 且不会发请求，其它失败
会按认证、权限、模型不存在、rate limit、网络、timeout、服务端、overloaded 或 unknown
分类。批量检查不会测试 `/model discover` 临时发现但未写入配置的模型，不会修改 YAML，
不会覆盖 `models`，不会写入 fallback 链，也不会改变当前 active provider/model。输出
只展示 key 的 available/missing 状态，不显示真实 API Key。

## 📝 Plan Mode

Plan Mode 将 Agent 限制在读取、提问、委派探索和维护当前计划文件的范围内。它允许只读工具、`Agent`、`ToolSearch`、`AskUserQuestion`、`ExitPlanMode`，并允许 `WriteFile` 或 `EditFile` 写入 `.codepacex/plans/` 下的计划文件。调用 `ExitPlanMode` 后由交互层展示审批界面。Plan Mode 是应用级权限约束，不等同于操作系统沙箱。

## 🧩 MCP 与延迟工具加载

CodePaceX 启动时连接 MCP Server 并获取工具列表，但未使用的 MCP 工具不会立即把完整 Schema 放入模型请求。Agent 先看到可用工具名称，再通过 `ToolSearch` 激活所需 Schema，以降低大量外部工具占用的上下文空间。

当前支持：

- 本地 stdio MCP Server
- Streamable HTTP MCP Server
- MCP Server instructions 注入
- 断线后的客户端重连
- Text、Image 和 Embedded Resource 结果摘要

## 🛠️ Skill

用户级 Skill 位于 `~/.codepacex/skills/`，项目级 Skill 位于 `.codepacex/skills/`。

```markdown
---
name: dependency-review
description: Review dependency changes and compatibility risks
allowedTools:
  - ReadFile
  - Grep
  - Bash
mode: inline
---

# Workflow

1. Read dependency manifests.
2. Inspect the lockfile diff.
3. Run relevant tests.
4. Summarize compatibility and security risks.

$ARGUMENTS
```

目录型 Skill 可以包含 `SKILL.md`、`tool.json` 和 `references/<tool>.py`。其中 Python 工具在当前进程内加载，因此只能使用可信 Skill。

## 🤖 自定义 Agent

用户级 Agent 位于 `~/.codepacex/agents/`，项目级 Agent 位于 `.codepacex/agents/`。

```markdown
---
name: api-reviewer
description: Review API design and backward compatibility
tools:
  - ReadFile
  - Grep
  - Glob
model: inherit
maxTurns: 30
permissionMode: default
background: false
isolation: worktree
---

Review public API changes, compatibility risks, and missing tests.
```

`isolation: worktree` 仅在 Git 仓库中可用。隔离 Agent 的改动会保留在独立 worktree 和分支中，不会自动合并到主工作区。

## 🧠 会话、上下文与记忆

- 会话以 JSONL 保存到 `.codepacex/sessions/`，支持恢复和不完整工具链截断。
- 超大工具结果保存到 `.codepacex/session/tool-results/`，模型只接收路径和预览。
- 接近模型窗口上限时，早期对话由 LLM 生成结构化摘要，近期消息保持原文。
- 压缩恢复附件保留最近读取文件和已启用 Skill 的有限快照。
- 用户级和项目级记忆分别位于 `~/.codepacex/memory/` 与 `.codepacex/memory/`。

上下文摘要是有损操作；完整会话记录用于在需要时回查原始细节。自动记忆提取要求模型返回受约束 JSON，经结构化校验后按 user/project 作用域原子写入记忆文件并重建索引；非法输出或写入失败不会推进提取游标，重复名称会更新现有记忆而不是创建重复索引项。

## 🔐 权限与安全边界

权限检查由危险命令检测、路径边界、权限规则、会话级放行和权限模式共同决定。

| 模式 | read | write | command |
| --- | --- | --- | --- |
| `default` | allow | ask | ask |
| `acceptEdits` | allow | allow | ask |
| `plan` | allow | ask | ask |
| `bypassPermissions` | allow | allow | allow |

`plan` 模式会额外放行当前会话唯一的计划文件和少数计划工具。计划文件必须解析为当前项目 `.codepacex/plans/` 下配置的精确目标；同名文件、相似目录和路径别名不会获得放行。危险删除和设备操作属于不可覆盖的强制安全层；路径、显式规则和 Hook 按 `deny > ask > allow` 聚合，显式 deny 也优先于 Plan allow，每次工具调用最多产生一次确认。`bypassPermissions` 只跳过普通模式兜底，不能覆盖强制安全决定。

Hook 配置当前只支持 `command`、`prompt` 和 `http` action。`agent` action 尚未实现，因此会在配置加载阶段被拒绝；防御性直接调用也返回失败，不会报告虚假成功。

安全边界：

- 应用级权限检查始终存在；macOS Seatbelt 与 Linux bubblewrap 是可选的额外 OS 进程边界，可用 `/sandbox` 查看状态。
- OS backend 不可用时不会静默自动放行，获准执行的 Shell 命令会回到普通权限确认。
- Seatbelt/bwrap 不是容器或虚拟机，也不宣称隔离所有秘密读取。
- MCP、Hook 和目录型 Skill 都可能运行外部代码或访问外部服务。
- `bypassPermissions` 只应在隔离、可信、可恢复的环境中使用。

## ✅ 测试

```bash
uv run pytest --collect-only -q
uv run pytest -q
uv run python -m compileall -q codepacex tests
```

PR #13 已合并到 `origin/main`（`e44f3a1`）。测试结论以对应 commit 的可复现命令输出为准；系统能力 smoke 的 skipped 状态必须单独报告，不能记为通过。

## 📊 性能指标说明

- 延迟工具测试使用 50 个模拟重型 Schema，并验证初始 Schema 字符体积降低 90% 以上；它不是对真实百级 MCP 工具的 Token benchmark。
- 上下文压缩具备阈值、摘要、近期原文和恢复附件机制，但尚无数小时连续会话的标准化耐久测试。
- 多 Agent 支持并行与 worktree 隔离，但实际加速比取决于任务拆分、模型延迟、限流和合并成本。

在建立可复现 benchmark 前，不将合成数据解释为生产性能结论。

## 🗺️ 已知限制与 Roadmap

已知限制：

- 当前项目主要用于学习和实验 AI Coding Agent 架构，不宣称替代生产级 Claude Code 或 Codex。
- 多 Agent、Agent Team 和 remote UI 能力还需要更多真实大型仓库任务验证。
- MCP 工具数量很大时，延迟加载策略仍需要更多压测。
- 权限系统是应用级安全边界，不等同于操作系统级沙箱。
- 复杂代码修改任务仍建议人工 review 后再提交。

Roadmap：

- TUI、Remote 和 `-p` 当前共享 provider/client、核心 ToolRegistry、权限检查、项目指令、`ToolSearch`、`InstallSkill`、Agent loop 与上下文遥测。TUI/Remote 额外具有会话、记忆、Skill loader/`LoadSkill` 和 MCP 生命周期；TUI 还装配文件历史、交互提问、Plan 退出及 worktree/team UI；TUI 与 `-p` 都装配子 Agent、worktree-backed delegation 和 Team 工具，而 Remote 当前没有；`-p` 保持非交互输出和拒绝式审批。下一阶段将以显式 capability profile 和共享 RuntimeBuilder 收口公共装配，并用入口级测试锁定合理差异。由于这会同时改变同步和异步入口的创建/清理生命周期，本轮不做局部伪统一。
- Worktree inspect → approve → integrate 属于后续新功能：先输出分支、commit、diffstat 和冲突预检，再由用户显式选择集成或保留；不得自动覆盖脏主工作区。
- Goal 2 已用 `ExperimentProfile` 实现 eager/deferred tools、compression、permission strategy 与 single/multi-agent 的真实 runtime mapping，并记录 effective profile、Runtime hash 与工具 Schema 字节数；legacy `feature_flags` 仍拒绝进入 live Run，避免只改标签的伪实验。
- MCP、Retention、Permission、Multi-Agent、Hook、长会话和 SWE-bench-Live 的 runner 已就绪，但真实指标仍以 [`evals/GOAL2_RUNBOOK.md`](evals/GOAL2_RUNBOOK.md) 中的预算、官方依赖和 Artifact 闸门为准。

详细修改建议见 [`CODE_CHANGE_PROPOSALS.md`](CODE_CHANGE_PROPOSALS.md)。
