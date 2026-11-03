# MCP 入门指南：原理、协议与从零搭建

面向第一次接触 MCP 的读者。读完这篇你能明白：

1. MCP 是什么、解决什么问题
2. 它的协议长什么样、和普通 HTTP 接口差在哪
3. 如何用 20 行代码跑通一个最小 MCP 服务

运行/部署/排障请看 [OPERATIONS.md](OPERATIONS.md)；本文只讲"是什么"和"怎么搭"。

---

## 1. MCP 是什么

**MCP（Model Context Protocol，模型上下文协议）** 是 2024 年 Anthropic 发布的开放标准，目前 Claude Code、Cursor、Trae、WorkBuddy 等主流 AI 客户端均已支持。

一句话定义：**MCP 是 LLM 应用与外部系统之间的标准接口协议**。它不解决"怎么查数据"，只解决**"模型怎么知道一个系统有哪些能力、以及怎么调用"**。

### 它解决的真实问题：集成数量

没有 MCP 时，M 个 AI 客户端要接 N 个业务系统，每个组合都要单独开发适配：

```
没有 MCP                          有 MCP
Claude Code ──┐                   Claude Code ──┐
Trae ─────────┼─× N 个系统         Trae ─────────┤ MCP 标准接口 ──┤ 系统A (Server)
WorkBuddy ────┘   (M×N 套适配)     WorkBuddy ────┘  (M+N 套适配)  ┤ 系统B (Server)
                                                                  └ 系统C (Server)
```

每个客户端实现一次"说 MCP"，每个系统实现一次"听 MCP"，两边即插即用。

### 与传统 REST 的关系（重要，容易误解）

MCP 在 streamable-http 模式下**就是跑在普通 HTTP 上的**，并不是替代 REST。它和 REST 是分层关系：

```
模型 ──MCP/JSON-RPC──> MCP Server ──HTTP/REST──> 业务后端 ──> 数据库
                       （协议适配层）              （现有系统，一行不改）
```


---

## 2. 三个角色与三类原语

### 2.1 角色

| 角色 | 谁来扮演 | 职责 |
|---|---|---|
| **Host** | Claude Code / Trae / 你的 IDE | 承载 LLM，管理连接与对话 |
| **Client** | Host 内部的组件 | 与一个 Server 保持 1:1 会话，转发消息 |
| **Server** | 任意系统 | 向外暴露工具，执行具体操作 |

### 2.2 Server 可以暴露的三类原语

| 原语 | 谁决定调用 | 语义 | 典型场景 |
|---|---|---|---|
| **Tools** | **模型自己决定** | 可执行的动作，可有副作用 | 查事件、写研判 |
| Resources | 客户端或用户挂载 | 只读数据，URI 寻址 | `event://12345` |
| Prompts | 用户显式选择 | 预置指令模板 | 斜杠命令 |

**只做 Tools 就够覆盖绝大多数场景**。Resources 和 Prompts 是为"把文档/数据预挂进上下文"设计的，按需查询的场景硬套只会增加复杂度。

---

## 3. 协议：JSON-RPC 2.0

MCP 不是新的网络协议，而是**一套基于 JSON-RPC 2.0 的消息约定**，可以跑在两种传输上：

| 传输 | 形态 | 适用 |
|---|---|---|
| **stdio** | 客户端拉起子进程，经 stdin/stdout 通信 | 单人本地使用 |
| **Streamable HTTP** | 独立服务进程常驻，每条消息一个 HTTP POST | 团队共享、服务器部署 |

### 3.1 与传统 REST 的关键差异：操作标识放在哪里

```
传统 REST：路径即操作                MCP：路径相同，method 不同
┌────────────────────────┐        ┌────────────────────────┐
│ POST /events/_filter   │        │ POST /mcp              │
│ GET  /events/detail/1  │        │  method: initialize    │
│ POST /events/judge     │        │  method: tools/list    │
│                        │        │  method: tools/call    │
└────────────────────────┘        └────────────────────────┘
```

MCP 只有一个端点（`/mcp`），"要做什么"放在请求体的 `method` 字段里。这不是风格偏好，而是被三件事逼出来的：

| 原因 | 说明 |
|---|---|
| 工具清单是运行时才确定的 | 服务进程启动时注册了什么，`tools/list` 就返回什么；若用路径表达，每加一个工具都要改路由，且客户端无法预知路径 |
| 客户端启动时一无所知 | REST 靠 Swagger 文档给"人"看；模型不会读文档，必须靠 `initialize` + `tools/list` 在运行时自描述 |
| 传输层必须可替换 | 同一份工具代码要能同时跑 stdio（没有 HTTP）和 HTTP，协议层必须与传输解耦 |

### 3.2 一次会话的生命周期

```
客户端                                服务端
   │ ① initialize（协商版本/能力）────────>│
   │ <─────── 返回协议版本 + 能力列表 ──────│
   │ ② notifications/initialized ────────>│   （无 id 的通知）
   │ ③ tools/list（拉取工具清单）──────────>│
   │ <── name + description + 参数Schema ──│
   │ ④ tools/call（模型决定调用哪个工具）───>│
   │ <──────────── 工具执行结果 ────────────│
```

模型的决策依据只有 `tools/list` 返回的 **name + description + JSON Schema**——它看不到你的代码。这就是为什么工具的描述文案如此重要（见第 6 节）。

### 3.3 用 curl 亲眼看看协议

服务启动后，可以直接观察原始报文：

```bash
# ① 握手
curl -s -X POST http://127.0.0.1:8765/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{
        "protocolVersion":"2025-06-18",
        "capabilities":{},
        "clientInfo":{"name":"curl-demo","version":"0.0.1"}}}'
```

响应体（streamable-http 可能以 SSE 帧返回，`data:` 后面才是 JSON）：

```json
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18",
 "capabilities":{"tools":{}},"serverInfo":{"name":"demo","version":"1.0.0"}}}
```

若服务端启用了会话，响应头会带 `Mcp-Session-Id: xxx`，后续请求需原样带回该头。

```bash
# ② 拉取工具清单
curl -s -X POST http://127.0.0.1:8765/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: <上一步返回的会话ID>" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

# ③ 调用工具
curl -s -X POST http://127.0.0.1:8765/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: <同上>" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{
        "name":"add","arguments":{"a":1,"b":2}}}'
```

> **最常见的首连失败**：`Accept` 头必须同时包含 `application/json` 和 `text/event-stream`，少一个就是 406。

---

## 4. 最小 Demo：20 行一个 MCP Server

下面是一个不依赖任何业务的完整 MCP 服务，任何装了 Python 3.10+ 的机器都能跑。

```python
# mini_server.py
from mcp.server import MCPServer

mcp = MCPServer("demo")

@mcp.tool()
def add(a: int, b: int) -> int:
    """计算两个整数的和。需要做加法时使用。"""
    return a + b

@mcp.tool()
def greet(name: str) -> str:
    """向指定的人打招呼。name 为对方的名字。"""
    return f"你好，{name}！"

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8765)
```

搭建并启动：

```bash
# 1. 环境（要求 Python ≥ 3.10）
python -m venv .venv
.venv\Scripts\activate            # Windows；Linux/macOS: source .venv/bin/activate

# 2. 安装官方 SDK
pip install "mcp[cli]>=2.0,<3"

# 3. 启动
python mini_server.py
```

三个关键点：

| 代码 | 说明 |
|---|---|
| `@mcp.tool()` | 装饰器把函数签名自动转成 JSON Schema（参数名、类型、默认值），**必须带括号**，裸写 `@mcp.tool` 会报 TypeError |
| docstring | 会原样进入 `tools/list` 的 description——**模型只看这段文字决定何时调用**，所以"什么时候用/什么时候不要用"都写在里面 |
| 类型注解 | `a: int` 不是摆设，SDK 据此生成参数 Schema 并做校验；写错的参数模型会收到可自纠的错误信息 |

换个传输只需改一行：`mcp.run()`（默认 stdio，供 Claude Desktop 这类本地客户端拉起）。协议层代码完全不变——这就是第 3.1 节说的"传输可替换"。

---

## 5. 把 REST 变成"好用"的 MCP：六个关键设计

第 4 节的 demo 跑通很容易，但**决定 MCP 服务好不好用的是契约层设计**。协议层 SDK 已包办，真正投入精力的是下面六件事 `client.py` / `server.py` 就是按这六条实现的，可对照源码：

| # | 能力 | 做法 | 对应实现 |
|---|---|---|---|
| 1 | **按任务聚合，不镜像 API** | 40 个 REST 接口 → 5 个工具；工具超过 10 个，模型选择准确率明显下降 | `server.py` 的 5 个 `@mcp.tool()` |
| 2 | **描述即接口** | 模型只看 name + description + Schema。"不要用于……"比"可以用于……"更重要 | `search_events` 的 docstring 写明"务必传时间窗，上游缺省会扫 365 天" |
| 3 | **参数防呆** | 模型生成的参数一律不可信，越界时截断并告知，而不是抛异常 | `client.py` 的 `clamp_window()`（时间窗≤3天）、`limit≤100`、`_meta` 里返回 `*_truncated` 标记 |
| 4 | **返回裁剪** | 上下文是最贵的资源；单次响应过大把模型的上下文吃光 | 分页强制生效；并发闸门 `Semaphore(2)` 防止一次对话并发大量查询 |
| 5 | **错误可自纠** | 错误信息是给**模型**看的：要写"该怎么改"，模型下一轮就能自己修正 | 超时报错"请收窄时间窗或减少 limit 后重试"；所有异常转成 `{"ok": false, "error": "..."}` 结构化返回，不向协议层抛 |
| 6 | **安全边界** | 读写下限、鉴权、写操作默认关闭 | `judge_event` 需 `ALLOW_WRITE=1`；Cookie 不进日志/health/异常信息 |

第 5 条最容易被忽略，也是 MCP 与传统 API 体验差异最大的地方：传统接口报 `400` 后程序员去查日志；MCP 里模型看到 `{"code":400}` 只会原样重试。把错误写成"时间窗超过 3 天上限，请把 start_time 收窄"，模型下一轮就能自己改对。

---

## 6. 常见坑速查

| 现象 | 原因 | 解法 |
|---|---|---|
| 客户端连接返回 406 | `Accept` 头缺少 `text/event-stream` | 两个类型同时带上（见 3.3 节 curl 示例） |
| `ModuleNotFoundError: ... fastmcp` | 网上教程多为 SDK v1 写法 `from mcp.server.fastmcp import FastMCP` | SDK 2.x（2026-07 起）改名：`from mcp.server import MCPServer`；注意独立 `fastmcp` 包是另一个项目，不要混装 |
| `@mcp.tool` 报 TypeError | 官方 SDK 的装饰器必须带括号 | 写成 `@mcp.tool()` |
| pip 装 mcp 失败 | SDK 要求 Python ≥ 3.10 | 用 3.11/3.12 独立环境，不要动系统 Python |
| 接口不报错但返回空列表 | 上游开启了数据域授权，`sessionid` 失效/未配置时**静默**返回 0 条 | 先调 `health` 工具排查；`session_cookie_set=true` 只代表已配置，不代表仍有效 |
| 容器健康检查永远 unhealthy | 拿 `/mcp` 做 HTTP healthcheck，但没带正确的 `Accept` 头 | 改用 TCP socket 探测 |
| 写操作报"未启用" | `judge_event` 默认关闭 | 设 `ALLOW_WRITE=1` 并重启（确认业务影响后再开） |

---

## 7. 延伸阅读

- MCP 官方规范：<https://modelcontextprotocol.io/specification>（含 lifecycle、transports、tools 各章节）
- 官方 Python SDK：<https://github.com/modelcontextprotocol/python-sdk>
