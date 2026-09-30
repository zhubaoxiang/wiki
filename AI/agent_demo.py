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
