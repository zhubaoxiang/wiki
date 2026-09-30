# 大模型与 Agent 协议速查

> 整理时间：2026-09-30
> 用途：接入模型、设计 Provider 层时的字段级参考

---

## 零、先分清三层

「协议」这个词在 AI 领域横跨三个不同层级，混在一起看必然混乱：

| 层级 | 解决什么 | 代表协议 | 什么时候需要关心 |
|------|----------|----------|------------------|
| **协作层** | Agent ↔ Agent | A2A | 多个独立 Agent 跨组织协作时 |
| **工具层** | Agent ↔ 工具 / 数据 / API | MCP | 想让别人写好的工具直接接进来时 |
| **模型层** | 你的代码 ↔ 模型 | OpenAI / Anthropic / Gemini / 本地推理 | **现在就要用** |

三层是**叠加关系，不是替代关系**。一个生产级 Agent 通常三层都用。

---

## 一、模型层：四套原生格式

### 1. OpenAI Chat Completions —— 事实标准

```
POST /v1/chat/completions
Authorization: Bearer sk-xxx
```

```json
{
  "model": "gpt-5",
  "messages": [
    {"role": "system", "content": "你是一个助手"},
    {"role": "user", "content": "你好"}
  ],
  "tools": [
    {"type": "function",
     "function": {"name": "get_weather",
                  "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}
  ]
}
```

- **无状态**：每次请求都要把完整历史重发一遍
- system 是 `messages` 数组里的一条消息
- 工具结果用独立的 `role: "tool"` + `tool_call_id` 回传
- 流式是 SSE，以 `data: [DONE]` 结束
- **兼容性最好，几乎所有人都认它**

### 2. OpenAI Responses API —— Agent 应用的新方向

```
POST /v1/responses
```

```python
response = client.responses.create(
    model="gpt-5",
    input="写一句睡前故事",
    tools=[{"type": "web_search"}],
)
print(response.output_text)
```

和 Chat Completions 的关键差异：

| 维度 | Chat Completions | Responses API |
|------|------------------|---------------|
| 状态 | 无状态，客户端持有历史 | **默认有状态**，用 `previous_response_id` 续接 |
| 输入容器 | `messages`（Message 数组） | `input`（Item 数组，类型更多） |
| 内置工具 | 无 | web search / file search / code interpreter / computer use / 远程 MCP |
| 结构化输出 | `response_format` | `text.format` |
| 流式事件 | 通用 delta | 强类型事件（`response.output_text.delta`） |
| 多轮并发 | 支持 `n` 参数 | 已移除，一次只生成一个 |

**OpenAI 官方立场**：Chat Completions 继续支持、不会废弃；但**所有新项目建议从 Responses 开始**，新 agentic 能力只往 Responses 加。

**关键时间线（2026）**：

| 时间 | 事件 |
|------|------|
| 2026-08-26 | **Assistants API 停用**，`/v1/threads` 端点报错，官方不提供自动迁移工具 |
| 2026-10-31 | Evals 平台转为只读 |
| 2026-11-30 | Agent Builder（可视化编排）下线、Evals 平台下线、可复用 prompt 对象移除 |

方向很明确：**编排逻辑必须留在自己的代码仓库里，不要依赖平台侧的可视化画布。**

### 附注：别搞混 —— OpenAI 有三套 API，不是一套

这是最常见的混淆点。**Assistants API 不是 Chat Completions 的一部分**，它是独立的第三套端点。

| | Chat Completions | Assistants API | Responses API |
|---|---|---|---|
| 发布 | 2023-03 | 2023-11 | 2025-03 |
| 端点 | `/v1/chat/completions` | `/v1/assistants`、`/v1/threads`、`/v1/runs` | `/v1/responses` |
| 状态 | 无状态 | 服务端存 Thread | 默认有状态 |
| 抽象层级 | 低（`messages`） | **高（5 个对象）** | 中（Item） |
| 内置工具 | 无 | code interpreter、file search | web search、file search、code interpreter、MCP |
| 交互方式 | 一次请求一次返回 | **创建 Run + 轮询** | 直接返回，或后台任务 + webhook |
| 现状 | **活着，从未废弃** | **2026-08-26 已停用** | 推荐新项目 |

Assistants API 当年的用法（**五个对象 + 必须轮询**，这是它被淘汰的主因）：

```
创建 Assistant → 创建 Thread → 加 Message → 创建 Run
  → 轮询 Run 状态直到 completed → 读 Thread 里的 Messages
```

**它是被 Responses API 替代的，不是被 Chat Completions。**
Assistants 想解决的两件事——服务端保存会话、服务端跑工具循环——正是 Responses 也在解决的，
而且只用一套更简单的原语（Item）就做到了，还支持流式与 `previous_response_id` 续接。

**影响面**：国内厂商的兼容端点模仿的都是 Chat Completions，很少有人抄 Assistants 的对象模型，
所以这次停用对 DeepSeek / Kimi / 通义这类接入**基本没有影响**。

**需要警惕的信号**：代码或平台文档里出现 `/v1/threads` 或 `/v1/runs` → 那就是 Assistants，已经跑不通了。
OpenAI 明确表示**不提供自动迁移工具**，Thread 数据要人工搬。

### 3. Anthropic Messages API

```
POST /v1/messages
x-api-key: sk-ant-xxx
anthropic-version: 2023-06-01        ← 必须带这个版本头
```

```json
{
  "model": "claude-sonnet-4",
  "system": "你是一个助手",          ← 顶层字段，不在 messages 里
  "max_tokens": 1024,                ← 必填，漏了直接 400
  "messages": [{"role": "user", "content": "你好"}]
}
```

工具调用格式：

```json
// 工具定义：没有 function 包装层，用 input_schema
{"tools": [{"name": "get_weather", "description": "...", "input_schema": {...}}]}

// 模型返回：内嵌在 content 数组里
{"content": [{"type": "tool_use", "id": "toolu_xxx", "name": "get_weather", "input": {"city": "深圳"}}]}

// 结果回传：塞进 user 消息，用 tool_result 块
{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_xxx", "content": "26°C"}]}
```

- `content` 可以是字符串，也可以是**带类型的 block 数组**（text / tool_use / thinking / image）
- 解析必须按 `type` 分支，不能当成单个字符串
- 支持 `cache_control` 做 prompt 缓存，长 system prompt 场景能省钱
- 有独立的 `/v1/messages/count_tokens` 精确计数接口（代价是走网络，比本地 tiktoken 慢得多）

### 4. Google Gemini generateContent

```
POST /v1beta/models/{model}:generateContent
x-goog-api-key: xxx        （或 ?key=xxx 放在 URL 里）
```

```json
{
  "systemInstruction": {"parts": [{"text": "你是一个助手"}]},
  "contents": [{"role": "user", "parts": [{"text": "你好"}]}],
  "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.7}
}
```

- **模型名在 URL 里**，不在请求体
- 助手角色叫 **`model`**，不叫 `assistant`
- 层级更深：`contents[] → parts[] → text`
- `parts` 里可能同时有文本、`functionCall`、`functionResponse`、图片，**不能假设第一个 part 是文本**
- 工具定义在 `tools[].functionDeclarations`
- 取正文路径：`candidates[0].content.parts[].text`

> Google 另外推出了更新的 Interactions API 并推荐用它，但 generateContent 仍是文档明确、集成最广的格式。

### 5. 本地推理：Ollama / vLLM / SGLang

| 服务 | 原生协议 | OpenAI 兼容端点 |
|------|----------|-----------------|
| Ollama | `/api/chat`、`/api/generate` | `/v1/chat/completions` ✔ |
| vLLM | — | `/v1/chat/completions` ✔（主推） |
| SGLang | — | `/v1/chat/completions` ✔ |

接本地模型时**优先用 OpenAI 兼容端点**，这样代码不用为本地/云端写两套。

### 6. 字段级对比总表

| 维度 | OpenAI CC | Anthropic Messages | Gemini |
|------|-----------|-------------------|--------|
| 路径 | `/v1/chat/completions` | `/v1/messages` | `/v1beta/models/{m}:generateContent` |
| 鉴权 | `Authorization: Bearer` | `x-api-key` + `anthropic-version` | `x-goog-api-key` |
| system | `messages[0]` role=system | 顶层 `system` | 顶层 `systemInstruction` |
| 消息容器 | `messages` | `messages` | `contents` |
| 助手角色 | `assistant` | `assistant` | **`model`** |
| 内容结构 | 字符串或 parts 数组 | block 数组（按 type 分支） | `parts` 数组 |
| 工具定义 | `tools[].function.{name,parameters}` | `tools[].{name,input_schema}` | `tools[].functionDeclarations` |
| 工具调用返回 | `message.tool_calls[]` | content 中 `type:"tool_use"` 块 | `functionCall` part |
| 工具结果回传 | `role:"tool"` + `tool_call_id` | user 消息中 `tool_result` 块 | `functionResponse` part |
| 最大长度参数 | `max_tokens`（可选） | `max_tokens`（**必填**） | `generationConfig.maxOutputTokens` |
| 结束原因 | `finish_reason` | `stop_reason` | `finishReason` |
| 取正文 | `choices[0].message.content` | `content[0].text` | `candidates[0].content.parts[].text` |
| 流式 | SSE，`data: [DONE]` 结束 | SSE，`message_start` / `content_block_delta` / `message_stop` | SSE（需 `?alt=sse`） |

**三个高频踩坑点：**

1. Anthropic 没有 `system` 角色，直接发 `{"role":"system"}` 不认
2. Anthropic 工具结果在 `user` 消息里，不能靠 role 简单映射
3. Gemini 助手角色是 `model`，正文埋在 `parts[]` 里

---

## 二、「OpenAI 兼容」到底是什么

**它是一个社区事实标准，不是 OpenAI 发布的规范。**

含义：只要服务端长得像 `/v1/chat/completions`、认 `Authorization: Bearer`、
返回 `choices[0].message`，所有人就能用现成的 OpenAI SDK 调你。

提供兼容端点的（非 OpenAI 官方）：DeepSeek、Kimi、通义、智谱、Ollama、vLLM、SGLang、
以及 OpenRouter、n4n.ai 这类聚合网关。

**准确表述**：原生协议有好几套，但**兼容层几乎都收敛到了 OpenAI 那一套**。

### 「兼容」通常是部分兼容

接入前用最小请求探这四项，比读文档靠谱：

1. **tools 支持程度** —— 是真支持 function calling，还是只接受参数然后忽略？
2. **流式事件格式** —— delta 结构是否和 OpenAI 完全一致？
3. **structured output** —— `response_format` / `json_schema` 支持吗？
4. **静默忽略的参数** —— 传了 `temperature`、`top_p` 但服务端不生效，最坑

---

## 二·补、国内厂商用的是哪套协议

**答案分三档**，关键区分不在请求体，而在**鉴权**。

| 档 | 特征 | 厂商 |
|---|---|---|
| **A · 原生即 OpenAI** | 从未设计过自己的一套，第一天就宣称兼容 | Kimi、DeepSeek、智谱 GLM |
| **B · 自研云 API + 兼容层** | 先有自家云 API 规范，再外包一层 OpenAI 兼容 | 阿里百炼、火山方舟、腾讯混元、百度千帆 |
| **C · 完全自研协议** | 端点、请求体、鉴权都不像 OpenAI | 讯飞星火（WebSocket 三段式） |

**A 档是模型公司，B 档是云厂商。** 模型公司的产品就是模型，协议用 OpenAI 格式零成本；
云厂商的模型是自家云的一个服务，必须挂进账号 / 子账号 / 权限体系，所以鉴权一定自研。

### 端点速查

| 厂商 | 原生形态 | OpenAI 兼容端点 | Anthropic 兼容 |
|------|---------|----------------|---------------|
| Kimi | OpenAI 格式 | `api.moonshot.cn/v1` | ✅ `/anthropic` |
| DeepSeek | OpenAI 格式 | `api.deepseek.com` | ✅ `/anthropic` |
| 智谱 GLM | OpenAI 格式 | `open.bigmodel.cn/api/paas/v4` | ✅ `/api/anthropic` |
| 阿里百炼 | DashScope（`input`/`parameters` 嵌套） | `dashscope.aliyuncs.com/compatible-mode/v1` | ❌ |
| 火山方舟 | Ark（路径 `/api/v3/…`，体是 OpenAI） | `ark.cn-beijing.volces.com/api/v3` | ❌ |
| 腾讯混元 | 腾讯云 API 3.0 | `api.hunyuan.cloud.tencent.com/v1` | ❌ |
| 百度千帆 | 千帆自研 | `qianfan.baidubce.com/v2` | ❌ |
| 讯飞星火 | WS `{header,parameter,payload}` | `spark-api-open.xf-yun.com/v2` | ❌ |

两个容易踩的：

- **火山方舟路径是 `/api/v3/chat/completions`，不是 `/v1/`** —— 照抄 DeepSeek 的拼接逻辑会 404；
  且模型 ID 是控制台创建推理点时生成的动态值（如 `doubao-seed-2-1-pro-260628`），不是固定字符串。
- **百度千帆鉴权是 AK/SK → access_token（30 天有效）**，不是纯 Bearer。新旧两套并存，
  拿 IAM 的密钥去调 `/v2/chat/completions` 会报 invalid client。

### 鉴权才是分化的主战场

| 鉴权方式 | 厂商 |
|---|---|
| `Authorization: Bearer sk-xxx` | DeepSeek / Kimi / 智谱 / 各家兼容端点 |
| AK/SK 换 access_token（需缓存 + 定时刷新） | 百度千帆（老路径） |
| TC3-HMAC-SHA256 签名 | 腾讯混元（原生） |
| Access Key 签名（7 个鉴权头） | 火山方舟（原生 AK 方式） |
| APPID + APIKey + APISecret 三段签名 | 讯飞星火（WS 协议） |

**为什么请求体都学 OpenAI，鉴权却各搞一套？** 请求体收敛能零成本拉来开发者，
鉴权绑定的是自家账号体系 —— 那是云厂商的护城河，不会让出去。

### 2026 的新变量：补 Anthropic / Responses 兼容

不是为了学 OpenAI，而是为了让 **Claude Code / Codex 能直连**：

| 厂商 | OpenAI Responses | Anthropic Messages | 时间 |
|------|-----------------|-------------------|------|
| DeepSeek | ✅（故意做成子集，无状态） | ✅ | 2026-08-13 / 2026-04 |
| Kimi | ✅ | ✅ | 2026-09-02 |
| 智谱 GLM | — | ✅ | — |
| 火山方舟 | ✅（自家的 `/api/v3/responses`） | ❌ | 2026 |

**趋势判断：协议不会收敛成一个标准，而是「一个主协议 + 多个被兼容的副协议」。**
谁不兼容 Anthropic Messages，就等于把 Claude Code 用户排除在外。

### 落到 Adapter 层

每家多两个必填字段，业务代码里依然不该出现厂商名：

- `endpoint_path` —— 不能写死 `/v1/chat/completions`
- `auth_strategy` —— Bearer / AK-SK 换 token / TC3 签名

## 三、工具层：MCP（Model Context Protocol）

- **发起方**：Anthropic，2024-11 发布；2025-12 捐给 Linux 基金会的 AAIF
- **解决**：Agent 与工具、数据、API 之间的统一调用
- **核心对象**：Tools / Resources / Prompts
- **现状**：已被 OpenAI、Google、Microsoft 全部采纳；官方 registry 有大量现成 server
- **2026-07-28 规范（第 5 版）**：无状态核心、移除 `initialize` 握手与 `Mcp-Session-Id`、
  强制 OAuth 2.1、Streamable HTTP 为基线
- **已知问题**：独立探测发现 **17.2% 的远端端点不可达**；无状态改造对老客户端是**破坏性升级**，
  auth 流程基本要重写

**价值**：把「每个 Agent 各自硬编码工具调用」变成「工具在 MCP Server 注册，增减不改 Agent 代码」。

---

## 四、协作层：A2A（Agent2Agent）

- **发起方**：Google，2025-04；2026-03-12 发布 1.0 GA；2026-08-20 转入 AAIF
- **解决**：Agent 与 Agent 之间的发现、委托任务、共享状态
- **核心对象**：Agent Card → Task（submitted / working / completed / failed）→ Artifact
- **现状**：150+ 组织支持（Salesforce、SAP、ServiceNow 等）

**类比**：MCP 是 USB（连接设备和外设），A2A 是 HTTP（连接设备和设备）。

**判断标准**：只需要「一个 Agent + 多个工具」→ MCP 够了。
需要多个独立 Agent 协作 → 才考虑 A2A。**绝大多数产品在 2026 年还不需要 A2A。**

---

## 五、工程建议：怎么隔离这些差异

**核心原则：内部定义一套中立的领域模型，各协议用 Adapter 适配。**

不要在业务代码里直接拼各家的 JSON。否则加一个模型要改十处。

```python
# 内部中立格式 —— 业务代码只认这个
@dataclass
class Message:
    role: str          # instruction | user | assistant | tool
    content: str
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

# 各协议一个 Adapter，负责翻译 + 解析
class Provider(Protocol):
    def complete(self, messages: list[Message], tools: list[ToolDef]) -> Message: ...
    def stream(self, messages: list[Message], tools: list[ToolDef]) -> Iterator[str]: ...

class OpenAIProvider(Provider): ...
class AnthropicProvider(Provider): ...
class GeminiProvider(Provider): ...
```

Adapter 要吸收的差异（就是上面那张对比表）：

- system prompt 放哪（数组里 vs 顶层字段）
- 角色名映射（`assistant` vs `model`）
- 工具定义结构（`function.parameters` vs `input_schema` vs `functionDeclarations`）
- 工具结果的回传方式（`role:tool` vs user 消息里的 `tool_result` vs `functionResponse`）
- 响应正文的取法
- 流式事件的解析分支

**做对了的标志**：业务代码里看不到任何一个厂商的名字。

---

## 五·补、Claude Code 接入非 Anthropic 模型

**能做，而且是官方明确支持的路径** —— Claude Code 有专门的 `LLM Gateway Protocol` 文档。
但「能连上」和「能用好」是两件事。

### 只要两个环境变量

`ANTHROPIC_BASE_URL` 把流量导向任何说 Anthropic Messages 格式的网关：

```bash
export ANTHROPIC_BASE_URL=http://localhost:4000
export ANTHROPIC_AUTH_TOKEN=sk-xxx
export ANTHROPIC_API_KEY=""    # 必须置空，否则它优先级更高、会盖掉上面那个
export CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1   # v2.1.129+，让 /model 列出网关模型
```

模型别名映射（Claude Code 内部按 opus / sonnet / haiku 三档调用）：

| 环境变量 | 用途 |
|---|---|
| `ANTHROPIC_MODEL` | 主模型 |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | 复杂推理档 |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | 日常编码档 |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | **后台轻量任务**（tab 补全、commit message）—— 映射到便宜模型最划算 |
| `CLAUDE_CODE_SUBAGENT_MODEL` | 子 agent |

### 网关的四项硬性要求

| 要求 | 做错的后果 |
|------|-----------|
| 暴露 `/v1/messages` **和** `/v1/messages/count_tokens` | 缺后者 → 上下文预算失准、`/context` 显示错乱 |
| 原样转发 `anthropic-beta` / `anthropic-version` 头 | 剥掉 → **prompt caching 静默失效**，成本翻数倍 |
| **不缓冲 SSE**，边收边转 | 缓冲 → agent 循环卡死（Claude Code 靠实时事件驱动） |
| 不在 system prompt 前注入内容 | attribution block **每个会话都变**，插在它后面的内容一律 cache miss |

**第四条最反直觉**：网关想加一句「你是 CorpX 的助手」，就会把 Claude Code 精心构造的缓存前缀全部作废。

### 会被静默破坏的功能

| 功能 | 破坏方式 |
|------|---------|
| Prompt caching | 剥掉 `cache_control` 块或 `anthropic-beta: prompt-caching` 头 |
| Extended thinking | 剥掉请求体里的 `thinking` 块 |
| 工具调用 | 不按 `index` 分组累积 `input_json_delta` —— 模型的散文会被混进工具参数 |
| 计费统计 | 丢掉 `message_start.usage.input_tokens` / `message_delta.usage.output_tokens` |

### 现成方案

| 方案 | 形态 | 备注 |
|------|------|------|
| **LiteLLM Proxy** | 自建 | Anthropic 官方文档推荐的统一网关；`drop_params: true` 丢弃目标模型不支持的参数 |
| **claude-code-router** | 自建 | 多 provider 路由，社区流行 |
| OpenRouter | 第三方托管 | 直接支持 Anthropic Messages |
| Vercel AI Gateway | 第三方托管 | `https://ai-gateway.vercel.sh` |
| DeepInfra | 第三方托管 | `https://api.deepinfra.com/anthropic` |
| **厂商原生端点** | 官方 | DeepSeek / Kimi / GLM 已自带 `/anthropic` —— **不需要中转** |

### 两个安全问题

**1. LiteLLM 1.82.7 / 1.82.8 两个版本曾被植入窃取凭证的恶意代码** —— Anthropic 官方网关文档专门标注。部署时避开这两个版本。

**2. 第三方托管网关会看到你的完整代码库。** Claude Code 每次请求都带上下文里的源码、文件路径，可能还有 `.env`。安全敏感场景只能用**内网自建**网关。

### 三个真实代价

- **Prompt caching 大概率失效** → Claude Code 重度依赖缓存，成本可能涨数倍
- **多一跳延迟**，长上下文请求易触发网关超时（`API_TIMEOUT_MS` 要放大）
- **工具调用准确率下降** —— Claude Code 的 Edit / Bash / Read 等工具在弱模型上容易调错

**一句话判断标准**：厂商自己有原生 Anthropic 端点就直接用，别中转；必须中转就内网自建 LiteLLM，别走公网第三方。

## 六、选型建议

| 你的情况 | 建议 |
|----------|------|
| 只是想快速接上模型 | **OpenAI Chat Completions 兼容端点**，一套代码接 N 家 |
| 要接 Claude 原生能力（prompt caching 等） | 写 Anthropic Adapter |
| 要用 OpenAI 内置工具（web search 等） | 上 Responses API |
| 国内部署 / 成本敏感 | DeepSeek、通义、Kimi 的兼容端点 |
| 数据不出内网 | Ollama / vLLM 的兼容端点 |
| 自研低代码平台接 AI | **写 Adapter 层**，内部中立格式，别绑死一家 |

**给低代码平台的额外提示**：
用户可能会想自己选模型。有 Adapter 层之后，这只是一个配置项；
没有 Adapter 层，这就是一次重构。

---

## 附：关键时间线

| 日期 | 事件 |
|------|------|
| 2024-11 | Anthropic 发布 MCP |
| 2025-03 | OpenAI 发布 Responses API 与 Agents SDK |
| 2025-04 | Google 发起 A2A |
| 2025-10 | LangChain 1.0 GA，`create_agent` 成为图运行的 facade |
| 2025-12 | MCP 捐赠给 Linux 基金会 AAIF |
| 2026-03-12 | A2A 1.0 GA |
| 2026-07-28 | MCP 第 5 版规范（无状态核心、强制 OAuth 2.1） |
| **2026-08-26** | **OpenAI Assistants API 停用** |
| 2026-08-20 | A2A 转入 AAIF |
| 2026-10-31 | OpenAI Evals 平台转只读 |
| 2026-11-30 | OpenAI Agent Builder、Evals 平台、可复用 prompt 对象全部下线 |
