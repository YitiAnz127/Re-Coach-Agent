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

一次只选择一个 provider。未配置、配置不完整或首字输出前远端调用失败时，系统会安全回退到 `template`。

---

## 3. 安装后端

以下命令在后端项目根目录执行（能看到 `app/`、`requirements.txt` 和 `.env.example` 的目录）。

### Windows PowerShell（推荐使用 uv）

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv -r requirements.lock.txt
```

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
RECOACH_DEEPSEEK_REASONING_EFFORT=high

RECOACH_LLM_MAX_TOKENS=6000
RECOACH_LLM_TIMEOUT=60
RECOACH_LLM_MAX_CONTINUATIONS=2
```

`RECOACH_LLM_MAX_CONTINUATIONS` 控制输出被 provider 截断（`finish_reason=length` / `stop_reason=max_tokens`）后的自动续写次数，默认 2，运行时硬上限为 2；三个 provider 通用。

当前项目中的 DeepSeek 默认行为：

- Thinking 默认开启；
- Reasoning effort 默认为 `high`；
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

RECOACH_LLM_MAX_TOKENS=6000
RECOACH_LLM_TIMEOUT=60
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

RECOACH_LLM_MAX_TOKENS=6000
RECOACH_LLM_TIMEOUT=60
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

项目提供了验证脚本。它不会打印完整 API key，只会说明 key 是否已配置。

### Windows PowerShell

```powershell
.\.venv\Scripts\python.exe scripts\verify_llm.py
```

### macOS / Linux

```bash
./.venv/bin/python scripts/verify_llm.py
```

配置 DeepSeek 且调用成功时，输出类似：

```text
requested_provider=deepseek
resolved_provider=deepseek
model=deepseek-v4-flash
base_url_configured=True
api_key_configured=True
thinking=enabled
reasoning_effort=high
response_provider=deepseek
response_model=deepseek-v4-flash
fallback=False
ttft_ms=<首字延迟毫秒>
preview=<回答正文前 240 字>
status=OK
```

`preview` 只展示正文（`content`），不包含思考片段。`ttft_ms` 是首个正文 token 的延迟。

### `status=NOT_CONFIGURED`

表示系统仍然解析成 `template`。检查：

- `.env` 是否位于后端项目根目录；
- 当前终端是否位于 `recoach-server`；
- `RECOACH_LLM_PROVIDER` 是否拼写正确；
- 对应 key、model 和 Base URL 是否完整；
- 修改配置后是否启动了新的 Python 进程。

### `status=FAILED_EXTERNAL_CALL` 或 `fallback=True`

表示外部请求失败并回退到了模板。检查：

- API key 是否正确；
- 账户是否有权限和额度；
- model ID 是否可用；
- Base URL 是否正确；
- 网络、代理或地区限制；
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

`/api/v1/meta` 会返回当前模型信息（`llm` 对象），例如：

```json
{
  "provider": "deepseek",
  "model": "deepseek-v4-flash",
  "configured": true,
  "thinkingEnabled": true,
  "reasoningEffort": "high"
}
```

注意：`/meta` 只能证明配置被后端识别。是否能成功调用远端模型，仍应以 `scripts/verify_llm.py` 的 `status=OK` 为准。

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

前端新复制创建的 `.env.local` 应为：

```env
VITE_DEMO_MODE=false
VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1
```

Performance 页面会显示后端返回的 provider 和 model。DeepSeek thinking 开启时，还会显示：

```text
Thinking · HIGH
```

前端不会直接连接任何模型供应商，也不会读取模型 API key。

---

## 12. 前后端联调

前后端都启动后，在 `recoach-frontend` 目录执行：

```powershell
npm run smoke
```

Smoke 测试会验证：

- 前端页面；
- 后端 health；
- CORS；
- Session 创建；
- SSE 完成事件；
- 记忆写入和应用；
- 幂等重放；
- provider、model、thinking 和 reasoning effort 元数据。

Smoke 测试主要验证 Re: Coach 前后端连接，不替代真实模型验证脚本。

---

## 13. 常见问题

| 现象 | 可能原因 | 处理方式 |
|---|---|---|
| 页面可以使用，但回答是固定模板 | Provider 未配置，或远端调用在首字前失败 | 运行 `scripts/verify_llm.py` |
| 后端仍显示 `template` | `.env` 未加载、provider 拼写错误或配置不完整 | 检查 `.env` 并重启后端 |
| `401` / `403` | Key 错误、权限不足或无可用额度 | 检查供应商账户和 key |
| `model not found` | 模型 ID 错误或账户无权使用 | 使用账户当前可用的精确 API model ID |
| `404 /chat/completions` | Base URL 已包含完整路径，或接口不兼容 | Base URL 只填 API 根地址 |
| DeepSeek 不显示 `Thinking · HIGH` | Thinking 未开启、后端未重启或前端未读取新 meta | 检查 DeepSeek 配置并重启前后端 |
| 页面不显示思考过程 | `RECOACH_DEEPSEEK_THINKING` 非 `enabled`，或模型不返回 `reasoning_content` | 检查配置；标准模型没有思考片段是正常行为 |
| Anthropic 调用失败 | Key、model ID、额度或网络问题 | 检查 `RECOACH_ANTHROPIC_*` 和后端日志 |
| 修改 `.env` 后没有变化 | 后端仍在使用旧进程中的 Settings 缓存 | 完全停止并重启 Uvicorn |
| 浏览器出现 CORS 错误 | 前端来源未加入后端 allowlist | 修改 `RECOACH_CORS_ORIGINS` 后重启后端 |

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
