# 知返 Re: Coach 后端

> 面向机器学习与深度学习学习场景的个性化讲解 Agent 的服务端。

**技术栈：** FastAPI + SQLite + SSE + 评测接口
**核心原则：** 先把问题弄清楚，再检索相关记忆；少调用、低延迟、自动记忆、作用域明确。

---

## 1. 这是什么

这是知返 Re: Coach 的 FastAPI + SQLite 后端。它负责学习会话的生命周期：判断是否需要澄清、按作用域读取和写入稳定记忆、用确定性规则编译上下文，并通过 SSE 流式输出回答。

一句话定位：

> **它记住用户如何理解知识、当前卡在哪个命题、怎样讲更有效，并在后续问题中自动应用用户偏好的方法。**

默认 `RECOACH_LLM_PROVIDER=template`，无需模型密钥即可跑通完整协议和记忆闭环；模板只提供教学结构骨架。要得到真实学科讲解，配置支持的模型供应商即可。

## 2. 已实现能力

### API 与会话

| 端点 | 说明 |
|---|---|
| `POST /api/v1/sessions` | 创建 Session，返回 `{data:{sessionId, locale}}` |
| `POST /api/v1/sessions/{sessionId}/turns` | 提交消息并返回 `text/event-stream` 流式回答；普通会话使用服务端默认记忆策略，Fork 会话使用创建时固定的分支策略 |
| `GET /api/v1/memories` | 只读查看当前用户的稳定记忆，可按 `status` / `type` / `domain` 过滤 |
| `GET /api/v1/meta` | 查看版本、schema 与真实能力开关；未实现能力明确为 `False` |
| `POST /api/v1/sessions/{sessionId}/forks` | 从同一 Session 创建固定 memory on/off 的公平对照分支 |
| `GET /api/v1/metrics/summary` | 按用户或 Session 汇总运行指标 p50/p95 |
| `GET /health` | 健康检查 |
| `GET /docs` | OpenAPI 交互文档 |

- 开发身份来自受信头 `X-User-Id` 或 `RECOACH_DEV_USER`；JSON body 不接受 `user_id`。
- Turn 会验证 Session 归属；不存在和越权统一返回 `404 SESSION_NOT_FOUND`。
- 请求校验失败统一返回 `422 {error:{code:"INVALID_REQUEST", ...}}`。

### 在线链路

- 最小 Session 状态和最近对话预读。
- Clarification Gate 输出三态：`READY`、`NEEDS_CLARIFICATION`、`ANSWER_WITH_ASSUMPTION`；每轮只问一个澄清问题，最多连续两轮，后续回答继承上一轮概念。
- 只有形成 `ResolvedTask` 后才执行完整长期记忆检索。
- 确定性 Context Compiler 去重、排序、覆盖和裁剪，不使用额外 Planner LLM。
- 单次主 Coach 流式输出；外部模型首字前失败时回退到模板，保证链路可用。
- SSE 事件共五种：`turn.started`、`assistant.delta`、`assistant.thinking`、`turn.completed`、`turn.error`；每条流恰好一个终止事件。
- `assistant.thinking` 转发模型 `reasoning_content`，思考过程实时可见；正文首字延迟（TTFT）与思考 TTFT 分开记录。
- canonical Turn 完成态落库成功后才发送 `turn.completed`。

### 记忆与状态

- SQLite 是本地真相来源：启用 WAL、参数化查询和 FTS5；FTS5 不可用时降级到 LIKE。
- 稳定记忆类型：`explanation_preference`（讲解偏好）与 `interaction_rule`（交互规则），由本地规则门控确定，不经过模型猜测。
- 五级作用域：`user_id + domain + concept_scope + proposition_scope + task_scope`；同领域不同概念的具体规则不会被错误召回。
- 当前请求可临时覆盖冲突的长期偏好，不删除原记忆。
- 同作用域更新保留 `archived + superseded_by` 演化链。
- 支持显式长期写入、Session-only 规则和自然语言遗忘（如"忘记之前关于……的偏好"）。
- `RECOACH_MEMORY_ON` 是普通 Chat 的服务端记忆策略；客户端不能在单个 Turn 中覆盖。需要 On/Off 对比时使用 Fair Fork，由服务端生成固定且只读的两个隔离分支。
- Session Brief 做 P0 字段级 Delta 更新：目标、当前焦点、开放问题和连续澄清计数。
- 命题级 Concept State 保存 `state + evidence_kind + source_event_id`，状态必须有来源事件。

### 幂等与迁移

- `(session_id, clientTurnId)` 唯一标识一个逻辑 Turn。
- 完成态重试重放相同 `turnId` 和 canonical 回答；失败态重试复用相同 Turn，不重复用户/助手消息、事件和显式记忆写入。
- 流仍在处理时返回 `409 TURN_IN_PROGRESS`，防止并发重复执行；同一 id 提交不同内容返回 `409 TURN_CONFLICT`。
- `schema_migrations` 记录轻量迁移，当前 schema 版本可从 `/api/v1/meta` 查看。

### 事件流水

所有可评估行为写入 Event Ledger（`events` 表），作为指标的**唯一事实来源**：

- 事件类型白名单 18 种：`turn_started`、`clarification_asked`、`clarification_resolved`、`memory_recalled`、`memory_selected`、`context_compiled`、`model_called`、`response_completed`、`feedback_received`、`memory_written`、`memory_archived`、`concept_state_updated`、`session_brief_updated`、`turn_failed` 等；
- `tool_called`、`memory_candidate_created`、`remote_sync_attempted`、`remote_sync_completed` 为预留事件：事件名已在白名单，对应功能尚未实现；
- 事件按 `(turn_id, kind)` 幂等记录，重试时返回既有 `event_id`。

### 自动化验证

当前后端测试共 **81 项**，覆盖：

- Session/SSE 基础契约与唯一终止事件；
- canonical 文本一致与失败重试幂等；
- Session 所有权与 404 语义；
- 澄清与上下文续接；
- 记忆写入、召回、覆盖、遗忘和冲突链；
- 同领域错误泛化防护；
- migration、health、meta 与 DeepSeek provider 行为（thinking 转发、TTFT 记录）；
- 服务端默认记忆策略、任意请求级 `memoryMode` 返回 422、旧幂等记录重放兼容、Fair Fork 分支模式固定与 Off 分支零个性化、fork 创建原子性。

## 3. 环境要求

- Python 3.11 或更高（当前 `.venv` 使用 Python 3.11）。
- Windows PowerShell 示例；其他平台可使用等价命令。

## 4. 安装

以下命令请在后端项目根目录执行（即能看到 `app/`、`requirements.txt` 和 `.env.example` 的目录）。README 不依赖任何特定电脑的盘符、用户名或项目绝对路径。

推荐用 [uv](https://docs.astral.sh/uv/) 创建独立虚拟环境并安装锁文件依赖：

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv -r requirements.lock.txt
```

不使用 uv 时，用标准库等价完成：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

> 后续所有启动、测试命令都通过 `.venv` 执行，不要使用系统全局 Python，避免依赖串环境。

## 5. 配置

```powershell
Copy-Item .env.example .env
```

最低可运行配置：

```env
RECOACH_DB_PATH=./recoach.db
RECOACH_DEV_USER=dev_user
RECOACH_CORS_ORIGINS=http://127.0.0.1:4173,http://localhost:4173
RECOACH_LLM_PROVIDER=template
```

真实模型配置参考 `.env.example`：在 `.env` 中填入**你自己的** API key，例如 DeepSeek：

```env
RECOACH_LLM_PROVIDER=deepseek
RECOACH_DEEPSEEK_API_KEY=你的密钥
RECOACH_DEEPSEEK_MODEL=deepseek-v4-flash
RECOACH_DEEPSEEK_THINKING=enabled
RECOACH_DEEPSEEK_REASONING_EFFORT=high
```

支持四种主模型提供方：`template`（默认，无 key）、`openai_compatible`、`deepseek`、`anthropic`。不要把 API key 放入前端的 `VITE_*` 变量——`VITE_*` 会进入浏览器产物。

## 6. 启动

在后端项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

（已激活 `.venv` 时可直接 `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload`。）

检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/meta
```

API 文档：`http://127.0.0.1:8000/docs`。

## 7. 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

前后端同时启动后，可在前端目录运行 `npm run smoke` 做 HTTP 级联调。

## 8. 当前开发身份边界

P0 没有正式登录系统。`X-User-Id` 仅用于本地测试或由受信反向代理注入；普通公网部署不能直接信任浏览器传入该请求头。

进入生产前至少需要：

- HTTP-only Session 或 Bearer token；
- 服务端身份注入和鉴权中间件；
- CSRF、防滥用/限流和安全响应头；
- 生产 CORS allowlist；
- 日志脱敏、密钥管理和数据备份策略。

## 9. 已实现与未实现边界

`GET /api/v1/meta` 的 `capabilities` 是能力诚实性的唯一权威：未实现的能力一律返回 `False`。当前为 `True` 的项：`sqlite`、`fts5`、`memoryOn`、`fairAbFork`、`metricsSummary`、`clarificationGate`、`deterministicContextCompiler`、`idempotentTurnRetry`。当前为 `False` 的项：

- `microExperiment`：受限 Python/NumPy 微型实验工具未实现；
- `complexFeedbackDistillation`：复杂、多意图反馈的回答后异步 LLM 蒸馏未实现；
- `sessionRecoveryApi`：`GET /turns/:turnId` 与断线恢复接口未实现；
- 记忆召回率、选择精度和错误泛化率等质量指标：尚无评测真值，暂不输出；运行指标 p50/p95 已通过 `metricsSummary` 提供。
- `supermemorySync`：Supermemory 异步同步适配器未实现。

## 10. 目录说明

```text
app/
  main.py                 FastAPI 入口、CORS、422 envelope、health
  config.py               集中式配置（RECOACH_* 环境变量）
  db.py                   SQLite schema、migration、FTS5
  routes/                 sessions / turns / memories / meta
  services/gate.py        Clarification Gate / ResolvedTask
  services/memory.py      作用域检索、写入、归档、遗忘、反馈分类
  services/compiler.py    确定性 Context Compiler 与系统提示词
  services/orchestrator.py 单 Turn 编排与 SSE 终止语义
  services/coach.py       模板 / OpenAI-compatible / DeepSeek / Anthropic 流式 Coach
  services/brief.py       Session Brief、消息、Concept State
  services/events.py      事件白名单与幂等写入
  services/turns.py       逻辑 Turn 存储
scripts/
  verify_llm.py           模型供应商配置自检
tests/
  test_api.py             基础契约
  test_memory.py          记忆与门控
  test_p0_contract.py     P0 契约
  test_deepseek_meta.py   meta 能力诚实性
  test_deepseek_provider.py DeepSeek provider 行为
requirements.txt          直接依赖
requirements.lock.txt     锁定版本（含传递依赖，可复现安装）
pytest.ini                pytest 配置
```

---

> **知返 Re: Coach 后端用最小状态理解会话，必要时补全问题；问题足够明确后，按作用域检索少量记忆。SQLite 负责本地真相和冲突治理，Context Compiler 确定性决定记忆如何进入上下文，主 Coach 一次调用完成讲解，最终由真实事件证明记忆是否有效。**
