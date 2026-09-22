# ES常用命令

#### 1.查看别名

```linux
http://10.253.146.71:19399/_cat/aliases
curl -XGET 'http://10.253.146.71:19399/_cat/aliases'
```

#### 2.查看健康状态

```linux
curl -XGET 'http://10.216.97.5:19399/_cluster/health?pretty'
```

#### 3.查看写入数量

```linux
curl -XGET 'http://10.188.47.4:19399/_cat/thread_pool/write?v'
```

#### 4.查看线程

```linux
curl -XGET 'http://10.234.88.7:19399/_cat/thread_pool?v'
```

#### 5.查看分片有异常的索引

```linux
curl -s '10.216.97.4:19399/_cat/shards' | grep UNASSIGNED
```

#### 6.查看节点使用内存情况

```linux
curl http://10.216.97.4:19399/_cat/nodes?v
ip：集群中节点的 ip 地址；
heap.percent：堆内存的占用百分比；
ram.percent：总内存的占用百分比，其实这个不是很准确，因为 buff/cache 和 available 也被当作使用内存；
cpu：cpu 占用百分比；
load_1m：1 分钟内 cpu 负载；
load_5m：5 分钟内 cpu 负载；
load_15m：15 分钟内 cpu 负载；
node.role：上图的dilmrt代表全部权限
master：* 代表是 master 节点，- 代表普通节点；
name：节点的名称。
```

#### 7.查看所有索引

```linux
curl -XGET 'http://10.188.47.4:19399/_cat/indices?v'
```

#### 8. 查询分片未分配原因

```linux
curl -XGET 'http://10.216.97.4:19399/_cluster/allocation/explain'
```

#### 9.查询攻击top10

```json
curl --location --request GET 'XJCJ-PSC-S2X2-MCORE-PM-OS01-CMSC-04:19399/internal_isop_log-*/_search' \
--header 'Content-Type: application/json' \
--data '{
  "query": {
    "bool": {
      "must_not": [
        {
          "terms": {
            "attacker": [
              "0.0.0.0",
              ""
            ]
          }
        }
      ],
      "must": [
        {
          "terms": {
            "log_type": [
              "僵尸网络",
              "勒索软件",
              "恶意后门",
              "蠕虫",
              "网站监测日志",
              "网站监测资产",
              "网站监测漏洞",
              "其他恶意样本",
              "使用弱密码登录",
              "账号口令爆破",
              "通用web攻击",
              "网页篡改",
              "钓鱼网页",
              "通用攻击入侵",
              "数据泄露",
              "信息泄露",
              "恶意站点",
              "异常日志流量",
              "注销日志",
              "异常攻击源IP流量",
              "钓鱼邮件",
              "异常被攻击IP流量",
              "恶意广告软件",
              "端口扫描",
              "漏洞扫描",
              "主机探测",
              "服务探测",
              "异常接口流量",
              "主机木马",
              "病毒"
            ]
          }
        }
      ],
      "filter": [
        {
          "range": {
            "timestamp": {
                // 开始时间-结束时间
              "gte": 0,
              "lte": 1678686476000
            }
          }
        }
      ]
    }
  },
  "size": 0,
  "aggs": {
    "sip_aggs": {
      "terms": {
        "field": "sip",
        "order": [
          {
              // 降序排序
            "occur_count_aggs_sum.value": "desc"
          },
          {
            "_count": "desc"
          }
        ],
         "size": 1
      },
      "aggs": {
        "occur_count_aggs_sum": {
          "sum": {
            "field": "occur_count"
          }
        }
      }
    }
  }
}'
```

#### 10.查看es 每秒gc情况

    jstat -gcutil (jps查看es的pid) 1000（一秒刷新一次）
    S0：幸存1区当前使用比例  S1：幸存2区当前使用比例 E：伊甸园区使用比例 O：老年代使用比例 M：元数据区使用比例 CCS：压缩使用比例 YGC：年轻代垃圾回收次数 FGC：老年代垃圾回收次数 FGCT：老年代垃圾回收消耗时间  GCT：垃圾回收消耗总时间

#### 11.查看jvm 内部线程，可以看到阻塞的索引

```linux
jstack -l (jps查看es的pid) >(1.log)
写入到文件后查看
```

#### 12.查看索引分片信息

    curl -XGET 'http://10.216.97.4:19399/_cat/shards?v&h=n,index,shard,prirep,state,sto,sc,unassigned.reason,unassigned.details'
    index：索引名称
    shard：分片数
    prirep：分片类型，p：primary为主分片，r：replicas为复制分片
    state：分片状态，STARTED为正常分片，INITIALIZING为异常分片
    docs：记录数
    store：存储大小
    ip：es节点ip
    node：es节点名称

#### 13.查看堆内存

```linux
jmap -histo (jps查看pid) >(1.log)
写入到文件后查看
```

#### 14.索引关闭和打开

    curl -XPOST 'http://10.234.88.9:19399/internal_isop_incident-20220804-0/_open'
    curl -XPOST 'http://10.232.45.4:19399/internal_isop_incident-20220809-0/_close'

#### 15.清理缓存

```linux
curl -XPOST 'http://10.188.47.4:19399/_cache/clear'
```

#### 16.索引重新分片

```linux
curl -XPOST 'http://10.216.97.4:19399/_cluster/reroute?retry_failed=true'
```

#### 17.索引迁移

```linux
curl -XPOST 'http://10.216.97.4:19399/_reindex' -H 'Content-Type:application/json' --data '
{
  "source": {
    "index":"old_index"
  },
  "dest": {
    "index":"new_index"
  }
}
 '
```

#### 18.增加和删除索引别名

```linux
curl -XPOST 'http://10.216.97.4:19399/_aliases' -H 'Content-Type:application/json' --data '
{
  "actions": [
    {
        "add": {
            "index": "netflow_ip_5min-000003",
            "alias": "netflow_ip_5min"
        }
    }
  ]
}
 '
 
 curl -XPOST 'http://10.216.97.4:19399/_aliases' -H 'Content-Type:application/json' --data '
{
  "actions": [
    {
        "remove": {
            "index": "netflow_ip_5min-000003",
            "alias": "netflow_ip_5min"
        }
    }
  ]
}
 '
```

#### 19.手动滚动索引

```linux
curl -XPOST 'http://10.216.97.4:19399/netflow_ip_5min/_rolllover' -H 'Content-Type:application/json' --data '
{
  "conditions": {
    "max_age": "7d",
    "max_docs": 1000,
    "max_size": "5gb"
  }
}
 '
 如想要立马触发滚动，可以将上述其中一个参数调整很小，比如max_docs设置成1，这样索引就会立马滚动一次
```

