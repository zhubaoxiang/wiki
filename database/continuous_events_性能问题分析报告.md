# continuous_events 表性能问题分析报告

> 表：`internal_app_bsa_gjk.continuous_events`  
> 数据量：3856 万行 / 47 GB / 41 列  
> 写入速率：约 40 万行/天（新增 + 更新），定时任务每 10 分钟批量更新约 1 万行

---

## 1. 问题表现

| 查询场景                      | 耗时         | 问题                  |
| ------------------------- | ---------- | ------------------- |
| 列表查询（带 `COUNT(*) OVER()`） | **24.5 秒** | 不可接受                |
| 列表查询（不带 COUNT）            | **4 ms**   | 正常                  |
| 单独 COUNT 查询               | **6.8 秒**  | 慢                   |
| 带 `src_ip` 筛选的查询          | **几分钟**    | `src_ip` 不在索引中，全表扫描 |

---

## 2. 根因分析

### 2.1 COUNT(*) OVER() 阻止了 Limit 下推

**原理：**

普通 `SELECT ... ORDER BY ... LIMIT 50` 查询，优化器可以把 LIMIT 下推到 Index Scan 节点——索引上找到 50 条匹配行立即停止，不需要扫描剩余数据。

但 `COUNT(*) OVER()` 是窗口函数，其语义要求**对整个结果集计算聚合**。优化器无法确定总数是否受 Limit 影响（实际不受影响），因此无法将 LIMIT 下推。Index Scan 必须扫描所有匹配行（76 万行）才能继续。

**执行计划对比：**

```
-- 带 COUNT：Index Scan 扫 76.3 万行，耗时 1.5 秒（开发）/ 23 秒（线上）
WindowAgg → Index Scan (rows=763,909)

-- 不带 COUNT：Index Scan 扫 50 行就停，耗时 0.35 ms
Limit → Index Scan (rows=50)
```

**结论：** `COUNT(*) OVER()` 本身只花 0.07 ms，但它迫使前置 Index Scan 做全量扫描，这是 24 秒的根源。

---

### 2.2 Bitmap Heap Scan 与 Lossy 退化

**Bitmap Heap Scan 是什么：**

PostgreSQL 的一种扫描策略，用于中等大小结果集（几千行到几十万行）：

1. 扫描索引，收集所有匹配行的物理位置（ctid）
2. 放入一个位图（bitmap），**按磁盘页号排序**
3. 按排序后的顺序读取堆表页面——把随机 IO 变成顺序 IO

**类比：** 去图书馆借 100 本书，先用索书号查到每本书的位置，记在小本子上，按书架编号排序后，一次性出发拿书——而不是查到一本跑一趟。

**Lossy 退化：**

位图存储在 `work_mem` 中。当候选行太多、位图超出 work_mem 时，PG 被迫退化：

```
正常（exact）：位图标记每一具体行 → 精确读取
退化（lossy）：位图只标记"这个页里可能有" → 整页读取后逐行 recheck
```

**线上的实际情况（默认 work_mem=4MB）：**

```
Bitmap Index Scan → 70.8 万行候选
  Heap Blocks: exact=43,564   (8%)
  Heap Blocks: lossy=534,937  (92%)  ← 92% 退化
  Recheck 行数: 6,397,593           ← 读取 640 万行再过滤
  Bitmap Heap Scan 耗时: ~22 秒
```

**调大 work_mem 后（256MB）：**

```
Heap Blocks: exact=515,616  (100%)
Heap Blocks: lossy=0
Recheck: 0
Bitmap Heap Scan 耗时: ~9.8 秒
```

---

### 2.3 Visibility Map 与 Index Only Scan 的 Heap Fetches

**Visibility Map（VM）是什么：**

PostgreSQL MVCC 机制下，一行被 UPDATE 后旧版本仍然物理存在（dead tuple）。VM 标记哪些数据页中的**所有行对当前事务都是可见的**。

**Index Only Scan 的前提：**

Index Only Scan 理论上只读索引、不碰堆表。但有一个硬条件——索引条目对应的堆表页必须在 VM 中标记为"全部可见"。如果 VM 标记缺失（该页有不可见版本），PG 必须回堆表检查可见性（Heap Fetch）。

**线上的问题：**

```
Index Only Scan using idx_ce_default_count_time
  rows=752,183
  Heap Fetches: 5,330,090    ← 533 万次回表检查！
  Execution time: 6.8 秒
```

**为什么 Heap Fetches 这么高：**

这跟你表的结构直接相关：

| 指标        | 数值                 |
| --------- | ------------------ |
| 表大小       | 47 GB              |
| 行数        | 3,856 万行           |
| 每行平均      | ~1.3 KB（含元组头 + 对齐） |
| 每页 8KB 能放 | 仅 5-6 行            |
| 每天 UPDATE | 40 万行              |

VM 的粒度是**页级别**。每页只放 5-6 行，每天 40 万行 UPDATE 会波及大量数据页（40 万 ÷ 5 ≈ 8 万页/天）。即使每天执行 VACUUM，到下一个 VACUUM 之前，这些页的 VM 标记已经失效——Page 里有一行被更新过，整个 Page 就不再"全部可见"。

**死循环：** UPDATE 量大 → VM 频繁失效 → Index Only Scan 永远要回表 → 6.8 秒 COUNT 怎么都降不下来。

> 验证：线上死元组占比只有 2.3%（88 万 / 3856 万），说明 VACUUM 在正常清理。**问题不是死元组堆积，而是每天 VACUUM 一次频率跟不上 40 万行/天的 UPDATE 节奏——两次 VACUUM 之间累积了 40 万个页的 dead tuples，这些页的 VM 全部失效。** 拆字段本身不减少 VM 失效页数（40 万 UPDATE = 40 万个页，不变），但拆表后表变小，VACUUM 跑得更快，可以提频到每 4-6 小时一次，这才是缩短 VM 失效窗口的正确方式。

---

### 2.4 索引覆盖不全（src_ip / dst_ip 查询）

现有索引 `idx_ce_judge_endtime_eventid_new(end_time DESC, event_id, judge_status, up_bytesall DESC)` 缺少 `src_ip` 和 `dst_ip`。

当查询带 `src_ip = 'x.x.x.x'` 时，PG 无法利用索引精确定位，只能用 `end_time + up_bytesall` 缩小范围后逐行 filter：

```
Index Cond: end_time + up_bytesall    ← 缩小到 76 万行
Filter: src_ip = '156.189.56.143'     ← 逐行过滤
Rows Removed by Filter: 768,923       ← 99.99% 白扫
```

线上 3000 万行，src_ip 的选择性极高，全扫几分钟很正常。

**解决：** 加 `(src_ip, end_time DESC, up_bytesall DESC)` 和 `(dst_ip, end_time DESC, up_bytesall DESC)` 两个索引。

---

## 3. 解决方案

### 3.1 立即见效：COUNT 拆分为两次查询

```sql
-- 数据查询（4 ms，Index Scan 找到 50 行就停）
SELECT e.*, t.*, cet.*
FROM continuous_events e LEFT JOIN ...
WHERE end_time >= ... AND judge_status IN (...)
ORDER BY end_time DESC LIMIT 50;

-- Count 查询（单独执行，不拖累数据查询）
SELECT COUNT(*) FROM continuous_events
WHERE end_time >= ... AND judge_status IN (...);
```

用户感知：页面 4 ms 渲染，count 数字几百毫秒后出现。

### 3.2 查询层面：SET LOCAL work_mem

在 Django 代码中，对这条慢查询设置 work_mem（走 Index Scan 时不需要此设置，仅 Bitmap Heap Scan 场景有效）：

```python
from django.db import connection

with connection.cursor() as cursor:
    cursor.execute("SET LOCAL work_mem = '64MB'")
# ORM 查询共享同一事务
```

`SET LOCAL` 在事务结束时自动恢复，不需要手动释放。

### 3.3 索引优化：加 src_ip / dst_ip 索引

```sql
CREATE INDEX CONCURRENTLY idx_ce_srcip_endtime
ON continuous_events (src_ip, end_time DESC, up_bytesall DESC);

CREATE INDEX CONCURRENTLY idx_ce_dstip_endtime
ON continuous_events (dst_ip, end_time DESC, up_bytesall DESC);
```

3000 万行 + 40 万行/天的写入量下，从 4 个索引增加至 6 个，对写入性能的影响可忽略（B-tree 操作微秒级，日均 ~85 次/秒索引操作）。逐个建，避开业务高峰。

### 3.4 治本方案：表垂直拆分

**拆走/删除的字段：**

| 操作    | 字段                                                                                                                     | 原因            |
| ----- | ---------------------------------------------------------------------------------------------------------------------- | ------------- |
| 删除    | `src_info`, `dst_info`, `src_threat_mark`, `dst_threat_mark`, `iot_tag`, `key_service_tag`, `key_unit_tag`, `app_type` | 已废弃不用         |
| 拆到明细表 | `related_alerts`, `judge_info`, `judge_file`, `src_com`, `dst_com`, `src_operator`, `dst_operator`, `key_unit`         | 列表页不展示，仅详情页需要 |

**拆分效果：**

| 指标                      | 拆分前         | 拆分后               |
| ----------------------- | ----------- | ----------------- |
| 主表字段数                   | 41 → 21     | -20 列             |
| 主表行宽                    | ~500-600 字节 | ~200-250 字节       |
| 每页行数                    | ~14 行       | ~30-35 行          |
| 主表大小                    | 47 GB       | ~8 GB             |
| COUNT (Index Only Scan) | 6.8 秒       | 200-500 ms        |

**核心逻辑（重要修正）：** 拆分后 40 万行/天的 UPDATE 量不变，触及的页数也不变（40 万次 UPDATE = 40 万个页）。VM 失效率在比例上甚至可能更差（8GB 表的 40 万页比 47GB 表的占比更高）。

**那为什么 COUNT 还能从 6.8s 降到 200ms？不是 VM 变好了，是扫描成本大幅降低了：**

1. **索引从 ~1.5GB 缩小到 ~400MB**——索引页更密，扫 75 万条索引条目的 IO 量减少 3-4 倍
2. **shared_buffers 缓存命中率飙升**——8GB 的主表在 16GB shared_buffers 中几乎可以全缓存，索引完全在内存
3. **Heap Fetch 从磁盘 IO 变成内存 IO**——同样的 Fetch 次数，磁盘随机读 10ms/次 → 内存读 0.01ms/次，差了 1000 倍
4. **VACUUM 运行更快**——8GB 表的 VACUUM 比 47GB 快 6 倍，可以考虑提高频率（每天 1 次 → 每 4-6 小时 1 次），这才是减少 VM 失效累积的正道

### 3.5 各方案优先级

| 优先级    | 方案                              | 成本           | 收益                      |
| ------ | ------------------------------- | ------------ | ----------------------- |
| P0（立即） | COUNT 拆分为两次查询                   | 代码改动         | 列表从 24s → 4ms           |
| P1     | SET LOCAL work_mem（仅 Bitmap 场景） | 一行 SQL       | 防止 lossy                |
| P1     | 加 src_ip / dst_ip 索引            | 一次 DDL + IO  | src_ip 查询几分钟 → 几毫秒      |
| P2     | 建明细表、删除废弃字段、垂直拆分                | 一次 DDL + 窗口期 | COUNT 降到百毫秒级，根本解决 VM 问题 |

---

## 4. 核心原理速查

### 4.1 堆表（Heap Table）与元组结构

**堆表是什么：**

PostgreSQL 表的数据存储在"堆"（Heap）中。堆是一种无序的存储结构——INSERT 的新行放到表的最后可用位置，不按任何键排序。你把表想象成一个仓库，新货来了就放到最近一个空货架上，不在乎货架上其他东西是什么。

**元组（Tuple）：**

堆表中的每一行数据叫做一个元组。一个元组的物理结构如下：

```
┌─────────────┬──────────┬──────────────────────────────┐
│ 元组头        │ NULL bitmap │ 列数据（按表定义的列序排列）    │
│ (23 字节)     │ (可变)      │ (可变)                       │
├─────────────┼──────────┼──────────────────────────────┤
│ xmin, xmax, │ 标记哪些   │ 实际列值，包括对齐填充（align    │
│ ctid,       │ 列为 NULL  │ padding）。int4 对齐 4 字节，  │
│ infomask,   │            │ int8 对齐 8 字节。             │
│ ...         │            │ 每行可能有大量无用对齐空间。    │
└─────────────┴──────────┴──────────────────────────────┘
```

**ctid（行物理地址）：**

每行有一个唯一的物理标识符 `ctid`，格式为 `(页号, 行偏移)`，如 `(12345, 3)` 表示第 12345 号数据页的第 3 个槽位。索引的叶子节点存储的就是 ctid，查询时通过 ctid 精准定位到堆表中的具体行。

**关键结论：** 47GB 的数据不是 3856 万行 × 列宽的纯数据。它实际包含了每行 23 字节的元组头、NULL bitmap、以及各列之间的对齐填充（padding）。列越多、类型越杂，对齐浪费越大。

---

### 4.2 数据页（Page）结构

PostgreSQL 以 **8KB 的固定大小页面** 来管理存储，所有 I/O 操作的最小单位是一个页。

```
┌──────────────────────────────────────────────────┐
│                   8 KB 数据页                       │
│                                                   │
│  PageHeader (24B)                                 │
│  ┌──────────────────────────────────────┐         │
│  │ 元组指针数组（ItemIdData）             │ ← 从页头向下生长│
│  │ 每个指针 4 字节，指向一个元组           │             │
│  ├──────────────────────────────────────┤             │
│  │        空闲空间（Free Space）          │             │
│  ├──────────────────────────────────────┤             │
│  │ 元组 1  │ 元组 2  │ ...  │ 元组 N    │ ← 从页尾向上生长│
│  └──────────────────────────────────────┘             │
│                                                   │
│  特殊空间（Special Space）                          │
└──────────────────────────────────────────────────┘
```

**关键参数：**

| 概念 | 说明 | 对 continuous_events 的影响 |
|---|---|---|
| 页大小 | 固定 8KB，不可调（编译时决定） | — |
| 每页可用空间 | 约 8,000 字节（减去页头和指针开销） | — |
| FILLFACTOR | 预留空间百分比，INSERT 时每个页不完全填满，默认 100%（即尽量填满） | UPDATE 会导致元组膨胀，预留空间不足时触发页分裂 |
| 元组对齐 | int4 按 4 字节对齐，int8 按 8 字节对齐 | 你的表有 41 列、多种类型，对齐浪费显著 |
| 最大行数/页 | 8,000 ÷ 元组大小 | **你的表：8,000 ÷ 1,300 ≈ 6 行/页** ← 这是所有问题的根源 |

**为什么行数/页这么重要：**

- 全表扫描需要读的页面数 = 总行数 ÷ 行数/页。你 38M 行 ÷ 6 ≈ 640 万页
- 每行/页翻倍（从 6 到 12），全表扫描量减半；每行/页翻 5 倍（从 6 到 30），扫描量降到 1/5
- **但注意：VM 失效的页数不取决于行数/页**——40 万次 UPDATE 就是 40 万个页被污染，无论每页有几行。窄表 VM 失效比例甚至更高（因为总页数更少），真正的改善来自于缩小表体积后，shared_buffers 缓存命中率上升、VACUUM 可以跑更频繁

---

### 4.3 MVCC 与 UPDATE 的本质

**MVCC（多版本并发控制）：**

PostgreSQL 使用 MVCC 实现事务隔离。核心机制是"不覆盖旧版本，而是创建新版本"。

每行元组头中记录：
- `xmin`：插入这行的事务 ID
- `xmax`：删除/更新这行的事务 ID（0 表示仍然活跃）
- `infomask`：可见性标志位

当一行被 SELECT 时，PG 根据**当前事务的快照**判断这行是否可见——比较 xmin/xmax 与快照的边界。

**UPDATE 在 PostgreSQL 中的真实行为：**

```
UPDATE ... SET end_time = 123, up_bytesall = 456;

│ • 不是修改原行
│ • 而是：
│   1. 在旧行上标记 xmax = 当前事务 ID（标记为 dead）
│   2. 在堆表末尾插入一行新版本（xmin = 当前事务 ID）
│   3. 更新所有索引：删除旧索引条目 + 插入新索引条目
```

```
┌──────────────────────────────────────────────────┐
│  页 #100                                          │
│  ┌──────────────────────────────────────┐        │
│  │ 元组 A（旧版）xmax=1001 ← dead        │        │
│  │ 元组 B（旧版）xmax=1001 ← dead        │        │
│  │ 元组 C 活跃                          │        │
│  │ [空闲空间被 dead tuples 占据]         │        │
│  └──────────────────────────────────────┘        │
│                                                   │
│  页 #6400000                                      │
│  ┌──────────────────────────────────────┐        │
│  │ 元组 A（新版）xmin=1001 ← live       │        │
│  │ 元组 B（新版）xmin=1001 ← live       │        │
│  └──────────────────────────────────────┘        │
└──────────────────────────────────────────────────┘
```

**对 continuous_events 的影响：**

每天 40 万行 UPDATE，意味着每天产生 **40 万个死元组**。如果 VACUUM 不及时，这些死元组会：
1. **占用堆表空间** → 表膨胀 → 47GB 包含大量 dead tuple 的占比
2. **破坏 VM** → 标记过 xmax 的页不再"全部可见"
3. **浪费 shared_buffers** → 缓存里存了大量死数据

---

### 4.4 VACUUM 机制详解

**VACUUM 做什么：**

```
1. 扫描堆表的所有页
2. 对每个页中的 dead tuple：
   - 清除 xmax 标记
   - 将元组空间回收，标记为可重用
   - 如果整页都是 dead tuple → 页归还给文件系统（truncate）
3. 同时清理索引中对应的死条目
4. 更新 Visibility Map → 标记哪些页全部可见
5. 更新 pg_stat_user_tables 中的 n_dead_tup → 0
6. （VACUUM ANALYZE）同时收集统计信息
```

**autovacuum 触发条件：**

PG 自动触发 VACUUM 的条件是同时满足：

```
dead_tup >= autovacuum_vacuum_threshold 
          + autovacuum_vacuum_scale_factor × live_tup
```

默认值：
- `autovacuum_vacuum_threshold = 50`
- `autovacuum_vacuum_scale_factor = 0.2`（20%）

**对你的表：** 50 + 0.2 × 38,560,000 ≈ 770 万死元组才会自动触发 autovacuum。你每天 40 万 UPDATE，按默认配置需要 **19 天** 才能触发一次 autovacuum。这就是为什么你选择手动定时 VACUUM——但如果只跑一次/天，两次 VACUUM 之间仍然积累了 40 万死元组，足以让 VM 大面积失效。

**VACUUM 的局限性：**
- VACUUM 回收空间后不会缩小文件——空间留给后续 INSERT/UPDATE 重用
- 要真正缩小文件，需要 `VACUUM FULL`（锁表重写），会阻塞所有读写
- VACUUM 只清理 dead tuple，不影响 live tuple 的物理分布

**为什么你每天 VACUUM 仍无法解决 VM 问题：**

你的表 40 万行/天 UPDATE，每天 VACUUM 一次意味着：
- VACUUM 跑完的那一刻 → 所有 dead tuple 被清理 → VM 全表恢复"全部可见"
- 但 10 分钟后定时任务执行，又有 1 万行被 UPDATE → 1 万个页被标记 dead tuple → 这 1 万个页的 VM 失效
- 24 小时积累 → 40 万个页的 VM 失效（**不管每页有几行，40 万次 UPDATE 就是 40 万个页**）
- 下次 VACUUM 之前，这 40 万个页上的 Index Only Scan 全部必须回表

**关键认知：VM 失效的页数 = UPDATE 触及的独立页数，不受行宽影响。** 窄表拆不拆分，40 万 UPDATE 都是 40 万个页被污染。真正导致 VACUUM 不够用的是 UPDATE 频率，不是行宽。解决之道是提高 VACUUM 频率（如每天→每 4 小时），而非靠拆分表减少波及页数。

---

### 4.5 Visibility Map 深度解析

**VM 的存储与粒度：**

Visibility Map 存储在独立的 `_vm` 文件中，每个页占用 **2 比特**（bits 不是 bytes）：
- bit 0：该页中所有元组是否对所有事务可见
- bit 1：VACUUM 是否已将该页中所有 dead tuple 冻结

粒度是**页级别**——只要页中有一行不可见，整页的 VM bit 就是 0。

**为什么你的表 VM 大面积失效：**

```
页 #100：
  元组 1: live (xmax=0)
  元组 2: dead (xmax=1001) ← 被昨天的 UPDATE 标记
  元组 3: live (xmax=0)
  元组 4: live (xmax=0)
  元组 5: dead (xmax=1002)
  元组 6: live (xmax=0)

→ 因为有 dead tuple，该页 VM 标记 = 0（不可全部可见）
→ Index Only Scan 扫到这个索引条目时必须回表检查
```

**行宽对 VM 的真实影响（重要修正）：**

```
40 万次 UPDATE/天 = 40 万个不同的行被修改 = 40 万个独立的页被污染

拆分前（6 行/页，共 640 万页）：40 万 ÷ 640 万 = 6.25% 的页 VM 失效
拆分后（30 行/页，共 130 万页）：40 万 ÷ 130 万 = 30.8% 的页 VM 失效

从 VM 失效比例看，窄表反而更差！
```

**行宽不影响"有多少页被 UPDATE 污染"，只影响"污染页占总页数的比例"。** 窄表 VM 失效比例更高，但因为表小、全在内存，每次 Heap Fetch 的成本从磁盘 10ms 降到内存 0.01ms，总耗时仍然大幅下降。

**真正改善 VM 的方式不是缩小行宽，而是：**
1. 提高 VACUUM 频率（每天 → 每 4-6 小时）
2. 拆分后表变小 → VACUUM 跑得更快（8GB 的表 VACUUM 可能只要 1-2 分钟）→ 更频繁执行不会影响业务
3. 如果 `judge_status` 更新最频繁，可考虑拆分到独立小表以减少对主表 VM 的破坏

---

### 4.6 B-tree 索引原理

**B-tree 结构：**

```
                     ┌───────────┐
                     │   根节点    │  (1 个页)
                     │ [100, 200] │
                     └──┬──┬──┬──┘
                 ┌──────┘  │  └──────┐
                 │         │         │
          ┌──────▼──┐ ┌───▼───┐ ┌───▼──────┐
          │ 内部节点  │ │内部节点│ │  内部节点  │  (中间层)
          │[10, 50] │ │[150]  │ │ [250,300]│
          └──┬──┬───┘ └───┬───┘ └──┬──┬───┘
      ┌──────┘  │         │        │  └──────┐
 ┌────▼──┐ ┌───▼───┐ ┌──▼──┐ ┌───▼──┐ ┌───▼───┐
 │叶子节点│ │叶子节点│ │叶子  │ │叶子  │ │叶子节点│  (叶子层)
 │ctid,  │ │ctid,  │ │节点  │ │节点  │ │ctid,  │
 │ctid.. │ │ctid.. │ │      │ │      │ │ctid.. │
 └───────┘ └───────┘ └──────┘ └──────┘ └───────┘
```

- **根节点和内部节点**：只存键值范围 + 指向子节点的指针，帮助快速导航
- **叶子节点**：存索引键值 + 指向堆表中对应行的 **ctid**（页号, 行偏移）
- 叶子节点之间通过**双向链表**连接，支持范围扫描（`ORDER BY` 直接利用这个链表，无需额外排序）

**以你的 `idx_ce_judge_endtime_eventid_new(end_time DESC, event_id, judge_status, up_bytesall DESC)` 为例：**

```
叶子节点内存的结构（按 end_time DESC, event_id 排序）：
┌────────────────────────────────────┐
│ end_time=1783490438,               │
│ event_id=99999,                    │
│ judge_status=1,                    │
│ up_bytesall=2000000,               │
│ → ctid=(6400000, 5)               │
├────────────────────────────────────┤
│ end_time=1783490437,               │
│ event_id=100000,                   │
│ judge_status=2,                    │
│ up_bytesall=1500000,               │
│ → ctid=(6399999, 3)               │
├────────────────────────────────────┤
│ ...                               │
└────────────────────────────────────┘
```

**查询 `WHERE end_time BETWEEN ... ORDER BY end_time DESC LIMIT 50` 的过程：**

1. 从根节点向下查找，定位到 end_time 范围内的第一个叶子节点
2. 顺着叶子节点的双向链表往后读
3. 读到 50 个匹配行 → 通过 ctid 回表取完整行数据 → 完成

**这就是 Index Scan 只扫 50 行的原因**——不需要遍历整个索引，找到够用就停。

**索引深度：** B-tree 的查找复杂度是 O(log n)。3856 万行的表，B-tree 深度通常在 3-4 层（取决于索引键的大小），每次等值查找只需读 3-4 个页。

---

### 4.7 索引扫描方式详解

PostgreSQL 根据行数估算选择不同的扫描策略：

```
结果集大小：
  极小（< 千行）  → Index Scan
        │
  中等（千~十万） → Bitmap Index Scan + Bitmap Heap Scan
        │
  大（> 数十万）  → Sequential Scan（全表扫描，不通过索引）
```

#### 4.7.1 Index Scan

直接按索引顺序扫描，读一行索引条目 → 回表读一行堆数据 → 交下一行。

```
Index Scan：
  idx 页1 → ctid(100, 3) → heap 页100 → 过滤 → 返回
  idx 页2 → ctid(500, 7) → heap 页500 → 过滤 → 返回
  idx 页3 → ctid(10, 2)  → heap 页10  → 过滤 → 返回
  ...
```

**优点：** 不需要排序，首个结果延迟低（读到第一行就返回）；能提前终止（配合 LIMIT）。  
**缺点：** IO 模式是随机的（索引顺序 ≠ 物理顺序），大量数据时磁盘寻道开销大。

你的无 COUNT 查询走 Index Scan → 只需 50 行 → 0.35 ms。

#### 4.7.2 Index Only Scan

Index Only Scan 与 Index Scan 的唯一区别是：**如果 SELECT 的列全部在索引中，且 VM 标记该页全部可见，则不需要回表读堆数据**。

```
Index Only Scan：
  idx 页1 → 索引中有所有需要的列 + VM 标记"可见" → 直接返回（不读 heap）
  idx 页2 → 索引中有所有需要的列 + VM 标记"可见" → 直接返回
  ...
```

**Index Only Scan 的前提必须同时满足：**
1. `SELECT` 的所有列都在索引中（或索引表达式能计算出查询所需值）
2. 该行所在的数据页，在 VM 中标记为"全部可见"

**当条件 2 不满足时：** 产生 Heap Fetch——PG 必须回堆表读取该行，检查 xmin/xmax 以确认可见性。这就是 `Heap Fetches: 5,330,090` 的来源。

你的 COUNT 查询 `SELECT COUNT(*) FROM continuous_events WHERE ...` → 索引 `idx_ce_default_count_time(end_time, up_bytesall, judge_status)` 已覆盖所有 WHERE 列 → 理论上完美走 Index Only Scan。但实际上因为 VM 大面积失效，每条索引条目都要回表 → 533 万次 Heap Fetches → 6.8 秒。

#### 4.7.3 Bitmap Index Scan + Bitmap Heap Scan

这是两阶段扫描：

**阶段 1 - Bitmap Index Scan：**
扫描索引，把所有匹配行的 ctid 收集到一个内存中的位图。

**阶段 2 - Bitmap Heap Scan：**
把位图中的 ctid 按**页号排序**，然后按页号顺序读取堆表。如果多个匹配行在同一页中，读一次页就能拿多行。

```
Bitamp Index Scan：
  idx 扫描 → 收集 ctid → 位图 = { (100,3), (500,7), (100,7), ... }

Bitmap Heap Scan：
  页 100 → 一次性读取元组 3 和元组 7
  页 500 → 读取元组 7
  ...（按页号顺序）
```

**优点：** 把随机 IO 变成顺序 IO（按页号排序），减少磁盘寻道。  
**缺点：** 需要等全部索引条目收集完才开始吐数据；如果候选行太多导致位图超出 work_mem → Lossy 退化。

#### 4.7.4 Lossy Bitmap 退化

当候选行太多、位图超出了 `work_mem` 时：

```
正常工作（exact）：
  位图: [页100的行3=✓, 页100的行7=✓, 页500的行7=✓, ...]
  读取时精确知道要读页的哪些行

退化（lossy）：
  位图: [页100=?, 页500=?, 页234=? ...]  ← 只知页号，不知行号
  读取时整页加载 → 逐行 recheck 是否匹配 WHERE 条件
```

**你的生产环境：** 70.8 万行候选 × 每行 6 字节位图开销 ≈ 4.3MB，刚好刚好撑爆默认的 4MB work_mem → lossy → 扫描了 640 万行 recheck → 消耗 22 秒。

#### 4.7.5 Sequential Scan（全表扫描）

直接跳过索引，按页号顺序从第 0 页读到最后一页，每行检查 WHERE 条件。

**何时使用：** 优化器估算认为大部分行都满足条件（即"选择性低"），与其随机 IO 读索引再回表，不如顺序读全表。你的 `src_ip` 查询如果没有对应索引，几十秒甚至几分钟的全表扫描是典型表现。

#### 4.7.6 各扫描方式对比总结

| 扫描方式 | IO 模式 | 启动延迟 | 能否提前终止 | 适合场景 |
|---|---|---|---|---|
| Index Scan | 随机 IO | 低（首行即出） | ✅（配合 LIMIT） | 极小结果集 |
| Index Only Scan | 随机 IO（不读堆表） | 低 | ✅ | 小结果集 + VM 有效 |
| Bitmap Heap Scan | 顺序 IO | 高（等全部收集完） | ❌ | 中等结果集 |
| Sequential Scan | 顺序 IO | 低 | 不适用 | 大结果集（全表扫） |

---

### 4.8 Limit 下推原理

PostgreSQL 优化器在生成执行计划时，会尝试将上层的 LIMIT 节点"下推"到扫描节点：

```
原始 SQL:
  SELECT ... FROM t WHERE ... ORDER BY col LIMIT 50;

优化前：
  Limit(50)
    └── Sort(col)
          └── Scan(全部行)

优化后（Limit 下推）：
  Limit(50)
    └── Sort(col)
          └── Limit(50)          ← LIMIT 下推到 Scan 层
                └── Index Scan(col)  ← 扫到 50 行就停
```

**哪些操作阻止 Limit 下推：**

| 操作 | 是否阻止 | 原因 |
|---|---|---|
| `ORDER BY + LIMIT` | 不阻止 | Index Scan 按索引序输出，天然支持提前终止 |
| `DISTINCT` | 阻止 | 去重需要比较所有行 |
| `GROUP BY` | 阻止 | 聚合需要全量输入 |
| `HAVING` | 阻止 | 需要在聚合后过滤 |
| `UNION / INTERSECT` | 阻止 | 集合操作需要比较两个子查询的全部结果 |
| **`COUNT(*) OVER()` 等窗口函数** | **阻止** | 窗口函数语义要求对整个 partition 计算，无法提前终止 |
| `ORDER BY ... LIMIT` 在子查询中 | 取决于外层 | 如果子查询被合并到外层，Limit 仍可能下推 |

**为什么窗口函数阻止 LIMIT 下推：**

```
-- 反例：LIMIT 不能让 Index Scan 提前停
SELECT *, COUNT(*) OVER() FROM t WHERE ... ORDER BY ... LIMIT 50;

问题：
  如果 Index Scan 扫到 50 行就停 → COUNT(*) 的值 = 50（而非真实总数 76,309）
  
  PG 优化器知道这个二义性 → 保守处理 →
  不允许 LIMIT 下推 → Index Scan 必须扫完所有匹配行
```

---

### 4.9 TOAST 机制

**TOAST（The Oversized-Attribute Storage Technique）：**

当一行的数据超过约 2KB（实际上是 2,000 字节左右，由编译参数决定），PostgreSQL 不会强行把整行塞进一个 8KB 页（根本塞不下），而是将大字段**自动迁移到独立的 TOAST 表**。

```
原始行在堆表中：
  event_id | src_ip | end_time | ... | related_alerts        | ...
  ─────────┼────────┼─────────┼─────┼───────────────────────┼────
  12345    │ 1.2.3.4│ 1783... │     │ [TOAST 指针]          │ ...

TOAST 指针指向：
  pg_toast.pg_toast_XXXXXXX 表：
  ┌──────────────────────────────────────────┐
  │ chunk_id=12345, chunk_seq=0, data: "abc.."│
  │ chunk_id=12345, chunk_seq=1, data: "def.."│
  │ chunk_id=12345, chunk_seq=2, data: "ghi.."│
  └──────────────────────────────────────────┘  ← 可能跨多个数据页
```

**四种 TOAST 策略：**

| 策略 | 行为 |
|---|---|
| `PLAIN` | 不允许压缩，不能超过页大小（int, bool 等） |
| `EXTENDED` | **默认**：先尝试压缩，压缩后仍太大则存 TOAST 表 |
| `EXTERNAL` | 不压缩，但存 TOAST 表（适合已压缩的数据如 jpg） |
| `MAIN` | 先尝试压缩，压缩后仍太大则存 TOAST 表（但不允许跨多页） |

**对 continuous_events 的影响：**

- `related_alerts` 部分行超过 10KB → 必须走 TOAST
- 每次 SELECT `related_alerts` 时：
  - 主表读到 TOAST 指针
  - 再到 TOAST 表读取实际数据 → **多一次（或多次）IO**
- 列表查询如果 `SELECT *` 包含了 TOAST 字段，即使只展示 50 行，每行都要多读几次 TOAST 表
- **TOAST 表有自己的 VM**，同样存在失效问题

---

### 4.10 work_mem 详解

**work_mem 控制哪些操作的内存：**

| 操作 | 使用 work_mem 方式 | 超出后行为 |
|---|---|---|
| **Sort** (`ORDER BY`, `GROUP BY`) | 内存快速排序 | 溢出到磁盘（外部排序），产生临时文件 |
| **Hash** (`hash join`, `hash aggregate`) | 哈希表存储 | 分批次（multiple batches），每批写入磁盘临时文件 |
| **Bitmap** (`Bitmap Heap Scan`) | 位图存储 | **Lossy 退化**——只存页号，不存行号 |
| **Merge Join** | 归并缓冲区 | 磁盘溢出 |
| **Materialize** | 物化结果集 | 磁盘溢出 |

**work_mem 是 per-operation，不是 per-query！**

```
一条 SQL 内部可能同时有：
  Sort (ORDER BY)         → 消耗 1 份 work_mem
  Sort (GROUP BY)         → 消耗 1 份 work_mem
  Hash Join (构建哈希表)   → 消耗 1 份 work_mem
  Bitmap Heap Scan        → 消耗 1 份 work_mem
  ───────────────────────────────────
  总共可能消耗 N × work_mem（N = 同时运行的操作数）
```

**业务影响：**

| 值 | Bitmap 行为 | Sort 行为 | 并发风险 |
|---|---|---|---|
| 4MB（默认） | 70 万行 → lossy 退化 | 小数据集够用 | 低 |
| 64MB | 70 万行 → exact | 中等数据集够用 | 中（×10 并发 = 640MB） |
| 256MB | 百万行 → exact | 大数据集内存排序 | 高（×10 并发 = 2.5GB） |

**最佳实践：**
- 用 `SET LOCAL` 而非全局修改——事务结束自动释放
- 不要盲目设大值——每 SQL 多个 operation 共用多份
- 先 EXPLAIN 确认是否需要（只有 Bitmap Heap Scan / Sort / Hash 节点才消费 work_mem）

---

### 4.11 shared_buffers 与缓存命中

**shared_buffers：** PostgreSQL 的共享内存缓冲区，用于缓存数据页和索引页。所有连接共享这一块内存。

```
查询执行流程：
  1. SELECT 请求
  2. PG 先检查 shared_buffers：数据页在吗？
     ├─ 在（Buffer Hit / 缓存命中）→ 直接从内存返回（微秒级）
     └─ 不在（Buffer Miss）→ 从磁盘读取到 shared_buffers → 返回（毫秒级）
```

**你的表对 shared_buffers 的压力：**

| 数据 | 大小 |
|---|---|
| 堆表 | 47 GB |
| 4 个索引 | 约 3-5 GB（估算） |
| 总数据量 | ~50 GB |
| 典型 shared_buffers | 4-16 GB |

**如果 shared_buffers = 8GB：**
- 只能缓存 8/50 ≈ 16% 的数据
- 84% 的查询需要从磁盘读取 → **线上比开发环境慢 10 倍的原因之一**
- 开发环境数据量小、全在内存 → 快速；线上 47GB、大量不在内存 → 慢

**优化方向：**

| 方向 | 效果 | 成本 |
|---|---|---|
| 调大 shared_buffers | 提高缓存命中率 | 占用系统内存（建议不超过 RAM 的 25%） |
| 缩小表大小（拆分） | 同样的 shared_buffers 能缓存更高比例数据 | 一次 DDL |
| 预热 | 将热点索引页提前加载到 shared_buffers | `pg_prewarm` 扩展 |

---

### 4.12 执行计划关键节点解读

| 节点 | 含义 | 出现在哪 | 慢的标志 |
|---|---|---|---|
| **Seq Scan** | 全表扫描，从第 0 页读到最后一页 | 无合适索引时 | rows 大 + 实际耗时高 |
| **Index Scan** | 按索引顺序扫描，逐个回表 | 有索引且估算行数少 | rows 大（全扫） |
| **Index Only Scan** | 同上，但不回表（需要 VM 有效） | 索引覆盖所有 SELECT 列 + VM 有效 | Heap Fetches 大 |
| **Bitmap Index Scan** | 扫描索引收集 ctid 到位图 | 中等结果集，配合下方 Bitmap Heap Scan | 单独看耗时低 |
| **Bitmap Heap Scan** | 按位图中排序的页号读堆表 | 中等结果集 | lossy + Recheck 大 |
| **CTE Scan** | 读取 WITH 子查询的物化结果 | CTE (WITH ... AS) | 如果 CTE 物化了大量宽行 |
| **Hash Join** | 对一侧构建哈希表，另一侧探测 | 等值 JOIN | 哈希表分多批次（Batches > 1） |
| **Nested Loop** | 外表每行查内表一次 | 小外表 + 内表有索引 | 外表行数 × 内表查找耗时 |
| **Merge Join** | 两边先排序再归并 | 大表等值 JOIN | 排序溢出到磁盘 |
| **Sort** | 排序操作 | ORDER BY, GROUP BY, DISTINCT | Method: external merge（磁盘排序） |
| **HashAggregate** | 哈希去重/聚合 | GROUP BY, DISTINCT | Batches > 1 |
| **WindowAgg** | 窗口函数计算 | COUNT(*) OVER(), ROW_NUMBER() 等 | 本身很快，但阻止下层提前终止 |
| **Limit** | 限制返回行数 | LIMIT N | 如果无法下推，则等价于没加 |
| **SubPlan** | 子查询，每次执行时重新计算 | 关联子查询 | 循环次数 × 每次耗时 |

---

### 4.13 pg_stats 统计信息

PostgreSQL 通过 `ANALYZE` 收集表和索引的统计信息，存储在 `pg_stats` 视图中。优化器依赖这些统计信息来估算行数和选择执行计划。

**关键字段：**

| 字段 | 含义 | 如果失真 |
|---|---|---|
| `n_distinct` | 该列的唯一值个数（负数 = 比例） | 选择性估算偏差，可能选错索引 |
| `most_common_vals` | 最常见值的列表 | IN 条件估算不准 |
| `most_common_freqs` | 对应频率 | 同上 |
| `histogram_bounds` | 直方图边界（等高分桶） | 范围查询估算不准 |
| `avg_width` | 列平均宽度（字节） | 行宽估算偏差，可能导致 work_mem 不足 |
| `null_frac` | NULL 比例 | IS NULL / IS NOT NULL 估算偏差 |
| `correlation` | 物理顺序与索引顺序的相关性（-1 到 1） | 影响 Index Scan 的 cost 估算 |

**为什么有时优化器选择"次优"计划：**

当你看到执行计划里 `rows=7`（估算）而 `actual rows=61`（实际）时，说明统计信息已经失真。原因可能：
- 上次 ANALYZE 后大量数据变更
- 默认采样比例不够（`default_statistics_target = 100`）
- 没有列级扩展统计（多列相关性，如 `judge_status` 和 `end_time` 的相关分布）

**建议：**
```sql
-- 每次大批量 UPDATE 后
ANALYZE internal_app_bsa_gjk.continuous_events;

-- 提高关键列的采样精度
ALTER TABLE internal_app_bsa_gjk.continuous_events
  ALTER COLUMN judge_status SET STATISTICS 1000;
ALTER TABLE internal_app_bsa_gjk.continuous_events
  ALTER COLUMN end_time SET STATISTICS 1000;
```

---

### 4.14 核心概念关联图

```
                    你的问题的因果链
                    ═══════════════

宽表（41列 / 1.3KB/行 / 每页6行 / 47GB）
    │
    ├─→ 高频UPDATE（40万行/天）
    │       │
    │       ├─→ 大量dead tuples（每天40万个 → 40万个页被污染）
    │       │       │
    │       │       ├─→ VM大面积失效（40万页 VM bit=0）
    │       │       │       │
    │       │       │       └─→ Index Only Scan 失效 → Heap Fetches 达 533 万次
    │       │       │               │
    │       │       │               └─→ COUNT(*) 从 200ms 变成 6.8 秒
    │       │       │
    │       │       └─→ 每天只 VACUUM 1 次 → 间隔太长 → 累积 40 万死元组才清理
    │       │               │
    │       │               └─→ 解决：拆表后表变小 → VACUUM 更快 → 可提频到每 4-6h 一次
    │       │
    │       └─→ UPDATE 修改 end_time/up_bytesall → 索引条目变更 → 索引膨胀
    │
    ├─→ 表太大（47GB）→ 缓存命中率低
    │       │
    │       ├─→ Index Scan 扫 76 万行大部分不在 shared_buffers → 磁盘 IO
    │       │       │
    │       │       └─→ 解决：拆表后 8GB → 高比例缓存 → 扫描从磁盘变内存
    │       │
    │       └─→ Heap Fetches 每 fetch 一次都可能是磁盘随机 IO（10ms/次）
    │               │
    │               └─→ 解决：8GB 表 → 大部分在内存 → fetch 成本降到 0.01ms
    │
    ├─→ Bitmap 候选行太多(70万) + work_mem=4MB
    │       │
    │       └─→ lossy 退化 → Recheck 640 万行 → 22 秒
    │
    └─→ COUNT(*) OVER() 阻止 Limit 下推
            │
            └─→ Index Scan 必须扫 76 万行（24 秒）而非 50 行（4ms）
            │
            └─→ 解决：COUNT 拆出单独查询，数据查询 4ms 返回

核心总结：
  拆表不减少 VM 失效页数（40 万 UPDATE = 40 万个页，不变）
  拆表减少的是每次扫描的数据量和 IO 成本（47GB → 8GB = 全在内存）
  拆表后 VACUUM 更快 → 可以更频繁执行（关键！这才是减少 VM 失效累积的方式）
```

---

## 5. 相关 SQL 速查

```sql
-- 查看表统计信息
SELECT n_live_tup, n_dead_tup, last_vacuum, last_autovacuum,
       pg_size_pretty(pg_total_relation_size('continuous_events')) AS total_size
FROM pg_stat_user_tables WHERE relname = 'continuous_events';

-- 查看每列平均大小（线上 pg_stats）
SELECT a.attname, coalesce(s.avg_width, 0) AS avg_bytes
FROM pg_class c
JOIN pg_attribute a ON a.attrelid = c.oid
LEFT JOIN pg_stats s ON s.tablename = c.relname AND s.attname = a.attname
WHERE c.relname = 'continuous_events' AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY avg_bytes DESC;

-- 当前会话设置 work_mem
SET LOCAL work_mem = '64MB';

-- VACUUM + 分析
VACUUM ANALYZE internal_app_bsa_gjk.continuous_events;

-- 建索引（不锁表）
CREATE INDEX CONCURRENTLY idx_ce_srcip_endtime
ON continuous_events (src_ip, end_time DESC, up_bytesall DESC);
```

---

## 6. 终极方案总结

### 6.1 问题因果链全景

```
宽表(41列/47GB) + 高频UPDATE(40万/天) + work_mem=4MB
    │
    ├──① COUNT(*) OVER() 阻止 Limit 下推 → Index Scan 必须全扫 73 万行
    │        │
    │        └── 解法：拆 COUNT，数据查询 4ms，COUNT 独立跑
    │
    ├──② 47GB 表 shared_buffers 缓存率 ~15% → 每行 Heap Fetch 走磁盘 IO
    │        │
    │        └── 解法：垂直拆分，主表 47GB→8GB，缓存率→80%+
    │             └── 附带收益：VACUUM 从 15min→2min，可提频到每 4-6h
    │
    ├──③ src_ip/dst_ip 不在任何索引中 → 带 IP 查询扫 76 万行
    │        │
    │        └── 解法：加两个精准索引
    │
    └──④ Bitmap Heap Scan + work_mem=4MB → lossy 退化 → Recheck 640 万行
             │
             └── 解法：查询前 SET LOCAL work_mem = '64MB'
```

### 6.2 四支箭详解

#### 箭一：拆 COUNT（代码层）

**作用**：让数据查询不再被 COUNT 拖慢。LIMIT 50 下推到 Index Scan，找到 50 行即停。

```sql
-- 数据查询：4ms 返回 50 行（不管有没有 src_ip/dst_ip）
SELECT e.*, t.*, cet.*
FROM continuous_events e
LEFT JOIN ...
WHERE end_time >= ... AND judge_status IN (...)
ORDER BY end_time DESC LIMIT 50;

-- COUNT 查询：独立跑
SELECT COUNT(*) FROM continuous_events
WHERE end_time >= ... AND judge_status IN (...);
```

**风险**：零。不涉及数据库变更，纯代码层修改。
**收益**：用户感知从 24 秒 → 4ms，count 数字稍后跟上。

#### 箭二：垂直拆分表（数据库层）

**作用**：缩小主表体积，提升缓存命中率，让 VACUUM 可以提频。

**操作**：
1. 建明细表 `continuous_events_detail`
2. 分批迁移 16 个大字段（8 个 text + 8 个 varchar）
3. 主表删字段（含 8 个确定无用的字段）
4. `VACUUM FULL` 回收空间

**拆走字段**（移入明细表）：`related_alerts`、`judge_info`、`judge_file`、`src_com`、`dst_com`、`src_operator`、`dst_operator`、`key_unit`

**删除字段**（确定无用）：`src_threat_mark`、`dst_threat_mark`、`src_info`、`dst_info`、`iot_tag`、`key_service_tag`、`key_unit_tag`、`app_type`

| 指标 | 拆分前 | 拆分后 |
|---|---|---|
| 主表大小 | 47 GB | ~8 GB |
| shared_buffers 缓存率 | ~15% | **~80%+** |
| 索引扫描 IO | 磁盘 IO | **内存 IO** |
| Heap Fetch 单次成本 | 10ms（磁盘） | **0.01ms（内存）** |
| VACUUM 耗时 | 8-15 分钟 | **1-2 分钟** |
| VACUUM 可提频 | 1 次/天 | **4-6 次/天** |
| COUNT 耗时 | 6.8 秒 | **< 200 ms** |

**注意**：拆非索引字段不直接减少 VM 失效页数（40 万 UPDATE = 40 万个页被污染，不变），但通过以下机制间接加速：
- 表缩小 → VACUUM 快 5-10 倍 → 可提频到每 4-6 小时 → VM 失效窗口从 24h 缩到 4h
- 表缩小 → shared_buffers 缓存率 80%+ → 同样 533 万次 Heap Fetch 从磁盘变内存 → 每次成本差 1000 倍
- 列表查询不碰明细表 → `related_alerts` 个别 >10KB 的大行不再拖慢列表

**风险**：中等。`VACUUM FULL` 需要短暂锁表（安排窗口期）。详情页查询需要加一次 JOIN（通过 event_id，走 PK 索引，微秒级）。

#### 箭三：加 IP 索引（数据库层）

```sql
CREATE INDEX CONCURRENTLY idx_ce_srcip_endtime
ON continuous_events (src_ip, end_time DESC, up_bytesall DESC);

CREATE INDEX CONCURRENTLY idx_ce_dstip_endtime
ON continuous_events (dst_ip, end_time DESC, up_bytesall DESC);
```

**作用**：让带 `src_ip` 或 `dst_ip` 的查询从几十秒 → 几毫秒。

**风险**：低。建索引期间 IO 和 CPU 升高，逐个建、低峰操作。写入侧开销可忽略（40 万行/天 × 6 个索引 = 峰值 < 20ms/s）。

#### 箭四：work_mem 按查询设定（代码层）

```sql
SET LOCAL work_mem = '64MB';
```

**作用**：Bitmap 从 lossy 退化变 exact，Recheck 640 万行 → 0。

**风险**：零。`LOCAL` 作用域仅当前事务，事务结束自动释放，不影响其他连接。

### 6.3 实施优先级与预期效果

| 优先级 | 动作 | 风险 | 上线方式 | 预期效果 |
|---|---|---|---|---|
| **P0** | 拆 COUNT + SET work_mem | 零 | **立即上线** | 用户感知 24s→4ms |
| **P0** | 加 src_ip/dst_ip 索引 | 低 | 低峰期逐个建 | IP 查询几十秒→几 ms |
| **P1** | 垂直拆分表 | 中 | 安排窗口期 | COUNT 6.8s→<200ms，根治缓存/VACUUM 问题 |
| **P1** | VACUUM 提频（每天→每 4-6h） | 零 | 拆表后自动可行 | VM 失效窗口 24h→4h |

### 6.4 各方案执行后的预期耗时对比

| 查询场景 | 当前 | P0 完成后 | P1 完成后 |
|---|---|---|---|
| 默认列表（无 IP，有 COUNT） | 24.5 秒 | 4 ms（数据）+ 6.8s（COUNT） | 4 ms（数据）+ <200ms（COUNT） |
| src_ip 查询（有 COUNT） | 几分钟 | 几 ms（数据）+ 6.8s（COUNT） | 几 ms（数据）+ <200ms（COUNT） |
| dst_ip 查询（有 COUNT） | 几分钟 | 几 ms（数据）+ 6.8s（COUNT） | 几 ms（数据）+ <200ms（COUNT） |
| 纯 COUNT 查询 | 6.8 秒 | 6.8 秒 | **< 200 ms** |

### 6.5 一句话

> **终极方案 = 拆 COUNT（让数据 4ms 返回）+ 拆表（让 COUNT 从 6.8s→200ms）+ 加 IP 索引（让 IP 查询从几十秒→几 ms）+ SET work_mem（让 Bitmap 不再 lossy 退化）。** 四箭齐发，各管因果链上的一段。P0 今天就能上线，P1 安排窗口期即可根治。`
