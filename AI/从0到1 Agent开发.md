# 从零手写一个 Agent · 完整 demo

不带任何 Agent 框架，把「循环」这件事从头写一遍。

这三样东西拆开看透之后，你再用 LangGraph / CrewAI 之类的框架，
就只是在看它们的实现细节，而不是在学新概念：

```
    模型（做决策）  ──►  循环（Agent 的本体）  ──►  工具（真正动手）
```

## 目录

| 文件 | 说明 |
|------|------|
| `agent.py` | **完整 demo**，可直接运行。本文件「完整代码」一节就是它 |
| `sync_readme.py` | 把 `agent.py` 同步进本文件，避免两份代码不一致 |
| `PROTOCOLS.md` | 协议速查：四套模型层格式的字段级对比、OpenAI 兼容的真相、MCP / A2A |

> 早期的 `step1_mock.py` / `step2_real.py` / `step3_real_tool.py` 三个分步文件，
> 内容已全部合并进 `agent.py`，不再单独维护。

---

## 跑起来

### 模式一：假模型 + 真工具（零成本，不需要 API key）

```bash
python agent.py --mock
```

模型是假的（按脚本吐决策），但**工具是真的** —— 它会真的发 HTTP 请求查实时天气。
所以你不花一分钱就能看清：循环什么时候继续、什么时候退出、上下文每一轮长几条。

### 模式二：真模型 + 真工具

```bash
export LLM_API_KEY=sk-xxxx
export LLM_BASE_URL=https://api.deepseek.com/v1
export LLM_MODEL=deepseek-chat
python agent.py
```

走的是 OpenAI 兼容协议，换厂商只改这两个环境变量：

| 厂商 | `LLM_BASE_URL` | `LLM_MODEL` |
|------|----------------|-------------|
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4` |

Windows PowerShell 用 `$env:LLM_API_KEY="sk-xxxx"` 这种写法。

其它参数：`--goal` 换任务，`--max-turns` 调轮数上限。

---

## 实测输出

`python agent.py --mock` 的真实输出（已跑通）：

```
================================================================
模式：假模型 + 真工具（零成本）
任务：北京和南京现在各多少度？另外算一下 5.3 乘以 3。
================================================================

────────────────────────────────────────────────────────────────
第 1 轮   上下文 1 条消息
────────────────────────────────────────────────────────────────
  [模型]     我要调 get_weather({"city": "北京"})
      [我的代码] → 真的在发 HTTP 请求：api.open-meteo.com（北京）
      [外部世界] ← 真实返回：19.7°C，阴
  [工具结果] 北京：19.7°C，阴

────────────────────────────────────────────────────────────────
第 2 轮   上下文 3 条消息
────────────────────────────────────────────────────────────────
  [模型]     我要调 get_weather({"city": "南京"})
      [我的代码] → 真的在发 HTTP 请求：api.open-meteo.com（南京）
      [外部世界] ← 真实返回：23.0°C，阴
  [工具结果] 南京：23.0°C，阴

────────────────────────────────────────────────────────────────
第 3 轮   上下文 5 条消息
────────────────────────────────────────────────────────────────
  [模型]     我要调 multiply({"a": 5.3, "b": 3})
  [工具结果] 15.9

────────────────────────────────────────────────────────────────
第 4 轮   上下文 7 条消息
────────────────────────────────────────────────────────────────
  [模型]     任务完成，不再请求调用工具 → 退出循环

================================================================
最终回答：（假模型不会真的总结 —— 换成真模型后，这一步由它自己生成。）
================================================================
```

**请盯着那个 `1 → 3 → 5 → 7` 看。**

每一轮多两条：一条是"模型说我要调工具"，一条是"工具的执行结果"。
这组数字说明了一件事：**Agent 本身没有记忆，"它记得什么"完全等于"你往 `messages` 里塞了什么"。**
框架帮你做的所有花哨事情，最终都落在这一条上。

---

## 完整代码

> ⚠️ **本节由 `sync_readme.py` 自动生成，请勿手工编辑。**
> 改代码请改 `agent.py`，然后运行 `python sync_readme.py` 同步过来。

<!-- BEGIN:agent.py -->

```python
"""
从零手写一个 Agent —— 完整可运行 demo
======================================

这个文件把三个教学阶段合并成了一个完整程序。读的时候按三块看：

    ┌──────────────────────────────────────────────────────────┐
    │ 第 1 块  工具层    Tools                                 │
    │   普通 Python 函数 + 一份给模型看的 JSON Schema           │
    │   职责：真正对世界做事（发 HTTP、查库、写文件）            │
    ├──────────────────────────────────────────────────────────┤
    │ 第 2 块  模型层    LLM                                   │
    │   职责：决策。发请求，拿回"我要调哪个工具、参数是什么"      │
    │   它不执行任何工具，也碰不到外部世界                       │
    ├──────────────────────────────────────────────────────────┤
    │ 第 3 块  循环层    run_agent()                           │
    │   职责：把前两块串起来。问模型 → 执行工具 → 结果塞回去     │
    │   这是 Agent 的本体，框架做的事本质都在这几十行里          │
    └──────────────────────────────────────────────────────────┘

两种跑法：

    # 1. 零成本：假模型 + 真工具（不需要 API key，但工具真的发 HTTP 请求）
    python agent.py --mock

    # 2. 完整：真模型 + 真工具
    export LLM_API_KEY=sk-xxxx
    export LLM_BASE_URL=https://api.deepseek.com/v1
    export LLM_MODEL=deepseek-chat
    python agent.py

    换任何 OpenAI 兼容厂商（通义 / Kimi / 智谱 / OpenAI / 本地 vLLM）都只改这两个变量。

────────────────────────────────────────────────────────────────
【维护提示】想加一个新工具？只需要动「第 1 块」的三处：

    ① 写一个普通的 Python 函数
    ② 在 TOOLS 注册表里加一条：名字 → 函数
    ③ 在 TOOL_SCHEMAS 里加一条 JSON Schema（这是给模型看的说明书）

循环层一行都不用改。这是这个 demo 里最重要的一个设计：
**工具层和循环层是解耦的** —— 循环只认识"名字"和"参数"，不关心你用什么技术实现。
════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from typing import Any, Callable

# Windows 控制台默认不是 UTF-8，这里兜一下底，避免中文输出乱码。
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:
    pass


# ══════════════════════════════════════════════════════════════════
# 第 1 块  工具层 —— Agent 能对世界做的事，全部在这里
#
# 注意：下面这些函数没有任何"智能"，就是普通的工程代码。
#       它们的正确性靠单元测试保证，不靠模型。
#       模型既不读你的 Python 代码，也不对你函数的 bug 负责。
# ══════════════════════════════════════════════════════════════════

# 城市 → 经纬度。这是纯工程数据，跟"智能"没有半点关系。
CITY_COORDS: dict[str, tuple[float, float]] = {
    "北京": (39.9042, 116.4074),
    "上海": (31.2304, 121.4737),
    "南京": (32.0603, 118.7969),
    "深圳": (22.5431, 114.0579),
}

# WMO 天气代码 → 中文描述（Open-Meteo 用的是这套编码，详见其文档）
WMO_CODES: dict[int, str] = {
    0: "晴", 1: "基本晴", 2: "局部多云", 3: "阴",
    45: "雾", 48: "雾凇", 51: "小毛毛雨", 53: "毛毛雨", 55: "浓毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 71: "小雪", 73: "中雪", 75: "大雪",
    80: "小阵雨", 81: "阵雨", 82: "强阵雨", 95: "雷暴",
}


def get_weather(city: str) -> str:
    """
    查询某个城市的实时天气。

    ★ 这是整个 demo 里唯一能碰到外部世界的函数之一。
      模型做不到这件事 —— 它只会输出一段文本说"我要调 get_weather"。
      真正发出 HTTP 请求的是下面这几行普通代码。
    """
    if city not in CITY_COORDS:
        return f"暂不支持的城市：{city}（可选：{'、'.join(CITY_COORDS)}）"

    lat, lon = CITY_COORDS[city]
    query = urllib.parse.urlencode(
        {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,weather_code",
            "timezone": "Asia/Shanghai",
        }
    )
    url = f"https://api.open-meteo.com/v1/forecast?{query}"

    # 这两行 print 只是为了把「执行边界」变得肉眼可见。
    # 真实项目里工具函数应该保持纯净、可测试，不要在里面 print。
    print(f"      [我的代码] → 真的在发 HTTP 请求：api.open-meteo.com（{city}）")

    req = urllib.request.Request(url, headers={"User-Agent": "agent-loop-lab"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    current = data["current"]
    temp = current["temperature_2m"]
    code = current["weather_code"]
    desc = WMO_CODES.get(code, f"未知天气码 {code}")

    print(f"      [外部世界] ← 真实返回：{temp}°C，{desc}")
    return f"{city}：{temp}°C，{desc}"


def multiply(a: float, b: float) -> str:
    """
    两数相乘。

    演示「纯计算类工具」—— 不需要任何外部依赖。
    它的存在是为了说明：工具可以是任何东西，只要输入输出都是可序列化的。
    """
    result = a * b
    # 直接 str() 会吐出 73.80000000000001 这种浮点尾巴。
    # 这里用 .10g 规整一下 —— 顺带说明一个道理：
    # **工具函数返回内容的质量由你负责，模型不会帮你擦屁股。**
    return f"{result:.10g}"


# ── 工具注册表：名字 → 可调用对象 ─────────────────────────────────
# 循环层靠这张表，把模型说的"名字"变成一次真正的函数调用。
TOOLS: dict[str, Callable[..., str]] = {
    "get_weather": get_weather,
    "multiply": multiply,
}


# ── 工具说明书：这段 JSON 会被发给模型 ────────────────────────────
#
# ★ 模型只读这段 JSON Schema，它看不见上面那些 Python 函数。
#   所以：description 写得含糊 → 模型选错工具；参数描述不清 → 模型编参数。
#   这是调 Agent 最高频的故障来源。
#
#   写这段的心法：当成"写给一个从没见过你代码的同事看"。
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询某个城市当前的实时天气，返回温度和天气现象。",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名称，例如：北京、上海、南京",
                    }
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "multiply",
            "description": "把两个数字相乘，用于任何需要精确计算的场景。",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "number", "description": "被乘数"},
                    "b": {"type": "number", "description": "乘数"},
                },
                "required": ["a", "b"],
            },
        },
    },
]


# ══════════════════════════════════════════════════════════════════
# 第 2 块  模型层 —— 只负责"决策"，不负责"执行"
#
# 下面有两个实现，接口完全一样（都提供 chat(messages)），
# 所以循环层可以无差别地使用它们 —— 这就是「换模型不用改循环」的原因。
#
# 记住这个分工：
#     模型   = 只会输出文本的一方（输出"我要调 get_weather"这句话）
#     循环   = 唯一能真正动手的一方
# ══════════════════════════════════════════════════════════════════


class RealLLM:
    """
    真模型：走 OpenAI 兼容协议。

    所谓"OpenAI 兼容"，指的是 URL 路径、鉴权头、请求体和响应体的形状
    都照着 OpenAI 的 /v1/chat/completions 来。所以换厂商通常只要改
    base_url 和 model 两个值，代码一行不用动。
    """

    def __init__(self) -> None:
        # 延迟导入：只在真的要用真模型时才 import，
        # 这样 --mock 模式下你连 openai 这个包都不用装。
        from openai import OpenAI

        api_key = os.environ.get("LLM_API_KEY")
        if not api_key:
            raise SystemExit(
                "缺少环境变量 LLM_API_KEY。\n"
                "  想先不花钱跑通循环？加 --mock 参数用假模型：\n"
                "      python agent.py --mock"
            )

        self.client = OpenAI(
            api_key=api_key,
            base_url=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1"),
        )
        self.model = os.environ.get("LLM_MODEL", "deepseek-chat")

    def chat(self, messages: list[dict[str, Any]]) -> Any:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=TOOL_SCHEMAS,
            # Agent 场景要的是"可靠"而不是"有创意"：
            # 工具参数是一段 JSON，任何字符发散都会让 json.loads() 炸掉。
            # 而且你需要一个能复现的 bug，才有机会修它。
            temperature=0,
        )
        return resp.choices[0].message


class MockLLM:
    """
    假模型：按预设脚本依次吐出决策，不花钱、不需要 API key。

    它的价值在于把「循环」和「模型」解耦开看 —— 你不需要任何 API key
    就能完整跑通 Agent 的控制流，还能反复重放同一个场景。

    ★ 关键设计：它返回的 dict 形状，和真模型 SDK 的 model_dump() 完全一致
      （role / content / tool_calls[].id / tool_calls[].function.name+arguments）。
      所以循环层不需要为假模型写任何第二套分支 —— 这就是"协议"的意义：
      **双方约定好形状，谁实现的不重要。**
    """

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self.script = script
        self.cursor = 0

    def chat(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        # messages 在这里其实没被用到 —— 这恰好说明：
        # 循环骨架不关心模型怎么决策，它只负责"把上下文递过去、把决定拿回来"。
        step = (
            self.script[self.cursor]
            if self.cursor < len(self.script)
            else {"type": "final", "content": "（脚本用完，自动收尾）"}
        )
        self.cursor += 1

        if step["type"] == "final":
            return {"role": "assistant", "content": step["content"]}

        return {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": f"call_mock_{self.cursor}",
                    "type": "function",
                    "function": {
                        "name": step["name"],
                        # 注意：参数是 JSON **字符串**，不是对象。
                        # 真模型吐出来的也是这样一段字符串，所以循环层必须
                        # 显式做 json.loads()，并且要能兜住解析失败。
                        "arguments": json.dumps(step["args"], ensure_ascii=False),
                    },
                }
            ],
        }


def _as_dict(msg: Any) -> dict[str, Any]:
    """
    把模型返回统一成普通 dict。

    真 SDK 返回的是 pydantic 对象（有 model_dump），假模型返回的已经是 dict。
    生产项目里你多半也需要这样一个薄薄的兼容层 —— 因为不同厂商 SDK
    返回的对象形状并不完全一致（有的 content 是 None，有的压根没这个字段）。

    这个函数就是后面要学的「Adapter 层」最小的雏形。
    """
    if isinstance(msg, dict):
        return msg
    # exclude_none：content 为 None 时剔除该字段，
    # 否则部分厂商的 API 会因为收到 null 而报错。
    return msg.model_dump(exclude_none=True)


# ══════════════════════════════════════════════════════════════════
# 第 3 块  循环层 —— Agent 的本体
#
# 框架（LangGraph / CrewAI / Agents SDK）替你做的事，本质都在下面这几十行里。
# 把这几十行看透，你再看任何框架都是在看它的实现细节，而不是在学新概念。
#
# 一个 Agent 循环的五个必需件（去掉任何一个都会出问题）：
#
#   ① 上下文容器  messages —— Agent 没有记忆，"它记得什么"完全等于
#                              "你往这里塞了什么"
#   ② 终止条件一  模型不再请求工具 → 任务完成
#   ③ 终止条件二  max_turns 兜底 —— 少了这条，一个死循环的 Agent
#                 能一直烧钱到你手动掐断
#   ④ 工具注册表  名字 → 函数。工具不存在、抛异常都必须兜住
#   ⑤ 结果回灌    工具结果作为新消息放回 messages，否则模型看不见结果，
#                 就会反复调用同一个工具
# ══════════════════════════════════════════════════════════════════


def run_agent(
    goal: str,
    llm: Any,
    max_turns: int = 10,
    verbose: bool = True,
) -> str:
    """
    跑一个 Agent。goal 是任务描述，llm 是模型实现，返回最终答案。

    这个函数就是整个 Agent 的全部骨架。
    """
    # ── ① 上下文容器 ──────────────────────────────────────────
    # 整个 Agent 的"记忆"就是这一个 list。没有任何魔法。
    messages: list[dict[str, Any]] = [{"role": "user", "content": goal}]

    for turn in range(1, max_turns + 1):
        if verbose:
            print(f"\n{'─' * 64}")
            print(f"第 {turn} 轮   上下文 {len(messages)} 条消息")
            print("─" * 64)

        # ── 问模型：下一步做什么？ ────────────────────────────
        reply = _as_dict(llm.chat(messages))

        # 把模型的输出也追加回上下文。
        # 别漏了这一句：模型需要"记得"自己刚才要求调用过什么工具，
        # 否则下一轮可能重复请求同一个调用。
        messages.append(reply)

        # ── ② 终止条件一：模型不再请求工具 → 它给出了最终答案 ──
        tool_calls = reply.get("tool_calls")
        if not tool_calls:
            if verbose:
                print("  [模型]     任务完成，不再请求调用工具 → 退出循环")
            return reply.get("content") or "（模型返回了空内容）"

        # ── ④⑤ 执行工具，并把结果回灌 ─────────────────────────
        for call in tool_calls:
            name = call["function"]["name"]
            raw_args = call["function"]["arguments"]
            call_id = call["id"]

            # 模型偶尔会吐出非法 JSON（尤其 temperature 调高时）。
            # 这里必须兜住，并且要让它知道"你给的不是合法 JSON"。
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = None
                result = "参数解析失败：不是合法 JSON，请重新调用并检查参数格式"
            else:
                if verbose:
                    print(f"  [模型]     我要调 {name}({json.dumps(args, ensure_ascii=False)})")

                # ★ 这是整个 Agent 里唯一真正"动手"的一行。
                #   在此之前的一切（JSON Schema、模型输出的那段文本）
                #   都只是描述，没有任何东西被执行。
                if name not in TOOLS:
                    result = f"错误：不存在名为 {name} 的工具"
                else:
                    try:
                        result = str(TOOLS[name](**args))
                    except Exception as exc:
                        # 工具报错不要往上抛，要回灌给模型。
                        # 模型拿到这个错误信息，往往能自己纠正参数重试一次；
                        # 直接 raise 出去，整个循环就断了。
                        # 这是 Agent 和传统程序在错误处理上最大的观念差异。
                        result = f"工具执行失败：{type(exc).__name__}: {exc}"

            if verbose:
                print(f"  [工具结果] {result}")

            # tool_call_id 必须带上：模型一轮可能并发请求多个工具，
            # 靠这个 id 才能把"哪个结果对应哪个请求"对上号。
            messages.append(
                {"role": "tool", "tool_call_id": call_id, "content": result}
            )

    # ── ③ 终止条件二：轮数兜底 ────────────────────────────────
    # 生产环境里这一条绝对不能省。
    return f"达到最大轮数 {max_turns}，强制停止"


# ══════════════════════════════════════════════════════════════════
# 第 4 块  入口
# ══════════════════════════════════════════════════════════════════

# 假模型的脚本。
#
# ★ 注意它**不会读上下文** —— 所有参数都是写死的道具值。
#   真实场景里这些参数是模型读完 messages 之后自己决定的（那是第 2 块的事）。
#   这里写死，是为了让你不花一分钱就能把控制流看透：什么时候调工具、
#   什么时候退出、上下文每一轮长了几条。
MOCK_SCRIPT: list[dict[str, Any]] = [
    {"type": "tool", "name": "get_weather", "args": {"city": "北京"}},
    {"type": "tool", "name": "get_weather", "args": {"city": "南京"}},
    {"type": "tool", "name": "multiply", "args": {"a": 5.3, "b": 3}},
    {
        "type": "final",
        "content": "（假模型不会真的总结 —— 换成真模型后，这一步由它自己生成。）",
    },
]

DEFAULT_GOAL = "北京和南京现在各多少度？另外算一下 5.3 乘以 3。"


def main() -> None:
    parser = argparse.ArgumentParser(description="从零手写的 Agent demo")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="用假模型跑（不需要 API key；但工具仍然真的发 HTTP 请求）",
    )
    parser.add_argument("--goal", default=DEFAULT_GOAL, help="交给 Agent 的任务")
    parser.add_argument(
        "--max-turns", type=int, default=10, help="最大轮数（死循环兜底，默认 10）"
    )
    args = parser.parse_args()

    llm = MockLLM(MOCK_SCRIPT) if args.mock else RealLLM()

    mode = "假模型 + 真工具（零成本）" if args.mock else "真模型 + 真工具"
    print("=" * 64)
    print(f"模式：{mode}")
    print(f"任务：{args.goal}")
    print("=" * 64)

    answer = run_agent(args.goal, llm, max_turns=args.max_turns)

    print("\n" + "=" * 64)
    print(f"最终回答：{answer}")
    print("=" * 64)


if __name__ == "__main__":
    main()
```

<!-- END:agent.py -->

---

## 代码导读

### 三块职责

代码分成三块，边界非常清楚。理解这三块的分工，比记住任何 API 都重要：

| 块 | 是什么 | 不能做什么 |
|---|--------|-----------|
| **第 1 块 工具层** | 普通 Python 函数 + 一份给模型看的 JSON Schema | 没有任何"智能"。正确性靠单元测试保证，**模型不对你函数的 bug 负责** |
| **第 2 块 模型层** | 发请求，拿回"下一步调哪个工具、参数是什么" | **碰不到外部世界**，不执行任何工具，不读你的代码 |
| **第 3 块 循环层** | 把前两块串起来 —— 这是 Agent 的本体 | 不关心模型怎么决策，也不关心工具怎么实现 |

**关键设计：三块之间是解耦的。**

- 第 3 块只认识工具**名字**和**参数**，不关心你用什么技术实现（HTTP、SQL、读文件都一样）
- 第 3 块对模型的要求只有"提供 `chat(messages)`"，所以 `RealLLM` 和 `MockLLM` 可以互换 —— 这就是「换模型不用改循环」的原因

### 五个必需件

写完就会发现，去掉任何一个都会出问题：

| # | 必需件 | 去掉会怎样 |
|---|--------|-----------|
| ① | **上下文容器**（`messages`） | Agent 没有记忆，"它记得什么"完全等于"你塞了什么" |
| ② | **终止条件一**：模型不再请求工具 | 循环永远不退出 |
| ③ | **终止条件二**：`max_turns` 兜底 | 一个死循环的 Agent 能一直烧钱到你手动掐断 |
| ④ | **工具注册表**（`TOOLS`） | 模型说要调 `get_weather`，你没有函数可以执行 |
| ⑤ | **结果回灌** | 模型看不见结果，会反复调同一个工具 |

第 ⑤ 条在真模型版本里还要带上 `tool_call_id`，因为模型一轮可能并发请求多个工具。

### 「谁真正去查的？」—— 最容易搞混的一件事

JSON Schema 只是一张**菜单**，模型只能**点菜**，永远不能**下厨**。

| 角色 | 能做 | 不能做 |
|------|------|--------|
| 模型 LLM | 输出一段文本，说「我要调 get_weather」 | 碰网络、读文件、查数据库、执行任何代码 |
| **你的 Python 代码** | **唯一能真正动手的一方** | —— |
| 外部系统 | 响应你的代码发出的 HTTP / SQL | 它不认识什么 JSON Schema |

那个 `{"name": "get_weather", "args": {"city": "北京"}}` **不是一次函数调用**，
它只是模型吐出来的一段字符串。真正让它变成行动的是循环里这一行：

```python
result = str(TOOLS[name](**args))      # <<< 整个 Agent 里唯一真正"动手"的一行
```

三个直接推论，都很实用：

1. **工具函数有没有 bug，模型不负责。** 它是普通工程代码，要靠单元测试保证，不能指望模型"聪明到绕过你的 bug"。
2. **模型只能调你给它的工具。** 你不在 `TOOLS` 里放 `delete_file`，它就没有删文件的能力 —— 这是权限管控的第一道门。
3. **模型给的参数永远不能直接信。** `city` 传进来是 `"北京"` 还是 `"'; DROP TABLE"`，你得自己校验。
   **在 `TOOLS[name](**args)` 这一行之前，参数必须过一遍校验层。**

顺带说一个后面会反复遇到的概念：既然工具函数是你自己写的，那**别人写好的工具能不能直接接进来用？**
能 —— 那就是 **MCP（Model Context Protocol）**。它本质上是"把工具函数按统一格式暴露出去"的标准协议，
让 GitHub、数据库、浏览器这些现成工具不用你重新写一遍。

### 怎么加一个新工具

这是这个 demo 最重要的维护动作。**只需要动第 1 块的三处，循环层一行都不用改：**

```python
# ① 写一个普通的 Python 函数
def get_stock_price(code: str) -> str:
    ...

# ② 在工具注册表里加一条：名字 → 函数
TOOLS = {
    "get_weather": get_weather,
    "multiply": multiply,
    "get_stock_price": get_stock_price,     # ← 新增
}

# ③ 在工具说明书里加一条 JSON Schema（这是给模型看的，不是给你看的）
TOOL_SCHEMAS = [
    ...,
    {                                        # ← 新增
        "type": "function",
        "function": {
            "name": "get_stock_price",
            "description": "查询某只股票的当前价格。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码，例如：600519"}
                },
                "required": ["code"],
            },
        },
    },
]
```

改完直接跑，模型立刻就能用上这个新工具。

---

## 两个容易被忽略的细节

**工具描述是给模型看的，不是给人看的。**
`TOOL_SCHEMAS` 里那段 JSON 才是模型真正读到的东西 —— 它不读你的 Python 代码。
`description` 写得含糊，模型就会选错工具。这是调 Agent 最高频的问题来源。

**工具报错不要往上抛，要回灌给模型。**

```python
except Exception as exc:
    result = f"工具执行失败：{type(exc).__name__}: {exc}"
```

模型拿到这个错误，往往能自己纠正参数重试一次。直接 `raise` 出去，循环就断了。
这是 Agent 和传统程序在错误处理上最大的观念差异。

---

## 附：`temperature` 怎么设

模型每生成一个词，内部都会算出一组候选概率。`temperature` 就是**把这条概率曲线压陡或抹平**的旋钮
（公式上就是 softmax 之前把 logits 除以 T）：

- **→ 0**：几乎总选概率最高的词，输出趋于确定
- **= 1**：模型原始分布，不做任何改动
- **→ 1.8**：分布被抹平，低概率词也有机会被选中 —— 幻觉的温床

以「今天北京天气很___」为例，候选词「香蕉」被选中的概率：T=0.2 时 **0.001%**，T=1.8 时 **9.9%**。

| 取值 | 效果 | 场景 |
|------|------|------|
| **0** | 几乎必选最优 | 工具调用参数、结构化 JSON、代码、要复现的实验 |
| 0.3–0.7 | 稳定 + 少量变化 | 客服问答、技术文档 |
| 1.0 | 原始分布 | 通用对话 |
| 1.2–1.8 | 明显发散 | 创意写作、头脑风暴 |

**Agent 场景为什么用 0**：工具参数是一段 JSON，任何字符发散都会让 `json.loads()` 炸掉；
而且你需要一个能复现的 bug，才有机会修它。

三个坑：

1. **`temperature=0` 不等于 100% 确定。** 浮点累加顺序、批处理、MoE 路由都会引入微小差异，
   同一个 prompt 跑两次结果仍可能不同。要严格复现还得固定 seed 并记录完整请求参数 ——
   **做 Agent 评估时结果抖动，先查这里。**
2. **推理模型可能不支持。** DeepSeek-R1、OpenAI o 系列这类模型常直接忽略该参数，传了甚至报错，用前先看厂商文档。
3. **别和 `top_p` 同时调。** `top_p` 是从另一个角度做同一件事：砍掉长尾，只从累计概率达标的那批词里抽。
   两个一起调会互相干扰、难以归因。**约定：只调一个，另一个保持默认。**

想让 Agent 回复更自然，正确做法不是全局调高，而是**分开控制**：
工具调用那一步用 0，最后生成自然语言总结那一步再放宽。

---

## 维护这个文档

代码只有一份真相（single source of truth）在 `agent.py`，README 里的是副本：

```bash
# 改完 agent.py 后，同步到 README
python sync_readme.py
```

脚本会替换 `<!-- BEGIN:agent.py -->` 和 `<!-- END:agent.py -->` 之间的内容，其余部分不动。

---

## 接下来自己动手做这四件事

循环跑通只是起点。按顺序给自己加难度，每一条都是生产环境里的真实问题：

1. **上下文裁剪** —— 聊到第 30 轮时 `messages` 已经膨胀到几十万 token。
   设计一个策略：保留最近 N 轮 + 把更早的压缩成摘要。怎么压？压什么？丢什么？
2. **失败熔断** —— 如果模型连续 3 次调用同一个工具都失败，循环该怎么处理？
   现实里这是死循环烧钱的头号原因。
3. **危险动作确认** —— 工具是「删文件」「发邮件」「下单」时，
   必须插一个人工确认节点。想想这个节点应该卡在循环的哪一步。
4. **评估** —— 改了一版 system prompt，你怎么知道 agent 是变好了还是变坏了？
   试着准备 10 个带标准答案的任务，写个脚本自动跑一遍、统计成功率。

第 4 条是这个行业里最少人系统做、但生产上最离不开的能力。

---

写完这四步，你就已经摸到了 Agent 开发真正的难点 ——
**它们全都不在框架 API 里，而在循环内部的这些决策上。**
