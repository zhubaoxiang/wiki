# Agent Skill 入门指南：原理、格式与从零搭建

面向第一次接触 Skill 的读者。读完这篇你能明白：

1. Skill 是什么、和 MCP 差在哪、各自解决什么问题
2. 一个 Skill 的目录结构与规范格式
3. 渐进式披露（Progressive Disclosure）三级加载机制
4. 如何从零写一个能用的 Skill，以及写"好"的关键设计

本文讲"是什么"和"怎么写"；与 MCP 的对比见第 6 节，配套的 MCP 原理见 [MCP入门指南](MCP入门指南.md)。

---

## 1. Skill 是什么

**Agent Skill** 是 2025 年 Anthropic 发布的开放标准（规范见 <https://skill.md/>），目前 Claude Code、claude.ai、Claude API、WorkBuddy 等主流 AI 客户端均已支持。

一句话定义：**Skill 是一个装着"领域知识与工作流程"的文件夹**。它不解决"模型怎么调外部系统"（那是 MCP 的事），解决的是**"模型怎么拥有某个领域的专业经验"**——就像给聪明但没经验的新员工一本岗位操作手册。

### 它解决的真实问题：能力 ≠ 专长

模型什么都能推理，但没有你的团队沉淀下来的"怎么做"：

- 每次都要重复交代"我们的周报是这个格式""改 SQL 前先看执行计划"
- 同样的流程（发版、写报告、排查故障）每次执行得都不一样
- 业务规则散落在 Wiki、聊天记录和老员工脑子里

Skill 把这些**程序性知识**打包成可版本管理（Git）、可复用、跨客户端的文件夹，agent 按需加载。

### 与 MCP 的位置关系（重要，容易误解）

Skill 不是 MCP 的替代品，两者解决的是不同层的问题：

```
MCP：接口层——模型怎么"够到"外部系统（协议、工具调用）
Skill：知识层——模型怎么"知道"该做什么、怎么做（流程、经验、脚本）

模型 ──按 Skill 指引──> 调用工具 ──MCP──> 外部系统 ──> 数据库
        （知识）            （接口）
```

一个 Skill 内部完全可以引用 MCP 工具；很多成熟的 Skill 本质上就是"MCP 工具的最佳使用说明书"。

---

## 2. 一个 Skill 长什么样

Skill 不是单个文件，而是一个**文件夹**：

```
my-skill/
├── SKILL.md        # 必需：YAML frontmatter（元数据）+ 指令正文
├── scripts/        # 可选：可执行脚本（被执行，不进上下文）
├── references/     # 可选：参考文档（按需读取）
└── assets/         # 可选：模板、字体、图标等输出素材
```

### 2.1 SKILL.md 的 frontmatter（必需字段）

```markdown
---
name: weekly-report
description: 生成团队周报，按固定模板汇总本周进展、风险与下周计划。当用户提到周报、weekly report、本周总结时使用。
---

# 周报生成

## 步骤
1. 读取本周 git log 与已完成 issue
2. 按 references/template.md 的模板组织内容
3. 输出 Markdown 格式周报
```

### 2.2 frontmatter 字段规范

| 字段 | 必需 | 规则 |
|---|---|---|
| `name` | 是 | **kebab-case 小写**（只允许字母/数字/连字符），≤64 字符，必须与文件夹名一致；不能含 `anthropic`/`claude` 字样 |
| `description` | 是 | ≤1024 字符；必须同时写清**做什么**和**什么时候用**（含用户可能输入的触发词）；用第三人称 |
| `allowed-tools` | 可选 | 限制该 Skill 可用的工具（最小权限），如 `"Bash(python:*) WebFetch"` |
| `metadata` | 可选 | 自定义键值：author、version、mcp-server 等 |
| `license` / `compatibility` | 可选 | 开源许可 / 环境要求说明 |

**`description` 是整个 Skill 杠杆率最高的字段**——模型靠它从上百个 Skill 里决定加载哪一个（原因见第 3 节）。

---

## 3. 运行机制：渐进式披露的三级加载

Skill 的核心设计是**只加载当前需要的部分**，上下文成本分三级：

```
启动时：所有 Skill 只注入 name + description（每个 ~100 token）
    │
    │  任务匹配了某个 description
    ▼
触发时：读取该 Skill 的 SKILL.md 正文（<5k token）
    │
    │  正文里引用了脚本/参考文档
    ▼
执行时：按需读取 references/ 文档；脚本直接执行，
        只有输出进上下文，代码本身不占上下文
```

| 级别 | 何时加载 | 内容 | 成本 |
|---|---|---|---|
| L1 元数据 | **始终**（启动时进 system prompt） | frontmatter 的 name + description | ~100 token / Skill |
| L2 指令正文 | 被触发时 | SKILL.md 主体（流程、最佳实践） | <5k token |
| L3 资源 | 按需 | references 文档、scripts 脚本、assets 素材 | 文档读取才计费；脚本只回传输出 |

对比：MCP 的 `tools/list` 也是"启动时只拿清单、运行时才知道细节"，但 Skill 把这个思想做到了**知识本身**上——你可以装几百个 Skill 而不撑爆上下文窗口。

### 生命周期三阶段

```
Discovery（发现）  →  Activation（激活）  →  Execution（执行）
启动时只看元数据      读入 SKILL.md 全文     照指令干活，
                    判断与任务是否匹配      跑脚本、读参考文档
```

---

## 4. 最小 Demo：一个 5 分钟写完的 Skill

下面是一个完整的真实可用 Skill——把"查数据库前先看执行计划"的团队规范固化下来。

```
sql-review/
└── SKILL.md
```

`sql-review/SKILL.md`：

```markdown
---
name: sql-review
description: 审查和分析 SQL 语句的性能风险。当用户要执行或优化 SQL、提到慢查询、
  EXPLAIN、索引、执行计划时使用。对 PostgreSQL 大表的 UPDATE/DELETE 尤其要先走本流程。
---

# SQL 审查

## 流程
1. 先要 EXPLAIN (ANALYZE, BUFFERS) 的输出，没有执行计划不做任何判断
2. 检查三个信号：候选行数、Heap Blocks 的 exact/lossy 比例、是否全表扫
3. 任何索引建议必须先评估写入代价（高频更新字段的写放大）
4. 输出格式：结论先行 + 执行计划关键行引用 + 风险清单

## 输出模板
见 references/output-template.md；正文中不要直接贴完整执行计划，只引用关键行。
```

安装位置（按客户端约定放入 skills 目录，重启会话生效）：

| 客户端 | 个人级（所有项目可用） | 项目级（随仓库共享） |
|---|---|---|
| Claude Code | `~/.claude/skills/sql-review/` | `.claude/skills/sql-review/` |
| WorkBuddy | `~/.workbuddy/skills/` | `{workspace}/.workbuddy/skills/` |

验证：问一下 agent *"什么时候该用 sql-review 这个 skill？"*——它答得出触发条件，说明 L1 元数据已生效。

---

## 5. 把"能跑"变成"好用"：六个关键设计

第 4 节写完就能用，但决定 Skill 好不好用的是契约层设计：

| # | 能力 | 做法 |
|---|---|---|
| 1 | **description 即触发器** | 模型只看 name + description 决定加载谁。必须含"做什么 + 何时用 + 用户会输入的触发词"；容易误触发时写负面触发（"不要用于……"） |
| 2 | **正文守 500 行红线** | SKILL.md 主体 <5k token；快超了就拆到 references/，引用只下沉一层（嵌套引用会被截断读取） |
| 3 | **能写脚本就不要写文字** | 确定性操作（格式转换、数据校验、模板套用）写成脚本**执行**——代码不进上下文，只有输出进；比一堆文字步骤更可靠且省 token |
| 4 | **执行意图要写死** | "运行 `./scripts/build.py`"（执行）和"算法见 analyze.py"（阅读）是两种完全不同的行为，不要让模型猜 |
| 5 | **长参考文档带目录** | references/ 里超过 100 行的文档，开头放目录表；模型可能只 `head -100` 部分读取 |
| 6 | **安全边界** | frontmatter 会注入 system prompt：**不要用 XML 尖括号**、不要拿 `anthropic`/`claude` 命名；写操作类 Skill 用 `allowed-tools` 收权，敏感动作默认关闭 |

第 1 条与 MCP 的"描述即接口"（见 MCP入门指南 第 6 节）完全同构：**模型决策依据永远是它看得见的那一小段文字**。

---

## 6. Skill vs MCP：怎么选

| 维度 | Skill | MCP |
|---|---|---|
| 本质 | 知识包（文件即标准） | 接口协议（JSON-RPC 消息约定） |
| 形态 | 文件夹，Git 即分发 | stdio 子进程 / HTTP 常驻服务 |
| 上下文成本 | 渐进披露，不触发几乎为零 | 工具清单常驻上下文（`tools/list`） |
| 提供什么 | 流程、经验、规范、脚本 | 能力、数据、工具调用 |
| 需要运行时 | 否（纯文件） | 是（进程/服务、依赖安装） |
| 典型场景 | 周报模板、SQL 审查规范、发布流程、品牌文档格式 | 连数据库、调业务 API、操作外部系统 |

选型口诀：

- 模型**缺的是"知道怎么做"** → Skill（把老员工的经验写成文件夹）
- 模型**缺的是"够得到的权限/数据"** → MCP（把系统能力包成工具）
- 大多数真实场景是**两者组合**：Skill 写流程和规范，流程里调用 MCP 工具干活

---

## 7. 常见坑速查

| 现象 | 原因 | 解法 |
|---|---|---|
| Skill 装了但从不触发 | description 太模糊，没有触发词 | 写明"做什么 + 何时用 + 用户原话关键词"（见第 5 节第 1 条） |
| 该触发的时候触发了别的 Skill | 描述互相打架 | 加负面触发："不要用于简单数据探索，用 xxx skill" |
| `name` 不合法 | 用了中文/大写/下划线 | 必须小写 kebab-case，与文件夹名一致 |
| 触发后表现时好时坏 | 正文太长被截断，或 references 嵌套过深 | 正文 <500 行；引用只下沉一层；长文档加目录 |
| 脚本跑不起来 | 只写了"参见 xxx.py"，模型把它当文档读了 | 明确写"运行 ./scripts/xxx.py <参数>"，声明依赖与用法 |
| Skill 疯狂吃上下文 | 把大段资料全塞进 SKILL.md 正文 | 资料下沉到 references/，正文只留流程指令 |
| frontmatter 导致注入告警 | 描述里用了 XML 尖括号 | frontmatter 进 system prompt，禁用 `<tag>` 写法 |

---

## 8. 延伸阅读

- Agent Skills 开放规范：<https://skill.md/>
- Anthropic 工程博客（为什么停做专用 Agent、转做 Skill）：<https://claude.com/blog/building-agents-with-skills-equipping-agents-for-specialized-work>
- 官方文档：Agent Skills Overview（platform.claude.com）
- 系列配套阅读：[MCP入门指南](MCP入门指南.md)（接口层）、[Harness入门指南](Harness入门指南.md)（执行层）
