***

解决方案：短期和长期策略

短期方案：立即回收空间 (需要停机或有风险)

1.  `VACUUM FULL` (有停机风险)：这是最直接的方法，但也是最粗暴的。它会锁住整张表，禁止任何读写，然后将表重写为一个全新的、没有碎片的文件，将空间还给操作系统。对于1300万行的表，这
    个过程可能需要很长时间。

1     -- 警告：这将导致表在操作期间完全不可用！
2     VACUUM FULL your\_table\_name;

1.  `pg_repack` (推荐，需要安装扩展)：这是一个非常流行的PostgreSQL扩展，它可以在线重组表，基本不产生长时间的排他锁，对业务影响极小。这是生产环境处理表膨胀的首选方案。
    *   首先你需要在数据库中安装 pg\_repack 扩展。
    *   然后执行命令行工具：

1         pg\_repack -d your\_database\_name -t your\_table\_name

长期方案：预防膨胀

仅仅手动清理一次是不够的，你需要调整配置来预防问题再次发生。

1.  为高频更新表定制 `AUTOVACUUM` 参数：
    不要使用全局配置，为这张特定的表设置更激进的 AUTOVACUUM 策略。目标是让清理更频繁地发生。

<!---->

    1     -- 将 autovacuum 的触发阈值从默认的 20% 降低到 1% (或更低)

<!---->

    2     -- 1300万 * 1% = 13万行变化就会触发
    3     ALTER TABLE your_table_name SET (autovacuum_vacuum_scale_factor = 0.01);
    4
    5     -- 同时设置一个固定的阈值，比如每变化10000行就触发
    6     ALTER TABLE your_table_name SET (autovacuum_vacuum_threshold = 10000);
    7
    8     -- 也可以适当降低 analyze 的阈值，以保证统计信息新鲜
    9     ALTER TABLE your_table_name SET (autovacuum_analyze_scale_factor = 0.005);

10     ALTER TABLE your\_table\_name SET (autovacuum\_analyze\_threshold = 5000);
`scale_factor` 和 `threshold` 的计算公式是：`autovacuum_vacuum_threshold + autovacuum_vacuum_scale_factor  number_of_tuples*。当死元组数量超过这个值时，就会触发
  AUTOVACUUM。通过降低 scale_factor 并设置一个合理的 threshold`，可以确保清理及时进行。

1.  调整 `fillfactor` (填充因子)：
    对于 UPDATE 非常频繁的表，这是一个高级但非常有效的优化。fillfactor 是一个表参数，范围从10到100。默认是100，表示PostgreSQL在写入时会把每个数据块（Page）填满。

    当 fillfactor 设置为比如 90 时，每个数据块只会填充到90%，留下10%的空闲空间。当这页上的某行被 UPDATE
    时，新版本的行数据就有很大概率可以直接存放在同一个数据块的预留空间里。这被称为 HOT (Heap-Only Tuples) 更新，效率极高，且不会产生需要 VACUUM 清理的死元组。

1     -- 为表设置填充因子为 90
2     ALTER TABLE your\_table\_name SET (fillfactor = 90);
注意：fillfactor 的设置只对未来的 INSERT 和 UPDATE 生效。要让整个表现有的数据都按新的 fillfactor 重新组织，你需要执行一次 VACUUM FULL 或 pg\_repack。

总结

1.  诊断：使用SQL查询确认表膨胀，并检查是否存在长事务或坏的复制槽。
2.  急救：使用 pg\_repack (推荐) 或 VACUUM FULL (有停机风险) 来立即回收磁盘空间。
3.  预防：为问题表设置更激进的 AUTOVACUUM 参数，并考虑调低 fillfactor 以优化 UPDATE 性能并从源头上减少死元组的产生。
4.  监控：将表膨胀和长事务的监控纳入你的常规数据库运维体系。

    1 -- 优化后的查询
    2 SELECT
    3     ce.event_id,
    4     ce.src_ip,
    5     ce.src_region,
    6     ce.src_operator,
    7     ce.src_threat_mark,
    8     ce.dst_ip,
    9     ce.dst_region,
   10     ce.dst_operator,
   11     ce.dst_threat_mark,
   12     ce.start_time,
   13     ce.end_time,
   14     ce.status,
   15     ce.event_type,
   16     ce.up_bytesall,
   17     ce.down_bytesall,
   18     ce.judge_status,
   19     ce.report_type,
   20     ce.judge_info,
   21     ce.src_com,
   22     ce.dst_com,
   23     ce.analysis_tech,
   24     ce.related_alerts,
   25     ce.reverse_tag,
   26     ce.related_alerts_count,
   27     ce.key_unit,
   28     ce.src_port,
   29     ce.dst_port,
   30     ce.work_time_tag,
   31     ce.flow_continue_time,
   32     custom_tags_agg.custom_tags,
   33     tag_agg.src_info,
   34     tag_agg.dst_info,
   35     tag_agg.app_type,
   36     tag_agg.port_distribution,
   37     tag_agg.flow_surge,
   38     tag_agg.long_connection
   39 FROM
   40     internal_app_bsa_gjk.continuous_events ce
   41 -- 使用 INNER JOIN 一次性完成对 continuous_events_tag 表的过滤和数据聚合
   42 JOIN (
   43     SELECT
   44         event_id,
   45         -- 使用 bool_or 来模拟 EXISTS 的过滤条件
   46         bool_or(tag_name='app_type' and tag_content != '' and tag_content is not null) as has_app_type,
   47         bool_or(tag_type = 0 AND tag_name = 'long_connection' AND tag_content IS NOT NULL) as has_long_connection,
   48         -- 同时完成原先的聚合取值逻辑
   49         string_agg(case when tag_type=1 then tag_content end,'&') as src_info,
   50         string_agg(case when tag_type=2 then tag_content end,'&') as dst_info,
   51         string_agg(case when tag_name='app_type' then tag_content end,'&') as app_type,
   52         MAX(case when tag_type=0 and tag_name='port_distribution' then tag_content end) as port_distribution,
   53         MAX(case when tag_type=0 and tag_name='flow_surge' then tag_content end) as flow_surge,
   54         MAX(case when tag_type=0 and tag_name='long_connection' then tag_content end) as long_connection
   55     FROM
   56         internal_app_bsa_gjk.continuous_events_tag
   57     GROUP BY
   58         event_id
   59 ) tag_agg ON ce.event_id = tag_agg.event_id
   60 -- LEFT JOIN 获取 custom_tags
   61 LEFT JOIN (
   62     SELECT
   63         event_id,
   64         array_to_string(ARRAY(SELECT unnest(array_agg(custom_tag_id))), ',') as custom_tags
   65     FROM
   66         internal_app_bsa_gjk.continuous_event_relate_tag
   67     GROUP BY
   68         event_id
   69 ) custom_tags_agg ON ce.event_id = custom_tags_agg.event_id
   70 WHERE
   71     ce.end_time >= 1758713702
   72     and ce.end_time <= 1761305725
   73     AND ce.judge_status = 1
   74     AND ce.up_bytesall > ce.down_bytesall
   75     AND ce.up_bytesall >= 104857600
   76     -- 将原先的 EXISTS 条件转为对聚合结果的判断
   77     AND tag_agg.has_app_type = TRUE
   78     AND tag_agg.has_long_connection = TRUE
   79 -- 将排序和分页放在查询的最外层
   80 ORDER BY
   81     ce.end_time DESC, ce.event_id ASC
   82 LIMIT 50 OFFSET 0;

  ---