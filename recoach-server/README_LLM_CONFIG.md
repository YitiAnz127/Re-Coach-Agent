# 知返 Re: Coach LLM 配置指南

本文说明如何为知返 Re: Coach 后端接入以下三类 LLM 服务：

1. DeepSeek 官方 API；
2. OpenAI-compatible Chat Completions API；
3. Anthropic 官方 Claude API。

所有模型请求都由后端发起。前端只连接 Re: Coach API，不应持有任何模型密钥。

---

## 1. 安全原则

- API key 只放在后端项目根目录的 `.env`，或设置为系统环境变量。
- 不要把 key 写入前端的 `.env.local`、源代码或任何 `VITE_*` 变量。
- 不要提交 `.env`；项目的 `.gitignore` 已排除该文件。
- 不要在日志、截图、Issue 或聊天中公开真实 key。
- 修改 `.env` 后必须完全重启后端进程。

---

## 2. 支持的 Provider

| `RECOACH_LLM_PROVIDER` | 用途 | 主要配置 |
|---|---|---|
| `deepseek` | DeepSeek 官方 API | `RECOACH_DEEPSEEK_*` |
| `openai_compatible` | OpenAI 或兼容 Chat Completions 的服务 | `RECOACH_LLM_BASE_URL`、`RECOACH_LLM_API_KEY`、`RECOACH_LLM_MODEL` |
| `anthropic` | Anthropic 官方 Claude API | `RECOACH_ANTHROPIC_*` |
| `template` | 不调用外部模型，用于本地协议和界面联调 | 无需 key |

一次只选择一个 provider。未配置、配置不完整或首字输出前远端调用失败时，系统会安全回退到 `template` （除非显式设置 `RECOACH_LLM_FAIL_FAST=true`，见第 9 节）。

---

## 3. 安装后端

以下命令在后端项目根目录执行（能看到 `app/`、`requirements.txt` 和 `.env.example` 的目录）。

### Windows PowerShell（推荐使用 uv）

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv -r requirements.txt
```

> 本地开发装 `requirements.txt`，**不要**装 `requirements.lock.txt`：锁文件由 Linux 的 `pip freeze` 生成、不保留环境标记，其中的 `uvloop` 只支持 Linux/macOS，在 Windows 上会编译失败并中断整条安装。锁文件用于镜像构建。

### Windows PowerShell（标准库）

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

后续所有启动、验证命令都通过 `.venv` 执行。如果项目已经包含可用的 `.venv`，无需重复创建。

---

## 4. 创建 `.env`

### Windows PowerShell

```powershell
Copy-Item .env.example .env
```

### macOS / Linux

```bash
cp .env.example .env
```

然后使用任意文本编辑器打开 `.env`，选择下面三种方式之一进行配置。

---

# 方式一：DeepSeek 官方 API

## 5. DeepSeek 配置

在 `.env` 中填写：

```env
RECOACH_LLM_PROVIDER=deepseek

RECOACH_DEEPSEEK_API_KEY=你的_DeepSeek_API_Key
RECOACH_DEEPSEEK_BASE_URL=https://api.deepseek.com
RECOACH_DEEPSEEK_MODEL=deepseek-v4-flash

RECOACH_DEEPSEEK_THINKING=enabled
RECOACH_DEEPSEEK_REASONING_EFFORT=low

RECOACH_LLM_MAX_TOKENS=10000
RECOACH_LLM_TIMEOUT=90
RECOACH_LLM_MAX_CONTINUATIONS=2
```

`RECOACH_LLM_MAX_CONTINUATIONS` 控制输出被 provider 截断（`finish_reason=length` / `stop_reason=max_tokens`）后的自动续写次数，默认 2，运行时硬上限为 2；三个 provider 通用。

### `RECOACH_DEEPSEEK_REASONING_EFFORT` 怎么选

代码默认值是 `medium`，但 `.env.example` **有意推荐 `low`**，并在注释里附了实测数据（`tools/consistency_audit.py` 把这条非默认值登记为有意偏离，避免被当成配置漂移）：

| 档位 | 首字延迟 | 端到端 | thinking 量级 | 适用 |
|---|---|---|---|---|
| `low`（`.env.example` 推荐） | 3.1s | 10.6s | ~3000 字符 | 日常学习对话、快速答疑 |
| `medium`（代码默认） | 48s | 67s | ~6000 字符 | 复杂数学推导、多步骤推理 |
| `high` | >60s | — | ~9000 字符 | 研究级问题、极深推理链 |

`RECOACH_LLM_MAX_TOKENS` 的对应建议：`low` 档 8000–10000，`medium` 档 12000 以上，其他 provider 6000–8000。

当前项目中的 DeepSeek 默认行为：

- Thinking 默认开启；
- 默认模型为 `deepseek-v4-flash`；
- 后端会请求 `{RECOACH_DEEPSEEK_BASE_URL}/chat/completions`；
- 正文 `content` 通过 `assistant.delta` SSE 事件实时转发前端；
- 思考过程 `reasoning_content` 通过 `assistant.thinking` SSE 事件实时转发前端折叠展示；它不进入聊天记录、Event Ledger 或记忆系统；
- 首字延迟（TTFT）与思考 TTFT 分开记录，随性能指标返回。

如果账户不支持示例模型，请将 `RECOACH_DEEPSEEK_MODEL` 替换为账户当前可用的精确 API model ID。

也可以不把 key 写入 `.env`，而是在启动后端的同一终端设置标准环境变量：

### Windows PowerShell

```powershell
$env:DEEPSEEK_API_KEY='你的_DeepSeek_API_Key'
```

### macOS / Linux

```bash
export DEEPSEEK_API_KEY='你的_DeepSeek_API_Key'
```

`RECOACH_DEEPSEEK_API_KEY` 优先用于项目配置；未填写时，代码会读取系统环境变量 `DEEPSEEK_API_KEY`。

---

# 方式二：OpenAI-compatible API

## 6. OpenAI-compatible 配置

适用于 OpenAI 官方 API，以及兼容 OpenAI Chat Completions 流式协议的模型服务或网关。

在 `.env` 中填写：

```env
RECOACH_LLM_PROVIDER=openai_compatible

RECOACH_LLM_BASE_URL=https://api.openai.com/v1
RECOACH_LLM_API_KEY=你的_API_Key
RECOACH_LLM_MODEL=你的_Chat_Completions_Model_ID

RECOACH_LLM_MAX_TOKENS=10000
RECOACH_LLM_TIMEOUT=90
```

模型 ID 必须使用账户实际可用、并支持 Chat Completions 的精确 ID。

### Base URL 填写规则

后端会自动在 `RECOACH_LLM_BASE_URL` 后拼接 `/chat/completions`。

正确：

```env
RECOACH_LLM_BASE_URL=https://api.openai.com/v1
```

错误：

```env
RECOACH_LLM_BASE_URL=https://api.openai.com/v1/chat/completions
```

如果服务商文档给出的完整请求地址是：

```text
https://example.com/v1/chat/completions
```

则通常应配置为：

```env
RECOACH_LLM_BASE_URL=https://example.com/v1
```

当前适配器要求服务端支持类似以下流式响应：

```text
data: {"choices":[{"delta":{"content":"文本片段"}}]}

data: [DONE]
```

如果服务只支持 Responses API、专有 SDK 或不同的流式格式，需要新增独立 provider adapter，不能直接使用当前 `openai_compatible` 配置。

---

# 方式三：Anthropic 官方 Claude API

## 7. Anthropic 配置

在 `.env` 中填写：

```env
RECOACH_LLM_PROVIDER=anthropic

RECOACH_ANTHROPIC_API_KEY=你的_Anthropic_API_Key
RECOACH_ANTHROPIC_MODEL=你的_Claude_API_Model_ID

RECOACH_LLM_MAX_TOKENS=10000
RECOACH_LLM_TIMEOUT=90
```

`RECOACH_ANTHROPIC_MODEL` 必须是账户当前可使用的精确 API model ID。不要把网页产品名称直接当成 API model ID。

后端通过 Anthropic 官方 Python SDK 的异步 Messages 流式接口调用模型。

也可以使用标准环境变量：

### Windows PowerShell

```powershell
$env:ANTHROPIC_API_KEY='你的_Anthropic_API_Key'
```

### macOS / Linux

```bash
export ANTHROPIC_API_KEY='你的_Anthropic_API_Key'
```

未填写 `RECOACH_ANTHROPIC_API_KEY` 时，代码会读取系统环境变量 `ANTHROPIC_API_KEY`。

---

## 8. 不要混用配置

只需将 `RECOACH_LLM_PROVIDER` 设置为当前要使用的 provider。其他 provider 的字段可以留空。

### DeepSeek

```env
RECOACH_LLM_PROVIDER=deepseek
RECOACH_DEEPSEEK_API_KEY=...
RECOACH_DEEPSEEK_MODEL=...
```

### OpenAI-compatible

```env
RECOACH_LLM_PROVIDER=openai_compatible
RECOACH_LLM_BASE_URL=...
RECOACH_LLM_API_KEY=...
RECOACH_LLM_MODEL=...
```

### Anthropic

```env
RECOACH_LLM_PROVIDER=anthropic
RECOACH_ANTHROPIC_API_KEY=...
RECOACH_ANTHROPIC_MODEL=...
```

Provider 名称必须全部小写，并与上述值完全一致。

---

## 9. 验证真实模型调用

本仓库**不提供**独立的模型自检脚本。要确认真实模型是否生效，用下面两步，都能从现有接口读出结果，且不会打印任何密钥内容。

### 第 1 步：确认配置被后端识别

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/meta
```

`llm` 对象会返回：

```json
{
  "provider": "deepseek",
  "model": "deepseek-v4-flash",
  "configured": true,
  "keysPresent": { "deepseek": true, "anthropic": false, "openaiCompatible": false },
  "thinkingEnabled": true,
  "reasoningEffort": "low"
}
```

- `configured` 为 `false` 且 `provider` 是 `template`，说明配置没被读到。
- `keysPresent` 只暴露"某 provider 是否配了密钥"的布尔值，**从不暴露密钥内容**。它的用途是识别最常见的配置失误：密钥已填但 `RECOACH_LLM_PROVIDER` 忘了切换——此时应用会一直安静地走模板。

注意：`/meta` 只证明配置被识别，**不证明远端调用成功**。

### 第 2 步：发一轮真实对话，看本轮实际 provider

新建一个 Session 并发一条消息，然后检查 `turn.completed` 事件里 `presentation.metrics` 的这几个字段：

| 字段 | 含义 |
|---|---|
| `provider` / `model` | **本轮实际**使用的 provider 与模型。真实模型失败降级时这里会是 `template` |
| `fallback` | 本轮是否发生了模板降级 |
| `fallbackReason` | 降级的粗粒度原因：`AUTH` / `QUOTA` / `TIMEOUT` / `NETWORK` / `PROVIDER_ERROR` / `HTTP_ERROR` / `ERROR` |

判断标准是这两个字段，**不是** `/meta` 里的配置值：配置了真 key 但鉴权失败时，`/meta` 依然显示 `deepseek`，而 `metrics.provider` 会是 `template`、`fallback` 为 `true`。Web 界面的 Performance 视图展示的就是这两个字段（`ModelBadge` 与侧栏都刻意不从 `/meta` 取值）。

`fallbackReason=AUTH` 说明密钥无效、过期或无权限；`NETWORK` 说明连不上 Base URL； `HTTP_ERROR` 通常是模型名或接口地址写错。

### 如果想让失败立刻可见

在 `.env` 中设置：

```env
RECOACH_LLM_FAIL_FAST=true
```

此时 provider 在首字前失败不再降级为模板，而是直接以 `MODEL_UNAVAILABLE` 结束本轮，并附带按 `fallbackReason` 生成的、可操作的提示（不含异常原文、URL、响应体或密钥）。两种模式都会如实告知用户，区别只是"给个兜底回答"还是"直接报错"。

### 排查清单

配置没生效时依次检查：

- `.env` 是否位于后端项目根目录；
- 当前进程的工作目录是否是 `recoach-server`（相对路径 `RECOACH_DB_PATH` 等以进程工作目录为基准）；
- `RECOACH_LLM_PROVIDER` 是否拼写正确（必须全小写）；
- 对应 key、model 和 Base URL 是否完整；
- 修改配置后是否启动了新的 Python 进程（`Settings` 带 `lru_cache`，只有重启后端才会重新读取）；
- 后端终端输出的 HTTP 错误。

---

## 10. 启动后端

在 `recoach-server` 根目录执行。

### Windows PowerShell

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### macOS / Linux

```bash
./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

检查服务：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/meta
```

注意：`/meta` 只能证明配置被后端识别。是否能成功调用远端模型，仍应以第 9 节第 2 步的 `metrics.provider` 与 `metrics.fallback` 为准。

---

## 11. 启动前端

另开一个终端，进入前端项目：

```powershell
cd recoach-frontend
npm ci
npm run dev
```

浏览器打开：

```text
http://127.0.0.1:4173
```

前端默认就是连真实后端：`VITE_API_BASE_URL` 缺省为同源 `/api/v1`（开发时由 Vite 代理到 `http://127.0.0.1:8000`），`VITE_DEMO_MODE` 缺省为关闭。仓库**不提供**前端 `.env.example`；需要覆盖时自建 `.env.local`，例如：

```env
VITE_DEMO_MODE=false
VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1
```

改任何 `VITE_*` 后必须重启开发服务器；生产构建必须重新构建。

Performance 页面会显示后端返回的 provider 和 model。DeepSeek thinking 开启时，还会显示：

```text
Thinking · LOW
```

（档位取 `RECOACH_DEEPSEEK_REASONING_EFFORT`，例如 `low` 时显示 `Thinking · LOW`。）

前端不会直接连接任何模型供应商，也不会读取模型 API key。

---

## 12. 前后端联调

前后端都启动后，在 `recoach-frontend` 目录执行：

```powershell
npm run smoke
```

Smoke 测试会验证：

- 前端页面可访问；
- 后端 health 与 CORS 响应头；
- `/api/v1/meta` 可读；
- Session 创建；
- SSE 完成事件（恰好一个终止事件，且为 `turn.completed`）；
- 第一轮写入偏好 / 第二轮应用偏好（`presentation.personalization` 非空）；
- 相同 `clientTurnId` 重放相同 `turnId`；
- 普通 Turn 带已删除的 `memoryMode` 字段返回 422；
- Fair Fork 两个分支都能真实完成，且 Off 分支的个性化依据为空。

可通过环境变量覆盖 smoke 地址：

```powershell
$env:RECOACH_FRONTEND_URL='http://127.0.0.1:4173'
$env:RECOACH_API_BASE_URL='http://127.0.0.1:8000/api/v1'
$env:RECOACH_BACKEND_URL='http://127.0.0.1:8000'
npm run smoke
```

Smoke 测试主要验证 Re: Coach 前后端连接，不替代真实模型调用验证。真实模型是否生效请用第 9 节的 `metrics.provider` / `metrics.fallback` 判断。

---

## 13. 常见问题

| 现象 | 可能原因 | 处理方式 |
|---|---|---|
| 页面可以使用，但回答是固定模板 | Provider 未配置，或远端调用在首字前失败并降级 | 看 `metrics.provider` / `metrics.fallback` / `metrics.fallbackReason`（第 9 节） |
| 后端仍显示 `template` | `.env` 未加载、provider 拼写错误或配置不完整 | 检查 `.env` 并重启后端 |
| 填了 key 但仍是 `template` | `RECOACH_LLM_PROVIDER` 没切换 | 看 `/meta` 的 `keysPresent`：为 `true` 而 `provider` 是 `template` 就是这个原因 |
| `401` / `403` | Key 错误、权限不足或无可用额度 | 检查供应商账户和 key（`fallbackReason=AUTH`） |
| `model not found` | 模型 ID 错误或账户无权使用 | 使用账户当前可用的精确 API model ID |
| `404 /chat/completions` | Base URL 已包含完整路径，或接口不兼容 | Base URL 只填 API 根地址（`fallbackReason=HTTP_ERROR`） |
| DeepSeek 不显示 `Thinking · ...` | Thinking 未开启、后端未重启或前端未读取新 meta | 检查 DeepSeek 配置并重启前后端 |
| 页面不显示思考过程 | `RECOACH_DEEPSEEK_THINKING` 非 `enabled`，或模型不返回 `reasoning_content` | 检查配置；标准模型没有思考片段是正常行为 |
| Anthropic 调用失败 | Key、model ID、额度或网络问题 | 检查 `RECOACH_ANTHROPIC_*` 和后端日志 |
| 修改 `.env` 后没有变化 | 后端仍在使用旧进程中的 Settings 缓存 | 完全停止并重启 Uvicorn |
| 浏览器出现 CORS 错误 | 前端来源未加入后端 allowlist | 修改 `RECOACH_CORS_ORIGINS` 后重启后端 |
| 想直接看到失败而不是兜底回答 | 默认会降级为模板 | 设置 `RECOACH_LLM_FAIL_FAST=true`（第 9 节） |

---

## 14. 当前适配器边界

当前版本尚未实现：

- 多 provider 自动切换；
- Provider 级重试和指数退避；
- OpenAI Responses API 专用适配器；
- Anthropic 自定义 Base URL；
- 模型工具调用和 P1 微型实验；
- 模型成本、速率和用量统计；
- DeepSeek reasoning 内容的持久化或安全摘要——当前 thinking 只实时转发前端展示，不持久化、不写入记忆。

需要接入新的模型协议时，应新增独立 provider adapter，而不是把供应商特例继续堆叠到通用配置中。
