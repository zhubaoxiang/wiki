# Agent Harness 入门指南：原理、组成与选型

面向第一次接触 Harness 概念的读者。读完这篇你能明白：

1. Harness 是什么、为什么模型很强但 agent 还是不可靠
2. Harness 到底负责哪几件事、一轮 agent loop 长什么样
3. Harness / Framework / Runtime / Platform 四个词怎么区分
4. 作为用户，怎么"调教"你正在用的 harness

本文是三层系列的收官篇：接口层见 [MCP入门指南](MCP入门指南.md)，知识层见 [Skill入门指南](Skill入门指南.md)，本文讲执行层。

---

## 1. Harness 是什么

一句话定义：**Harness 是包裹在模型外面、把"只会生成文本的模型"变成"能干活的 agent"的那层软件**。

LangChain 2026 年 3 月的工程文章给了个精辟的说法：

> **"如果你不是模型，那你就属于 harness。"**
> （agent = model + harness）

模型是发动机，harness 是整车——方向盘、油门、刹车、仪表盘。发动机决定上限，但车能不能安全开到目的地，是整车的事。

### 它解决的真实问题：长任务必败

把一个强模型放进长任务里放养，失败方式高度可预测：

- **上下文溢出**：任务跑几小时，context 塞满后"忘了"自己在干嘛
- **自我宣称完成**：模型评审自己刚写的东西，天然偏向"做完了"
- **死循环重试**：工具调用失败后原地重复同一个失败动作
- **断点即归零**：进程一断，没有任何记录说明干到了哪一步

注意：这些**不是推理失败**——模型全程输出的文字都很连贯。这是**基础设施失败**，所以解法不是换更强的模型，而是补上 harness 这一层。

---

## 2. Harness 负责哪几件事

Anthropic 对 agent 的定义是"LLM 在循环里根据环境反馈使用工具"。这句话里除了 LLM，其余全是 harness：

| 职责 | 干什么 | 为什么模型自己做不了 |
|---|---|---|
| **循环（agent loop）** | 调模型 → 执行它选的工具 → 结果拼回上下文 → 再调模型，直到完成或触发停止条件 | 模型只能输出文本（包括"我想调工具"的请求），没有东西替它"执行" |
| **工具执行** | 把模型的工具请求变成真实的 shell 命令、文件编辑、API 调用 | 同上——请求要有人落地执行并回传真实结果 |
| **上下文组装** | 决定每轮模型能看到什么：系统提示、项目文件（CLAUDE.md/AGENTS.md）、历史输出、压缩摘要 | 上下文窗口有限且固定，装什么、裁什么是代码逻辑 |
| **权限与沙箱** | 哪些动作自动执行、哪些等人工批准、哪些直接拒绝 | 自治没有边界就是安全事故 |
| **记忆与持久化** | 跨会话保存进度（进度文件、checkpoint），context 满了做压缩（compaction） | 模型无状态，窗口重置后一片空白 |
| **Hooks** | 在循环的固定节点跑脚本：提交前自动 lint、模型想停时强制跑测试 | 把"纪律"从劝告变成强制 |

**关键认知**：从外面看很多"AI 的判断力"，其实是 harness 的基础设施。同一个模型放进两个 harness，表现可以差很远——一个 context 满了乱丢细节，另一个靠进度文件第二天接着干。

---

## 3. 一轮 agent loop 长什么样

用一次真实的编码任务走一遍（简化自典型场景）：

```
开发者：给结算页加"允许修改配送地址"功能
    │
    ▼
① Harness 组装上下文：任务 + 项目 AGENTS.md（约定：改完跑 npm test）
    │
    ▼
② 模型返回工具请求：编辑 checkout/AddressForm.tsx
    │
③ Harness 查权限规则：项目内文件编辑 → 自动放行 → 写文件
    │
    ▼
④ 模型返回工具请求：运行 npm test
    │
⑤ Harness 查权限：shell 命令 → 需人工批准 → 开发者点确认 → 执行 → 返回 "42 passed"
    │
    ▼
⑥ 模型不再请求工具，输出总结（任务自认为完成）
    │
⑦ Stop hook 触发：Harness 强制跑 npx tsc --noEmit → 通过 → 本轮结束
```

数一下：②④⑥ 是模型，**①③⑤⑦ 全是 harness**。这轮结束后开发者手工验收，发现改地址会把购物车清空——因为 harness 没提供浏览器工具、测试和 hook 也没覆盖这条路径。这个例子说明一个残酷的事实：**agent 的能力边界，很大程度是 harness 给了什么工具决定的**。

---

## 4. 四个长得像的词：Framework / Runtime / Harness / Platform

营销材料里经常混用，按"你拿它做什么"来锚定：

| 术语 | 你拿它做什么 | 例子 |
|---|---|---|
| **Framework** | 写代码用的类库，给你原语自己搭 | LangGraph、CrewAI |
| **Runtime** | 生产环境托管执行 agent 循环（状态、重试、限额） | LangGraph Runtime、各类 serverless agent 运行时 |
| **Harness** | **装好就能用的成品 agent**：循环、工具、权限模型、交互界面都已选好 | Claude Code、OpenAI Codex CLI、OpenCode |
| **Platform** | 多团队、多 agent 的治理体系（身份、租户隔离、审计） | 各企业 agent 平台 |

边界会模糊：Claude Agent SDK 本质是把 Claude Code 的 harness（循环 + 工具 + 上下文管理）打包成了库。实用判据是**信任边界**：harness 通常服务单一操作者或互信团队；要服务互不信任的多个主体，就得升级成带身份与审计的 platform。

### 本系列三层的关系：拼出完整的 agent

```
┌─────────────────────────────────────────┐
│  Harness（执行层）                        │
│  循环 · 工具执行 · 权限/沙箱 · 上下文 · 记忆 │
│                                          │
│  ├── Skill（知识层）: 该做什么、怎么做       │
│  └── MCP（接口层）  : 怎么够到外部系统       │
│          └── 外部系统 / 数据库 / API        │
│                                          │
│  模型（推理）— 只是这整张图里的一个函数       │
└─────────────────────────────────────────┘
```

同一套外部系统（MCP Server）+ 同一批知识（Skill），换一个 harness，agent 的体验就是另一个产品——**harness 决定模型每一步能看见什么、能碰什么、出错后怎么办**。

---

## 5. Harness Engineering：怎么调教你正在用的 harness

Anthropic 2025 年 11 月的《Effective harnesses for long-running agents》给出了一套长任务实践，其中大部分**你作为用户就能做**：

| 做法 | 用户侧落点 |
|---|---|
| **结构化进度笔记** | 让 agent 把进度写到固定文件（如 PROGRESS.md），跨会话可恢复——比指望 context 记住靠谱得多 |
| **一次只做一个特性** | 别把三个需求塞进一次会话；上下文越杂，中间越容易丢细节 |
| **端到端验证再前进** | 用 hooks 把纪律固化：Stop hook 强制跑测试/lint，模型"自认为完成"不算完成 |
| **项目指令文件** | CLAUDE.md / AGENTS.md 写清构建命令、代码规范——harness 每轮自动注入 |
| **权限分级** | 只读自动放行、写文件放行、shell 需确认、危险操作直接禁 |
| **控制上下文质量** | 大输出让 agent 落盘再引用，别让几十万 token 的日志灌进 context |

**失败归因口诀**（排查 agent 出问题时先问自己）：

- 丢进度、忘了约定、死循环重试 → **harness 的锅**（记忆/循环/重试没配好）
- 输入齐全但方案本身烂 → **模型的锅**（能力上限，换提示词或换模型）

先分层再动手，能省掉大量"换个模型试试"的无效折腾。

---

## 6. 常见坑速查

| 现象 | 原因 | 解法 |
|---|---|---|
| 长会话后半段质量明显下滑 | context 溢出，早期细节被挤出或压缩失真 | 关键约定写进 CLAUDE.md（每轮注入）+ 进度落盘文件 |
| Agent 说"完成了"但没完成 | 自评审偏差，无外部验证 | Stop hook 强制跑测试 / 用另一个模型或人复核 |
| 断线重连后从零开始 | 没有持久化，模型无状态 | 要求 agent 维护进度文件；用支持 checkpoint 的 runtime |
| 危险命令直接跑了 | 权限模型没配置 | 给 shell/写操作设确认，最高危操作用 deny 规则 |
| 工具很多但 agent 不用/乱用 | 工具描述差（MCP 侧问题）或 context 里工具清单太长 | 修 description（见 MCP入门指南 第 6 节）；精简不相关工具 |
| 同一模型换个工具表现天差地别 | harness 不同（上下文组装/权限/循环参数不同） | 先对比两边的系统提示与权限配置，别急着怪模型 |
| Hook 不生效 | Hook 挂错了循环节点，或脚本退出码没约定 | 确认 hook 绑定的事件（PreToolUse/Stop 等）与退出码语义 |

---

## 7. 延伸阅读

- Anthropic：《Effective harnesses for long-running agents》（2025-11，长任务 harness 工程实践）
- Anthropic：《Building effective agents》（agent loop 的定义来源）
- LangChain：The Anatomy of an Agent Harness（2026-03，"agent = model + harness"）
- Mitchell Hashimoto：My AI Adoption Journey（"harness engineering" 一词的出处）
- 系列配套：[MCP入门指南](MCP入门指南.md)（接口层）、[Skill入门指南](Skill入门指南.md)（知识层）
