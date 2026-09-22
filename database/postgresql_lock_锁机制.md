# PostgreSQL 锁机制参考文档

> 适用于 PostgreSQL 9.6+，结合 CNCERT 生产环境实际场景整理。

---

## 一、表级锁模式（8 级，从弱到强）

PostgreSQL 的表级锁按强度从弱到强分为 8 个级别。**高级别锁兼容所有比它弱的锁，但与同级或更高级别的锁冲突。**

| 优先级 | 锁模式 | 典型触发操作 | 冲突说明 |
|:------:|--------|-------------|----------|
| 1（最弱） | `ACCESS SHARE` | `SELECT` | 只与 `ACCESS EXCLUSIVE` 冲突 |
| 2 | `ROW SHARE` | `SELECT FOR UPDATE` / `SELECT FOR SHARE` | 与 `EXCLUSIVE`、`ACCESS EXCLUSIVE` 冲突 |
| 3 | `ROW EXCLUSIVE` | `INSERT` / `UPDATE` / `DELETE` | 与 `SHARE`、`SHARE ROW EXCLUSIVE`、`EXCLUSIVE`、`ACCESS EXCLUSIVE` 冲突 |
| 4 | `SHARE UPDATE EXCLUSIVE` | `VACUUM`（非 FULL）、`ANALYZE`、`CREATE INDEX CONCURRENTLY`、`ALTER TABLE ... VALIDATE CONSTRAINT` | 与同级别及以上冲突 |
| 5 | `SHARE` | `CREATE INDEX`（非 CONCURRENTLY）、`CREATE TRIGGER` | 与 `ROW EXCLUSIVE` 及以上冲突 |
| 6 | `SHARE ROW EXCLUSIVE` | 较少直接使用，部分 `ALTER TABLE` 子操作 | 与 `ROW EXCLUSIVE` 及以上冲突 |
| 7 | `EXCLUSIVE` | 罕见，仅 `REFRESH MATERIALIZED VIEW CONCURRENTLY` 等 | 与 `ROW SHARE` 及以上冲突，**只允许 ACCESS SHARE 并存** |
| 8（最强） | `ACCESS EXCLUSIVE` | `DROP`、`ALTER TABLE`、`TRUNCATE`、`REINDEX`、`CLUSTER`、`VACUUM FULL`、`DROP TRIGGER`、`LOCK TABLE` | **与所有锁模式冲突，包括最弱的 ACCESS SHARE** |

### 关键理解

- **"冲突"意味着互斥**：如果事务 A 持有锁 X，事务 B 申请的锁 Y 与 X 冲突，B 必须等 A 释放后才能拿到。
- **锁是会话级的**：事务提交或回滚后才释放，不是语句结束就释放。
- **SELECT 也要锁**：`SELECT` 会获取 `ACCESS SHARE` 锁，虽然很弱，但遇到 `ACCESS EXCLUSIVE`（如 DROP TRIGGER）时也要等待。

---

## 二、锁冲突矩阵

✅ = 兼容（可并发持有），❌ = 冲突（必须等前一个释放）

|  | ACCESS SHARE | ROW SHARE | ROW EXCL | SHARE UPDATE EXCL | SHARE | SHARE ROW EXCL | EXCLUSIVE | ACCESS EXCL |
|--|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **ACCESS SHARE** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| **ROW SHARE** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| **ROW EXCLUSIVE** | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| **SHARE UPDATE EXCL** | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **SHARE** | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **SHARE ROW EXCL** | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **EXCLUSIVE** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **ACCESS EXCLUSIVE** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

### 实用速查

| 场景 | 能否并发？ |
|------|:---:|
| SELECT ↔ SELECT | ✅ |
| SELECT ↔ INSERT/UPDATE/DELETE | ✅ |
| SELECT ↔ VACUUM | ✅ |
| SELECT ↔ CREATE INDEX CONCURRENTLY | ✅ |
| SELECT ↔ CREATE INDEX（非 CONCURRENTLY） | ✅ |
| SELECT ↔ DROP / ALTER / TRUNCATE | **❌** |
| INSERT/UPDATE/DELETE ↔ VACUUM | ✅ |
| INSERT/UPDATE/DELETE ↔ CREATE INDEX（非 CONCURRENTLY） | **❌** |
| INSERT/UPDATE/DELETE ↔ DROP / ALTER | **❌** |
| VACUUM ↔ CREATE INDEX CONCURRENTLY | ✅ |
| DROP TRIGGER ↔ 任何操作（含 SELECT） | **❌** |

---

## 三、行级锁模式（4 种）

行级锁通过 `SELECT ... FOR ...` 子句显式获取，或由 `UPDATE` / `DELETE` 隐式获取。

| 行锁模式 | 语法 | 阻止什么 | 允许什么 |
|----------|------|----------|----------|
| `FOR UPDATE` | `SELECT ... FOR UPDATE` | 其他事务的 UPDATE / DELETE / 所有 FOR 锁 | 无 |
| `FOR NO KEY UPDATE` | `SELECT ... FOR NO KEY UPDATE` | UPDATE / DELETE / FOR UPDATE / FOR NO KEY UPDATE | FOR SHARE / FOR KEY SHARE |
| `FOR SHARE` | `SELECT ... FOR SHARE` | UPDATE / DELETE / FOR UPDATE / FOR NO KEY UPDATE / FOR SHARE | FOR KEY SHARE |
| `FOR KEY SHARE` | `SELECT ... FOR KEY SHARE`（最弱行锁） | UPDATE / DELETE / FOR UPDATE | FOR NO KEY UPDATE / FOR SHARE / FOR KEY SHARE |

### 行级锁冲突矩阵

|  | FOR KEY SHARE | FOR SHARE | FOR NO KEY UPDATE | FOR UPDATE |
|--|:---:|:---:|:---:|:---:|
| **FOR KEY SHARE** | ✅ | ✅ | ✅ | ❌ |
| **FOR SHARE** | ✅ | ✅ | ❌ | ❌ |
| **FOR NO KEY UPDATE** | ✅ | ❌ | ❌ | ❌ |
| **FOR UPDATE** | ❌ | ❌ | ❌ | ❌ |

### 注意

- 行级锁不会阻塞不同行的操作，只锁住当前行。
- `UPDATE` 隐式获取 `FOR NO KEY UPDATE`（如果只改非键列）或 `FOR UPDATE`（如果改了主键/唯一键）。
- 行级锁在事务结束时释放，不是语句结束。
- MVCC 机制下，普通的 `SELECT` 不加任何行锁（读不阻塞写）。

---

## 四、锁队列与优先级机制

### 队列规则

PostgreSQL 的锁队列**不是严格 FIFO**，而是有优先级跳队：

1. **新来的锁请求**：如果与当前队列中**所有已持有锁和等待中的锁**都兼容，可以直接获取，不需要排队。
2. **如果冲突**：进入等待队列。
3. **队列中的锁**按请求顺序排队，但后续来的新请求如果与等待队列中的强锁冲突，也会排到它后面。

### 级联阻塞（最危险的场景）

```
时刻 T0: 事务 A 执行 SELECT（持有 ACCESS SHARE）
时刻 T1: 事务 B 执行 DROP TRIGGER（申请 ACCESS EXCLUSIVE，与 A 冲突 → 进入等待）
时刻 T2: 事务 C 执行 SELECT（申请 ACCESS SHARE，与 B 等待中的 ACCESS EXCLUSIVE 冲突 → 也进入等待！）
时刻 T3: 事务 D 执行 UPDATE（申请 ROW EXCLUSIVE，与 B 冲突 → 继续等待）
```

**结果**：虽然 A 和 C/D 之间本可以并发，但因为 B 在队列里等 ACCESS EXCLUSIVE，后面所有人都被堵住了。

这就是"DROP 动作在等待时表被锁死"的根本原因。

### 防范措施

```sql
-- DDL 操作前设置锁超时，避免无限等待拖死全表
SET lock_timeout = '5s';
-- 执行 DDL
DROP TRIGGER trigger_name ON table_name;
-- 如果 5 秒内拿不到锁，自动放弃，不会级联阻塞
RESET lock_timeout;
```

---

## 五、死锁检测

PostgreSQL 内置死锁检测器，默认每 `deadlock_timeout`（默认 1 秒）检查一次。

### 死锁产生条件

两个事务互相等待对方持有的锁，形成循环依赖：

```
事务 A: 锁住了行 1，等待行 2
事务 B: 锁住了行 2，等待行 1
→ 死锁，PostgreSQL 自动 kill 其中一个事务
```

### 触发后的表现

```
ERROR:  deadlock detected
DETAIL:  Process 12345 waits for ShareLock on transaction 67890;
         blocked by process 67891.
         Process 67891 waits for ShareLock on transaction 67892;
         blocked by process 12345.
```

被 kill 的事务会收到上述错误并回滚，另一个事务正常继续。

### 相关参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `deadlock_timeout` | 1s | 多久后开始死锁检测 |
| `log_lock_waits` | off | 设为 on 可记录锁等待日志 |

---

## 六、监控锁的实用 SQL

### 1. 查看当前所有锁等待

```sql
SELECT
    blocked.pid     AS blocked_pid,
    blocked.query   AS blocked_query,
    blocking.pid    AS blocking_pid,
    blocking.query  AS blocking_query,
    blocking.state  AS blocking_state,
    now() - blocking.query_start AS blocking_duration
FROM pg_stat_activity blocked
JOIN pg_stat_activity blocking
  ON blocking.pid = ANY(pg_blocking_pids(blocked.pid))
WHERE blocked.wait_event_type = 'Lock';
```

### 2. 查看表级锁持有情况

```sql
SELECT
    l.relation::regclass AS table_name,
    l.mode AS lock_mode,
    l.granted,
    a.pid,
    a.state,
    a.query,
    now() - a.query_start AS query_duration
FROM pg_locks l
JOIN pg_stat_activity a ON a.pid = l.pid
WHERE l.locktype = 'relation'
  AND l.relation::regclass::text LIKE '%continuous_events%'
ORDER BY l.granted, l.relation::regclass::text;
```

### 3. 查看被阻塞的会话及等待链

```sql
SELECT
    pid,
    state,
    wait_event_type,
    wait_event,
    now() - query_start AS wait_duration,
    left(query, 100) AS query
FROM pg_stat_activity
WHERE wait_event_type IS NOT NULL
  AND wait_event_type != 'Activity'
ORDER BY wait_duration DESC;
```

### 4. 找出 ACCESS EXCLUSIVE 锁（危险操作）

```sql
SELECT
    l.relation::regclass AS table_name,
    a.pid,
    a.state,
    now() - a.query_start AS duration,
    left(a.query, 100) AS query
FROM pg_locks l
JOIN pg_stat_activity a ON a.pid = l.pid
WHERE l.mode = 'AccessExclusiveLock'
  AND l.granted = true;
```

### 5. 查看某个表上的所有锁

```sql
SELECT
    l.pid,
    l.mode,
    l.granted,
    a.state,
    a.wait_event_type,
    a.wait_event,
    now() - a.query_start AS duration,
    left(a.query, 80) AS query
FROM pg_locks l
JOIN pg_stat_activity a ON a.pid = l.pid
WHERE l.relation = 'internal_app_bsa_gjk.continuous_events'::regclass
ORDER BY l.granted DESC, l.mode;
```

---

## 七、Advisory Lock（咨询锁）

应用层自定义锁，不锁任何数据，仅用于跨进程协调。

### 会话级（连接断开自动释放）

```sql
-- 获取
SELECT pg_advisory_lock(key);
SELECT pg_advisory_lock(key1, key2);

-- 释放
SELECT pg_advisory_unlock(key);
SELECT pg_advisory_unlock(key1, key2);

-- 尝试获取（不阻塞，拿不到返回 false）
SELECT pg_try_advisory_lock(key);
```

### 事务级（事务结束自动释放）

```sql
SELECT pg_advisory_xact_lock(key);
SELECT pg_try_advisory_xact_lock(key);
```

### 典型用途

- 防止并发任务重复执行（如归并任务分布式锁）
- 限流/排队
- 应用层互斥

---

## 八、DDL 操作与 CONCURRENTLY 的锁机制对比

### 普通 DDL（需 AccessExclusiveLock）

| 操作 | 锁级别 | 是否阻塞业务 |
|------|--------|:---:|
| `DROP TABLE` | ACCESS EXCLUSIVE | ✅ 阻塞所有 |
| `ALTER TABLE` | ACCESS EXCLUSIVE | ✅ 阻塞所有 |
| `TRUNCATE` | ACCESS EXCLUSIVE | ✅ 阻塞所有 |
| `DROP TRIGGER` | ACCESS EXCLUSIVE | ✅ 阻塞所有 |
| `DROP INDEX` | ACCESS EXCLUSIVE | ✅ 阻塞所有 |
| `REINDEX` | ACCESS EXCLUSIVE | ✅ 阻塞所有 |
| `CREATE INDEX`（非 CONCURRENTLY） | SHARE | 阻塞写，不阻塞读 |
| `VACUUM FULL` | ACCESS EXCLUSIVE | ✅ 阻塞所有 |

### CONCURRENTLY 操作（弱锁，不阻塞业务）

| 操作 | 锁级别 | 是否阻塞业务 | 特殊等待 |
|------|--------|:---:|----------|
| `CREATE INDEX CONCURRENTLY` | SHARE UPDATE EXCLUSIVE | ❌ | 等待旧 snapshot 释放 |
| `DROP INDEX CONCURRENTLY` | ACCESS EXCLUSIVE | ❌* | 分阶段释放锁 |
| `REINDEX CONCURRENTLY`（PG 12+） | SHARE UPDATE EXCLUSIVE | ❌ | 等待旧 snapshot 释放 |
| `ALTER TABLE ... VALIDATE CONSTRAINT` | SHARE UPDATE EXCLUSIVE | ❌ | — |

> *`DROP INDEX CONCURRENTLY` 内部分两个阶段，第一阶段的锁很短暂，不会长时间持有。

### CREATE INDEX CONCURRENTLY 的等待机制

CONCURRENTLY **不锁表**，但在两个阶段之间会等待持有旧 snapshot 的事务结束：

```
Phase 1: 扫表建索引 ──→ 等待点 1（等所有旧 snapshot 释放）──→ Phase 2: 捕获增量 ──→ 等待点 2（等所有旧 snapshot 释放）──→ 标记 valid
```

**阻塞源**：持有旧 snapshot 的**普通事务**（idle in transaction、长 SELECT、未提交的写事务）。

**不阻塞源**：VACUUM / autovacuum（使用特殊 vacuum snapshot，不参与 CONCURRENTLY 的 snapshot 等待）。

---

## 九、生产环境 DDL 最佳实践

### 1. DDL 前必做的检查

```sql
-- 检查目标表有无长事务
SELECT pid, state, now() - xact_start AS xact_duration,
       wait_event_type, left(query, 100) AS query
FROM pg_stat_activity
WHERE state IN ('active', 'idle in transaction')
  AND xact_start IS NOT NULL
  AND now() - xact_start > interval '5 minutes'
ORDER BY xact_start;
```

### 2. 所有 DDL 加 lock_timeout

```sql
SET lock_timeout = '5s';
-- DDL 操作
-- ...
RESET lock_timeout;
```

### 3. 高危 DDL 的替代方案

| 高危操作 | 替代方案 |
|----------|----------|
| `VACUUM FULL` | `pg_repack`（在线重建，不锁表） |
| `CLUSTER` | `pg_repack` |
| `REINDEX` | `REINDEX CONCURRENTLY`（PG 12+）或 `pg_repack` |
| `ALTER TABLE ADD COLUMN ... DEFAULT` | PG 11+ 已优化为不重写表，可直接执行 |
| `ALTER TABLE ... TYPE` | 需评估是否重写表，分步迁移更安全 |

### 4. 低峰期执行

对于无法用 CONCURRENTLY 替代的 DDL（如 DROP TRIGGER），挑业务低峰期（凌晨）执行，减少撞锁概率。

---

## 十、相关参数速查

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `deadlock_timeout` | 1s | 死锁检测间隔 |
| `lock_timeout` | 0（禁用） | 锁等待超时，超时后语句自动取消 |
| `statement_timeout` | 0（禁用） | 语句执行超时 |
| `idle_in_transaction_session_timeout` | 0（禁用） | idle in transaction 超时自动 kill，**建议设为 5-10 分钟** |
| `log_lock_waits` | off | 设为 on 记录锁等待日志 |
| `max_locks_per_transaction` | 64 | 每个事务最大锁数量 |

### 生产环境推荐配置

```ini
# 防止 idle in transaction 无限持有锁
idle_in_transaction_session_timeout = '5min'

# 记录锁等待，方便事后排查
log_lock_waits = on

# deadlock_timeout 调低一点，加快死锁检测
deadlock_timeout = '500ms'
```

---

## 附：CNCERT 生产环境实际案例

### 案例 1：CREATE INDEX CONCURRENTLY 被长事务阻塞

- **现象**：`CREATE INDEX CONCURRENTLY idx_ce_src_ip` 执行 18 小时未完成
- **根因**：有持有旧 snapshot 的普通长事务未结束，CONCURRENTLY 在等待点反复轮询
- **教训**：建索引前检查 `pg_stat_activity` 有无长跑事务（不限本表）；VACUUM/autovacuum 不是阻塞源

### 案例 2：DROP TRIGGER 等待时表被锁死

- **现象**：执行 DROP TRIGGER 后，表上所有后续 SELECT/UPDATE 都被阻塞
- **根因**：DROP TRIGGER 申请 ACCESS EXCLUSIVE，与已持有的 ACCESS SHARE（SELECT）冲突 → 进入等待 → 后续所有请求因队列优先级也被阻塞
- **教训**：DDL 前加 `SET lock_timeout = '5s'`，避免无限等待级联阻塞

---

> 文档版本：2026-08-14 | 基于 PostgreSQL 9.6 | 维护人：宝祥
