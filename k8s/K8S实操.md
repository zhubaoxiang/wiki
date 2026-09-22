# 1.二级中心资源池2节点升级flink版本

## 1.1 首先通过sudo kubectl get pod -n bsa-product查看容器名称

以下操作的容器名称以各个资源池查询出来的为准

## 1.2 从容器中拷贝flink-1.12.7文件

sudo kubectl  -n bsa-product cp data-center-7bcc4fff67-zt4zr:/home/master/flink-1.12.7 /home/master/flink-1.12.7

## 1.3 给文件master权限

sudo chown -R  master\:master /home/master/flink-1.12.7

# 2.二级中心给K8S节点打标签

## 2.1 通过sudo kubectl get nodes查看123节点信息

![image.png](https://cdn.nlark.com/yuque/0/2023/png/22609334/1680012444480-c4d5f2c3-bf4a-40fe-b3fe-03e836b1e7c1.png#averageHue=%232a2927\&clientId=u0b3cdc2e-0db6-4\&from=paste\&height=69\&id=ua415b80b\&name=image.png\&originHeight=103\&originWidth=830\&originalType=binary\&ratio=1.5\&rotation=0\&showTitle=false\&size=342694\&status=done\&style=none\&taskId=u98a420cd-8bb3-4444-b3df-591524b6635\&title=\&width=553.3333333333334)

## 2.2 执行如下命令，给节点打标签

三条命令均可节点1上执行
命令行中的hostname以各资源池查询为准
1节点添加标签:
kubectl label nodes bjjd-psc-p12f4-mcore-pm-os01-cmsc-01 node.app=bsa

2节点添加标签:
kubectl label nodes bjjd-psc-p12f4-mcore-pm-os01-cmsc-02 node.app=isop

3节点添加标签：
kubectl label nodes bjjd-psc-p12f4-mcore-pm-os01-cmsc-03 node.app=ncss

# 3.二级中心添加coredns

## 3.1.登录各资源池节点1，执行：

sudo kubectl get nodes

## 3.2.执行：

kubectl edit configmap coredns -n kube-system
添加：具体ip与hostname以第一步查询为准，第一步只出查询k8s集群的三台机器，需要补充456三台机器的信息。
hosts {
10.184.104.1 bjjd-psc-p12f4-mcore-pm-os01-cmsc-01
10.184.104.2 bjjd-psc-p12f4-mcore-pm-os01-cmsc-02
10.184.104.3 bjjd-psc-p12f4-mcore-pm-os01-cmsc-03
10.184.104.4 bjjd-psc-p12f4-mcore-pm-os01-cmsc-04
10.184.104.5 bjjd-psc-p12f4-mcore-pm-os01-cmsc-05
10.184.104.6 bjjd-psc-p12f4-mcore-pm-os01-cmsc-06
fallthrough
}
效果如下：
![image.png](https://cdn.nlark.com/yuque/0/2023/png/22609334/1680012694764-e58a07dc-3158-4fe4-a437-f8240cbdb9a3.png#averageHue=%2321201f\&clientId=u0b3cdc2e-0db6-4\&from=paste\&height=369\&id=u12b3a7d3\&name=image.png\&originHeight=554\&originWidth=831\&originalType=binary\&ratio=1.5\&rotation=0\&showTitle=false\&size=1845102\&status=done\&style=none\&taskId=ua9395d32-51ed-490d-b57c-99a0a47d129\&title=\&width=554)

# 4.二级中心docker commit

## 4.1 在机器1上完成以下操作

首先要通过kubectl get pod -n bsa-product得到实际的pod name，以下操作的pod name均以每个资源池查询出的pod name为准

## 4.2 data-center

通过sudo kubectl -n bsa-product get pod data-center-74bf9b5dbc-5tlm6 -o yaml打印出部署时的信息，记录下镜像id,如红框所示
![image.png](https://cdn.nlark.com/yuque/0/2023/png/22609334/1680068233228-34debde0-949f-4a84-be65-aa27cb6ebcca.png#averageHue=%23262323\&clientId=uaf9c4c12-1845-4\&from=paste\&height=393\&id=ud7493b9d\&name=image.png\&originHeight=590\&originWidth=1888\&originalType=binary\&ratio=1.5\&rotation=0\&showTitle=false\&size=64952\&status=done\&style=none\&taskId=u28d94cc0-0382-45d7-9b8d-094066a5368\&title=\&width=1258.6666666666667)
执行sudo docker commit -m comment -a zxl eb239301cef302c53f30df24bfde09cdcbef327c37d6834343c3d5b52641013c data-center-bak:20230330
命令中的镜像id以每个资源池查询出来的为准

## 4.3 data-sdk

通过sudo kubectl -n bsa-product get pod data-sdk-74bf9b5dbc-5tlm6 -o yaml打印出部署时的信息，记录下镜像id
执行sudo docker commit -m comment -a zxl eb239301cef302c53f30df24bfde09cdcbef327c37d6834343c3d5b52641013c data-sdk-bak:20230330
命令中的镜像id以每个资源池查询出来的为准

## 4.4 isop-front

通过sudo kubectl -n bsa-product get pod isop-front-74bf9b5dbc-5tlm6 -o yaml打印出部署时的信息，记录下镜像id
执行sudo docker commit -m comment -a zxl eb239301cef302c53f30df24bfde09cdcbef327c37d6834343c3d5b52641013c isop-front-bak:20230330
命令中的镜像id以每个资源池查询出来的为准

# 5.二级中心创建表资产画像功能需要的表

## 5.1 首先通过sudo kubectl get pod -n bsa-product查看容器名称

以下操作的容器名称以各个资源池查询出来的为准

# 5.2把20230330.sql，execute\_sql\_20230330.py拷贝到data-center容器/home/master目录下

kubectl -n bsa-product cp 20230330.sql data-center-74bf9b5dbc-5tlm6:/home/master/
kubectl -n bsa-product cp execute\_sql.py data-center-74bf9b5dbc-5tlm6:/home/master/

## 5.3 执行

python execute\_sql\_20230330.py
![image.png](https://cdn.nlark.com/yuque/0/2023/png/22609334/1680067660776-15137db1-c3b6-450c-9527-c5920366415c.png#averageHue=%23292726\&clientId=uaf9c4c12-1845-4\&from=paste\&height=41\&id=ub1d2923b\&name=image.png\&originHeight=61\&originWidth=888\&originalType=binary\&ratio=1.5\&rotation=0\&showTitle=false\&size=6777\&status=done\&style=none\&taskId=ue134289f-8d50-4f0c-bedc-3bb1355bcd1\&title=\&width=592)

# 5.4 查看表是否被创建

进入数据库，查看
internal\_app\_assetmgr.risk\_vul\_asset
internal\_app\_assetmgr.asset\_risk\_cache
internal\_app\_assetmgr.asset\_risk\_analysis\_cache
这三张表是否已经存在

# 6.一级中心上线情报同步功能

# 6.1 一级中心data-center容器修改如下文件，替换前先备份

ISOP/threatIntelligence/bin/sync\_data\_to\_second.py（新增）
ISOP/threatIntelligence/jobs/jobmeta.json（替换）
ISOP/threatIntelligence/models/sync\_threat\_intelligence\_model.py（新增）
ISOP/threatIntelligence/ti\_plugins/nti\_vulnerability.py（替换）
ISOP/threatIntelligence/ti\_plugins/nti\_whitelist.py（替换）
ISOP/threatIntelligence/urls.py（替换）
ISOP/threatIntelligence/views/sync\_threat\_intelligence\_view\.py  （新增）

# 6.2 替换完成后reload uwsgi

uwsgi --reload /home/master/logs/uwsgi.pid

# 6.3 检查一下页面属于data-center的功能有无报错，如预警，情报库等

# 7.部署新的data-center,data-sdk,isop-front镜像

由苏研CICD流水线执行

# 8.二级中心注册情报同步任务

## 8.1 首先通过sudo kubectl get pod -n bsa-product查看容器名称

以下操作的容器名称以各个资源池查询出来的为准

# 8.2 将ti\_task\_add.py拷贝进data-center容器

kubectl -n bsa-product cp ti\_task\_add.py data-center-74bf9b5dbc-5tlm6:/home/master/

## 8.3 运行ti\_task\_add.py注册任务

进入data-center容器，执行
python ti\_task\_add.py
![image.png](https://cdn.nlark.com/yuque/0/2023/png/22609334/1680063954319-d7b74f50-b717-4f04-b4e8-386cf445501e.png#averageHue=%23282726\&clientId=uaf9c4c12-1845-4\&from=paste\&height=55\&id=u6138697a\&name=image.png\&originHeight=83\&originWidth=921\&originalType=binary\&ratio=1.5\&rotation=0\&showTitle=false\&size=6783\&status=done\&style=none\&taskId=ud6fb5e2d-d328-4532-9a09-02e23cf58a8\&title=\&width=614)
输出10000表示正常注册
进入任务管理页面，查看有无对应任务
![image.png](https://cdn.nlark.com/yuque/0/2023/png/22609334/1680064026582-6ce27c78-e997-4e1d-9155-69fe23d17873.png#averageHue=%23faf9f9\&clientId=uaf9c4c12-1845-4\&from=paste\&height=166\&id=u98672c2f\&name=image.png\&originHeight=249\&originWidth=2051\&originalType=binary\&ratio=1.5\&rotation=0\&showTitle=false\&size=33040\&status=done\&style=none\&taskId=u81d58283-198d-474b-a2c5-8871cfafe3a\&title=\&width=1367.3333333333333)
