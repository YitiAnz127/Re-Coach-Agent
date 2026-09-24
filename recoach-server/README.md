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
| `POST /api/v1/sessions/{sessionId}/turns/{turnId}/calibration` | 对已完成的概念讲解提交 `too_basic` / `just_right` / `too_fast`，可对同一回答改评；仅影响该用户、该概念的后续教学起点 |
| `GET /api/v1/sessions/{sessionId}/turns` | 读取该会话已完成的历史轮次（`turnId` / `userText` / `assistantText` / `mode` / `presentation` / `createdAt`），供客户端刷新后恢复对话；单次最多 200 条，只返回 `completed` 且带 presentation 的轮次 |
| `POST /api/v1/sessions/{sessionId}/forks` | 从同一 Session 创建固定 memory on/off 的公平对照分支 |
| `GET /api/v1/memories` | 只读查看当前用户的稳定记忆，可按 `status` / `type` / `domain` 过滤 |
| `GET /api/v1/metrics/summary` | 按用户或 Session 汇总运行指标 p50/p95 |
| `GET /api/v1/meta` | 查看版本、schema 与真实能力开关；未实现能力明确为 `False` |
| `GET /health` | 健康检查（始终免鉴权） |
| `GET /docs` | OpenAPI 交互文档（令牌模式下默认关闭） |

- 开发身份来自受信头 `X-User-Id` 或 `RECOACH_DEV_USER`；JSON body 不接受 `user_id`。
- Turn 会验证 Session 归属；不存在和越权统一返回 `404 SESSION_NOT_FOUND`。
- 请求校验失败统一返回 `422 {error:{code:"INVALID_REQUEST", ...}}`。
- `x-user-id` 只在调用方通过鉴权后才可信：令牌模式校验 `RECOACH_API_TOKEN`，未配置令牌时进入开发模式（只接受本机回环客户端）。
- 默认信任模型是「单实例 + 可信客户端」：共享令牌只回答"谁能访问"，**任何持令牌者都能用 `x-user-id` 指定任意身份**。本机单人使用没有实际风险；需要收紧时设置 `RECOACH_LOCKED_USER`，身份被钉死为该用户，请求里指定他人返回 `401 UNAUTHORIZED`（会话级路由按既有约定收敛为 `404`）。非法值会让进程启动失败，而不是被静默忽略。

### 在线链路

- 最小 Session 状态和最近对话预读。
- Clarification Gate 输出三态：`READY`、`NEEDS_CLARIFICATION`、`ANSWER_WITH_ASSUMPTION`；每轮只问一个澄清问题，最多连续两轮，后续回答继承上一轮概念。
- 只有形成 `ResolvedTask` 后才执行完整长期记忆检索。
- 确定性 Context Compiler 去重、排序、覆盖和裁剪，不使用额外 Planner LLM；注入的记忆与历史对话以定界符包裹（`<untrusted_memory>`），并要求系统提示词声明其不可信语义。
- 本轮目标深度与教学起点分开：明确自述优先，其次同概念反馈，再次同命题的显式状态；没有证据保持 `unknown`。概念反馈独立于长期表达偏好，关闭记忆时不提供；Fair Fork 不写入或读取新反馈。
- 单次主 Coach 流式输出；外部模型首字前失败时回退到模板，保证链路可用（`RECOACH_LLM_FAIL_FAST=true` 时改为直接返回 `MODEL_UNAVAILABLE`）。
- SSE 事件共五种：`turn.started`、`assistant.delta`、`assistant.thinking`、`turn.completed`、`turn.error`；每条流恰好一个终止事件。
- `assistant.thinking` 转发模型 `reasoning_content`，思考过程实时可见；正文首字延迟（TTFT）与思考 TTFT 分开记录。
- canonical Turn 完成态落库成功后才发送 `turn.completed`。
- 进程启动时把遗留的 `streaming` Turn 标为 `error`（`CANCELLED` / `ProcessRestart`），客户端用同一 `clientTurnId` 重试即可原子 claim 后重新执行。只回收 `owner_instance` 等于本实例的行（以及该列引入之前的空值行）：多实例共享同一份 DB 时，无条件扫描会把别的实例**正在流式输出**的轮次误标为失败，那些轮次随后被重试认领，等于同一轮重复执行（双份 LLM 成本 + 两个写入者）。实例标识持久化在库里而非主机名，因此容器重建后仍是同一个实例，单实例部署保持"启动即恢复"。

  流式期间由独立线程按墙钟刷新心跳（`turns.TURN_HEARTBEAT_SECONDS`，5 秒）。心跳刻意不挂在 SSE 事件上：上游可能出现「持续有数据但不产出任何事件」（代理注入的 `: keep-alive`、空 delta 分片），挂在事件上的心跳会静默停掉，让正在进行的轮次被判成孤儿并抢走。

  另一条兜底：`streaming` 行只要**失去心跳**超过 `llm_timeout × 2 + 60` 秒（`turns.stale_streaming_cutoff`），就按孤儿回收/认领。判据是「多久没有心跳」而不是「跑了多久」——`llm_timeout` 限的是两次数据之间的间隔，一轮合法 Turn 的持续输出时间可以远超它。它保证 owner 不匹配却又不属于本实例的行（例如改过 `RECOACH_INSTANCE_ID`、或只恢复了 `turns` 而没恢复 `runtime_meta`）也能自愈，而不是让那个 `clientTurnId` 永远 409。

### 记忆与状态

- SQLite 是本地真相来源：启用 WAL、参数化查询和 FTS5；FTS5 不可用时降级到 LIKE。
- 稳定记忆类型：`explanation_preference`（讲解偏好）与 `interaction_rule`（交互规则），由本地规则门控确定，不经过模型猜测。
- 五级作用域：`user_id + domain + concept_scope + proposition_scope + task_scope`；同领域不同概念的具体规则不会被错误召回。
- 当前请求可临时覆盖冲突的长期偏好，不删除原记忆。
- 同作用域更新保留 `archived + superseded_by` 演化链。
- 支持显式长期写入、Session-only 规则和自然语言遗忘（如"忘记之前关于……的偏好"）。
- `RECOACH_MEMORY_ON` 是普通 Chat 的服务端记忆策略；客户端不能在单个 Turn 中覆盖（请求带 `memoryMode` 会返回 422）。需要 On/Off 对比时使用 Fair Fork，由服务端生成固定且只读的两个隔离分支。
- Session Brief 做 P0 字段级 Delta 更新：目标、当前焦点、开放问题和连续澄清计数；CAS 冲突可被调用方观测。
- 命题级 Concept State 保存 `state + evidence_kind + source_event_id`，状态必须有来源事件。

### 幂等与迁移

- `(session_id, clientTurnId)` 唯一标识一个逻辑 Turn。
- 可被 claim 重跑的状态有三种：失败态 `error`、超时孤儿的 `streaming`、以及 `completed` 但 `presentation_json` 为空的不一致态——后者既不能被重放也不能被认领，不处理就会让那个 `clientTurnId` 永远 409。
- 完成态重试重放相同 `turnId` 和 canonical 回答；失败态重试复用相同 Turn，不重复用户/助手消息、事件和显式记忆写入。
- 流仍在处理时返回 `409 TURN_IN_PROGRESS`，防止并发重复执行；同一 id 提交不同内容返回 `409 TURN_CONFLICT`。
- `schema_migrations` 记录轻量迁移，当前 schema 版本可从 `/api/v1/meta` 查看。

### 事件流水

所有可评估行为写入 Event Ledger（`events` 表），作为指标的**唯一事实来源**：

- 事件类型白名单 18 种：`turn_started`、`clarification_asked`、`clarification_resolved`、`memory_recalled`、`memory_selected`、`context_compiled`、`model_called`、`response_completed`、`feedback_received`、`memory_written`、`memory_archived`、`concept_state_updated`、`session_brief_updated`、`turn_failed` 等；
- `tool_called`、`memory_candidate_created`、`remote_sync_attempted`、`remote_sync_completed` 为预留事件：事件名已在白名单，对应功能尚未实现；
- 事件按 `(turn_id, kind)` 幂等记录，重试时返回既有 `event_id`。

### 请求保护

- `RECOACH_RATE_LIMIT_PER_MINUTE`：每身份每分钟计费型请求上限，超限返回 `429`（附 `retry-after`）。完成态重放与 409 冲突不消耗配额。
- `RECOACH_MAX_CONCURRENT_TURNS`：并发的流式 Turn 上限，超限返回 `503 SERVICE_BUSY`。
- `RECOACH_MAX_BODY_BYTES`：请求体上限，按实际收到的字节数复核（不信任 `Content-Length`），超限返回 `413`。
- 以上计数都在进程内存中，多副本部署时各算一份。

### 自动化验证

后端测试（`pytest -q`）覆盖：

- Session/SSE 基础契约与唯一终止事件；
- canonical 文本一致与失败重试幂等；
- Session 所有权与 404 语义、令牌模式与开发模式的鉴权边界；
- 澄清与上下文续接、编号选项与非信息性输入的处理、内部标记剥离；
- 记忆写入、召回、覆盖、遗忘和冲突链；
- 同领域错误泛化防护；
- 提示注入定界与 Brief CAS 可观测性；
- 会话历史恢复（顺序、原始输入保留、404 语义、条数上限、只有 completed 才返回）；
- migration、health、meta 与 DeepSeek provider 行为（thinking 转发、TTFT 记录）；
- 服务端默认记忆策略、请求级 `memoryMode` 返回 422、旧幂等记录重放兼容、Fair Fork 分支模式固定与 Off 分支零个性化、fork 创建原子性；
- provider 失败分类、降级披露与 `RECOACH_LLM_FAIL_FAST` 行为；
- 逐问教学起点（明确自述优先、同概念反馈校正、无证据保持 `unknown`）与概念反馈接口（只有归属正确的已完成讲解轮次可评）；
- 单用户身份锁定、自建端点 Base URL 的明文 `http` 拦截、流式单行上限与指标扫描上限；
- 实例归属的启动恢复、孤儿 `streaming` 行认领与「completed 但无 presentation」的恢复。

跨文件一致性另有 `tools/consistency_audit.py`（在仓库根目录运行）：核对 `config.py` 的 32 个配置字段是否全部在 `.env.example` 有说明且默认值一致、错误码是否被前端硬编码、后端 Metrics 字段是否同步到前端与 TUI 类型、是否残留调试输出。

## 3. 环境要求

- Python 3.10 或更高（`Dockerfile` 基础镜像与 CI 都用 3.10；本文档的示例 venv 用 3.11）。
- Windows PowerShell 示例；其他平台可使用等价命令。

## 4. 安装

以下命令请在后端项目根目录执行（即能看到 `app/`、`requirements.txt` 和 `.env.example` 的目录）。README 不依赖任何特定电脑的盘符、用户名或项目绝对路径。

推荐用 [uv](https://docs.astral.sh/uv/) 创建独立虚拟环境并安装依赖：

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv -r requirements.txt
```

不使用 uv 时，用标准库等价完成：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

> 后续所有启动、测试命令都通过 `.venv` 执行，不要使用系统全局 Python，避免依赖串环境。

### `requirements.lock.txt` 只用于镜像，不要在 Windows 上装它

`requirements.lock.txt` 是**给 Linux 镜像用的**锁文件：它由 `python:3.10-slim` 里的 `pip freeze` 生成，而 `pip freeze` 不保留环境标记，解析平台专属的依赖会被写成无条件依赖——第 41 行 `uvloop==0.22.1` 只支持 Linux/macOS，在 Windows 上会因源码编译失败而中断整条安装（`RuntimeError: uvloop does not support Windows at the moment`）。

- **Windows 本地开发**：装 `requirements.txt`。`uvicorn[standard]` 自带 `sys_platform != 'win32'` 标记，在 Windows 上不会选中 uvloop（已实测通过）。
- **镜像构建**：继续用 `requirements.lock.txt`，保证与镜像环境一致。改依赖后必须重新生成（见[部署指南](../docs/deployment.md#其他)），且必须用与镜像相同版本的 Python 解析。

要跨平台复现同一套版本，需要保留环境标记的锁文件，或按平台各生成一份——`pip freeze` 做不到这两点。

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
RECOACH_DEEPSEEK_REASONING_EFFORT=low
```

支持四种主模型提供方：`template`（默认，无 key）、`openai_compatible`、`deepseek`、`anthropic`。 `RECOACH_DEEPSEEK_REASONING_EFFORT` 的代码默认值是 `medium`，`.env.example` 有意推荐 `low` （附实测延迟数据）；详细取舍见 [LLM 配置指南](README_LLM_CONFIG.md)。

不要把 API key 放入前端的 `VITE_*` 变量——`VITE_*` 会进入浏览器产物。

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

测试全部通过才算完成；用例数量不写在这里——写死的数字会随每次新增测试而腐烂。

前后端同时启动后，可在前端目录运行 `npm run smoke` 做 HTTP 级联调。

## 8. 当前开发身份边界

当前没有正式登录系统。`X-User-Id` 仅用于本地测试或由受信反向代理注入；普通公网部署不能直接信任浏览器传入该请求头。内置 Web 客户端不会持有或发送 `RECOACH_API_TOKEN`，令牌模式用于可信 API 客户端；对外提供 Web 界面需要由外层身份代理完成登录并在服务端侧注入凭证。

这不是待修的缺陷，而是刻意保留的单机单人定位：令牌只解决"谁能访问"，不解决"用户之间如何隔离"。把服务交给更多人使用时，`RECOACH_LOCKED_USER` 可以把身份钉死为单一用户、拒绝任何冒充尝试，代价是同时失去多用户能力。要做到"多人各自隔离且不可伪造"，需要令牌⇄用户绑定或签名会话，属于独立于本项目的改造。

进入生产前至少需要：

- HTTP-only Session 或 Bearer token；
- 服务端身份注入和鉴权中间件；
- CSRF、防滥用/限流和安全响应头；
- 生产 CORS allowlist；
- 日志脱敏、密钥管理和数据备份策略。

## 9. 已实现与未实现边界

`GET /api/v1/meta` 的 `capabilities` 是能力诚实性的唯一权威：未实现的能力一律返回 `False`。当前为 `True` 的项：`sqlite`、`fts5`、`memoryOn`、`fairAbFork`、`metricsSummary`、`clarificationGate`、`deterministicContextCompiler`、`idempotentTurnRetry`；`teachingCalibration` 在启用记忆时为 `True`。当前为 `False` 的项：

- `microExperiment`：受限 Python/NumPy 微型实验工具未实现；
- `complexFeedbackDistillation`：复杂、多意图反馈的回答后异步 LLM 蒸馏未实现；
- `sessionRecoveryApi`：按 `turnId` 查询单轮的接口与断线恢复重连未实现（会话级历史恢复已可用，见 `GET /api/v1/sessions/{sessionId}/turns`）；
- 记忆召回率、选择精度和错误泛化率等质量指标：尚无评测真值，暂不输出；运行指标 p50/p95 已通过 `metricsSummary` 提供。
- `supermemorySync`：Supermemory 异步同步适配器未实现。

## 10. 目录说明

```text
app/
  main.py                 FastAPI 入口、CORS、422 envelope、request-id、请求体上限、health
  config.py               集中式配置（32 个 RECOACH_* 字段）
  db.py                   SQLite schema、migration、FTS5
  auth.py                 访问控制中间件（令牌模式 / 本机回环开发模式）
  errors.py               错误码表与 provider 失败分类
  schemas.py              Pydantic 契约
  sse.py / ids.py / tokens.py
  routes/                 sessions / turns / forks / memories / meta / metrics
  services/gate.py        Clarification Gate / ResolvedTask
  services/teaching.py    逐问教学起点推断、概念反馈读取与写入
  services/instances.py   进程实例标识（启动恢复的归属判定）
  services/memory.py      作用域检索、写入、归档、遗忘、反馈分类
  services/compiler.py    确定性 Context Compiler、系统提示词、不可信内容定界
  services/orchestrator.py 单 Turn 编排与 SSE 终止语义
  services/coach.py       模板 / OpenAI-compatible / DeepSeek / Anthropic 流式 Coach
  services/brief.py       Session Brief、消息、Concept State
  services/events.py      事件白名单与幂等写入
  services/selection.py   记忆选择结果与 presentation 加载
  services/metrics.py     运行指标 p50/p95 汇总
  services/ratelimit.py   每身份限流
  services/turn_gate.py   并发 Turn 闸门
  services/turns.py       逻辑 Turn 存储、claim、历史恢复、启动时恢复
tests/
  test_api.py                       基础契约
  test_memory.py                    记忆与门控
  test_p0_contract.py               P0 契约
  test_v11_contract.py              v1.1 契约（错误信封、Fair Fork、指标）
  test_session_history.py           会话历史恢复
  test_auth_hardening.py            鉴权边界
  test_deepseek_meta.py             meta 能力诚实性
  test_deepseek_provider.py         DeepSeek provider 行为
  test_provider_faults.py           provider 故障
  test_provider_degradation.py      失败分类、降级披露与 fail-fast
  test_prompt_and_cas_hardening.py  提示注入定界与 Brief CAS
  test_ux_input_handling.py         选项选择、非信息性输入、内部标记剥离
  test_hardening_b4_b7.py           并发与幂等加固
  test_hardening_b8_b13.py          INSERT 竞态与闸门归还
  test_bugfix_regression.py         缺陷回归
  test_review_race.py               评审竞态
  test_review_snapshot.py           评审快照
  test_second_review.py             二轮评审
  test_teaching_start.py            教学起点推断与概念反馈
  test_identity_lock.py             单用户身份锁定
  test_instance_scoped_recovery.py  实例归属的启动恢复
  test_base_url_security.py         自建端点的明文 http 拦截
  test_stream_line_bound.py         流式单行缓冲上限
  test_metrics_scan_bound.py        指标汇总的扫描上限
requirements.txt          直接依赖
requirements.lock.txt     锁定版本（含传递依赖；Linux 镜像用，不保留环境标记）
pytest.ini                pytest 配置
```

---

> **知返 Re: Coach 后端用最小状态理解会话，必要时补全问题；问题足够明确后，按作用域检索少量记忆。SQLite 负责本地真相和冲突治理，Context Compiler 确定性决定记忆如何进入上下文，主 Coach 一次调用完成讲解，最终由真实事件证明记忆是否有效。**
