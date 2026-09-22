# PostgreSQL continuous_events 系列表性能优化总结

> 日期：2026-07-23 ~ 07-24（持续更新）

## 背景

数据库 `internal_app_bsa_gjk` 中三张核心表存在严重性能问题：
- `continuous_events`（约 3000 万行）
- `continuous_events_port`（约 3.86 亿行，2 个索引）
- `continuous_events_tag`（大表）

问题表现：COUNT 查询耗时 33 秒、手动 VACUUM 跑 22 小时未完成、死元组大量堆积、Python 程序 OOM。

## 服务器配置

| 资源 | 规格 |
|------|------|
| 内存 | 251GB（实际使用约 12GB） |
| Swap | 8GB（已用 4.6GB） |
| 磁盘 vdb | util 100%，await 40~70ms（I/O 瓶颈） |
| PostgreSQL 版本 | 需确认 |

## 问题排查过程

### 1. COUNT 查询慢（33 秒）

执行计划关键指标：
- `Heap Fetches: 177,000+` — 瓶颈
- 根因：VM（Visibility Map）失效，大量元组需回表检查可见性

### 2. VACUUM 慢的原因

| 因素 | 详情 |
|------|------|
| 磁盘 I/O 饱和 | vdb util=100%，机械盘/SATA SSD 撑不住 3.86 亿行顺序扫描 |
| 表数据量巨大 | port 表 3.86 亿行 / 102GB，全扫一遍本身就耗时 |
| cost 限速 | autovacuum 参数未配置，走默认低速 |
| 多 VACUUM 竞争 | 手动 VACUUM 和 autovacuum 同时跑同一张表，互相阻塞（ShareUpdateExclusiveLock） |
| ANALYZE 拖累 | VACUUM ANALYZE 比纯 VACUUM 多一个全表采样阶段 |
| TOAST 表 | continuous_events 的 TOAST 表也有死元组，需额外清理 |

> port 表仅 2 个索引（pkey + event_id_idx），索引数量不是瓶颈。

### 3. 死锁

`netflow_cont_event_update` 脚本并发 UPDATE 同一行导致死锁，需加重试逻辑。

### 4. Python MemoryError

`fetchall()` 一次性加载大量数据到内存，触发 Linux OOM Killer。需改流式读取或分页。

### 5. 配置问题总结

| 参数 | 当前值 | 问题 |
|------|--------|------|
| `shared_buffers` | 128MB（已有人改过，PG 9.x 默认 32MB） | 251G 内存只用了 0.05%，远低于 25% 的建议值 |
| `effective_cache_size` | 4GB（默认） | 规划器低估缓存，倾向全表扫描 |
| `vm.swappiness` | 60（默认） | 内存充裕时仍 swap，无意义 I/O |
| `vacuum_cost_delay` | 0 | 手动 VACUUM 全速 ✅ |
| `autovacuum_vacuum_cost_delay` | 未设置（默认 2ms） | autovacuum 被限速 |
| `autovacuum_vacuum_cost_limit` | 未设置（继承 200） | autovacuum I/O 配额过小 |

## 已执行的操作

### 1. 三张表 autovacuum 参数调优

**目的**：解决死元组严重堆积、autovacuum 几乎不触发或触发后跑太慢的问题。

#### 调高触发频率（解决"不触发"的问题）

| 参数 | 改前 | 改后 | 效果 |
|------|------|------|------|
| `autovacuum_vacuum_scale_factor` | 0.2（默认） | 0.001 | 死元组占比 0.1% 就触发，不再等到 20% |
| `autovacuum_vacuum_threshold` | 50（默认） | 20000 | 基础阈值提高到 2 万，避免空跑 |

**改进前**：port 表默认触发阈值 = 50 + 3.86亿 × 0.2 = 7735 万，实际死元组 4520 万都没到门槛，autovacuum 根本没启动。

**改进后**：port 表触发阈值 = 20000 + 3.86亿 × 0.001 = 40.6 万，死元组一旦超过 40 万立即触发清理。

#### 提速执行（解决"触发后跑太慢"的问题）

| 参数 | 改前 | 改后 | 效果 |
|------|------|------|------|
| `autovacuum_vacuum_cost_limit` | 200（继承全局） | 2000 | 每轮 I/O 配额提高 10 倍，一次能干更多活 |
| `autovacuum_vacuum_cost_delay` | 2ms（默认） | 5ms | 屁股休息时间适当放宽，避免打满磁盘 |

**改进前**：autovacuum 每读 200 单位 I/O 就歇 2ms，工作占比约 50%，磁盘 util 打满时实际干得更少。

**改进后**：2000/5ms，工作占比约 80%，单轮清理量翻数倍。

```sql
ALTER TABLE internal_app_bsa_gjk.continuous_events SET (
    autovacuum_vacuum_scale_factor = 0.001,
    autovacuum_vacuum_threshold = 20000,
    autovacuum_vacuum_cost_limit = 2000,
    autovacuum_vacuum_cost_delay = 5
);

ALTER TABLE internal_app_bsa_gjk.continuous_events_port SET (
    autovacuum_vacuum_scale_factor = 0.001,
    autovacuum_vacuum_threshold = 20000,
    autovacuum_vacuum_cost_limit = 2000,
    autovacuum_vacuum_cost_delay = 5
);

ALTER TABLE internal_app_bsa_gjk.continuous_events_tag SET (
    autovacuum_vacuum_scale_factor = 0.001,
    autovacuum_vacuum_threshold = 20000,
    autovacuum_vacuum_cost_limit = 2000,
    autovacuum_vacuum_cost_delay = 5
);
```

> ✅ port 表的 ALTER TABLE 已在手动 VACUUM 完成后成功执行，三张表参数已全部生效。

---

### 2. effective_cache_size 调整（已执行，热加载不重启）

**目的**：让查询规划器正确感知操作系统的缓存能力，从而选择更优的查询计划。

#### 第一次调整：4GB → 180GB（7/23 执行，7/24 回滚）

| 参数 | 改前 | 改后 |
|------|------|------|
| `effective_cache_size` | 4GB（PG 9.x 默认） | 180GB |

**预期效果**：规划器认为数据大概率在内存中，更愿意走索引扫描。

**实际效果（7/24 用户反馈查询变慢）**：调大后，`continuous_events` 的复杂查询（带 tag 表子查询聚合）出现严重性能回退。原因是规划器选择了 **Merge Join + 大范围索引扫描**（预估扫 3.65 亿行），而非之前的 **Nested Loop**（逐行索引 seek，总读 3,396 行）。

根因分析：虽然 OS buff/cache 有 ~240GB，但 vdb 磁盘 util=100%（被 autovacuum 占满），**Merge Join 的大范围顺序索引扫描产生的随机 I/O 在饱和磁盘上极慢**。而 Nested Loop 配合 LIMIT 时 PG 能按需计算，只读主查询实际需要的行数。

#### 第二次调整：180GB → 8GB（7/24 执行，当前值）

| 参数 | 改前 | 改后 |
|------|------|------|
| `effective_cache_size` | 180GB | 8GB |

**目的**：回滚到一个保守但不再低估的值，避免规划器盲目选择大范围扫描策略。

**选择 8GB 的原因**：
- 4GB 时计划正确（Nested Loop），但过于保守，可能让其他查询错失索引优化的机会
- 8GB 是一个合理中间值——告诉规划器"有一定缓存，但别太乐观"
- 等 VACUUM 完成后，可逐步提到 32~64GB

```sql
ALTER SYSTEM SET effective_cache_size = '8GB';
SELECT pg_reload_conf();
```

#### 后续：VACUUM 完成后的长期值

```sql
-- VACUUM 完成、磁盘 I/O 回落后再设
ALTER SYSTEM SET effective_cache_size = '32GB';
SELECT pg_reload_conf();
```

---

### 3. 执行计划对比验证（7/24）

对同一条复杂查询（events + tag 子查询聚合，无 LIMIT），在不同 `effective_cache_size` 下的执行计划：

| 设置 | Join 策略 | tag 表扫描方式 | 实际耗时 |
|------|-----------|---------------|----------|
| 180GB（故障值）| Merge Join | 大范围索引扫描，预估 3.65 亿行 | 数分钟（用户反馈极慢）|
| 4GB | Nested Loop | 逐 event_id 索引 seek，377 loops，共 3,396 行 | **991 ms** |

**结论**：`effective_cache_size` 设得过大，规划器误判数据全在内存，选了 `Merge Join` 而非 `Nested Loop`。`Nested Loop` 配合 LIMIT 时 PG 能按需计算子查询，总 I/O 量远小于 `Merge Join` 的全量扫描。此参数值必须与**实际磁盘 I/O 能力**匹配，不能仅看内存大小。

---

### 4. 更新统计信息（待执行）

`continuous_events_tag` 表 `last_autoanalyze` 停留在 **2026-05-21**，超过 2 个月未更新统计信息。规划器对行数、唯一值分布全用的旧数据，这也是选错执行计划的原因之一。

```sql
ANALYZE internal_app_bsa_gjk.continuous_events;
ANALYZE internal_app_bsa_gjk.continuous_events_tag;
ANALYZE internal_app_bsa_gjk.continuous_events_port;
```

> ANALYZE 不锁表，可在线执行。但在 vdb util=100% 时会较慢，建议等当前 autovacuum 进程结束后再跑。

## 待执行操作

### 1. 降低 OS swappiness（零停机）

**目的**：防止内核将 PostgreSQL 的内存页换出到 swap，避免无意义的磁盘颠簸。

当前 swap 已用了 4.6GB（共 8GB），内存明明很充裕（251G 只用 12G），但由于 `vm.swappiness=60`（默认值），内核仍在积极 swap。PG 的 shared_buffers 一旦被 swap 出去，访问时又要从磁盘读回来，凭空增加 I/O。

降到 1 后：内存充裕时几乎不下 swap，PG 数据页常驻物理内存。

```bash
sysctl -w vm.swappiness=1
echo "vm.swappiness = 1" >> /etc/sysctl.conf
```

---

### 2. 增大 shared_buffers（需重启 PG，择机）

**目的**：让 PostgreSQL 自己管理更多热数据缓存，减少系统调用和内核态拷贝。

当前 `shared_buffers=128MB`，对于 251G 内存的机器只有 0.05% 利用率。数据虽然被 OS page cache 缓存了，但每次访问都需要 `read()` 系统调用 + 内核态→用户态内存拷贝，效率远低于 PG 自己的 buffer 命中。

调到 32GB 后：热点表（如索引页、频繁查询的数据页）留在 PG 缓冲区内，零系统调用直接访问。

```ini
# postgresql.conf，择机重启
shared_buffers = 32GB
```

```bash
# 同时调大内核共享内存上限
sysctl -w kernel.shmmax=34359738368
```

## 关键知识点

### 手动 VACUUM 注意事项

**目的**：当 autovacuum 来不及清理时，手动干预的正确姿势。

```sql
-- 先调高内存，再执行（不加 ANALYZE，autoanalyze 会自动补）
SET maintenance_work_mem = '1GB';
VACUUM internal_app_bsa_gjk.continuous_events;
```

要点：
- **不加 ANALYZE**：ANALYZE 会对整表采样统计信息，大表耗时与 VACUUM 本身相当，但 autoanalyze 会自动补，没必要等
- **先调 maintenance_work_mem**：默认 64MB，调到 1GB 让 VACUUM 有更大的内存排序死元组指针
- **一张一张来**：不要并行 VACUUM 多张大表，磁盘扛不住

### autovacuum 并行限制
- 同一张表不会同时跑两个 VACUUM（调度器保护）
- 全局 `autovacuum_max_workers` 控制三张表是否可以并行清理
- 调高频率后，高频小批量清理比低频大批量更健康

### VACUUM 不会回头清理
- VACUUM 是顺序扫描，扫描过的区域之后产生的新死元组不会被本轮清理
- 这就是提高 autovacuum 频率的意义：间隔短 → 新产生的死元组更快被下一轮追上

### shared_buffers vs OS page cache
- shared_buffers：PG 自身管理，零系统调用，热点感知
- OS page cache：通用 LRU，需系统调用 + 内存拷贝，可能被 swap
- 二者互补，不是替代关系

### effective_cache_size 设置陷阱
- **不分配内存**，仅影响规划器的**成本估算**，决定走全表扫描还是索引扫描
- 设得过大 → 规划器误判数据全在内存 → 选 Merge Join / Index Scan → 磁盘扛不住时反而更慢
- **必须与实际磁盘 I/O 能力和当前 I/O 负载匹配**，不是内存越大就设越大
- 对有 LIMIT 的查询影响小（PG 按需计算），无 LIMIT 的大结果查询影响巨大
- 稳妥做法：在磁盘 I/O 瓶颈解除前保持保守值（8~32GB），磁盘空闲后再逐步提高

### emergency 清理流程
当死元组严重堆积时：
1. 杀光其他 VACUUM 进程
2. `SET maintenance_work_mem = '1GB'`
3. 只跑 `VACUUM`（不加 ANALYZE）
4. 一张一张来，不要并行
