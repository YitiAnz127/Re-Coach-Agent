# 知返 Re: Coach 前端

> 面向机器学习与深度学习学习场景的个性化讲解 Agent 的 Web 客户端。

**技术栈：** React 19 + TypeScript 5.9 + Vite
**后端 API：** `http://127.0.0.1:8000/api/v1`

---

## 1. 这是什么

这是知返 Re: Coach 的浏览器客户端，直接连接 `recoach-server`：创建学习会话、流式接收讲解、展示思考过程、个性化依据与性能指标。仓库保留 Demo 数据，便于后端不可用时单独展示 UI；未设置环境变量时默认经同源 `/api/v1` 连接真实后端。

一句话定位：

> **它把后端的澄清、记忆应用与流式讲解呈现为可读的对话，并诚实区分真实能力与 Demo 演示。**

## 2. 已实现能力

### 对话与展示

- 首次发送自动创建 Session，后续 Turn 复用同一个 `sessionId`。
- 支持澄清轮、流式回答、Markdown 与 KaTeX 公式渲染（`react-markdown` + `remark-math` + `rehype-katex`）。
- 思考过程以可折叠块实时展示（`assistant.thinking` 事件），正文出现后自动折叠，避免"静默等待"。
- 个性化依据（personalization）、执行摘要、性能指标和建议动作随回答展示。
- Performance 视图读取 `/api/v1/meta`，展示真实 provider、model 与 DeepSeek Thinking 强度。
- 普通 Chat 不提供逐轮 Memory On/Off 开关，统一使用后端配置的默认记忆策略。
- Performance 只保留 `Fair Fork Comparison`：用户输入问题并主动点击后，系统才会冻结当前 Session，并行生成固定 On/Off 两个只读分支；不会自动运行。
- `experiment`、`retrospective` 等可选模块缺失时自动隐藏，不显示占位假数据。
- 错误消息使用纯文本展示，不执行服务端 HTML。
- 桌面/移动响应式布局、键盘操作、可见焦点和减少动画样式。

### HTTP/SSE 契约

- `POST /api/v1/sessions` 建会话。
- `POST /api/v1/sessions/{sessionId}/turns` 发消息，请求带 `credentials: include`。
- 解析标准 SSE `data:` frame：支持多行 data 和没有结尾空行的最后一个 frame。
- 事件共五种：`turn.started`、`assistant.delta`、`assistant.thinking`、`turn.completed`、`turn.error`。
- 强制每条流恰好一个 `turn.completed` 或 `turn.error`；缺少终止事件、非法 JSON、终止后额外事件都转换为可重试的协议错误。
- 保留后端 `error.code`、`error.message` 和 `error.retryable`。

### 逻辑 Turn 重试

- 每个逻辑 Turn 在首次发送时生成一个 `clientTurnId`。
- 可重试错误点击"重新发送"时复用原 `clientTurnId`，复用原助手消息位置，不重复追加用户消息。
- 不可重试错误不显示重试按钮。
- 与后端 `(sessionId, clientTurnId)` 幂等重放契约一致：完成态重放相同 `turnId`，流处理中等待后重试。

### 测试与构建

当前有 **11 个 Node 内置单元测试**，覆盖：

- SSE 尾部 frame flush、缺少终止事件、重复终止事件；
- 完成事件结构校验；
- 重试复用 `clientTurnId` 且不复制用户消息；
- 不可重试错误拦截；
- 后端 LLM meta 的运行时校验、Fair Fork 能力声明解析与 provider 展示名称格式化。

此外已通过 TypeScript 检查和 Vite 生产构建；`npm run smoke` 提供前后端 HTTP 级联调。

## 3. 环境要求

- Node.js 22 或更高，npm 11 或更高。
- 后端默认运行于 `http://127.0.0.1:8000`。

## 4. 安装

在前端项目根目录执行：

```powershell
npm ci
```

## 5. 连接真实后端

仓库提供 `.env.example` 模板，首次使用先复制一份：

```powershell
Copy-Item .env.example .env.local
```

默认内容：

```env
VITE_DEMO_MODE=false
VITE_API_BASE_URL=/api/v1
```

开发服务器将 `/api` 转发到 `http://127.0.0.1:8000`。修改任何 `VITE_*` 后必须重启开发服务器；生产构建必须重新构建。Docker Compose 通过 Nginx 同源代理连接 backend，`VITE_*` 由构建参数传入，不能通过容器运行时环境修改已构建页面。未显式设置 `VITE_DEMO_MODE=true` 时默认连接真实后端。模型密钥和数据库凭据绝不能放入 `VITE_*`，因为它们会进入浏览器产物。

如需只展示 Mock 演示：

```env
VITE_DEMO_MODE=true
```

## 6. 启动

先启动后端，再启动前端：

```powershell
npm run dev
```

打开：`http://127.0.0.1:4173`（Vite 固定该端口，与后端 CORS 白名单一致）。

## 7. 验证命令

```powershell
npm test
npm run check
npm run build
```

前后端均已启动时：

```powershell
npm run smoke
```

Smoke 会验证：前端页面可访问、后端 health、CORS、Session 创建、SSE 完成事件、第一轮写入偏好 / 第二轮应用偏好、普通 Turn 拒绝已删除的 `memoryMode` 字段、相同 `clientTurnId` 重放相同 `turnId`、Fair Fork 两分支真实完成且 Off 分支无个性化依据。

可通过环境变量覆盖 smoke 地址：

```powershell
$env:RECOACH_FRONTEND_URL='http://127.0.0.1:4173'
$env:RECOACH_API_BASE_URL='http://127.0.0.1:8000/api/v1'
$env:RECOACH_BACKEND_URL='http://127.0.0.1:8000'
npm run smoke
```

## 8. 主要文件

```text
src/App.tsx                          Session、逻辑 Turn、流消费和重试
src/components/Composer.tsx          消息输入
src/components/Conversation.tsx      回答、思考折叠、澄清、实验、复盘和重试 UI
src/components/FormulaText.tsx       Markdown + KaTeX 公式渲染
src/components/FairForkComparison.tsx Performance 中按需启动并展示严格 Fair Fork
src/components/SideRail.tsx          个性化依据、计划和性能
src/services/agent-client.ts        HTTP、结构化错误、Demo / 真实模式
src/services/sse-protocol.ts        SSE frame 与唯一终止事件校验
src/services/turn-retry.ts          可测试的重试状态变换
src/services/service-meta.ts        LLM 服务信息验证和展示名称
src/data/demo.ts                     Demo 数据（真实模式不调用）
tests/                               协议与重试单元测试
scripts/e2e-smoke.mjs                前后端 smoke 联调
```

旧版 Fork 仅保存记录 ID，无法追溯恢复创建时的内容；升级后请重新创建对照，以使用完整内容快照。服务启动时会自动迁移数据库，保留既有会话和记录。

## 9. 已实现与未实现边界

### 依赖后端 P1/P2 后才能真实展示

- 受限微型实验的真实工具事件、代码和结果；当前仅 Demo 模式有实验卡片。
- 主 Coach 同轮 `retrospective`（复盘）：v1.1 已返回结构化字段；复杂模型级复盘仍不单独调用复盘模型。
- 完整的记忆应用验证状态，而不只是本轮 selected personalization 投影。
- 记忆召回率、选择精度、错误泛化率等质量指标；v1.1 已提供运行指标 p50/p95 API，质量指标待评测真值。

### 前端自身未完成

- 页面刷新后的 Session 恢复和历史消息分页。
- `GET /memories` 的只读记忆列表、来源链和作用域调试界面（后端接口已具备）。
- Session Brief、Concept State 和 Event Ledger 调试面板。
- `GET /turns/:turnId` 的断线恢复轮询/重连。
- 正式登录、退出、身份过期和权限错误体验。
- 自动化真实浏览器 E2E、视觉回归和无障碍审计；当前 smoke 是 HTTP 级联调。
- 生产部署的缓存策略、CSP 和错误监控。Docker 已提供同源反向代理。

## 10. 常见问题

### 页面仍显示"本地演示"

确认 `.env.local` 中 `VITE_DEMO_MODE=false`，然后停止并重新运行 `npm run dev`。

### 创建 Session 失败

确认后端已启动且 health 正常：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

### CORS 错误

后端 `.env` 的 `RECOACH_CORS_ORIGINS` 必须包含前端精确来源，例如 `http://127.0.0.1:4173`，修改后重启后端。

### `TURN_IN_PROGRESS`

说明相同逻辑 Turn 仍在服务端处理。等待片刻后点击同一错误消息的重试按钮；前端会继续复用原 `clientTurnId`。

---

> **知返 Re: Coach 前端把后端的最小状态理解、作用域记忆检索与一次主模型调用呈现为清晰对话：思考可见、依据可查、错误可重试，未实现的能力不假装存在。**
