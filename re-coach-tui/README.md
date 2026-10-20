# 知返 Re: Coach TUI

> 知返 Re: Coach 的终端(TUI)版本 —— 面向机器学习/深度学习的个性化学习教练。

用 `@earendil-works/pi-tui` 构建。**独立应用**：不依赖 `recoach-server` 后端，澄清门控、作用域记忆、确定性上下文编译器、LLM 流式输出全部在本进程内用 TypeScript 实现（与后端逻辑 1:1 对齐）。

## 功能

- 🧠 **智能澄清**：宽泛提问先问一个高信息量问题，连续两轮后带假设继续
- 📍 **逐问教学起点**：目标深度与已有前置分开；讲解后输入 `/pace basic`、`/pace ok` 或 `/pace fast`，只校正上一个概念的后续起点
- 📝 **作用域记忆**：五级作用域（领域/概念/命题/任务）+ 反馈门控自动记忆
- 🎯 **确定性编译**：记忆按证据强度加权召回，预算内进 Capsule
- ⚡ **流式输出**：思考过程与正文实时渲染（Markdown）
- 🔄 **自动学习**：从反馈中提取稳定偏好（"以后先给公式"→长期记忆）
- 🔎 **澄清选项**：弹出选择列表，一键选卡点方向
- 💾 **会话恢复**：启动时自动回到上次会话并回放历史轮次（无历史则静默开新会话）
- 🔒 **端点校验**：带密钥的自定义 Base URL 必须是 https（仅本机地址允许 http），否则拒绝启动

## 快速开始

需要 Node.js >= 22.19（见 `package.json` 的 `engines`）。

```bash
npm install
npm run build

# 启动（默认 template provider，无需密钥即可跑通全流程）
npm start
```

### 在 TUI 中登录和选模型（pi-ai）

TUI 新增 `@earendil-works/pi-ai@0.85.1` 模型层，版本与现有 `pi-tui` 对齐。教学门控、作用域记忆、上下文编译与会话仍由 Re:Coach 管理。启动后输入：

```text
/login                  选择 Provider 和授权方式
/model                  选择模型并保存
/auth                   查看本地凭证状态，不显示密钥
/logout                 删除某个 Provider 的本地凭证
/models deepseek        查看该 Provider 的内置模型目录
/help                   查看命令帮助
```

`/login` 按 Provider 实际能力提供浏览器/设备码 OAuth 或 API key 输入。授权链接和设备码会显示在临时操作区域，请在浏览器完成授权；密钥与回调码输入不回显，不进入学习对话、事件台账或记忆。按 Esc 取消操作。不是每个 Provider 都有 OAuth；例如 DeepSeek 使用 API key。

登录与选择模型是两步：登录后运行 `/model`，才能将讲解切换到新账号对应的模型。选择模型时也可以直接输入 `/model provider/model-id`；模型 ID 以 `/models provider` 或交互菜单为准。选择完成后，下方状态栏显示当前模型。

DeepSeek V4.1 Flash 的官方 API ID 是 `deepseek-flash`，可直接输入 `/model deepseek/deepseek-flash`。TUI 在 DeepSeek 登录后、打开该 Provider 的选择菜单或执行 `/models deepseek` 时，通过已配置凭证请求官方 `GET /models`，更新名称、ID、上下文与输出限制等元数据。网络或凭证错误会提示并保留离线目录；旧 `deepseek-v4-flash` 等名称显示为兼容旧 ID。该接口不提供价格，本 TUI 不将目录中的内部价格占位值用于费用报告。参考 [DeepSeek 模型目录](https://api-docs.deepseek.com/api/list-models/) 和 [模型说明](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)。

也支持在启动 TUI 前配置，避免授权过程中切换窗口：

```powershell
npm start -- login
npm start -- model
npm start

# 指定 Provider（示例，不会自动登录）
npm start -- login openai-codex oauth
npm start -- login deepseek api_key
npm start -- auth
npm start -- logout deepseek
```

全局安装后可使用相同的 `recoach login`、`recoach model`、`recoach auth` 等命令。

凭证独立保存在 `~/.recoach/auth.json`，模型选择保存在 `~/.recoach/model.json`；`RECOACH_DATA_DIR` 可同时覆盖它们与学习数据的目录。凭证是本地 JSON 文件：采用受限文件模式、原子替换和跨进程锁，不提供 OS 密钥链加密。不要提交、分享该文件或把它当作普通学习数据导出。TUI 不会自动读取或改写其他 pi 应用的账号文件。

没有显式旧 Provider/模型环境变量时，重启会恢复已保存的模型；`RECOACH_LLM_PROVIDER=pi` 且未指定 `RECOACH_LLM_MODEL` 时也会恢复。其他显式环境配置优先。命令行指定 pi 模型时，使用 `RECOACH_LLM_PROVIDER=pi`，`RECOACH_LLM_MODEL=provider/model-id`。

新路径继续保留流式正文/思考分流、读取空闲超时、有限续写与降级披露。OAuth 刷新由 pi-ai 执行，并在 Re:Coach 的持久凭证锁内提交；刷新失败保留原凭证供重试或重新登录。登录成功只说明取得并保存了凭证，实际模型权限、配额和连接仍需真实请求验证。

相关官方接口：[Models 与 CredentialStore](https://github.com/earendil-works/pi/blob/v0.85.1/packages/ai/README.md#auth)、[OAuth](https://github.com/earendil-works/pi/blob/v0.85.1/packages/ai/README.md#oauth-providers)。

### 兼容原有环境变量方式

原有 `template`、`deepseek`、`openai_compatible`、`anthropic` 调用路径保留。自建 OpenAI-compatible Base URL 继续用此方式；TUI 不会自动读取 `.env`。Windows PowerShell 示例：

```powershell
$env:RECOACH_LLM_PROVIDER = "deepseek"
$env:RECOACH_DEEPSEEK_API_KEY = "你的密钥"
$env:RECOACH_DEEPSEEK_MODEL = "账户可用的模型ID"
npm start
```

以下为 Windows CMD 环境变量示例（不是 Bash 或 PowerShell 语法），任选其一后用 `npm start`：

```bash
# DeepSeek
set RECOACH_LLM_PROVIDER=deepseek
set RECOACH_DEEPSEEK_API_KEY=你的密钥

# 或 OpenAI 兼容
set RECOACH_LLM_PROVIDER=openai_compatible
set RECOACH_LLM_BASE_URL=https://api.openai.com/v1
set RECOACH_LLM_API_KEY=你的密钥
set RECOACH_LLM_MODEL=gpt-4o-mini

# 通用配置；TIMEOUT 与服务端**语义一致**：限的是两次数据之间的间隔，
# 不是整轮总时长——持续吐字的回答不会被掐断（high 档首字就可能 >60s）
set RECOACH_LLM_TIMEOUT=90
set RECOACH_LLM_MAX_TOKENS=10000

# 首字之前模型失败时：默认降级为模板并如实标注降级原因；
# 设为 true 则不再兜底，直接以 MODEL_UNAVAILABLE 报错（与服务端同语义）
set RECOACH_LLM_FAIL_FAST=false
```

数据默认存于 `~/.recoach/store.json`（可用 `RECOACH_DATA_DIR` 覆盖）。

`RECOACH_*` 环境变量名与后端 `app/config.py` 对齐，**取值也保持一致**：例如 `RECOACH_DEEPSEEK_MODEL` 默认 `deepseek-v4-flash`、`RECOACH_ANTHROPIC_MODEL` 默认 `claude-opus-5`。两边默认值漂移会让"只配密钥不配模型"的用户在两个界面静默跑在不同模型上，因此 `tools/consistency_audit.py` 会逐项核对它们。`RECOACH_DEV_USER` 默认为 `dev_user`；`RECOACH_DEEPSEEK_REASONING_EFFORT` 在 TUI 中默认为 `medium`。

## 测试

```bash
npm test        # vitest 单元测试
npm run typecheck
npm run build   # 走 tsconfig.build.json，与 typecheck 的 tsconfig.json 覆盖范围不同
```

测试覆盖澄清门控、作用域记忆、确定性编译、完整 Turn 流水线，以及**与后端的对齐回归**和加固回归。这三条命令都由 CI 的 `tui` 作业执行（`.github/workflows/test.yml`）。

TUI 是后端管线的独立实现，两侧靠人工保持 1:1，所以除离线测试外还有一个**连真端点**的端到端用例——它需要付费密钥与外网，因此默认整体跳过，只在你手动提供密钥时执行：

```bash
RECOACH_DEEPSEEK_API_KEY=sk-xxx npx vitest --run tests/live-provider.test.ts
```

它验证的是替身测不到的东西：SSE 分帧（跨块边界、多字节字符、三种行终止符）、thinking/content 分流、降级与 fail-fast 的实际分类，以及流式增量与落库正文是否逐字一致。

## 与后端逻辑对齐

| 模块 | 对应后端 |
|---|---|
| `src/core/gate.ts` | `app/services/gate.py` |
| `src/core/memory.ts` | `app/services/memory.py` |
| `src/core/compiler.ts` | `app/services/compiler.py` |
| `src/core/coach.ts` | `app/services/coach.py` |
| `src/core/brief.ts` | `app/services/brief.py` |
| `src/core/events.ts` | `app/services/events.py` |
| `src/core/selection.ts` | `app/services/selection.py` |
| `src/orchestrator.ts` | `app/services/orchestrator.py` |
| `src/store.ts` | `app/db.py`（JSON 持久化替代 SQLite） |
| `src/ui/` | `recoach-frontend`（pi-tui 替代 React） |

## 目录结构

```text
src/
  cli.ts                入口：配置校验、数据目录、会话恢复
  index.ts              对外导出（startApp / runTurn / Store / 门控 / 编译 / 记忆）
  config.ts             配置（RECOACH_* 环境变量）
  store.ts              JSON 文件持久化
  agent.ts              Turn 事件契约
  orchestrator.ts       Turn 编排流水线
  core/                业务逻辑（gate/memory/compiler/coach/brief/events/selection）
  text.ts              码点长度（与 Python len() 对齐）
  ui/                  pi-tui 界面（app / theme / sanitize）
tests/                 核心逻辑、加固回归、与后端的对齐回归，以及可选的真模型 E2E
```

## 使用示例

```
> 讲讲反向传播                     ← 宽泛 → 触发澄清选项
> 我不懂 反向传播                  ← 困惑声明 → 先澄清卡点
> 讲 X 的时候先给公式              ← 反馈 → 写入长期记忆
> 忘记之前关于公式的偏好           ← 自然语言遗忘
> 以后都先讲直觉，最后再给公式      ← 交互规则
> /pace basic                       ← 上轮讲解太基础 → 抬高该概念后续起点
```

### 存储与 Fork 兼容性

存储文件格式损坏时会报错并保留原文件，不再静默清空。新建 Fork 保存完整记忆与概念状态快照；旧版仅保存 ID 的 Fork 仍可读取，但需重新创建才能获得内容冻结保证。
