# 知返 Re: Coach TUI

> 知返 Re: Coach 的终端(TUI)版本 —— 面向机器学习/深度学习的个性化学习教练。

用 `@earendil-works/pi-tui` 构建。**独立应用**：不依赖 `recoach-server` 后端，澄清门控、作用域记忆、确定性上下文编译器、LLM 流式输出全部在本进程内用 TypeScript 实现（与后端逻辑 1:1 对齐）。

## 功能

- 🧠 **智能澄清**：宽泛提问先问一个高信息量问题，连续两轮后带假设继续
- 📝 **作用域记忆**：五级作用域（领域/概念/命题/任务）+ 反馈门控自动记忆
- 🎯 **确定性编译**：记忆按证据强度加权召回，预算内进 Capsule
- ⚡ **流式输出**：思考过程与正文实时渲染（Markdown）
- 🔄 **自动学习**：从反馈中提取稳定偏好（"以后先给公式"→长期记忆）
- 🔎 **澄清选项**：弹出选择列表，一键选卡点方向

## 快速开始

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

# 通用配置；TIMEOUT 与服务端一致，单位为秒
set RECOACH_LLM_TIMEOUT=90
set RECOACH_LLM_MAX_TOKENS=10000
```

数据默认存于 `~/.recoach/store.json`（可用 `RECOACH_DATA_DIR` 覆盖）。

## 测试

```bash
npm test        # vitest 单元测试（澄清门控/记忆/编译/完整 Turn）
npm run typecheck
```

## 与后端逻辑对齐

| 模块 | 对应后端 |
|---|---|
| `src/core/gate.ts` | `app/services/gate.py` |
| `src/core/memory.ts` | `app/services/memory.py` |
| `src/core/compiler.ts` | `app/services/compiler.py` |
| `src/core/coach.ts` | `app/services/coach.py` |
| `src/core/brief.ts` | `app/services/brief.py` |
| `src/orchestrator.ts` | `app/services/orchestrator.py` |
| `src/store.ts` | `app/db.py`（JSON 持久化替代 SQLite） |
| `src/ui/` | `recoach-frontend`（pi-tui 替代 React） |

## 目录结构

```text
src/
  cli.ts                入口
  config.ts             配置（RECOACH_* 环境变量）
  store.ts              JSON 文件持久化
  agent.ts              Turn 事件契约
  orchestrator.ts       Turn 编排流水线
  core/                业务逻辑（gate/memory/compiler/coach/brief/events）
  ui/                  pi-tui 界面（app / theme）
tests/                 核心逻辑单元测试
```

## 使用示例

```
> 讲讲反向传播                     ← 宽泛 → 触发澄清选项
> 我不懂 反向传播                  ← 困惑声明 → 先澄清卡点
> 讲 X 的时候先给公式              ← 反馈 → 写入长期记忆
> 忘记之前关于公式的偏好           ← 自然语言遗忘
> 以后都先讲直觉，最后再给公式      ← 交互规则
```

### 存储与 Fork 兼容性

存储文件格式损坏时会报错并保留原文件，不再静默清空。新建 Fork 保存完整记忆与概念状态快照；旧版仅保存 ID 的 Fork 仍可读取，但需重新创建才能获得内容冻结保证。
