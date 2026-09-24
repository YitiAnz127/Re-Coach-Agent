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

配置真实模型（任选其一）后用 `npm start`：

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
