# 第二章 能定位问题并验证方案（6-18 个月）

> **阶段目标**：从「回答问题」升级到「定义问题 + 给出可执行建议」。
>
> 第一章解决的是「数取得对不对」。这一章解决的是三个更难的问题：
> - **数从哪来、可不可信**（2.1 数仓 / 2.2 埋点）
> - **怎么证明一个改动有效**（2.4 A/B 实验）
> - **指标动了怎么定位原因**（2.5 分析方法论）
>
> 这一章的分水岭意义在于：阶段一的能力是「别人问你答」，阶段二的能力是「你告诉别人该看哪、该做什么」。

---

## 2.0 延续的示例数据

沿用第一章的四张表，本章新增两张，用于埋点和实验相关的例子。

**第一章已有**（详见 [chapter-01-sql.md](./chapter-01-sql.md)）：

| 表名 | 粒度 | 关键字段 |
|------|------|----------|
| `dim_user` | 一个用户一行 | user_id, reg_date, channel, city |
| `dwd_order` | 一个订单一行 | order_id, user_id, pay_time, status, amount, dt |
| `dwd_order_item` | 一个订单的一个商品一行 | order_id, item_id, sku, qty, item_amount |
| `dwd_event` | 一次事件一行 | user_id, event_name, event_time, props, dt |

**本章新增**：

### `ods_fe_event` 前端埋点原始表 — 粒度：一次前端事件一行

| event_id | user_id | device_id | event_name | client_time | server_time | request_id | props | dt |
|----------|---------|-----------|------------|-------------|-------------|------------|-------|-----|
| E1 | 1 | d_aaa | card_expose | 09:58:01 | 09:58:03 | r_001 | `{"card_id":"C1","pos":1,"dur_ms":1200}` | 2026-09-01 |
| E2 | 1 | d_aaa | card_click | 09:58:05 | 09:58:06 | r_001 | `{"card_id":"C1","pos":1}` | 2026-09-01 |
| E3 | NULL | d_bbb | card_expose | 09:59:00 | 09:59:01 | r_002 | `{"card_id":"C2","pos":2,"dur_ms":300}` | 2026-09-01 |

> 注意三个特征，后面反复用到：`client_time` 和 `server_time` 不同（网络延迟 + 客户端时钟漂移）；`user_id` 可能为 NULL（未登录）；`request_id` 是串联前后端的钥匙。

### `dwd_exp_assignment` 实验分流表 — 粒度：一个用户在一个实验里一行

| exp_id | user_id | variant | assign_time | dt |
|--------|---------|---------|-------------|-----|
| exp_001 | 1 | control | 2026-09-01 00:10:00 | 2026-09-01 |
| exp_001 | 2 | treatment | 2026-09-01 00:12:00 | 2026-09-01 |
| exp_001 | 3 | treatment | 2026-09-01 08:30:00 | 2026-09-01 |

---

## 2.1 数据仓库与数据工程

### 2.1.1 为什么分析师必须懂这一节

不懂数仓的分析师会犯三类错误，且都很贵：

| 错误 | 后果 | 真实场景 |
|------|------|----------|
| **查错层** | 查询慢 10-100 倍，或算出和官方报表不一致的数 | 从 ODS 层现算 DAU，而不是用 DWS 层现成的 |
| **忽略历史状态** | 用「当前状态」去分析「历史行为」 | 用用户今天的会员等级，去分析他半年前的下单行为 |
| **不知道上游坏了** | 拿脏数据做了结论，汇报完才发现 | 上游任务失败补跑，数据重复，GMV 虚高 80% |

### 2.1.2 维度建模：事实表 vs 维度表

**核心区分**：

| | 事实表（Fact） | 维度表（Dimension） |
|---|---|---|
| 存什么 | **发生的事**：订单、点击、支付 | **事物的属性**：用户是谁、商品是什么 |
| 特征 | 行数巨大、持续增长、有时间戳 | 行数小、增长慢、描述性字段多 |
| 字段类型 | 主要是**度量**（数值，可加和） | 主要是**属性**（文本，用于筛选/分组） |
| 例子 | `dwd_order`、`dwd_event` | `dim_user`、`dim_sku`、`dim_date` |
| 命名习惯 | `dwd_*` / `fact_*` / `*_detail` | `dim_*` |

**一句话判断**：这张表的一行回答的是「发生了什么」还是「这是什么」。

#### 事实表的三种类型（很多人只知道第一种）

**① 事务事实表（Transaction Fact）** — 最常见

一行 = 一次业务事件，发生了就插入，不会修改。

```
dwd_order（一行 = 一个订单创建事件）
dwd_event（一行 = 一次埋点上报）
```
特点：可加和。`SUM(amount)` 天然有意义。

**② 周期快照事实表（Periodic Snapshot）**

一行 = 某个实体在某个时间点的状态。每天/每周定时生成一份完整快照。

```
dws_user_snapshot_daily（一行 = 某天某用户的累计状态）
dt | user_id | cum_order_cnt | cum_gmv | balance | vip_level
2026-09-01 | 1 | 5  | 1200 | 300 | V2
2026-09-02 | 1 | 5  | 1200 | 300 | V2
2026-09-03 | 1 | 6  | 1400 | 250 | V3
```

**⚠️ 关键：快照表的度量不能跨天加和。**

```sql
-- ✗ 荒谬：把每天的余额加起来
SELECT SUM(balance) FROM dws_user_snapshot_daily WHERE dt BETWEEN '2026-09-01' AND '2026-09-03';
-- 得到 850，但用户从来没有过 850 块

-- ✓ 快照表只能「取某一天」或「算跨天的差值/平均」
SELECT SUM(balance) FROM dws_user_snapshot_daily WHERE dt = DATE '2026-09-03';  -- 某天的总余额
SELECT AVG(balance) FROM ... GROUP BY user_id;                                   -- 平均余额
```

这类字段叫 **半可加（semi-additive）度量**：能跨用户加，不能跨时间加。余额、库存、在线人数都属于这一类。

**③ 累积快照事实表（Accumulating Snapshot）**

一行 = 一个有生命周期的流程实例，随着流程推进**原地更新**。

```
dwd_order_lifecycle（一行 = 一个订单的完整生命周期）
order_id | create_time | pay_time | ship_time | receive_time | refund_time | cur_status
O1       | 09-01 09:50 | 09-01 10:00 | 09-02 14:00 | 09-04 11:00 | NULL | received
O2       | 09-03 22:20 | 09-03 22:30 | NULL        | NULL        | 09-05 10:00 | refunded
```

**这类表是漏斗分析和时长分析的最佳载体**：

```sql
-- 各环节平均耗时，一条 SQL 搞定，不用自己 JOIN 事件表
SELECT
  AVG(date_diff('minute', create_time, pay_time))    AS avg_create_to_pay_min,
  AVG(date_diff('hour',   pay_time,   ship_time))    AS avg_pay_to_ship_hr,
  1.0 * COUNT(pay_time)  / COUNT(*)                  AS pay_rate,
  1.0 * COUNT(ship_time) / NULLIF(COUNT(pay_time),0) AS ship_rate
FROM dwd_order_lifecycle
WHERE date(create_time) BETWEEN DATE '2026-09-01' AND DATE '2026-09-07';
```

> **⚠️ 用累积快照表做分析的最大坑**：它是**原地更新**的表，昨天跑出来的数和今天跑出来的数会不一样（因为昨天还没收货的订单今天收货了）。
> 报数时必须说明「数据截止时间」，否则同一份报表隔天重跑，数字会变，你会被质疑造假。

#### 星型模型 vs 雪花模型

```
星型模型（推荐）                    雪花模型
  dim_user                            dim_user → dim_city → dim_province
      ↘                                   ↘
  dim_sku → [ fact_order ] ← dim_date   dim_sku → dim_category → dim_cat_l1
      ↗                                   ↗
  dim_channel                         dim_channel → dim_channel_type
```

| | 星型 | 雪花 |
|---|---|---|
| 维度表 | 扁平、有冗余（城市和省份存一张表） | 规范化、多层（城市表引用省份表） |
| JOIN 次数 | 少 | 多 |
| 存储 | 略大 | 略小 |
| 查询性能 | **快** | 慢 |
| 大数据场景 | **首选** | 一般不用 |

**为什么大数据场景选星型**：存储便宜，JOIN 贵。多一次 JOIN 就多一次 shuffle（见第一章 1.10.3）。维度表冗余几个字段带来的存储成本，远低于每次查询多 JOIN 两张表的计算成本。

#### 一致性维度（Conformed Dimension）

**定义**：同一个维度表被多个事实表共用，保证口径一致。

```
dim_user ←── dwd_order（订单事实）
         ←── dwd_event（行为事实）
         ←── dwd_refund（退款事实）
```

**为什么重要**：如果订单分析用 A 表的渠道定义、行为分析用 B 表的渠道定义，两份报表永远对不上，而且没人说得清谁对。

> **分析师的实操动作**：拿到一个新需求，先问「这个维度的权威表是哪张」。如果发现团队里有两张「用户表」，这就是一个需要暴露给数据团队的问题，而不是你自己挑一张用。

### 2.1.3 数仓分层：ODS → DWD → DWS → ADS

| 层 | 全称 | 放什么 | 粒度 | 数据量 | 分析师该不该查 |
|---|---|---|---|---|---|
| **ODS** | Operational Data Store | 原始数据，与源系统**一模一样** | 源系统粒度 | 最大 | ⚠️ 尽量不查 |
| **DWD** | Data Warehouse Detail | 清洗后的明细：去重、补维度、统一格式 | 最细业务粒度 | 大 | ✅ 常查 |
| **DWS** | Data Warehouse Summary | 按主题预聚合的轻度汇总 | 日×维度 | 中 | ✅ 优先查 |
| **ADS** | Application Data Store | 直接给报表/看板用的结果表 | 已是最终结果 | 小 | ✅ 核对用 |

#### 每层长什么样（同一份订单数据）

```sql
-- ODS：和业务库一模一样，字段名是英文缩写，状态是数字码，时间是时间戳
ods_order_binlog: id, uid, st, amt, ct, ut, ...   -- st=2 表示已支付

-- DWD：清洗后。状态翻译成可读值、时间统一时区、关联上维度、去重
dwd_order: order_id, user_id, status('paid'), amount, pay_time, channel, city, dt

-- DWS：按主题聚合。这里是「用户×日」主题
dws_user_order_daily: dt, user_id, order_cnt, paid_cnt, gmv, refund_amt

-- ADS：直接对应某个报表的一行
ads_channel_gmv_daily: dt, channel, gmv, order_cnt, uv, arpu, mom_rate, yoy_rate
```

#### 「我该查哪层」决策树

```
需求来了
  │
  ├─ 已有的报表指标，只是想看/核对？
  │    └→ 查 ADS。和业务方看到的是同一个数，不会出现口径分歧
  │
  ├─ 常规维度组合（渠道×日、品类×日）的聚合分析？
  │    └→ 查 DWS。快、口径已对齐
  │
  ├─ 需要 DWS 没有的维度组合、或要看明细？
  │    └→ 查 DWD。灵活，但要自己保证口径
  │
  └─ DWD 里没有你要的字段，或怀疑 DWD 清洗逻辑有问题？
       └→ 才查 ODS，并且必须先搞清清洗规则，否则算出来的数和全公司都不一样
```

**三条实操纪律**：

1. **能查上层就不查下层**。DWS 一条 SQL 两秒出结果，DWD 可能要五分钟，ODS 可能跑半小时还错。
2. **从 ODS 自己算核心指标 = 自己造一套口径**。哪怕算对了，只要和 DWS 差一点点，你就得花一天解释差异。
3. **发现 DWD/DWS 有问题，提给数据团队修，不要自己在查询里绕过去**。你绕过去了，下一个人还会踩。

### 2.1.4 缓慢变化维（SCD）与拉链表

#### 问题：用户的属性会变，历史分析该用哪个版本？

user_1 在 2026-01 是「普通会员」，2026-06 升级成「金卡会员」。现在分析 2026-03 的订单，这笔订单应该算在「普通」还是「金卡」名下？

**答案：普通**。用户在下单那一刻是普通会员。用今天的状态去归因历史行为，叫**时间穿越（time travel bug）**，是分析里非常隐蔽的错误来源。

> **它造成的典型错觉**：「金卡会员的人均消费是普通会员的 5 倍，所以要多发金卡」。
> 实际因果是反的：**是因为消费多才升的金卡**。用当前等级回溯历史订单，把「升级后的消费」也算进了金卡名下，得出必然为真的循环结论。

#### SCD 三种处理方式

| 类型 | 做法 | 能否查历史 | 适用 |
|------|------|-----------|------|
| **Type 1** | 直接覆盖旧值 | ❌ 不能 | 纠错型变更（名字打错了） |
| **Type 2** | 新增一行，用起止时间标记有效期 | ✅ 能 | **业务性变更（会员等级、所属城市）** |
| **Type 3** | 加一列存上一个值 | 只能查上一版 | 只关心「变更前后」对比 |

#### 拉链表（SCD Type 2 的标准实现）

```
dim_user_zip（拉链表）
user_id | vip_level | city | start_date | end_date   | is_current
1       | normal    | 上海 | 2026-01-05 | 2026-06-14 | 0
1       | gold      | 上海 | 2026-06-15 | 9999-12-31 | 1
2       | normal    | 北京 | 2026-02-11 | 9999-12-31 | 1
```

**三个约定**：
- `end_date = '9999-12-31'` 表示当前有效（不用 NULL，NULL 参与比较会出问题，见第一章 1.9）
- 区间是**左闭右闭** `[start_date, end_date]`（也有团队用左闭右开，**必须确认，差一天会错**）
- `is_current` 是冗余字段，为了方便查当前状态

#### 三种查询姿势

```sql
-- ① 查当前状态（最常见）
SELECT * FROM dim_user_zip WHERE is_current = 1;
-- 或
SELECT * FROM dim_user_zip WHERE end_date = DATE '9999-12-31';

-- ② 查某个历史时点的全量快照（2026-03-01 那天所有用户是什么状态）
SELECT * FROM dim_user_zip
WHERE start_date <= DATE '2026-03-01'
  AND end_date   >= DATE '2026-03-01';
-- 结果：user_1 是 normal ✓

-- ③ ⭐️ 最重要：把事实表和「当时的」维度状态关联
SELECT
  o.order_id, o.pay_time, o.amount,
  z.vip_level AS vip_level_at_order_time   -- 下单那一刻的等级，不是现在的
FROM dwd_order o
JOIN dim_user_zip z
  ON  o.user_id = z.user_id
  AND date(o.pay_time) >= z.start_date      -- ← 时间区间匹配，不只匹配 user_id
  AND date(o.pay_time) <= z.end_date;
```

**姿势 ③ 是这一节的核心**。记住这个 JOIN 模式：`ON 主键相等 AND 事实时间 落在 维度有效区间内`。

> **⚠️ 粒度警告**：这个 JOIN 看起来是一对多（一个 user_id 对应拉链表多行），但因为时间区间不重叠，实际每个订单**只会匹配到一行**，不会膨胀。
> **但前提是拉链表本身没做错**。如果上游拉链逻辑有 bug 导致区间重叠，就会静默膨胀。JOIN 后一定要按第一章 1.3.5 校验行数。

```sql
-- 拉链表健康检查：同一用户是否有重叠区间
SELECT user_id, COUNT(*) AS overlap_cnt
FROM dim_user_zip a
WHERE EXISTS (
  SELECT 1 FROM dim_user_zip b
  WHERE b.user_id = a.user_id
    AND b.start_date <> a.start_date
    AND b.start_date <= a.end_date
    AND b.end_date   >= a.start_date
)
GROUP BY user_id;
-- 应该返回 0 行
```

#### 没有拉链表怎么办

很多公司的维度表只有 Type 1（当前状态）。这时候：

1. **看事实表里有没有冗余快照字段**。好的 DWD 设计会把「下单时的会员等级」直接冗余进订单表。先找这个。
2. **用周期快照表反推**。如果有 `dws_user_snapshot_daily`，取订单当天的那条快照。
3. **实在没有，就在结论里明确标注**：「本分析使用用户当前状态，可能存在时间穿越偏差」。**标注出来，不要装作没这回事。**

### 2.1.5 分区表原理与时区陷阱

#### 分区是怎么回事

分区表在存储上按分区字段分目录：

```
/warehouse/dwd_order/
  dt=2026-09-01/part-0000.parquet
  dt=2026-09-02/part-0000.parquet
  dt=2026-09-03/part-0000.parquet
```

`WHERE dt = '2026-09-01'` → 引擎只打开一个目录。这就是第一章 1.10.1 讲的分区裁剪。

**分区字段的选择原则**：
- 几乎所有查询都会带的字段（99% 是日期）
- 基数适中：按天分区合理（一年 365 个），按 user_id 分区是灾难（几百万个目录）
- 二级分区谨慎：`dt + country` 可以，`dt + user_id` 不行

**小文件问题**：分区太细（比如按小时 × 国家 × 平台）会产生大量小文件，元数据开销超过数据本身，查询反而变慢。这是数据工程的事，但分析师建表时要知道。

#### ⚠️ 时区陷阱（第一章 1.8.2 的完整版）

这是数仓里最贵的隐蔽 bug。**根本原因：分区字段的时区和时间戳字段的时区是两套独立的约定，经常不一致。**

**典型配置**（很常见）：
- `dt` 分区字段 = **业务时区**（比如北京时间）的日期，由调度任务按本地时间切
- `event_time` 时间戳字段 = **UTC**，由服务端直接落库

**踩坑演示**：

```sql
-- ✗ 想查「北京时间 9 月 3 日全天」
SELECT COUNT(*) FROM dwd_event
WHERE dt = DATE '2026-09-03'
  AND event_time >= TIMESTAMP '2026-09-03 00:00:00'
  AND event_time <  TIMESTAMP '2026-09-04 00:00:00';
```

`dt=2026-09-03` 这个分区里装的是北京时间 09-03 00:00 ~ 23:59 的数据，对应 UTC 的 **09-02 16:00 ~ 09-03 15:59**。

再用 UTC 的 `event_time >= 09-03 00:00` 一筛，只剩下 UTC 09-03 00:00 ~ 15:59，即北京时间 **08:00 ~ 23:59**。

**结果：每天丢失早上 8 小时的数据，而且数字看起来只是「偏低了一点」，完全不会报错。**

**正确写法**：

```sql
-- ✓ 分区多取一天做缓冲，时间戳显式转时区
SELECT COUNT(*) FROM dwd_event
WHERE dt BETWEEN DATE '2026-09-02' AND DATE '2026-09-04'       -- 前后各多取一天
  AND event_time AT TIME ZONE 'Asia/Shanghai' >= TIMESTAMP '2026-09-03 00:00:00'
  AND event_time AT TIME ZONE 'Asia/Shanghai' <  TIMESTAMP '2026-09-04 00:00:00';
```

**拿到新表必做的时区体检**：

```sql
-- 看每个分区里时间戳的实际范围，立刻就能看出两者的时区关系
SELECT
  dt,
  MIN(event_time) AS min_ts,
  MAX(event_time) AS max_ts,
  COUNT(*)        AS cnt
FROM dwd_event
WHERE dt BETWEEN DATE '2026-09-01' AND DATE '2026-09-03'
GROUP BY dt ORDER BY dt;
```

结果怎么读：

| 观察到的 min_ts / max_ts | 结论 |
|--------------------------|------|
| `09-03 00:00` ~ `09-03 23:59` | 分区和时间戳同时区 ✅ |
| `09-02 16:00` ~ `09-03 15:59` | 分区是 UTC+8，时间戳是 UTC（差 8 小时）⚠️ |
| `09-03 08:00` ~ `09-04 07:59` | 分区是 UTC，时间戳是 UTC+8 ⚠️ |
| 跨度超过 24 小时 | 分区逻辑有问题，或有延迟数据回补 ⚠️ |

> **把这段 SQL 存成模板**。每接触一张新的分区事实表就跑一次，30 秒，能省掉后面几天的返工。

**时间戳单位陷阱**：`from_unixtime(1757000000)` 得到 2025 年，`from_unixtime(1757000000000)` 会得到公元 57000 年。拿到裸时间戳先看位数：10 位是秒，13 位是毫秒。

### 2.1.6 幂等与重跑

#### 什么是幂等

**幂等 = 同一个任务跑一次和跑十次，结果完全相同。**

为什么必须幂等：数据任务会失败（上游延迟、集群抖动、代码 bug），失败就要重跑。如果不幂等，重跑一次数据就翻倍。

#### 幂等 vs 不幂等的写法

```sql
-- ✓ 幂等：先删除该分区再写入，重跑多少次结果都一样
INSERT OVERWRITE TABLE dwd_order PARTITION (dt = '2026-09-01')
SELECT ... FROM ods_order WHERE dt = '2026-09-01';

-- ✗ 不幂等：每跑一次追加一份，重跑 3 次数据变 3 倍
INSERT INTO TABLE dwd_order
SELECT ... FROM ods_order WHERE dt = '2026-09-01';
```

**三种典型的不幂等写法**（分析师写临时任务时也会踩）：

```sql
-- ✗ ① INSERT INTO 追加
-- ✗ ② 用 current_date 而不是参数化日期：今天重跑昨天的任务，写进了今天的分区
INSERT OVERWRITE ... PARTITION (dt = current_date) ...
-- ✓ 应该参数化
INSERT OVERWRITE ... PARTITION (dt = '${bizdate}') ...

-- ✗ ③ 依赖自增 ID 或 rand()：每次跑结果不同，无法校验
SELECT rand() AS bucket, ...
-- ✓ 用确定性哈希
SELECT abs(from_big_endian_64(xxhash64(to_utf8(user_id)))) % 100 AS bucket, ...
```

#### 分析师视角：怎么发现「上游重跑导致数据重复」

这是实际工作中最常遇到的数据事故。**症状**：某天的指标突然翻倍或多出一截。

**30 秒排查三连**：

```sql
-- ① 主键唯一性：最直接的证据
SELECT dt,
       COUNT(*)                 AS rows,
       COUNT(DISTINCT order_id) AS uniq,
       COUNT(*) - COUNT(DISTINCT order_id) AS dup
FROM dwd_order
WHERE dt BETWEEN DATE '2026-09-01' AND DATE '2026-09-07'
GROUP BY dt ORDER BY dt;
-- dup > 0 的那天就是重跑事故日
```

```sql
-- ② 看重复记录长什么样：是完全一样，还是 update_time 不同
SELECT * FROM dwd_order
WHERE order_id IN (
  SELECT order_id FROM dwd_order WHERE dt = DATE '2026-09-03'
  GROUP BY order_id HAVING COUNT(*) > 1
)
ORDER BY order_id, update_time;
```

```sql
-- ③ 临时止血：用第一章 1.7.3 的去重取最新
SELECT * FROM (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY update_time DESC) rn
  FROM dwd_order WHERE dt = DATE '2026-09-03'
) t WHERE rn = 1;
```

> **止血之后一定要把问题提给数据团队**。你在自己的查询里去重了，别人的报表还是错的。

### 2.1.7 数据血缘

**血缘 = 数据的上下游依赖关系。**

两个方向，对应两类问题：

| 方向 | 问题 | 用途 |
|------|------|------|
| **向上追溯** | 这个指标从哪些表算来的？ | 口径核对、找 bug 根因 |
| **向下影响** | 这张表坏了，会影响哪些报表？ | 故障影响面评估、变更通知 |

#### 没有血缘工具时怎么手动追

```bash
# ① 在数仓代码仓库里搜表名，找出谁在读它、谁在写它
grep -rn "dwd_order" --include="*.sql" --include="*.py" ./warehouse/

# ② 找建表语句，看它的上游
grep -rn "INSERT OVERWRITE TABLE dwd_order" ./warehouse/
```

```sql
-- ③ 有的引擎能从元数据反查（Hive metastore / information_schema）
SELECT * FROM information_schema.tables WHERE table_name LIKE 'dws_%order%';

-- ④ 看表的更新时间，判断上游是否正常产出
SHOW PARTITIONS dwd_order;   -- 最新分区是不是今天的？
```

#### 分析师应该建立的三个习惯

1. **做任何重要分析前，先确认上游今天产出正常**：
   ```sql
   -- 最新分区 + 当天行数是否在正常区间
   SELECT MAX(dt) AS latest_partition FROM dwd_order;
   SELECT dt, COUNT(*) FROM dwd_order WHERE dt >= current_date - INTERVAL '7' DAY GROUP BY dt ORDER BY dt;
   ```
2. **自己产出的表要登记**：谁在用你的表、他们的联系方式。你改字段时要通知到。
3. **口径变更要走通告流程**，不要静默改。见 2.8 节第 5 题。

### ✅ 2.1 自检

<details><summary>Q：为什么「金卡会员人均消费是普通会员 5 倍」这个结论可能是假的？</summary>

如果用户等级取的是**当前**等级（SCD Type 1），那么所有「因为消费多而升级成金卡」的用户，他们升级**之前**的消费也被算进了金卡组。这是典型的时间穿越，因果方向被颠倒了。

正确做法：用拉链表关联「下单时刻的等级」（2.1.4 姿势 ③），或者用「升级前后对比」做准实验分析。
</details>

<details><summary>Q：某天 GMV 突然翻倍，你的前三步排查是什么？</summary>

1. **查主键唯一性**（`COUNT(*)` vs `COUNT(DISTINCT order_id)`）→ 确认是不是上游重跑导致数据重复
2. **看最新分区和行数趋势** → 确认是不是分区写错位置或数据回补
3. **拆维度看是全局翻倍还是某个渠道/品类翻倍** → 全局翻倍几乎必是数据问题，局部暴涨才可能是业务事件

顺序很重要：**先排除数据问题，再谈业务解释**。见 2.5.7 节的完整 SOP。
</details>

---

## 2.2 埋点与数据质量

> 这一节的价值观：**所有数据都是有偏的，问题只在于你知不知道偏在哪、偏多少。**
> 不知道偏差的分析师会把数据当真理；知道偏差的分析师会在结论里标注适用范围。后者才是可信的。

### 2.2.1 前端埋点 vs 后端日志

这是数据可信度的第一道分水岭。

| | 前端埋点 | 后端日志 |
|---|---|---|
| 采集位置 | 用户设备（App / 浏览器） | 服务器 |
| 典型丢失率 | **5% ~ 20%**（部分场景更高） | < 0.1% |
| 延迟 | 秒级到小时级（离线补传可达数天） | 毫秒级 |
| 时钟可信度 | ⚠️ 客户端时间可被篡改、可漂移 | 服务器时间可信 |
| 能测什么 | 曝光、滚动、停留、点击位置、前端渲染耗时 | 请求、下单、支付、接口耗时 |
| **不能**测什么 | 无法保证完整性 | 看不到用户实际看没看到 |

#### 前端数据丢失的九个原因

1. **用户在上报前关闭了页面/App**（最大头，尤其是曝光和离开类事件）
2. **网络失败**且无重试或重试队列被清
3. **广告拦截插件 / 隐私模式**拦截了上报域名（Web 端可达 10%+）
4. **App 崩溃**导致内存中未发送的批次丢失
5. **批量上报的队列上限**：队列满了丢弃最早的
6. **采样**：高频事件（滚动、心跳）常配置采样率，你拿到的是 10% 的数据
7. **SDK 版本差异**：老版本 App 没有新埋点，但老版本用户还在用
8. **系统权限/后台限制**：iOS 后台上报被系统杀掉
9. **风控/反爬**过滤掉了疑似机器流量（有时会误伤真实用户）

#### 选择原则（记住这张表）

| 指标类型 | 用哪个 | 原因 |
|----------|--------|------|
| GMV、订单数、支付成功率 | **后端** | 关乎钱，不能丢 |
| 接口耗时、错误率 | **后端** | 服务端才有完整信息 |
| 曝光量、可见时长、滚动深度 | **前端**（只能） | 后端不知道用户看没看 |
| 点击量 | **两边都有** → 优先后端（如果点击会触发请求） | 后端更全 |
| CTR（点击率） | ⚠️ **分子分母必须同源** | 见下 |

> **CTR 的黄金法则：分子和分母必须来自同一个数据源。**
> 用前端曝光当分母、后端点击当分子，等于「一个丢 15%、一个丢 0.1%」相除，CTR 会被系统性高估约 17%。
> 如果两边数据都要用，至少要先算出匹配率并做校正（见 2.2.4）。

### 2.2.2 曝光埋点的定义

「曝光」这个词看起来简单，实际有**至少三个参数**，不同配置下曝光量能差 3 倍以上。

| 参数 | 常见取值 | 影响 |
|------|----------|------|
| **可见面积阈值** | 1px / 50% / 100% | 阈值越低曝光越多 |
| **可见时长阈值** | 0ms（进入即算）/ 300ms / 1s | 阈值越高曝光越少 |
| **去重口径** | 每次进入视口都算 / 一次会话只算一次 / 一天只算一次 | 差异最大 |

#### 同一批用户行为，不同定义下的 CTR

假设 1000 个用户，卡片被快速滑过 5000 次，其中「停留超过 1 秒」的有 1200 次，点击 100 次：

| 曝光定义 | 曝光数 | CTR |
|----------|--------|-----|
| 进入视口即算，不去重 | 5000 | **2.0%** |
| 可见 ≥1 秒，不去重 | 1200 | **8.3%** |
| 可见 ≥1 秒，按用户去重 | 800 | **12.5%** |

**同一份数据，CTR 从 2% 到 12.5%，差 6 倍。**

> **所以：报 CTR 必须同时说明曝光口径。**
> 更重要的是：**不同渠道/页面如果曝光口径不同，它们的 CTR 绝对不能直接比较**。这是跨页面对比时最常见的错误。

#### 实操：确认你的曝光口径

```sql
-- ① 看曝光事件里有没有时长字段，分布如何
SELECT
  CAST(json_extract_scalar(props, '$.dur_ms') AS INTEGER) / 500 * 500 AS dur_bucket_ms,
  COUNT(*) AS cnt
FROM ods_fe_event
WHERE dt = DATE '2026-09-01' AND event_name = 'card_expose'
GROUP BY 1 ORDER BY 1;
-- 如果大量集中在 0-100ms，说明是「进入即算」口径，噪声很大

-- ② 看同一个 (user, card) 一天曝光几次，判断去重口径
SELECT expose_cnt, COUNT(*) AS user_card_pairs
FROM (
  SELECT user_id,
         json_extract_scalar(props, '$.card_id') AS card_id,
         COUNT(*) AS expose_cnt
  FROM ods_fe_event
  WHERE dt = DATE '2026-09-01' AND event_name = 'card_expose'
  GROUP BY 1, 2
) t
GROUP BY expose_cnt ORDER BY expose_cnt;
-- 如果大量 pair 的 expose_cnt 在 10 以上，说明完全没去重
```

#### 推荐的默认口径

除非有特殊理由，建议：**可见面积 ≥50%、可见时长 ≥500ms、同一会话内同一元素只算一次**。理由：过滤掉快速滑过的无效曝光，又不至于太严苛；会话内去重让 CTR 更接近「用户级决策率」的业务含义。

### 2.2.3 埋点参数设计

分析师要参与埋点设计评审。**埋点一旦上线就很难改**（老版本 App 会长期存活），设计阶段的 10 分钟能省掉后面半年的麻烦。

#### 事件命名规范

```
推荐格式： {页面/模块}_{对象}_{动作}
例：       search_result_card_expose
           search_result_card_click
           cart_checkout_button_click
```

原则：
- **全小写 + 下划线**，不要驼峰和中文
- **动词放最后**，便于按对象聚合（`grep card_` 能拿到所有卡片相关事件）
- **不要在事件名里编码可变信息**。❌ `card_click_pos1` / `card_click_pos2`；✅ 事件名统一是 `card_click`，位置放 `props.pos` 里
- 事件名一旦定了就**不改**。要改就新增一个，老的保留一段时间双写

#### 公共属性 vs 业务属性

| | 公共属性（SDK 自动带） | 业务属性（手动传） |
|---|---|---|
| 内容 | user_id, device_id, app_version, os, network, screen, timestamp, session_id | card_id, pos, item_type, dur_ms, page_no |
| 谁负责 | 埋点 SDK | 业务开发 |
| 分析师关注点 | 有没有缺失、版本分布 | 够不够分析用 |

**设计业务属性时问自己三个问题**：
1. 这个字段能回答什么分析问题？答不上来就不要加（埋点也有成本）
2. 我要分析的维度，是从这个事件带、还是能通过 id 关联到维度表？**能关联的就不要冗余**（否则改一次维度要改埋点）
3. 位置、排序、分页这类**上下文信息**带了吗？没带就无法分析「位置偏差」

#### 串联链路的 id

这是埋点设计里最容易被忽略、但对排查最关键的部分。

| id | 作用域 | 用途 |
|----|--------|------|
| `device_id` | 设备，卸载重装前不变 | 未登录用户的唯一标识 |
| `user_id` | 账号 | 登录后的唯一标识 |
| `session_id` | 一次会话（通常 30 分钟无操作过期） | 会话级分析、路径分析 |
| `request_id` | **一次请求** | ⭐️ 前后端数据 join 的钥匙 |

**`request_id` 是最重要的那个**。有了它，你能把「前端看到了什么」和「后端返回了什么」精确对上：

```sql
-- 前端曝光的卡片 ↔ 后端实际返回的卡片，逐条比对
SELECT
  b.request_id,
  b.returned_card_cnt,
  COUNT(DISTINCT json_extract_scalar(f.props, '$.card_id')) AS exposed_card_cnt
FROM dwd_backend_response b
LEFT JOIN ods_fe_event f
  ON f.request_id = b.request_id AND f.event_name = 'card_expose'
WHERE b.dt = DATE '2026-09-01'
GROUP BY b.request_id, b.returned_card_cnt;
```

> **⚠️ 不同层面的 id 不要混为一谈。**
> 前端 `session_id`、后端 `request_id`、链路追踪的 `trace_id`、业务侧的调用标识——这些是**各自独立的标识体系，彼此之间通常没有任何字符串关系**。
> 拿一个体系的 id 去另一个体系查，结果一定是空。写文档时也不要统称为「trace id」或「会话 id」，必须写明是哪一个。这是踩过就不会忘的坑。

#### 埋点设计评审 Checklist

- [ ] 事件名符合命名规范，且在现有事件表里不重名
- [ ] 每个属性都有明确的分析用途
- [ ] 属性的类型、枚举值范围、是否可空，都写清楚了
- [ ] 带了 `request_id`（或等价的链路 id）
- [ ] 曝光类事件明确了面积/时长/去重三个参数
- [ ] 定义了上报时机（同步还是批量？失败重试几次？）
- [ ] 采样率是多少？有采样的话，下游怎么还原总量？
- [ ] 有对应的**验收方案**：上线后怎么验证埋点是对的

### 2.2.4 前后端数据 join 的匹配率评估

#### 怎么算匹配率

```sql
WITH be AS (   -- 后端：这些请求确实返回了卡片
  SELECT DISTINCT request_id FROM dwd_backend_response
  WHERE dt = DATE '2026-09-01' AND returned_card_cnt > 0
),
fe AS (        -- 前端：这些请求上报了曝光
  SELECT DISTINCT request_id FROM ods_fe_event
  WHERE dt = DATE '2026-09-01' AND event_name = 'card_expose'
)
SELECT
  (SELECT COUNT(*) FROM be)                                     AS backend_cnt,
  (SELECT COUNT(*) FROM fe)                                     AS frontend_cnt,
  (SELECT COUNT(*) FROM be JOIN fe USING (request_id))          AS matched_cnt,
  1.0 * (SELECT COUNT(*) FROM be JOIN fe USING (request_id))
      / (SELECT COUNT(*) FROM be)                                AS match_rate;
```

#### 匹配率多少算可用

| 匹配率 | 判断 | 能做什么 |
|--------|------|----------|
| **> 95%** | 健康 | 前后端 join 的绝对值和比率都可用 |
| **85% ~ 95%** | 可用但需标注 | 比率类指标（CTR、转化率）可用；**绝对值需要按匹配率反推校正** |
| **70% ~ 85%** | 只能看趋势 | 不能报绝对值；同口径的环比、实验组对照组对比仍然有效 |
| **< 70%** | ❌ 不可用 | 必须先修埋点。用这种数据出结论是对自己和团队不负责 |

**为什么「比率比绝对值耐受度高」**：如果丢失是**随机的**，分子分母同比例缩小，比率不变。所以：

> **丢失率高不可怕，可怕的是「非随机丢失」。**

#### 匹配率低的排查顺序

```sql
-- ① 是不是系统性偏差？按维度拆，看丢失是否集中在某类用户
SELECT
  app_version, os, network,
  COUNT(*) AS backend_req,
  SUM(CASE WHEN f.request_id IS NOT NULL THEN 1 ELSE 0 END) AS matched,
  1.0 * SUM(CASE WHEN f.request_id IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*) AS rate
FROM dwd_backend_response b
LEFT JOIN (SELECT DISTINCT request_id FROM ods_fe_event WHERE event_name='card_expose' AND dt=DATE '2026-09-01') f
  ON b.request_id = f.request_id
WHERE b.dt = DATE '2026-09-01'
GROUP BY 1,2,3
ORDER BY rate ASC;
```

**看什么**：
- 某个 `app_version` 匹配率特别低 → 该版本埋点有 bug 或没上埋点
- iOS 远低于 Android（或反之）→ 端侧实现差异
- 弱网（2G/3G）匹配率低 → 上报失败，属于**非随机丢失**，会让你的 CTR 系统性偏向网络好的用户
- 匹配率随时间突变 → 某次发版引入的问题，去查发版记录

```sql
-- ② 是不是时间窗口切错了？（跨天的请求，前端事件落到了第二天）
-- 把前端事件的查询范围放宽一天再算一次匹配率，如果显著上升，就是跨天问题
```

```sql
-- ③ 是不是 id 格式不一致？（大小写、前后空格、类型）
SELECT request_id FROM ods_fe_event WHERE dt = DATE '2026-09-01' LIMIT 5;
SELECT request_id FROM dwd_backend_response WHERE dt = DATE '2026-09-01' LIMIT 5;
-- 肉眼比对格式。见过 'R_001' vs 'r_001'、' r_001' 这类惨案
```

#### 匹配率不达标时的降级方案

1. **改用单边口径**：CTR 全部用前端算（曝光和点击都来自前端），虽然绝对值偏低，但内部可比
2. **只报相对值**：不报「CTR = 8.3%」，报「实验组 CTR 比对照组高 12%」
3. **按匹配率反推**：`真实曝光 ≈ 上报曝光 / 匹配率`（**仅当丢失是随机的**才成立，必须先用上面 ① 验证）
4. **明确标注**：在报告里写「本数据基于前端埋点，匹配率 87%，绝对值偏低约 13%，趋势和对比可信」

### 2.2.5 数据可信度校验清单

**把这段做成一个可复用的脚本，每次分析新表前跑一遍。**

```sql
-- ========== ① 总量连续性：有没有断档或暴增 ==========
SELECT dt, COUNT(*) AS cnt,
       LAG(COUNT(*)) OVER (ORDER BY dt) AS prev_cnt,
       1.0 * COUNT(*) / NULLIF(LAG(COUNT(*)) OVER (ORDER BY dt), 0) - 1 AS dod_rate
FROM dwd_order
WHERE dt >= current_date - INTERVAL '14' DAY
GROUP BY dt ORDER BY dt;
-- 判据：|dod_rate| > 30% 需要解释；某天为 0 或缺失 = 任务失败
```

```sql
-- ========== ② 空值率：关键字段有没有突然变空 ==========
SELECT dt,
       COUNT(*)                                                  AS total,
       1.0 * SUM(CASE WHEN user_id IS NULL THEN 1 ELSE 0 END)/COUNT(*) AS null_user,
       1.0 * SUM(CASE WHEN amount  IS NULL THEN 1 ELSE 0 END)/COUNT(*) AS null_amount,
       1.0 * SUM(CASE WHEN channel IS NULL OR channel = '' THEN 1 ELSE 0 END)/COUNT(*) AS null_channel
FROM dwd_order
WHERE dt >= current_date - INTERVAL '7' DAY
GROUP BY dt ORDER BY dt;
-- 判据：空值率本身高不一定是问题（有的字段天然可空），
--       但空值率「突变」一定是问题（上游改了字段、或关联表没产出）
```

```sql
-- ========== ③ 主键唯一性 ==========
SELECT dt, COUNT(*) AS rows, COUNT(DISTINCT order_id) AS uniq,
       COUNT(*) - COUNT(DISTINCT order_id) AS dup
FROM dwd_order WHERE dt >= current_date - INTERVAL '7' DAY
GROUP BY dt ORDER BY dt;
-- 判据：dup 必须为 0。不为 0 = 上游重跑或 join 膨胀
```

```sql
-- ========== ④ 枚举值：有没有冒出没见过的值 ==========
SELECT status, COUNT(*) AS cnt, MIN(dt) AS first_seen, MAX(dt) AS last_seen
FROM dwd_order WHERE dt >= current_date - INTERVAL '30' DAY
GROUP BY status ORDER BY first_seen DESC;
-- 判据：first_seen 是最近几天的枚举值 = 上游新增了状态，你的 CASE WHEN 可能漏了它
--       某个老枚举值的 last_seen 停在几天前 = 该状态不再产生，可能是上游改动
```

```sql
-- ========== ⑤ 数值分布漂移：均值没变不代表分布没变 ==========
SELECT dt,
       COUNT(*) AS cnt,
       AVG(amount) AS avg_amt,
       APPROX_PERCENTILE(amount, 0.5)  AS p50,
       APPROX_PERCENTILE(amount, 0.95) AS p95,
       MAX(amount) AS max_amt,
       SUM(CASE WHEN amount <= 0 THEN 1 ELSE 0 END) AS non_positive
FROM dwd_order WHERE dt >= current_date - INTERVAL '14' DAY
GROUP BY dt ORDER BY dt;
-- 判据：p50 稳定但 p95/max 暴涨 = 出现异常大额（可能是测试数据或刷单）
--       non_positive > 0 = 金额为 0 或负数，需要确认是退款还是脏数据
```

```sql
-- ========== ⑥ 时间戳合理性 ==========
SELECT
  SUM(CASE WHEN pay_time > current_timestamp             THEN 1 ELSE 0 END) AS future_ts,
  SUM(CASE WHEN pay_time < TIMESTAMP '2020-01-01 00:00:00' THEN 1 ELSE 0 END) AS ancient_ts,
  SUM(CASE WHEN date(pay_time) <> dt                     THEN 1 ELSE 0 END) AS ts_partition_mismatch
FROM dwd_order WHERE dt >= current_date - INTERVAL '7' DAY;
-- 判据：future_ts > 0 = 客户端时钟问题或时区处理错误
--       ts_partition_mismatch 大量出现 = 时区不一致（回 2.1.5）
```

### 2.2.6 区分「数据问题」和「业务问题」的标准流程

**这是分析师最高频的工作场景，也是最能体现水平的地方。**

指标异常时，新手的第一反应是「找业务原因」，老手的第一反应是「先证明数据是对的」。因为**数据问题的概率远高于业务问题**，而且数据问题排查起来更快。

#### 判据速查

| 现象 | 大概率是 | 理由 |
|------|----------|------|
| 指标**整体**翻倍 / 归零 | **数据问题** | 业务不会一夜之间整体翻倍 |
| 变化发生在**整点 / 零点** | **数据问题** | 任务调度时间点 |
| 某个维度**完全消失**（某渠道数据没了） | **数据问题** | 上游表没产出或 join 失败 |
| 变化是**阶梯状**（某一刻突变后维持新水平） | **数据问题**（发版/口径变更） | 业务变化通常是渐变 |
| 变化**渐进、有波动**、只在部分维度 | **业务问题** | 符合真实业务的变化形态 |
| 变化能和**已知业务动作对上**（大促、发版、投放） | **业务问题** | 有因可循 |
| 其他相关指标**同向变化** | **业务问题** | 数据问题通常只影响单个链路 |

#### 标准流程（建议给自己设 30 分钟时间盒）

```
【0-5 分钟】明确异常本身
  ├─ 哪个指标？变化多少？从什么时候开始？
  ├─ 和谁比出来的异常？（同比 / 环比 / 目标 / 其他渠道）
  └─ ⚠️ 先确认「异常」不是正常波动：看过去 30 天的波动区间，
      如果这次变化落在历史 ±2 倍标准差内，可能根本不是异常

【5-15 分钟】数据层排查（2.2.5 的六项校验）
  ├─ 上游分区产出了吗？行数正常吗？
  ├─ 主键唯一吗？（重跑导致重复）
  ├─ 关键字段空值率变了吗？
  ├─ 枚举值有新增吗？（新状态没被 CASE WHEN 覆盖）
  ├─ 口径/代码最近改过吗？（查 git log）
  └─ 埋点最近发版了吗？（查发版记录）
      │
      ├─ 发现问题 → 定位到具体表/字段 → 提给数据团队 → 结束
      └─ 全部正常 → 进入下一步

【15-30 分钟】业务层排查（2.5.7 会详细展开）
  ├─ 维度下钻：是全局还是局部？哪个子群贡献最大？
  ├─ 链路下钻：漏斗哪一环节掉的？
  ├─ 对齐业务动作：发版、活动、投放、竞品、外部事件
  └─ 形成假设 → 设计验证方式
```

#### 一个完整的排查示例

**现象**：9 月 3 日卡片 CTR 从 8% 跌到 5%。

```sql
-- 步骤 1：确认异常幅度和历史波动
SELECT dt,
       SUM(clicks) AS clk, SUM(exposes) AS exp,
       1.0*SUM(clicks)/NULLIF(SUM(exposes),0) AS ctr
FROM dws_card_daily WHERE dt >= current_date - INTERVAL '30' DAY
GROUP BY dt ORDER BY dt;
-- 看过去 30 天 CTR 的波动范围。如果平时就在 5%-9% 之间跳，那这不是异常。
```

```sql
-- 步骤 2：分子分母分别看 —— 这一步能直接定位一半的问题
-- 是点击少了（分子跌）？还是曝光多了（分母涨）？
-- · 曝光暴涨 + 点击不变 → 大概率曝光埋点口径变了（数据问题）
-- · 点击暴跌 + 曝光不变 → 可能是点击埋点坏了，或者真的没人点（需继续查）
-- · 两者同比例变化 → CTR 不该变，检查计算逻辑
```

```sql
-- 步骤 3：拆 app_version —— 区分「发版引入」还是「全量业务变化」
SELECT app_version, SUM(clicks) AS clk, SUM(exposes) AS exp,
       1.0*SUM(clicks)/NULLIF(SUM(exposes),0) AS ctr
FROM dws_card_daily WHERE dt = DATE '2026-09-03'
GROUP BY app_version ORDER BY exp DESC;
-- 如果只有新版本 CTR 低 → 新版本的埋点或 UI 有问题（数据问题 or 产品问题）
-- 如果所有版本一起低 → 是服务端/内容侧的变化（业务问题）
```

```sql
-- 步骤 4：看匹配率是否同时下跌（2.2.4）
-- 如果匹配率从 95% 掉到 70%，那 CTR 的变化很可能是埋点丢失造成的假象
```

> **输出纪律**：排查完之后，无论结论是数据问题还是业务问题，都要明确写出：
> **① 结论是什么 ② 依据是哪几个数 ③ 建议谁做什么 ④ 还有哪些没排除的可能**。
> 只说「查了，是数据问题」不算完成工作。

### ✅ 2.2 自检

<details><summary>Q：前端曝光比后端返回少 15%，CTR 还能用吗？</summary>

**分情况**：

1. **如果点击也来自前端**（分子分母同源）：**可以用**。只要丢失是随机的，分子分母同比例缩小，CTR 不变。但绝对曝光量不能报。
2. **如果点击来自后端**（分子分母异源）：**不能直接用**。分母丢 15%、分子几乎不丢，CTR 会被高估约 17.6%（1/0.85 - 1）。
3. **无论哪种，都要先验证丢失是不是随机的**：按 app_version / os / network 拆开看匹配率。如果弱网用户丢得多，那么「网络好的用户」被过度代表，CTR 偏向他们的行为特征，这不是靠比率就能消除的偏差。

**结论表述应该是**：「基于前端同源口径，CTR 为 X%，趋势可信；绝对曝光量因 15% 上报丢失偏低，不建议对外报。已验证丢失在各版本/网络类型间分布均匀（最大差异 3pp），不存在系统性偏差。」
</details>

---

## 2.3 Python 数据分析

### 2.3.1 什么时候该从 SQL 切到 Python

**不要为了用 Python 而用 Python。** SQL 能做的就用 SQL 做——它更快（数据不用出库）、更容易 review、更容易复用。

| 场景 | 用什么 |
|------|--------|
| 聚合、join、窗口计算 | **SQL** |
| 数据量大（千万行以上） | **SQL**（在数据库里算完再取结果） |
| 统计检验、置信区间、bootstrap | **Python** |
| 复杂的循环逻辑、递归 | **Python** |
| 出图（尤其是多子图、复杂标注） | **Python** |
| 需要调接口、爬数据、读文件 | **Python** |
| 建模、预测 | **Python** |
| 要做成定时报告并发出去 | **Python**（SQL 取数 + Python 组装） |

**标准工作模式**：SQL 把数据聚合到「几万行以内」，Python 接手做分析和出图。不要把千万行原始数据拉到本地。

### 2.3.2 pandas 核心

#### `read_sql`：取数

```python
import pandas as pd
from sqlalchemy import create_engine

engine = create_engine("trino://user@host:443/hive")

# ✓ 参数化日期，不要硬编码
sql = """
SELECT dt, channel, SUM(amount) AS gmv, COUNT(DISTINCT user_id) AS uv
FROM dwd_order
WHERE dt BETWEEN DATE '{start}' AND DATE '{end}' AND status = 'paid'
GROUP BY dt, channel
"""
df = pd.read_sql(sql.format(start="2026-09-01", end="2026-09-07"), engine)

# 大结果集分块读，避免内存爆掉
for chunk in pd.read_sql(sql, engine, chunksize=100_000):
    process(chunk)
```

#### `merge`：⭐️ 一定要用 `validate` 参数

pandas 的 `merge` 和 SQL 的 JOIN 一样会膨胀（第一章 1.3）。**但 pandas 给了一个 SQL 没有的神器**：

```python
# ✓ validate 会在粒度不符时直接抛异常，而不是静默膨胀
orders = pd.merge(
    order_df, user_df,
    on="user_id",
    how="left",
    validate="many_to_one",   # ← 断言：左表多行对右表一行
)
```

`validate` 的四个取值：

| 值 | 断言 | 用在哪 |
|----|------|--------|
| `"one_to_one"` | 两边键都唯一 | 两张同粒度的表拼接 |
| `"many_to_one"` | **右表键唯一** | ⭐️ 事实表 join 维度表，最常用 |
| `"one_to_many"` | 左表键唯一 | 维度表 join 明细表 |
| `"many_to_many"` | 不校验 | 明知会膨胀时才用 |

> **把 `validate="many_to_one"` 写成肌肉记忆**。它把第一章 1.3 那个「JOIN 前手动检查唯一性」的动作自动化了，一行代码换来永远不会静默膨胀。

```python
# 其他必须知道的参数
pd.merge(a, b, on="k", how="left",
         indicator=True,          # 加一列 _merge，标记每行来自 both/left_only/right_only
         suffixes=("_l", "_r"))   # 同名列的后缀，默认 _x/_y 太隐晦

# 用 indicator 快速看匹配率
m = pd.merge(a, b, on="k", how="left", indicator=True)
print(m["_merge"].value_counts(normalize=True))
# both: 0.87, left_only: 0.13  ← 匹配率 87%
```

#### `groupby`：聚合

```python
# ✓ 多指标一次算完，命名清晰（named aggregation）
result = df.groupby("channel").agg(
    gmv       = ("amount",  "sum"),
    orders    = ("order_id","count"),
    uv        = ("user_id", "nunique"),
    avg_amt   = ("amount",  "mean"),
    p95_amt   = ("amount",  lambda s: s.quantile(0.95)),
).reset_index()

# ✓ transform：保留原行数，加一列组内聚合值（相当于 SQL 的窗口函数）
df["channel_gmv"] = df.groupby("channel")["amount"].transform("sum")
df["pct_of_channel"] = df["amount"] / df["channel_gmv"]

# ✓ 组内排序取 Top N（相当于 ROW_NUMBER）
top3 = df.sort_values("amount", ascending=False).groupby("channel").head(3)
```

**两个坑**：
```python
# ⚠️ ① groupby 默认丢弃 key 为 NaN 的行 —— 和 SQL 的 GROUP BY 行为不同！
df.groupby("city").size()              # city 为 NaN 的行凭空消失
df.groupby("city", dropna=False).size() # ✓ 保留

# ⚠️ ② 聚合后 index 变成了 key，记得 reset_index()
```

#### `pivot_table`：透视

```python
# 行=日期，列=渠道，值=GMV
pt = df.pivot_table(
    index="dt", columns="channel", values="amount",
    aggfunc="sum",
    fill_value=0,        # ← 缺失补 0，不是 NaN
    margins=True,        # ← 加合计行/列
)
```

> `pivot_table` 会聚合，`pivot` 不会（遇到重复会报错）。**优先用 `pivot_table`**。

#### `apply`：⭐️ 能不用就不用

`apply` 是 Python 级的逐行循环，比向量化操作慢 **10-100 倍**。

```python
# ✗ 慢
df["level"] = df.apply(lambda r: "high" if r["amount"] > 300 else "low", axis=1)

# ✓ 快 100 倍：向量化
df["level"] = np.where(df["amount"] > 300, "high", "low")

# ✓ 多条件分档：pd.cut（相当于 SQL 的 CASE WHEN 分桶）
df["bucket"] = pd.cut(
    df["amount"],
    bins=[-np.inf, 100, 300, 1000, np.inf],
    labels=["0-100", "100-300", "300-1000", "1000+"],
)

# ✓ 映射：map / replace
df["status_cn"] = df["status"].map({"paid": "已支付", "refund": "已退款"})

# ✓ 多条件选择：np.select
df["tier"] = np.select(
    [df["gmv"] > 10000, df["gmv"] > 1000, df["gmv"] > 0],
    ["VIP", "普通", "低价值"],
    default="未消费",
)
```

**`apply` 的合理用途**：逻辑确实复杂到无法向量化，且数据量在几万行以内。

#### `resample`：时间序列重采样

```python
df = df.set_index(pd.to_datetime(df["dt"]))

daily  = df["amount"].resample("D").sum()      # 按天
weekly = df["amount"].resample("W-MON").sum()  # 按周（周一为起点）
monthly= df["amount"].resample("MS").sum()     # 按月（月初）

# ⚠️ resample 会自动补齐缺失日期并填 NaN —— 这正是我们想要的（呼应第一章 1.8.3）
daily = daily.fillna(0)
```

### 2.3.3 缺失值、异常值、重复值

#### 缺失值：先搞清楚「为什么缺」，再决定怎么补

| 缺失原因 | 正确处理 | 错误处理 |
|----------|----------|----------|
| 业务上本来就没有（未填手机号） | 保留 NaN，或填「未知」作为一个类别 | 填 0 或均值 |
| 该有但埋点丢了 | **不要填充**，评估丢失率并标注 | 当成 0 |
| 数值型指标的天然零值（当天无订单） | 填 0 | 保留 NaN（会让 mean 算错，见第一章 1.4.4） |
| 上游任务失败导致整天缺失 | 排除该天，不要插值 | 用前后均值插值（会掩盖事故） |

```python
# 先看清楚缺失情况
df.isna().sum() / len(df)              # 各列缺失率
df.isna().sum(axis=1).value_counts()   # 每行缺几个字段

# 填充
df["amount"] = df["amount"].fillna(0)
df["city"]   = df["city"].fillna("未知")
df["gmv"]    = df["gmv"].ffill()       # 前向填充（时间序列，慎用）
```

> **最重要的一条纪律：填充之前先记录缺失率，并写进报告。** 填完就看不出来了，读者会以为数据是完整的。

#### 异常值：业务数据长尾多，别乱用 3σ

| 方法 | 原理 | 适用 | 不适用 |
|------|------|------|--------|
| **3σ** | 超出均值 ±3 标准差 | 近似正态的数据 | ❌ 长尾数据（会误删大量真实值） |
| **IQR** | 超出 Q1-1.5IQR ~ Q3+1.5IQR | 偏态数据 | 极端长尾仍会误判 |
| **分位数截断（winsorize）** | 把 P1 以下、P99 以上截断到边界 | ⭐️ **业务数据首选** | — |
| **业务规则** | 金额 ≤ 0、下单时间在未来 | ⭐️ **优先用这个** | — |

```python
# ✓ 先用业务规则，这是最可靠的
df = df[(df["amount"] > 0) & (df["pay_time"] <= pd.Timestamp.now())]

# ✓ 再用分位数截断（保留行，只压缩极值 —— 比直接删除更安全）
lo, hi = df["amount"].quantile([0.01, 0.99])
df["amount_w"] = df["amount"].clip(lo, hi)

# ⚠️ 删除异常值前一定要看清楚它们是什么
outliers = df[df["amount"] > df["amount"].quantile(0.999)]
print(outliers[["user_id", "order_id", "amount", "pay_time"]])
# 常见发现：是测试账号、是真实的大客户、是同一秒的批量刷单
# 这三种的处理方式完全不同！
```

> **核心原则：异常值不是「数值大」，而是「不属于你要分析的总体」。**
> 一个真实的大客户不是异常值，删掉它你的 GMV 就错了。一个测试账号才是异常值。**分清楚要靠看明细，不靠统计规则。**

#### 重复值

```python
df.duplicated().sum()                              # 完全重复的行
df.duplicated(subset=["order_id"]).sum()           # 业务主键重复

# ✓ 按主键去重，保留最新（相当于第一章的 ROW_NUMBER）
df = df.sort_values("update_time").drop_duplicates(subset=["order_id"], keep="last")
```

### 2.3.4 时间序列处理

```python
# rolling：移动窗口（相当于 SQL 的 SUM OVER ROWS BETWEEN n PRECEDING）
daily["ma7"]  = daily["gmv"].rolling(7, min_periods=1).mean()
daily["std7"] = daily["gmv"].rolling(7).std()

# shift：取前 n 期（相当于 LAG）
daily["prev"] = daily["gmv"].shift(1)
daily["dod"]  = daily["gmv"] / daily["prev"] - 1
daily["wow"]  = daily["gmv"] / daily["gmv"].shift(7) - 1   # 周同比，自动对齐星期

# ⭐️ reindex：补齐缺失日期（对应第一章 1.8.3 的日期骨架）
full_idx = pd.date_range("2026-09-01", "2026-09-07", freq="D")
daily = daily.reindex(full_idx, fill_value=0)
```

> **顺序纪律（和第一章 Q5 一致）**：**先 reindex 补齐日期，再算 rolling / shift**。
> 否则 `shift(1)` 取的是「结果里的上一行」而不是「日历上的前一天」，缺一天就全错位。

### 2.3.5 出图

| 库 | 用在哪 | 特点 |
|----|--------|------|
| **matplotlib** | 精细控制、出版级图 | 啰嗦但万能 |
| **seaborn** | 统计图（分布、相关、分组对比） | 一行出图，默认好看 |
| **plotly** | 交互式、要嵌网页 | 能 hover、能缩放 |

```python
import matplotlib.pyplot as plt

# ⚠️ 中文乱码（Mac/Linux 上最常见的第一个坑）
plt.rcParams["font.sans-serif"] = ["PingFang SC", "Hiragino Sans GB", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False   # 负号显示为方块的修复
```

```python
# 日线 + MA7 的标准画法（呼应第一章 1.7.5：报趋势必须带 MA7）
fig, ax = plt.subplots(figsize=(12, 4))
ax.plot(daily.index, daily["gmv"], alpha=0.35, lw=1, label="日 GMV")
ax.plot(daily.index, daily["ma7"], lw=2, label="7 日移动平均")
ax.set_title("GMV 趋势")
ax.legend()
ax.grid(alpha=0.3)
fig.autofmt_xdate()
plt.tight_layout()
```

出图的五条纪律：
1. **一张图只说一件事**
2. **坐标轴从 0 开始**（除非你明确说明是截断轴，否则会夸大差异）
3. **标题写结论，不写「XX 趋势图」**。写「9 月 3 日后 CTR 下降 3pp，主要来自 Android 新版本」
4. **趋势图必须带平滑线**（MA7），否则周内波动会盖住真实趋势
5. **标注关键事件**（发版、大促）：`ax.axvline(pd.Timestamp("2026-09-03"), ls="--", c="red")`

### 2.3.6 Jupyter 规范用法

**核心原则：Restart Kernel & Run All 必须能跑通。**

做不到这一点的 notebook 是不可信的——你不知道当前的变量是哪一版代码产生的，结论可能来自一个已经被你改掉的逻辑。

**标准结构**：

```python
# ===== Cell 1：参数（全部集中在这里，方便改）=====
START_DATE = "2026-09-01"
END_DATE   = "2026-09-07"
CHANNEL    = None          # None 表示不筛渠道
RANDOM_SEED = 42

# ===== Cell 2：导入 + 全局配置 =====
import pandas as pd, numpy as np, matplotlib.pyplot as plt
np.random.seed(RANDOM_SEED)
pd.set_option("display.max_columns", 50)
plt.rcParams["font.sans-serif"] = ["PingFang SC", "SimHei"]

# ===== Cell 3：取数（原始数据单独存一份，后续都基于副本）=====
df_raw = pd.read_sql(SQL.format(start=START_DATE, end=END_DATE), engine)
df_raw.to_parquet(f"cache/raw_{START_DATE}_{END_DATE}.parquet")   # 缓存，避免反复查库

# ===== Cell 4：清洗（从 raw 复制，不要原地改 raw）=====
df = df_raw.copy()
df = df[df["amount"] > 0]

# ===== Cell 5+：分析 =====
```

**六条纪律**：
1. 参数集中在第一个 cell，不要散落在各处
2. 不要原地修改原始数据（`df_raw` 保持不动，分析用 `df = df_raw.copy()`）
3. 取数结果缓存成文件，避免每次重跑都查库
4. 定期 **Restart & Run All** 验证
5. 每个分析 cell 上面写一句 markdown 说明「这一步在回答什么问题」
6. 交付前删掉所有试错 cell —— **notebook 是给人看的，不是你的草稿纸**

### 2.3.7 从 notebook 到可复用代码

**信号：同一段代码复制粘贴第 3 次时，就该抽成函数了。**

```python
# analysis_utils.py
from typing import Optional
import pandas as pd

def load_orders(start: str, end: str, channel: Optional[str] = None) -> pd.DataFrame:
    """取指定日期区间的已支付订单。"""
    sql = """
    SELECT dt, user_id, order_id, channel, amount
    FROM dwd_order
    WHERE dt BETWEEN DATE '{start}' AND DATE '{end}' AND status = 'paid'
      {channel_filter}
    """.format(
        start=start, end=end,
        channel_filter=f"AND channel = '{channel}'" if channel else "",
    )
    return pd.read_sql(sql, get_engine())

def add_trend_cols(df: pd.DataFrame, value_col: str, window: int = 7) -> pd.DataFrame:
    """补齐日期后加移动平均和环比。顺序不可颠倒。"""
    full = pd.date_range(df.index.min(), df.index.max(), freq="D")
    df = df.reindex(full, fill_value=0)                 # ① 先补日期
    df[f"ma{window}"] = df[value_col].rolling(window, min_periods=1).mean()
    df["dod"] = df[value_col].pct_change()              # ② 再算环比
    return df
```

配置与代码分离：

```python
# config.yaml —— 口径、阈值、表名都放这里，改口径不用改代码
date_range: {start: "2026-09-01", end: "2026-09-07"}
tables:     {order: "dwd_order", event: "dwd_event"}
thresholds: {min_match_rate: 0.85, outlier_quantile: 0.99}
```

### 2.3.8 polars / duckdb（可选，但值得一学）

**什么时候需要**：本地处理几百万到几千万行，pandas 开始吃不消（内存爆、慢到无法迭代）。

```python
# duckdb：在本地文件上直接写 SQL —— 对分析师最友好
import duckdb
df = duckdb.sql("""
    SELECT channel, SUM(amount) AS gmv, COUNT(DISTINCT user_id) AS uv
    FROM 'data/orders_*.parquet'      -- 直接查文件，支持通配符
    WHERE dt >= '2026-09-01'
    GROUP BY channel
""").df()   # 结果转成 pandas DataFrame

# 也可以混着用：DataFrame 直接当表查
duckdb.sql("SELECT * FROM df WHERE amount > 300").df()
```

```python
# polars：pandas 的高性能替代，API 更严格（不容易写出隐式错误）
import polars as pl
df = pl.read_parquet("data/orders.parquet")
out = (df.filter(pl.col("status") == "paid")
         .group_by("channel")
         .agg([pl.col("amount").sum().alias("gmv"),
               pl.col("user_id").n_unique().alias("uv")]))
```

**推荐顺序**：先把 pandas 用熟 → 遇到性能瓶颈时上 **duckdb**（学习成本最低，你已经会 SQL 了）→ 有需要再学 polars。

### ✅ 2.3 自检

<details><summary>Q：为什么 merge 之后行数变多了，但你没发现？</summary>

因为 pandas 的 merge **默认不校验粒度**，右表有重复键时会静默膨胀，和 SQL 的 JOIN 一模一样（第一章 1.3）。

**根治办法**：养成写 `validate="many_to_one"` 的习惯。粒度不符时它会直接抛 `MergeError`，把静默错误变成显式报错。
</details>

---

## 2.4 A/B 实验 ⭐️ 核心竞争力

> **为什么这一节最重要**：
> 会取数的人很多，能判断「一个改动到底有没有效果」的人很少。前者是工具人，后者参与决策。
>
> 更现实的一点：**A/B 实验是分析师唯一能「否决」一个上线决定的场合**。这是专业权威的来源。

### 2.4.1 一个实验的完整生命周期

```
① 设计      假设 → 指标 → 随机化单元 → 样本量估算
                ↓
② 上线前    AA 实验验证分流与埋点
                ↓
③ 运行中    SRM 校验（每天）+ 护栏指标监控 + 【不看主指标】
                ↓
④ 到期      到达预设样本量/时长 → 才开始分析
                ↓
⑤ 分析      主指标显著性 + 置信区间 + 分维度 + 陷阱排查
                ↓
⑥ 决策      上线 / 不上线 / 继续跑 / 重新设计
                ↓
⑦ 复盘      上线后回归验证：实际效果和实验估计一致吗？
```

**新手最常跳过的是 ②、③、⑦**。而这三步恰恰是「实验结论可信」的保障。

### 2.4.2 实验设计

#### 假设怎么写

坏的假设：「优化推荐算法，提升用户体验」——不可证伪，没法验。

好的假设有四个要素：

```
【改动】把首页卡片从 3 个增加到 5 个
【机制】用户可选择的内容变多，找到感兴趣内容的概率上升
【预期】首页卡片点击率提升
【幅度】相对提升 5% 以上（从 8% 提升到 8.4% 以上）
```

**为什么要预估幅度**：没有预期幅度就没法算样本量（2.4.3），没有样本量就不知道该跑多久，跑到什么时候停全凭感觉——这就是「偷看数据」的温床。

#### 指标三层结构

| 层级 | 作用 | 数量 | 例子 |
|------|------|------|------|
| **主指标（OEC）** | 决定上不上线 | **1 个**（最多 2 个） | 卡片 CTR |
| **护栏指标** | 防止为主指标牺牲其他 | 3-5 个 | 页面加载耗时、崩溃率、退款率、次日留存 |
| **观测指标** | 帮助解释原因，**不参与决策** | 不限 | 各位置的 CTR、滚动深度、会话时长 |

**三条硬规则**：

1. **主指标必须在实验开始前确定，写进实验文档。** 事后从 20 个指标里挑一个显著的，叫 p-hacking，不叫分析。
2. **主指标只能有一个。** 两个指标一升一降时，没有事先定好的规则就无法决策，只会变成谁嗓门大谁赢。
3. **护栏指标的判定是「不显著恶化」，不是「要显著改善」。** 护栏是底线，不是目标。

#### 随机化单元（这是最容易设计错的地方）

| 单元 | 说明 | 适用 | 风险 |
|------|------|------|------|
| **用户级** | 同一用户始终在同一组 | ⭐️ 绝大多数场景 | 需要稳定的 user_id |
| **设备级** | 按 device_id 分 | 未登录场景 | 一人多设备会分到不同组 |
| **会话级** | 每次会话重新分 | 短期体验类改动 | 同一用户体验不一致，且会污染留存类指标 |
| **请求级** | 每个请求重新分 | 纯后端性能实验 | ⚠️ 用户会看到忽明忽暗的效果，且**方差计算必须特殊处理** |

**⭐️ 最关键的一条规则**：

> **随机化单元的粒度，必须 ≥ 指标的粒度。**
>
> - 用户级分流 + 用户级指标（人均 GMV）→ ✅ 标准情况
> - 用户级分流 + 请求级指标（CTR，每人多次曝光）→ ⚠️ **方差会被低估**，见 2.4.9 陷阱 ⑤
> - 会话级分流 + 用户级指标（次日留存）→ ❌ **完全错误**，同一用户跨组，指标归属不清

### 2.4.3 样本量与 MDE

#### 三个量的关系

```
样本量 n ↑  →  能检出的最小效应 MDE ↓（越敏感）
基线方差 ↑  →  需要的样本量 ↑
要求的显著性/功效 ↑  →  需要的样本量 ↑
```

**MDE（Minimum Detectable Effect）= 在给定样本量下，你能可靠检出的最小效应。** 比 MDE 小的真实效应，你的实验根本看不见。

#### 比例型指标（CTR、转化率）的样本量公式

$$n \approx \frac{2 \cdot (z_{\alpha/2} + z_{\beta})^2 \cdot p(1-p)}{\delta^2}$$

- $n$ = **每组**样本量
- $p$ = 基线转化率
- $\delta$ = 绝对提升量（不是相对！）
- $\alpha=0.05, \beta=0.2$（功效 80%）时，$(z_{\alpha/2}+z_\beta)^2 \approx 7.85$

```python
from scipy import stats
import math

def sample_size_proportion(p_baseline, mde_relative, alpha=0.05, power=0.8):
    """比例型指标的每组样本量。mde_relative 传 0.05 表示相对提升 5%。"""
    delta = p_baseline * mde_relative              # 绝对提升
    z_a = stats.norm.ppf(1 - alpha / 2)
    z_b = stats.norm.ppf(power)
    p_bar = p_baseline + delta / 2                 # 两组的平均比例
    n = 2 * (z_a + z_b) ** 2 * p_bar * (1 - p_bar) / delta ** 2
    return math.ceil(n)

# 基线 CTR 8%，想检出相对 5% 的提升
print(sample_size_proportion(0.08, 0.05))   # 73,855  每组
print(sample_size_proportion(0.08, 0.10))   # 18,873
print(sample_size_proportion(0.08, 0.02))   # 455,428
```

#### 💡 最重要的直觉：**样本量 ∝ 1/MDE²**

> **想检出的效应小一半，需要的样本量是四倍。**

这条直觉能帮你在实验设计阶段就做出判断：

| 基线 CTR 8%，目标相对提升 | 每组样本量 | 每组每天 2.5 万新用户时需要 |
|---------------------------|-----------|---------------------------|
| 20% | 4,922 | < 1 天 |
| 10% | 18,873 | < 1 天 |
| 5% | 73,855 | 约 3 天 |
| 2% | 455,428 | 约 19 天 |
| 1% | 1,813,480 | **约 73 天** ❌ 不现实 |

#### 均值型指标（人均 GMV）

$$n \approx \frac{2 \cdot (z_{\alpha/2}+z_\beta)^2 \cdot \sigma^2}{\delta^2}$$

```python
def sample_size_mean(std, mde_absolute, alpha=0.05, power=0.8):
    z_a, z_b = stats.norm.ppf(1 - alpha/2), stats.norm.ppf(power)
    return math.ceil(2 * (z_a + z_b)**2 * std**2 / mde_absolute**2)

# 人均 GMV 均值 100、标准差 300（长尾！），想检出 +5 元
print(sample_size_mean(300, 5))   # 56,512  每组
```

> **⚠️ GMV 类指标的 σ 通常远大于均值（长尾），样本量需求会大得惊人。**
> 这也是为什么很多公司的 GMV 实验永远跑不出显著结果——不是没效果，是功效不够。
> 缓解手段：① 用截断后的 GMV（winsorize 到 P99）作为主指标，能大幅降方差；② 用 CUPED（2.4.11）。

#### 实操流程

```
① 查基线：过去 7-14 天该指标的均值和标准差
② 定 MDE：业务上「多小的提升才值得上线」？（考虑开发成本、维护成本）
③ 算样本量 → 除以每日可分流人数 → 得到需要的天数
④ ⚠️ 天数向上取整到 7 的倍数（覆盖完整的周内周期，消除周内效应）
⑤ 如果天数 > 4 周 → 回到 ② 重新谈 MDE，或换指标，或放弃这个实验
```

### 2.4.4 分流机制

#### 哈希分桶

```python
import hashlib

def assign_variant(user_id: str, exp_id: str, traffic: float = 1.0) -> str | None:
    """
    exp_id 作为 salt 混入哈希 —— 这一步至关重要。
    traffic: 实验总流量占比，0.2 表示只有 20% 用户进入实验。
    """
    key = f"{user_id}:{exp_id}".encode()
    bucket = int(hashlib.md5(key).hexdigest(), 16) % 10000   # 0-9999
    if bucket >= traffic * 10000:
        return None                       # 不进实验
    return "control" if bucket < traffic * 10000 / 2 else "treatment"
```

**为什么必须用 `exp_id` 当 salt**：如果所有实验都用 `hash(user_id) % 100`，那么在实验 A 里进 treatment 的用户，在实验 B 里也一定进 treatment。两个实验的效应完全混在一起，谁也说不清是哪个改动起的作用。

#### 分层正交（Layer）

多个实验同时跑时的标准方案：

```
流量 100%
  ├─ Layer 1（UI 层）：  exp_ui_001    50% / 50%
  ├─ Layer 2（算法层）： exp_algo_002  50% / 50%
  └─ Layer 3（文案层）： exp_copy_003  50% / 50%

每一层独立哈希（各自的 salt），层与层之间正交：
UI 实验的 treatment 组里，算法实验的 treatment 和 control 各占一半。
→ 三个实验可以同时跑，互不干扰。
```

**互斥实验**：如果两个实验改的是同一个位置（比如都在改首页卡片），它们必须放在**同一层**，流量互斥切分（各占 50%，一个用户只能进其中一个）。放在不同层会导致两个改动叠加，页面变成谁也没设计过的样子。

#### 分流的四项校验

```sql
-- ① 分组是否稳定：同一用户有没有在两组都出现过（最严重的 bug）
SELECT COUNT(*) AS flip_users FROM (
  SELECT user_id FROM dwd_exp_assignment WHERE exp_id = 'exp_001'
  GROUP BY user_id HAVING COUNT(DISTINCT variant) > 1
) t;
-- 必须为 0。不为 0 说明分流不稳定（换了 salt / 用了随机数 / user_id 变过）
```

```sql
-- ② 分组比例（SRM 前置检查，详见 2.4.5）
SELECT variant, COUNT(DISTINCT user_id) AS uv
FROM dwd_exp_assignment WHERE exp_id = 'exp_001' GROUP BY variant;
```

```sql
-- ③ 两组的「实验前特征」是否均衡（新老用户比、渠道分布、历史消费）
SELECT a.variant,
       COUNT(DISTINCT a.user_id)                                        AS uv,
       AVG(CASE WHEN u.reg_date >= current_date - INTERVAL '30' DAY THEN 1.0 ELSE 0 END) AS new_user_rate,
       AVG(h.hist_gmv_30d)                                              AS avg_hist_gmv
FROM dwd_exp_assignment a
JOIN dim_user u ON a.user_id = u.user_id
LEFT JOIN dws_user_hist h ON a.user_id = h.user_id
WHERE a.exp_id = 'exp_001'
GROUP BY a.variant;
-- 两组这些「实验开始前就已确定」的特征应该基本一致。差异大 = 分流有偏
```

```sql
-- ④ 分流时间分布是否一致（一组集中在早上、一组集中在晚上 = 有问题）
SELECT variant, date_trunc('hour', assign_time) AS hr, COUNT(*) AS cnt
FROM dwd_exp_assignment WHERE exp_id = 'exp_001'
GROUP BY 1, 2 ORDER BY 2, 1;
```

### 2.4.5 SRM 校验 ⭐️ 过不了这关，所有结论作废

#### 什么是 SRM

**SRM（Sample Ratio Mismatch，样本比例不匹配）**：实际分组比例和设计比例显著不符。

设计 50/50，实际 50.3/49.7 —— 看起来只差 0.6%。在 10 万样本下这完全正常（p=0.058），但在 **100 万样本**下，同样的比例偏差对应 p≈2×10⁻⁹，即十亿分之二的概率。**这时它意味着分流或数据链路某处有系统性 bug。**

> **注意：SRM 的判定依赖样本量。** 同样的比例偏差，样本量越大越可疑。所以必须跑卡方检验，不能靠肉眼看比例。

#### 为什么 SRM 是「一票否决」

因为 SRM 说明**两组的用户不是随机可比的**。既然不可比，两组指标的差异就无法归因于实验改动——可能是「treatment 组恰好少了一批低活用户」造成的。

**这时候主指标显著与否已经没有意义了。**

#### 怎么算

用卡方检验：

```python
from scipy import stats

def srm_check(n_control, n_treatment, expected_ratio=0.5):
    total = n_control + n_treatment
    exp_c = total * expected_ratio
    exp_t = total * (1 - expected_ratio)
    chi2 = (n_control - exp_c)**2 / exp_c + (n_treatment - exp_t)**2 / exp_t
    p = 1 - stats.chi2.cdf(chi2, df=1)
    return {"chi2": round(chi2, 2), "p_value": p,
            "srm": p < 0.001,            # ← 阈值用 0.001，不是 0.05
            "actual_ratio": round(n_control / total, 4)}

print(srm_check(50000, 50000))   # chi2=0.00,  p=1.0       ✅ 正常
print(srm_check(50300, 49700))   # chi2=3.60,  p=0.058     ✅ 正常（10 万样本下这点偏差不算异常）
print(srm_check(50600, 49400))   # chi2=14.40, p=1.5e-4    ❌ SRM
print(srm_check(51000, 49000))   # chi2=40.00, p=2.5e-10   ❌ 严重 SRM
```

> **阈值为什么用 p < 0.001 而不是 0.05**：SRM 检验会在每个实验、每天都跑，检验次数极多。用 0.05 会产生大量误报，导致「狼来了」。业界标准是 0.001。

#### SRM 的常见原因（按出现频率排序）

| 原因 | 表现 | 怎么查 |
|------|------|--------|
| **① 埋点/日志在某一组丢失更多** | treatment 组人数偏少 | 对比分流表人数 vs 曝光表人数，看两组的「分流→曝光」留存率 |
| **② treatment 组有 bug 导致崩溃/白屏** | treatment 组人数偏少 | 查崩溃率、接口错误率 |
| **③ 分流代码在某个分支提前 return** | 比例固定偏移 | Code review 分流逻辑 |
| **④ 缓存/CDN 导致部分用户看到错误版本** | 比例随时间漂移 | 看比例的时间序列 |
| **⑤ 分析时的过滤条件对两组不对称** | 只在分析阶段出现 SRM | 检查 WHERE 条件（⚠️ 最常见的分析师自身错误） |
| **⑥ 机器人/爬虫流量在一组聚集** | 某组有异常高频用户 | 看人均请求数的分布 |
| **⑦ 用户 id 变化**（登录/登出、多设备） | 缓慢漂移 | 看 flip_users |

#### ⚠️ 原因 ⑤ 是分析师自己最容易犯的

```sql
-- ✗ 这个 WHERE 引入了 SRM
SELECT a.variant, COUNT(DISTINCT a.user_id)
FROM dwd_exp_assignment a
JOIN dwd_event e ON a.user_id = e.user_id      -- ← 只统计「有行为」的用户
WHERE a.exp_id = 'exp_001'
GROUP BY a.variant;
-- 如果 treatment 让更多用户流失（没有行为），那么这个 JOIN 会把他们过滤掉，
-- 剩下的两组不再可比 —— 你把实验效应本身当成了过滤条件。
```

> **规则：分析实验时，分母永远用「所有被分流的用户」，不要用「有某种行为的用户」。**
> 这在实验分析里叫 **ITT（Intent-To-Treat，意向治疗分析）原则**：只要被分流了就算数，不管他有没有真的体验到改动。
> 这条规则违反起来极其自然（「我只想看用过这个功能的人啊」），但它会系统性地高估实验效果。

#### 每日 SRM 监控

```sql
-- 按天看比例漂移，能定位问题从哪天开始
SELECT dt,
       SUM(CASE WHEN variant='control'   THEN 1 ELSE 0 END) AS n_c,
       SUM(CASE WHEN variant='treatment' THEN 1 ELSE 0 END) AS n_t,
       1.0*SUM(CASE WHEN variant='control' THEN 1 ELSE 0 END)/COUNT(*) AS ctrl_ratio
FROM dwd_exp_assignment WHERE exp_id = 'exp_001'
GROUP BY dt ORDER BY dt;
```

### 2.4.6 AA 实验

**AA 实验 = 两组都用同样的版本，跑一遍看指标有没有「显著差异」。**

正常情况下 AA 实验应该什么都测不出来。如果测出了显著差异，说明你的实验系统本身有问题。

**AA 实验能发现什么**：

| 问题 | 表现 |
|------|------|
| 分流不均 | SRM 检出 |
| 埋点在某组丢失 | 某组指标系统性偏低 |
| 指标计算逻辑有 bug | 两组差异稳定存在 |
| **方差估计不准** | ⭐️ 多次 AA 的 p 值分布不均匀 |

**高级用法：A/A 的 p 值应该服从均匀分布**

```python
# 跑 100 次 AA（或用历史数据做 100 次随机切分），收集 p 值
# 正确的方差估计下，p 值应均匀分布在 [0,1]，即 p<0.05 的比例约为 5%
import numpy as np
p_values = run_many_aa_tests(n=100)
print("p<0.05 的比例:", np.mean(np.array(p_values) < 0.05))
# ≈ 0.05  → 正常
# ≫ 0.05（比如 0.20）→ ⚠️ 方差被低估，假阳性率远超预期，多半是 2.4.9 陷阱 ⑤
```

> **这是检验「方差算法对不对」的唯一可靠方法**，比任何理论推导都直接。新搭的实验平台必须做这一步。

### 2.4.7 假设检验

#### 选哪个检验

| 指标类型 | 检验方法 | Python |
|----------|----------|--------|
| 比例（CTR、转化率） | 双比例 z 检验 | `statsmodels.stats.proportion.proportions_ztest` |
| 均值（人均 GMV、时长） | Welch's t 检验 | `scipy.stats.ttest_ind(..., equal_var=False)` |
| 分类分布（多个状态的占比） | 卡方检验 | `scipy.stats.chi2_contingency` |
| 比率型 + 用户级分流 | **Delta method / Bootstrap** | 见 2.4.9 陷阱 ⑤ |

> **t 检验一律用 `equal_var=False`（Welch）**。两组方差相等的假设在实验里几乎不成立，而 Welch 在方差相等时也几乎无损失。没有理由用 Student's t。

#### 完整的分析代码

```python
import numpy as np
from scipy import stats
from statsmodels.stats.proportion import proportions_ztest, confint_proportions_2indep

def analyze_proportion(x_c, n_c, x_t, n_t, alpha=0.05):
    """比例型指标。x=转化数, n=样本量。"""
    p_c, p_t = x_c / n_c, x_t / n_t
    stat, pval = proportions_ztest([x_t, x_c], [n_t, n_c])
    lo, hi = confint_proportions_2indep(x_t, n_t, x_c, n_c, method="wald", alpha=alpha)
    return {
        "control":      f"{p_c:.4%}",
        "treatment":    f"{p_t:.4%}",
        "abs_lift":     f"{p_t - p_c:+.4%}",
        "rel_lift":     f"{(p_t/p_c - 1):+.2%}",
        "p_value":      round(pval, 4),
        "CI95_abs":     f"[{lo:+.4%}, {hi:+.4%}]",   # ⭐️ 比 p 值重要
        "significant":  pval < alpha,
    }

print(analyze_proportion(x_c=8000, n_c=100000, x_t=8300, n_t=100000))
# {'control': '8.0000%', 'treatment': '8.3000%', 'abs_lift': '+0.3000%',
#  'rel_lift': '+3.75%', 'p_value': 0.0143, 'CI95_abs': '[+0.0600%, +0.5400%]',
#  'significant': True}
```

#### ⭐️ 置信区间比 p 值重要得多

上面的结果如果只报 `p=0.014, 显著`，你丢掉了最关键的信息。

**置信区间 `[+0.06pp, +0.54pp]` 告诉你**：
- 真实效应**至少**是 +0.06pp（相对 +0.75%）
- 真实效应**最多**是 +0.54pp（相对 +6.75%）
- 这个区间跨度很大，说明估计还不够精确

**这才是决策需要的信息**：

```
如果业务上「相对提升 2% 以上才值得上线」：
  置信区间下界对应的相对提升是 +0.75% < 2%
  → 虽然统计显著，但【不能确定】它达到了业务门槛
  → 建议：继续跑，收窄置信区间；或接受不确定性上线并做后验监控
```

> **汇报模板（照抄）**：
> 「实验组 CTR 8.30%，对照组 8.00%，**相对提升 3.75%（95% CI: +0.75% ~ +6.75%），p=0.014**。」
>
> 只说「提升 3.75%，显著」是不完整的——它隐瞒了真实效应可能只有 0.75% 这个事实。

### 2.4.8 p 值的正确解读

**p 值的定义**：

> 假设两组**真的没有差异**，那么观察到「当前这么大或更大的差异」的概率。

**p 值不是什么**（四个最常见的误解）：

| ❌ 错误理解 | ✅ 正确 |
|------------|--------|
| p=0.03 表示「有 3% 的概率两组没差异」 | p 是「假设没差异时，看到这个结果的概率」，不是「没差异的概率」。前者是 P(数据\|假设)，后者是 P(假设\|数据)，两者完全不同 |
| p=0.03 比 p=0.04 「更显著」，效果更好 | p 值大小**不代表效应大小**。大样本下，一个毫无业务意义的 0.01pp 差异也能得到 p<0.001 |
| p>0.05 表示「两组没有差异」 | 只表示「没有足够证据证明有差异」。**可能是真没差异，也可能是样本量不够**（见 2.4.11） |
| p<0.05 表示「结论有 95% 把握」 | 在多次检验、偷看数据等情况下，实际假阳性率远高于 5% |

**p 值的正确用法**：它只是一个「这个差异是不是可能由随机波动造成」的粗筛。**过了这道筛之后，真正用来决策的是置信区间和效应大小。**

> **一句话总结**：**p 值决定「要不要相信有差异」，置信区间决定「差异值不值得上线」。** 缺一不可，但后者更重要。

### 2.4.9 六个必知陷阱

#### 陷阱 ① 偷看数据（Peeking）

**机制**：每看一次数据就做一次检验。检验次数越多，「至少有一次偶然显著」的概率越高。

| 检验次数 | 实际假阳性率 |
|----------|-------------|
| 1 次 | 5% |
| 5 次 | ~14% |
| 10 次 | ~19% |
| **每天看，跑 30 天** | **~35%** |

**更糟的是「看到显著就停」这个行为**：它让假阳性率趋近于 **100%**（只要跑得够久，随机波动迟早会跨过显著线一次）。

**解法**：

1. ✅ **事先定好样本量和时长，到点才看主指标**（最简单，最推荐）
2. ✅ 运行期间**只看护栏指标和 SRM**（这两个是为了及时止损，不涉及决策）
3. ✅ 必须中途看就用**序贯检验**：`alpha spending`、`always-valid p-value`、贝叶斯方法
4. ❌ 不要「看到显著就停」，也不要「看到不显著就延长」——**后者同样是 peeking**

#### 陷阱 ② 多重比较

**机制**：看 20 个指标，即使全都无效，平均也会有 1 个 p<0.05。

**解法**：

```python
from statsmodels.stats.multitest import multipletests

p_values = [0.01, 0.03, 0.04, 0.20, 0.45, 0.60]
# Benjamini-Hochberg 控制 FDR（推荐，比 Bonferroni 更不保守）
reject, p_adj, _, _ = multipletests(p_values, alpha=0.05, method="fdr_bh")
print(list(zip(p_values, p_adj.round(4), reject)))
```

**但更根本的解法是设计层面的**：
- **主指标只有一个，只有它参与决策**
- 护栏指标做校正（它们是多个）
- 观测指标**只用于生成假设，不用于下结论**。在一个观测指标上发现的效应，应该作为**下一个实验的主指标**去验证，而不是当作本次实验的结论

> 这就是「探索（exploratory）」和「验证（confirmatory）」的区别。混淆这两者是分析可信度崩塌的主要原因。

#### 陷阱 ③ 新奇效应与学习效应

| | 新奇效应（Novelty） | 学习效应（Primacy） |
|---|---|---|
| 方向 | 短期**高估**效果 | 短期**低估**效果 |
| 机制 | 用户对新东西好奇，点了再说 | 用户习惯了旧版，新版需要学习成本 |
| 典型 | UI 大改、新功能入口 | 交互逻辑变更、导航结构调整 |
| 曲线形状 | 开始高，逐渐回落 | 开始低，逐渐回升 |

**怎么识别**：

```sql
-- 按「用户进入实验后的第 N 天」看效应，而不是按日历日期
SELECT
  date_diff('day', date(a.assign_time), e.dt) AS days_since_assign,
  a.variant,
  1.0*SUM(e.clicks)/NULLIF(SUM(e.exposes),0)  AS ctr
FROM dwd_exp_assignment a
JOIN dws_user_card_daily e ON a.user_id = e.user_id AND e.dt >= date(a.assign_time)
WHERE a.exp_id = 'exp_001'
GROUP BY 1, 2
HAVING date_diff('day', date(a.assign_time), e.dt) BETWEEN 0 AND 20
ORDER BY 1, 2;
```

**怎么读**：把两组的 CTR 差值按 `days_since_assign` 画出来。
- 差值持续收敛到 0 → 新奇效应，真实长期效果可能是 0
- 差值稳定在某个水平 → 真实效应
- 差值从负转正 → 学习效应，早停会得出错误的负面结论

> **实操建议**：
> ① 实验至少跑 2 周，只用**第 2 周之后**的数据做主结论
> ② 对大改动，保留 1-5% 的长期 holdout 组，观察 1-3 个月

#### 陷阱 ④ 辛普森悖论

**现象**：每个分组里 treatment 都更好，合并后 treatment 反而更差。

**具体例子**：

| | 新用户 CTR | 老用户 CTR | 合计 CTR |
|---|---|---|---|
| **control** | 100/1000 = **10%** | 1800/9000 = **20%** | 1900/10000 = **19.0%** |
| **treatment** | 990/9000 = **11%** | 210/1000 = **21%** | 1200/10000 = **12.0%** |

两个分组里 treatment 都赢了 1pp，合计却输了 7pp。

**原因**：两组的**用户构成完全不同**——control 组 90% 是高 CTR 的老用户，treatment 组 90% 是低 CTR 的新用户。

**关键认知**：

> **在一个分流正确的 A/B 实验里，辛普森悖论不应该出现。**
> 因为随机分流保证了两组的用户构成一致。
>
> **所以一旦出现辛普森悖论，第一反应不是「怎么解释」，而是「分流出问题了」**——去查 SRM（2.4.5）。上面这个例子的分组比例是 1000:9000 vs 9000:1000，这是严重 SRM。

**什么情况下会真的遇到**：
- 观察性数据分析（非实验）——这时辛普森悖论是常态，必须分层看
- 实验中途改过流量配比
- 不同维度的进入实验时间不同（比如新用户是逐渐进入的）

#### 陷阱 ⑤ ⭐️ 用户级分流 + 请求级指标 → 方差低估

**这是最技术性、也最容易被忽略的一个陷阱。**

**场景**：按用户分流，但指标是 CTR = 总点击 / 总曝光。一个用户可能贡献 100 次曝光。

**为什么方差会被低估**：

标准的比例检验假设**每次曝光都是独立样本**。但实际上：
- 同一个用户的 100 次曝光**高度相关**（这个人爱点就都点，不爱点就都不点）
- 真实的独立样本数是**用户数**（比如 1 万），不是曝光数（比如 100 万）
- 用 100 万当样本量算方差 → 方差被低估约 **√100 = 10 倍**
- → 置信区间过窄 → p 值过小 → **大量假阳性**

**怎么发现**：跑 AA 实验（2.4.6），如果 p<0.05 的比例远超 5%，多半就是这个问题。

**两种解法**：

```python
# ✓ 解法一：Delta Method（快，适合生产环境）
import numpy as np

def delta_method_ratio(clicks, exposes):
    """
    clicks, exposes: 长度为「用户数」的数组，每个元素是该用户的点击数/曝光数。
    返回 ratio 及其正确的标准误。
    """
    n = len(clicks)
    x_bar, y_bar = clicks.mean(), exposes.mean()
    ratio = x_bar / y_bar
    var_x, var_y = clicks.var(ddof=1), exposes.var(ddof=1)
    cov_xy = np.cov(clicks, exposes, ddof=1)[0, 1]
    # Delta method: Var(X̄/Ȳ) ≈ (1/n)·(1/ȳ²)·[Var(X) - 2r·Cov(X,Y) + r²·Var(Y)]
    var_ratio = (var_x - 2*ratio*cov_xy + ratio**2*var_y) / (n * y_bar**2)
    return ratio, np.sqrt(var_ratio)
```

```python
# ✓ 解法二：Bootstrap（慢，但最稳妥，也适合任何复杂指标）
def bootstrap_ratio_diff(c_clicks, c_exp, t_clicks, t_exp, n_boot=2000, seed=42):
    """按【用户】重采样，而不是按曝光重采样 —— 这是关键。"""
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(n_boot):
        ci = rng.integers(0, len(c_clicks), len(c_clicks))   # 重采样用户
        ti = rng.integers(0, len(t_clicks), len(t_clicks))
        r_c = c_clicks[ci].sum() / c_exp[ci].sum()
        r_t = t_clicks[ti].sum() / t_exp[ti].sum()
        diffs.append(r_t - r_c)
    diffs = np.array(diffs)
    return {"mean_diff": diffs.mean(),
            "CI95": (np.percentile(diffs, 2.5), np.percentile(diffs, 97.5))}
```

> **✅ 解法三（最简单，推荐给没有实验平台的团队）：把指标改成用户级。**
> 不用「总点击/总曝光」，改用「每个用户的点击率」的平均值，或者「是否点击过」的用户占比。
> 这样每个用户就是一个独立样本，直接用标准 t 检验即可。**指标的业务含义略有不同，但统计上干净得多。**

#### 陷阱 ⑥ 离群值（一个大客户毁掉整个实验）

**场景**：人均 GMV 实验，treatment 组恰好有一个用户下了 100 万的单。

这一个点能让 treatment 组的均值提升几个百分点，看起来「效果显著」，实际是纯粹的随机。

**解法**：

```python
# ① 截断（winsorize）—— 推荐，主指标就用截断后的
cap = np.percentile(np.concatenate([c_gmv, t_gmv]), 99)   # ⚠️ 两组合并算阈值，不要分别算
c_gmv_w, t_gmv_w = np.clip(c_gmv, 0, cap), np.clip(t_gmv, 0, cap)

# ② 同时报截断前后的结果，如果结论相反，说明结论不稳健
# ③ 换用对离群值不敏感的指标：下单用户占比、中位数 GMV
```

> **⚠️ 截断阈值必须用两组合并的数据算**。分别算会引入偏差（treatment 组本来就分布更右，分别截断会抹掉真实效应）。
> 而且阈值必须**事先定好**，不能看完结果再调。

### 2.4.10 什么时候不能做 A/B

| 场景 | 为什么不行 | 替代方案 |
|------|-----------|----------|
| **网络效应** | 社交、即时通讯：treatment 用户的行为会影响 control 用户，两组不独立 | 按**地区/社群**整体分流（cluster randomization） |
| **双边市场** | 打车、外卖：给一半司机加派单权重，会抢走另一半的订单，control 被污染 | **时间片轮转**（switchback）：整个城市交替使用 A/B，按时间段对比 |
| **品牌/长期影响** | 改 logo、调价格策略：影响周期以月计，且会溢出到未参与用户 | 地理实验（geo experiment）、合成控制法 |
| **样本量根本不够** | 低频高价业务（B2B、大宗交易）一天只有几十单 | 用前置指标（线索量、意向率）替代最终指标；或做定性研究 |
| **法规/伦理限制** | 不能给一部分用户更差的安全保护、更慢的救助响应 | 观察性研究、前后对比 |
| **一次性事件** | 春节活动只办一次 | 前后对比 + 同比 + 可比对照组（比如去年同期、未参加活动的相似城市） |
| **改动无法切分** | 底层架构重构，无法同时运行两套 | 灰度放量 + 分阶段监控 + 快速回滚能力 |

> **对分析师的要求**：能在实验设计阶段就识别出「这个不能做 A/B」，并给出替代方案。
> 硬做一个有网络效应污染的 A/B，跑出来的数是错的，比不做还糟。

### 2.4.11 结果不显著怎么办

**这是实验分析里最需要专业判断的时刻。** 不显著有两种完全不同的含义：

```
p > 0.05
  │
  ├─ 【真的没效果】 置信区间窄，且完全落在「业务无意义」的范围内
  │    例：CI95 = [-0.05pp, +0.08pp]，而业务门槛是 +0.5pp
  │    → 结论：可以确定这个改动没有达到预期效果。不上线。
  │
  └─ 【功效不足】   置信区间宽，业务上有意义的效应仍在区间内
       例：CI95 = [-2pp, +3pp]，业务门槛是 +0.5pp
       → 结论：这个实验【什么都没证明】。不能说「没效果」。
       → 选项：继续跑 / 增加流量 / 降方差 / 承认这个问题当前测不了
```

> **最重要的一条**：**「不显著」≠「没效果」。**
> 把「p>0.05」汇报成「实验证明该功能无效」是严重的专业错误。正确表述是「本实验未能检出显著效应，在当前样本量下，我们能排除大于 X 的效应」。

#### 事后功效分析

```python
def observed_mde(p_baseline, n_per_group, alpha=0.05, power=0.8):
    """在当前样本量下，实际能检出的最小相对提升。"""
    z_a, z_b = stats.norm.ppf(1-alpha/2), stats.norm.ppf(power)
    delta = np.sqrt(2 * (z_a+z_b)**2 * p_baseline*(1-p_baseline) / n_per_group)
    return delta / p_baseline

print(f"{observed_mde(0.08, 20000):.2%}")   # 9.50% —— 只能检出 9.5% 以上的相对提升
# 如果你的预期效应是 3%，这个实验从设计上就不可能检出，跑了也白跑
```

> **⚠️ 注意**：这里算的是「在这个样本量下 MDE 是多少」，这是**设计层面**的问题，任何时候算都有意义。
> 但不要做「用观测到的效应量反推功效」（observed power / post-hoc power）——那是统计学上公认的错误做法，它和 p 值是一一对应的，不提供任何新信息。

#### 降方差的三个手段（让同样的样本量更敏感）

| 手段 | 原理 | 方差降幅 |
|------|------|----------|
| **CUPED** | 用实验前的同一指标作为协变量做回归调整 | 通常 30-50% |
| **指标截断** | winsorize 到 P99，去掉长尾的方差贡献 | 长尾指标可达 50%+ |
| **分层抽样** | 按新老用户等强预测维度分层后分别分流 | 10-30% |

```python
# CUPED：最有效、实现最简单的降方差方法
def cuped_adjust(y, y_pre):
    """
    y     : 实验期指标（两组合并的数组）
    y_pre : 同一批用户在【实验开始前】同期的同一指标
    返回调整后的指标，方差更小但均值的期望不变。
    """
    theta = np.cov(y, y_pre, ddof=1)[0, 1] / np.var(y_pre, ddof=1)
    return y - theta * (y_pre - y_pre.mean())

# 用调整后的 y_cuped 代替 y 做 t 检验，其余步骤完全不变
# 前提：y_pre 必须是【实验开始前】的数据，绝不能用实验期数据（会引入偏差）
```

#### 决策矩阵

| 统计显著 | 业务意义（CI 下界 ≥ 门槛） | 决策 |
|:---:|:---:|---|
| ✅ | ✅ | **上线**。同时做上线后回归验证 |
| ✅ | ❌ | 显著但效应太小。看**开发/维护成本**：成本低可上线，成本高不值得 |
| ❌ | CI 窄，排除了有意义的效应 | **不上线**，结论明确：该方向无效 |
| ❌ | CI 宽，什么都没排除 | **实验无结论**。继续跑 / 降方差 / 换指标 / 承认测不了 |

### 2.4.12 实验报告模板

```markdown
## 实验名称：首页卡片数量 3→5

### 一、结论（先说结论）
建议上线 / 不上线 / 继续观察。理由一句话。

### 二、实验设计
- 假设：[改动] → [机制] → [预期指标] → [预期幅度]
- 主指标：卡片 CTR（事先确定）
- 护栏指标：页面加载 P95、崩溃率、次日留存、退款率
- 随机化单元：用户级
- 流量：50% / 50%，总流量 20%
- 计划样本量：每组 73,855（MDE 相对 5%，功效 80%）
- 计划时长：2026-09-01 ~ 2026-09-14（14 天，含 2 个完整周）

### 三、数据质量校验 ⭐️ 必须放在结果之前
- [x] SRM：control 74,123 / treatment 74,098，chi2=0.004，p=0.95 ✅
- [x] 分组稳定性：flip_users = 0 ✅
- [x] 实验前特征均衡：新用户占比 31.2% vs 31.4%，历史 GMV 均值差异 0.8% ✅
- [x] 埋点匹配率：control 94.1% / treatment 93.8%，差异 0.3pp ✅
- [x] 无中途配置变更 ✅

### 四、主指标结果
| 指标 | 对照组 | 实验组 | 绝对差 | 相对提升 | 95% CI（相对） | p 值 |
|------|--------|--------|--------|----------|----------------|------|
| 卡片 CTR | 8.00% | 8.30% | +0.30pp | +3.75% | [+0.75%, +6.75%] | 0.014 |

### 五、护栏指标
| 指标 | 对照 | 实验 | 变化 | 判定 |
|------|------|------|------|------|
| 页面加载 P95 | 1.20s | 1.23s | +2.5% | ⚠️ 轻微恶化，未超阈值 |
| 崩溃率 | 0.12% | 0.11% | -8% | ✅ |
| 次日留存 | 42.1% | 42.3% | +0.5% | ✅ |

### 六、分维度结果（观测性，不用于决策）
新用户 +6.2%，老用户 +2.1%；iOS +4.5%，Android +3.1%。
→ 提出假设：新用户受益更多。**这是下一个实验的题目，不是本次的结论。**

### 七、陷阱排查
- [x] 无偷看：首次查看主指标在 2026-09-14，达到预设样本量后
- [x] 新奇效应：按 days_since_assign 看，第 8-14 天效应稳定在 +3.5% 左右，未衰减
- [x] 方差处理：CTR 为请求级指标，已用 delta method 校正（未校正时 p=0.001，校正后 p=0.014）
- [x] 离群值：CTR 指标不受离群值影响；已核查无异常高频用户

### 八、风险与限制
- 置信区间下界 +0.75% 低于业务门槛 2%，真实效应可能小于预期
- 未覆盖大促期间的表现
- 加载耗时轻微恶化，建议上线后持续监控

### 九、上线后验证计划
全量后第 7 / 14 / 30 天，对比实验估计值与实际值；保留 2% holdout 观察 30 天长期效应。
```

### ✅ 2.4 自检

<details><summary>Q：实验组转化率比对照组高 3%，p=0.04。你敢上线吗？</summary>

**不敢，至少还需要看六件事**：

1. **SRM 过了吗？** 不过一票否决，后面都不用看了。
2. **置信区间多宽？** p=0.04 意味着 CI 下界刚过 0。如果 CI 是 [+0.1%, +5.9%]，真实效应可能只有 0.1%，远不够上线成本。
3. **主指标是事先定的吗？** 如果是从 20 个指标里挑出来的显著项，p=0.04 经多重比较校正后大概率不显著。
4. **有没有偷看？** 如果是「看到显著就停」，这个 p 值完全不可信。
5. **护栏指标呢？** 转化涨了但加载变慢、退款率上升、留存下降，就是负收益。
6. **方差算对了吗？** 如果是用户级分流 + 请求级指标且没做 delta method 校正，p 值被系统性低估，真实 p 可能是 0.3。

另外还要看：**新奇效应**（跑了几天？按 days_since_assign 看效应是否衰减）、**离群值**（少数用户是否主导了差异）。

**六项都过了，才轮到讨论「3% 值不值得上线」这个业务问题。**
</details>

---

## 2.5 分析方法论

> A/B 实验回答「这个改动有没有用」。分析方法论回答「指标为什么变了」。
> 前者是验证，后者是定位。两者合起来才是完整的分析能力。

### 2.5.1 维度下钻

#### 贡献度公式

整体指标变了，要找出哪个子群贡献最大。**关键是用「绝对变化量的占比」，不是「各组自己的变化率」。**

$$\text{贡献度}_i = \frac{M_{i,t_1} - M_{i,t_0}}{M_{\text{total},t_1} - M_{\text{total},t_0}}$$

**为什么不能只看变化率**：

| 渠道 | 上周 GMV | 本周 GMV | 变化率 | 绝对变化 | **贡献度** |
|------|---------|---------|--------|----------|-----------|
| A | 1,000,000 | 900,000 | **-10%** | -100,000 | **83%** |
| B | 20,000 | 4,000 | **-80%** | -16,000 | 13% |
| C | 30,000 | 25,000 | -17% | -5,000 | 4% |
| 合计 | 1,050,000 | 929,000 | -11.5% | -121,000 | 100% |

渠道 B 跌了 80%，看起来最严重。但它只贡献了 13% 的跌幅。**优先要查的是渠道 A**——它跌了 10%，却贡献了 83%。

> **纪律：下钻时按绝对贡献度排序，不按变化率排序。** 变化率用来做二次判断（贡献度高 + 变化率也高 = 最可疑）。

```sql
-- 贡献度下钻的标准 SQL 模板
WITH cur AS (
  SELECT channel, SUM(amount) AS gmv FROM dwd_order
  WHERE dt BETWEEN DATE '2026-09-08' AND DATE '2026-09-14' AND status='paid'
  GROUP BY channel
),
pre AS (
  SELECT channel, SUM(amount) AS gmv FROM dwd_order
  WHERE dt BETWEEN DATE '2026-09-01' AND DATE '2026-09-07' AND status='paid'
  GROUP BY channel
),
joined AS (
  SELECT COALESCE(c.channel, p.channel) AS channel,
         COALESCE(p.gmv, 0) AS gmv_pre,
         COALESCE(c.gmv, 0) AS gmv_cur
  FROM cur c FULL OUTER JOIN pre p ON c.channel = p.channel   -- ← FULL JOIN，防止新增/消失的渠道漏掉
)
SELECT
  channel, gmv_pre, gmv_cur,
  gmv_cur - gmv_pre                                              AS abs_change,
  1.0 * gmv_cur / NULLIF(gmv_pre, 0) - 1                         AS rel_change,
  1.0 * (gmv_cur - gmv_pre) / NULLIF(SUM(gmv_cur - gmv_pre) OVER (), 0) AS contribution
FROM joined
ORDER BY abs(gmv_cur - gmv_pre) DESC;
```

> **注意 `FULL OUTER JOIN`**：如果某个渠道本周完全消失（GMV 从 50 万变成 0），用 INNER JOIN 会直接看不到它——而这往往正是问题所在。

#### 下钻顺序怎么定

不要盲目穷举所有维度组合。按这个顺序：

```
① 先拆【最可能是数据问题】的维度：app_version、平台、数据源
   → 如果异常集中在某个版本/平台，多半是数据或技术问题，不用往业务方向查了
② 再拆【业务上最相关】的维度：渠道、品类、地区、新老用户
③ 最后拆【细粒度】维度：具体 SKU、具体页面
```

**判断某个维度值不值得拆的经验法则**：如果拆完之后，某个子群的贡献度 > 50%，这个维度就是有信息量的；如果所有子群的贡献度都和它们的流量占比差不多（均匀分摊），这个维度无信息，换一个。

### 2.5.2 贡献度拆解：量 vs 结构 vs 效率

维度下钻告诉你「哪个子群变了」。这一节告诉你「变化的性质是什么」。

#### 加法拆解 vs 乘法拆解

```
加法（构成型）：GMV = GMV_A + GMV_B + GMV_C           → 用 2.5.1 的贡献度
乘法（链路型）：GMV = UV × 转化率 × 客单价              → 用下面的拆解
```

**乘法链路的拆解（对数法 / LMDI）**：

$$\ln\frac{GMV_1}{GMV_0} = \ln\frac{UV_1}{UV_0} + \ln\frac{CVR_1}{CVR_0} + \ln\frac{AOV_1}{AOV_0}$$

每一项就是该因子的贡献份额。好处是**可加、可归一化**，比「变化率相乘」直观得多。

```python
import numpy as np

def lmdi_decompose(before: dict, after: dict) -> dict:
    """乘法链路的贡献度拆解。before/after 形如 {'uv':10000,'cvr':0.08,'aov':200}"""
    total_ln = np.log(np.prod(list(after.values())) / np.prod(list(before.values())))
    out = {}
    for k in before:
        out[k] = np.log(after[k] / before[k]) / total_ln if total_ln != 0 else 0
    return out

print(lmdi_decompose(
    {"uv": 100000, "cvr": 0.08, "aov": 200},   # GMV = 1,600,000
    {"uv":  95000, "cvr": 0.075,"aov": 210},   # GMV = 1,496,250
))
# {'uv': 0.76, 'cvr': 0.95, 'aov': -0.71}
# 解读：GMV 跌 6.5%，CVR 下降贡献了 95% 的跌幅，UV 下降贡献 76%，
#       客单价上升抵消了 71% —— 主要问题在转化率。
```

#### ⭐️ 结构效应 vs 效率效应（最有价值、也最少人会的一个拆解）

**问题**：整体转化率从 10% 跌到 8.5%，但每个渠道的转化率都没变。怎么回事？

**答案**：流量结构变了。高转化渠道的流量占比下降，低转化渠道占比上升，整体被拉低。

**拆解公式**（R = 整体率，w = 各组流量占比，r = 各组的率）：

$$\Delta R = \underbrace{\sum_i (w_{i,1}-w_{i,0}) \cdot r_{i,0}}_{\text{结构效应}} + \underbrace{\sum_i w_{i,0} \cdot (r_{i,1}-r_{i,0})}_{\text{效率效应}} + \underbrace{\sum_i (w_{i,1}-w_{i,0})(r_{i,1}-r_{i,0})}_{\text{交互项}}$$

**例 1：纯结构效应**

| 渠道 | 期初占比 w₀ | 期初转化 r₀ | 期末占比 w₁ | 期末转化 r₁ |
|------|------------|------------|------------|------------|
| A（优质） | 60% | 12% | 30% | 12% |
| B（低质） | 40% | 7% | 70% | 7% |

- R₀ = 0.6×12% + 0.4×7% = **10.0%**
- R₁ = 0.3×12% + 0.7×7% = **8.5%**，ΔR = **-1.5pp**
- 结构效应 = (0.3-0.6)×12% + (0.7-0.4)×7% = -3.6pp + 2.1pp = **-1.5pp**
- 效率效应 = 0.6×0 + 0.4×0 = **0**

**结论：转化率的下跌 100% 来自流量结构恶化，产品和运营侧没有任何问题。**
**该找的人是投放团队，不是产品团队。**

**例 2：两种效应都有**

| 渠道 | w₀ | r₀ | w₁ | r₁ |
|------|----|----|----|----|
| A | 60% | 12% | 40% | 11% |
| B | 40% | 7% | 60% | 7% |

- R₀ = **10.0%**，R₁ = 0.4×11% + 0.6×7% = **8.6%**，ΔR = **-1.4pp**
- 结构效应 = (0.4-0.6)×12% + (0.6-0.4)×7% = -2.4pp + 1.4pp = **-1.0pp**（71%）
- 效率效应 = 0.6×(11%-12%) + 0.4×0 = **-0.6pp**（43%）
- 交互项 = (-0.2)×(-1pp) + 0.2×0 = **+0.2pp**（-14%）
- 校验：-1.0 - 0.6 + 0.2 = **-1.4pp** ✓

**结论：71% 是结构问题（投放），43% 是渠道 A 自身效率下降（产品/体验），需要两边同时看。**

```python
def structure_efficiency_decompose(w0, r0, w1, r1):
    """w/r 为等长数组：各分组的流量占比与转化率。"""
    w0, r0, w1, r1 = map(np.asarray, (w0, r0, w1, r1))
    struct = np.sum((w1 - w0) * r0)
    effic  = np.sum(w0 * (r1 - r0))
    inter  = np.sum((w1 - w0) * (r1 - r0))
    return {"total": struct + effic + inter, "structure": struct,
            "efficiency": effic, "interaction": inter}

print(structure_efficiency_decompose([.6,.4], [.12,.07], [.4,.6], [.11,.07]))
# {'total': -0.014, 'structure': -0.010, 'efficiency': -0.006, 'interaction': 0.002}
```

> **这个拆解为什么值钱**：它直接决定了「该找谁」。
> 结构效应主导 → 流量/投放的问题；效率效应主导 → 产品/体验的问题。
> 没有这个拆解，产品和投放会互相甩锅，而且双方都能拿出支持自己的数据。

### 2.5.3 漏斗分析

#### 严格漏斗 vs 宽松漏斗

| | 严格漏斗 | 宽松漏斗 |
|---|---|---|
| 要求 | 必须按顺序、无跳步 | 只要求发生过，不要求顺序 |
| 实现 | 事件序列匹配 | 各步骤独立计数 |
| 转化率 | 偏低 | 偏高 |
| 适用 | 强流程（注册、支付） | 弱流程（浏览、搜索） |

**同一批数据，两种口径的结果能差 30% 以上。所以报漏斗必须说明口径。**

#### 三个必须明确的参数

1. **时间窗口**：从第一步开始算起，多久内完成才算转化？（1 小时？当天？7 天？）
   - 窗口太短：漏掉「加购后第二天下单」的真实转化
   - 窗口太长：把无关的行为算进来
2. **同一用户多次进入漏斗怎么算**：取第一次？取最后一次？算多次？
3. **跳步算不算**：直接从「浏览」到「下单」（跳过加购），算不算转化？

```sql
-- 宽松漏斗（最常用，实现简单）
WITH steps AS (
  SELECT user_id,
    MAX(CASE WHEN event_name = 'view'     THEN 1 ELSE 0 END) AS s1,
    MAX(CASE WHEN event_name = 'add_cart' THEN 1 ELSE 0 END) AS s2,
    MAX(CASE WHEN event_name = 'pay'      THEN 1 ELSE 0 END) AS s3
  FROM dwd_event
  WHERE dt BETWEEN DATE '2026-09-01' AND DATE '2026-09-07'
  GROUP BY user_id
)
SELECT
  SUM(s1) AS step1_view,
  SUM(CASE WHEN s1=1 AND s2=1 THEN 1 ELSE 0 END) AS step2_cart,
  SUM(CASE WHEN s1=1 AND s2=1 AND s3=1 THEN 1 ELSE 0 END) AS step3_pay,
  1.0*SUM(CASE WHEN s1=1 AND s2=1 THEN 1 ELSE 0 END)/NULLIF(SUM(s1),0) AS cvr_1_2,
  1.0*SUM(CASE WHEN s1=1 AND s2=1 AND s3=1 THEN 1 ELSE 0 END)
     /NULLIF(SUM(CASE WHEN s1=1 AND s2=1 THEN 1 ELSE 0 END),0)         AS cvr_2_3
FROM steps;
```

```sql
-- 严格漏斗（要求时间顺序 + 时间窗口）
WITH ordered AS (
  SELECT user_id, event_name, event_time,
         MIN(CASE WHEN event_name='view' THEN event_time END)
           OVER (PARTITION BY user_id) AS t_view
  FROM dwd_event
  WHERE dt BETWEEN DATE '2026-09-01' AND DATE '2026-09-07'
),
funnel AS (
  SELECT user_id,
    MIN(CASE WHEN event_name='add_cart' AND event_time > t_view
             AND event_time <= t_view + INTERVAL '24' HOUR THEN event_time END) AS t_cart,
    MIN(CASE WHEN event_name='pay'      AND event_time > t_view
             AND event_time <= t_view + INTERVAL '24' HOUR THEN event_time END) AS t_pay,
    MIN(t_view) AS t_view
  FROM ordered GROUP BY user_id
)
SELECT
  COUNT(t_view)                                               AS s1,
  COUNT(CASE WHEN t_cart > t_view THEN 1 END)                 AS s2,
  COUNT(CASE WHEN t_pay > t_cart THEN 1 END)                  AS s3   -- ← 要求 pay 在 cart 之后
FROM funnel;
```

#### 找「最该优化的环节」：两种口径缺一不可

| 口径 | 公式 | 回答什么 |
|------|------|----------|
| **最低转化率环节** | min(各步转化率) | 哪一步最难过 |
| **最大流失量环节** | max(上一步人数 - 这一步人数) | 哪一步损失的人最多 |

```
例：
  浏览 100,000 → 加购 10,000（10%，流失 90,000）→ 支付 5,000（50%，流失 5,000）

  最低转化率：浏览→加购，10%
  最大流失量：浏览→加购，90,000 人
  → 两个口径一致，优先优化这一步

例：
  浏览 100,000 → 加购 50,000（50%，流失 50,000）→ 支付 2,500（5%，流失 47,500）

  最低转化率：加购→支付，5%
  最大流失量：浏览→加购，50,000 人（略高于第二步的 47,500）
  → 两个口径不一致。这时要看【提升空间】：
     支付环节 5% 的转化率明显偏低（行业基准通常 50%+），提升空间大；
     浏览→加购 50% 已经不错。所以优先查支付环节。
```

> **纪律：两个口径都算，不一致时用「行业基准/历史水平」做第三方判断。** 只看一个口径会漏掉真正的问题。

### 2.5.4 留存分析

#### ⚠️ 首先搞清楚：「第 N 日留存」还是「N 日内留存」

这是留存分析里最常见的口径分歧，两者数值能差一倍。

| 口径 | 定义 | 别名 |
|------|------|------|
| **第 N 日留存** | 恰好在第 N 天回来 | 精确留存、classic retention |
| **N 日内留存** | 第 1 到第 N 天里**任意一天**回来过 | 滚动留存、rolling retention |
| **N 日后留存** | 第 N 天**及以后**回来过 | unbounded retention |

```
用户注册后在第 1、5 天活跃：
  第 7 日留存 = 否（第 7 天没回来）
  7 日内留存 = 是（第 1 天回来过）
```

> **报「次留 40%」之前，先确认是哪个口径，并在报告里写明。** 跨团队、跨公司对比留存数据时，口径不同就是耍流氓。

```sql
-- 标准 Cohort 留存表（第 N 日留存口径）
WITH reg AS (
  SELECT user_id, reg_date FROM dim_user
  WHERE reg_date BETWEEN DATE '2026-08-01' AND DATE '2026-08-31'
),
act AS (
  SELECT DISTINCT user_id, dt AS act_date FROM dwd_event
  WHERE dt BETWEEN DATE '2026-08-01' AND DATE '2026-09-30'
)
SELECT
  r.reg_date,
  COUNT(DISTINCT r.user_id) AS cohort_size,
  1.0*COUNT(DISTINCT CASE WHEN date_diff('day',r.reg_date,a.act_date)=1  THEN r.user_id END)
     /COUNT(DISTINCT r.user_id) AS d1,
  1.0*COUNT(DISTINCT CASE WHEN date_diff('day',r.reg_date,a.act_date)=7  THEN r.user_id END)
     /COUNT(DISTINCT r.user_id) AS d7,
  1.0*COUNT(DISTINCT CASE WHEN date_diff('day',r.reg_date,a.act_date)=30 THEN r.user_id END)
     /COUNT(DISTINCT r.user_id) AS d30
FROM reg r LEFT JOIN act a ON r.user_id = a.user_id
GROUP BY r.reg_date ORDER BY r.reg_date;
```

> **⚠️ 计算 d30 时，最近 30 天注册的 cohort 数据是不完整的**（他们还没到第 30 天）。
> 必须在图表上把这些 cohort 置灰或排除，否则会看到「留存断崖式下跌」的假象。这是留存报表最常见的视觉错误。

#### 留存曲线的三种形状

```
① 收敛型（健康）        ② 持续下降型（危险）      ③ 微笑型（罕见，很好）
100%│●                    100%│●                    100%│●
    │ ●                       │ ●                       │ ●
    │  ●                      │  ●                      │  ●      ●
    │   ●───●───●───●         │   ●                     │   ●  ●
    └─────────────────        │    ●___                 └─────────────────
    曲线趋于水平               └─────────────────        先降后升
    → 找到了稳定的核心用户      一直往下掉                → 产品有网络效应或
    → 产品有 PMF               → 没有留住任何人的理由       长周期价值被逐渐发现
                               → 优先解决留存，不要拉新
```

**读留存曲线的关键是看「有没有收敛」，不是看绝对值。**
- D30 留存 15% 但曲线水平 → 健康。这 15% 是真正的用户，可以放心拉新。
- D30 留存 25% 但还在往下掉 → 危险。现在拉的新用户最终都会流失，拉新是在漏水的桶里加水。

#### Cohort 表怎么读

```
reg_date    size    D1     D3     D7     D14    D30
2026-08-01  1200   42.1%  28.3%  21.0%  17.2%  15.1%
2026-08-08  1350   43.5%  29.1%  21.8%  17.9%  15.6%
2026-08-15  1180   38.2%  24.1%  16.5%  12.0%   9.8%   ← 这一周有问题
2026-08-22  1420   44.0%  29.8%  22.3%  18.1%   -      ← 数据未完整，留白
```

**三个读法**：
1. **纵向看（同一列往下）**：不同时间注册的用户，同一留存期的表现。用来发现「某周的新用户质量突然变差」——上例 08-15 那周，全列都低，要去查那周的投放渠道或活动。
2. **横向看（同一行往右）**：单个 cohort 的衰减曲线。看有没有收敛。
3. **对角线看**：同一个日历日期上所有 cohort 的表现。用来发现「某天发生了影响所有用户的事件」（比如线上事故）。

### 2.5.5 用户分层

#### RFM

| 维度 | 含义 | 常用取值 |
|------|------|----------|
| **R**ecency | 最近一次消费距今多久 | 越小越好 |
| **F**requency | 统计周期内消费次数 | 越大越好 |
| **M**onetary | 统计周期内消费金额 | 越大越好 |

```sql
WITH rfm_raw AS (
  SELECT user_id,
         date_diff('day', MAX(date(pay_time)), current_date) AS recency,
         COUNT(DISTINCT order_id)                            AS frequency,
         SUM(amount)                                         AS monetary
  FROM dwd_order
  WHERE status = 'paid' AND dt >= current_date - INTERVAL '180' DAY
  GROUP BY user_id
),
rfm_score AS (
  SELECT *,
    -- ⭐️ 用分位数分档（NTILE），不要用固定阈值
    6 - NTILE(5) OVER (ORDER BY recency)   AS r_score,   -- recency 越小越好，所以反转
    NTILE(5) OVER (ORDER BY frequency)     AS f_score,
    NTILE(5) OVER (ORDER BY monetary)      AS m_score
  FROM rfm_raw
)
SELECT *,
  CASE
    WHEN r_score >= 4 AND f_score >= 4 AND m_score >= 4 THEN '重要价值'
    WHEN r_score <= 2 AND f_score >= 4 AND m_score >= 4 THEN '重要挽留'   -- 曾经很好，最近不来了
    WHEN r_score >= 4 AND f_score <= 2                  THEN '新客/潜力'
    WHEN r_score <= 2 AND f_score <= 2                  THEN '流失'
    ELSE '一般'
  END AS segment
FROM rfm_score;
```

> **为什么用 NTILE 而不是固定阈值**：固定阈值（比如「消费 > 1000 算高价值」）在业务变化后就失效了，且无法跨品类/跨地区比较。分位数分档自动适应分布。
> **但要注意**：分位数分档下，「高价值用户」永远占 20%——它衡量的是**相对位置**，不能用来看「高价值用户变多了没有」。两种用途要分清。

#### 分层的两个反模式

❌ **为了分层而分层**：分出 8 个层，然后没有任何一层对应一个具体的运营动作。

✅ **从动作反推分层**：先问运营「你能做哪几种动作」（发券、推送、专属客服、召回短信），有几种动作就分几层。

❌ **分层后不验证**：假设「重要挽留」层发券能召回，但从没验证过。

✅ **每个分层的运营策略都要用 A/B 实验验证**（回到 2.4）。

### 2.5.6 归因分析

**问题**：一个用户先看了信息流广告，再搜索点进来，最后从 push 下单。这一单算谁的？

| 模型 | 规则 | 偏向 | 适用 |
|------|------|------|------|
| **首次触点** | 100% 给第一次接触 | 拉新渠道 | 评估品牌/种草类投放 |
| **末次触点** | 100% 给最后一次 | 收割渠道 | ⭐️ 最常用（简单、可复现） |
| **末次非直访** | 100% 给最后一个非直接访问 | 收割渠道 | 避免把功劳都给「直接打开 App」 |
| **线性** | 平均分给所有触点 | 中性 | 触点少且同等重要时 |
| **时间衰减** | 越接近转化权重越高 | 收割渠道（较温和） | 决策周期短的业务 |
| **位置（U 型）** | 首尾各 40%，中间分 20% | 首尾 | 兼顾拉新和收割 |
| **数据驱动（Shapley/Markov）** | 按各触点的边际贡献计算 | 无先验偏向 | 数据量足够时最科学 |

**同一批数据，不同模型下渠道 A 的归因 GMV 能差 3 倍。**

```
某笔 1000 元订单，触点路径：信息流广告 → 搜索 → push

  首次触点：   信息流 1000，搜索 0，   push 0
  末次触点：   信息流 0,    搜索 0,   push 1000
  线性：       信息流 333,  搜索 333, push 333
  时间衰减：   信息流 143,  搜索 286, push 571
  U 型：       信息流 400,  搜索 200, push 400
```

> **选型建议**：
> 1. **对外/跨团队的统一口径用「末次非直访」**——简单、可复现、争议最小。
> 2. **内部优化时同时看多个模型**：如果某个渠道在首次模型下很强、末次模型下很弱，说明它是「种草型」渠道，不能用末次口径考核它，否则会砍掉真正有价值的投放。
> 3. **归因模型的选择本质上是分配预算的规则，不是科学真理。** 最重要的是**全公司用同一套，并且长期不变**——换模型会让所有历史对比失效。
> 4. **归因永远只是相关性，不是因果。** 想知道某渠道的真实增量，只有做实验（地理实验、投放暂停测试）。

### 2.5.7 ⭐️ 异常诊断 SOP

**这是分析师最高频的实战场景。给自己设时间盒，按固定顺序走，不要凭直觉乱查。**

```
═══════ Step 0：确认这是不是「异常」（3 分钟）═══════
  · 看过去 30-90 天的波动区间
  · 算历史波动的均值和标准差，这次变化在几个 σ 之外？
  · 是不是周内效应/节假日？（和上周同一天比，不是和昨天比）
  ⚠️ 很多「异常」其实是正常波动。先排除这个，能省掉一整天。

═══════ Step 1：数据层（10 分钟，走 2.2.5 的六项校验）═══════
  □ 上游分区产出了吗？行数正常吗？
  □ 主键唯一吗？（上游重跑 → 数据翻倍）
  □ 关键字段空值率突变了吗？
  □ 枚举值有新增吗？（新状态没被 CASE WHEN 覆盖 → 数据凭空消失）
  □ 计算口径/SQL 最近改过吗？（git log）
  □ 埋点/客户端最近发版了吗？（发版记录）
  □ 前后端匹配率变了吗？（2.2.4）
  → 发现问题：定位到具体表/字段，提给数据团队，结束
  → 全部正常：进 Step 2

═══════ Step 2：定位到最小子群（10 分钟）═══════
  · 按 2.5.1 的贡献度公式逐维度下钻
  · 顺序：app_version/平台 → 渠道/地区 → 品类/页面 → 用户分层
  · 每层找出贡献度 > 50% 的子群，继续往下钻
  · 目标：把「大盘跌 5%」收敛成「Android 新版本的搜索页卡片 CTR 跌 40%」

═══════ Step 3：判断变化的性质（5 分钟）═══════
  · 结构效应还是效率效应？（2.5.2）→ 决定该找投放还是找产品
  · 是量变了还是率变了？（乘法拆解）
  · 链路上哪一环掉的？（漏斗，2.5.3）

═══════ Step 4：对齐外部事件（5 分钟）═══════
  · 发版记录、实验上下线记录、运营活动日历、投放计划变更
  · 竞品动作、行业事件、天气/节假日
  · 关键动作：把指标曲线和这些事件的时间点【画在同一张图上】
    —— 时间点对上是最强的证据，比任何统计检验都直观

═══════ Step 5：形成结论（不超过 5 分钟）═══════
  必须包含四部分：
  ① 结论：发生了什么（一句话）
  ② 证据：支撑它的 2-3 个数字
  ③ 建议：谁该做什么
  ④ 未排除项：还有哪些可能性没验证，需要谁配合
```

> **最重要的纪律：Step 1 和 Step 2 不能跳，也不能换顺序。**
> 跳过 Step 1 直接找业务原因，是新手最典型的错误——你会花半天编出一个漂亮的业务故事，最后发现是上游任务重跑了。

### 2.5.8 常见偏差识别

| 偏差 | 机制 | 业务场景中长什么样 | 怎么防 |
|------|------|-------------------|--------|
| **幸存者偏差** | 只分析留下来的样本 | 「我们的用户满意度 4.8 分」——填问卷的都是活跃用户，流失的人不会来填 | 把流失样本纳入分析；单独分析流失用户 |
| **选择偏差** | 样本不能代表总体 | 「用了新功能的用户留存高 30%」——本来就是高活用户才会去试新功能 | 做实验；或用 PSM 匹配可比人群 |
| **回归均值** | 极端值自然回归 | 「给上周表现最差的销售做了培训，这周他们都进步了」——即使什么都不做也会进步 | 设对照组；看多期趋势不看单期 |
| **辛普森悖论** | 分组权重变化 | 见 2.4.9 陷阱 ④ | 分层看；检查各组样本量占比是否变化 |
| **时间穿越** | 用未来信息解释过去 | 用当前会员等级分析历史订单（2.1.4） | 用拉链表取历史时点状态 |
| **确认偏差** | 只找支持自己假设的证据 | 老板说「一定是竞品降价」，于是只查竞品相关数据 | 主动列出「什么证据会推翻我的结论」，然后去查它 |

> **回归均值是最容易被包装成「工作成果」的偏差。** 任何「针对表现最差的一批做干预，然后他们变好了」的结论，在没有对照组的情况下都不可信。

### ✅ 2.5 自检

<details><summary>Q：整体转化率跌了，但每个渠道的转化率都没跌，可能吗？</summary>

**完全可能，而且很常见。** 这是纯结构效应（2.5.2 例 1）：高转化渠道的流量占比下降、低转化渠道占比上升，加权平均被拉低。

**排查动作**：算出各渠道的流量占比变化，用结构/效率拆解公式定量。如果结构效应占主导，问题在流量侧（投放策略变了、某个优质渠道被砍预算、SEO 排名掉了），不在产品侧。
</details>

---

## 2.6 可视化与看板

### 2.6.1 BI 工具

| 工具 | 定位 | 上手难度 |
|------|------|----------|
| **Tableau** | 探索分析能力最强 | 中 |
| **Power BI** | 微软生态、性价比高 | 中 |
| **Superset / Metabase** | 开源，SQL 友好 | 低 |
| **Looker** | 语义层（LookML）统一口径，工程化最好 | 高 |
| **QuickBI / 帆软** | 国内企业常见 | 低 |

> **熟练一个就够，工具之间是可迁移的。** 真正的能力在于「知道该画什么」，不在于会用哪个工具。
> 如果可以选，优先学**有语义层的工具**（Looker / dbt metrics 这类）——它把指标定义从图表里抽出来集中管理，是解决「口径打架」的根本方案。

### 2.6.2 看板设计三问

做任何看板之前，把这三个问题的答案写下来：

1. **谁看？** CEO / 业务负责人 / 一线运营 —— 决定指标的抽象层级
2. **看完要做什么决策？** 如果答案是「没有决策，就是看看」→ **这个看板不该做**
3. **多久看一次？** 实时 / 日 / 周 / 月 —— 决定刷新频率和数据粒度

**指标组织的金字塔结构**：

```
第一屏  ┌─────────────────────────────┐
（结论）│  北极星指标 + 3-5 个一级指标   │  大字号、带同环比、带目标达成率
        │  ⚠️ 不要超过 5 个              │
        ├─────────────────────────────┤
第二屏  │  一级指标的拆解（渠道/品类/地区）│  表格 + 趋势图
（拆解）├─────────────────────────────┤
第三屏  │  过程指标、明细、下钻入口       │  给想深挖的人
（细节）└─────────────────────────────┘
```

### 2.6.3 监控看板 vs 分析看板

| | 监控看板 | 分析看板 |
|---|---|---|
| 目的 | **发现异常** | **定位原因** |
| 更新 | 实时 / 小时级 | 日级 |
| 指标数 | 少（5-10 个核心） | 多 |
| 交互 | 几乎没有（一眼看完） | 大量筛选、下钻 |
| 性能要求 | **必须 3 秒内出** | 可以慢一点 |
| 数据源 | 预聚合的 ADS 表 | 可查 DWD |
| 失败后果 | 事故发现晚了 | 分析慢一点 |

**最常见的设计错误：把两者做成一个。** 结果是加了一堆筛选器的监控看板，打开要 30 秒，没人愿意每天看——监控功能就此失效。

> **分开做。监控看板追求「快和稳」，分析看板追求「深和全」。**

### 2.6.4 告警设计

| 类型 | 规则 | 优点 | 缺点 |
|------|------|------|------|
| **固定阈值** | CTR < 5% 告警 | 简单直观 | 业务增长后要手动调，节假日误报 |
| **同环比** | 环比跌 20% 告警 | 自适应 | 周内效应导致周一必报 |
| **同比（周同比）** | 和上周同一天比 | ⭐️ 消除周内效应 | 遇节假日仍误报 |
| **动态基线** | 超出「过去 N 周同一时段」的 ±3σ | 最准 | 实现复杂，需要足够历史 |
| **分位数** | P95 耗时 > 2s | 适合性能指标 | — |

#### 降低误报的五个手段

1. **连续 N 次才告警**：单点抖动不报，连续 3 个周期异常才报
2. **加绝对量门槛**：「转化率跌 50%」但只有 10 个样本 → 不报
3. **分维度告警而非只看大盘**：大盘跌 3% 可能不触发，但某个渠道跌 40% 应该报
4. **节假日/大促日历**：这些日子用单独的基线
5. **分级**：P0 立即电话、P1 群通知、P2 日报汇总。**不要所有告警都是 P0**

> **告警疲劳是告警系统的头号死因。** 一个每天报 20 次的告警，等于没有告警——所有人都会屏蔽它。
> **宁可漏报一些低优先级问题，也要保证「报出来的一定值得看」。** 误报率的目标应该是 < 20%。

#### 告警必须携带的信息

```
❌ 「CTR 异常」
✅ 「[P1] 首页卡片 CTR 跌至 5.2%（较上周同一时段 -35%），
    已持续 3 个周期。主要来自 Android v8.2.0（贡献 78%）。
    看板链接：xxx  |  值班人：@某某」
```

告警的价值不在于「通知」，而在于**让收到的人立刻知道该做什么**。

### 2.6.5 配色与可访问性

- **色盲友好**：约 8% 的男性有红绿色盲。**不要只用红/绿区分涨跌**，同时用箭头（↑↓）或正负号。推荐调色板：ColorBrewer、Okabe-Ito
- **顺序型数据用单色渐变**（浅→深），**分类数据用色相区分**（不超过 7 类，超过就合并成「其他」）
- **发散型数据用双色渐变**（比如增长率，负值蓝、正值红，中点为白）
- **深浅色模式**：定义语义色变量（`--color-up` / `--color-down`），不要硬编码具体颜色
- **打印和投影**：用灰度打印检查一遍——如果灰度下分不清，配色就有问题

---

## 2.7 工程素养

> 这一节的内容单独看都很简单，但它们决定了你的分析**能不能被别人复现、能不能被自己复现**。
> 不可复现的分析在三个月后等于不存在。

### 2.7.1 Git

分析师需要的是够用，不是精通：

```bash
git checkout -b analysis/ctr-drop-0903   # 每个分析开一个分支
git add . && git commit -m "分析：9月3日CTR下降归因"
git push -u origin analysis/ctr-drop-0903
git log --oneline -20                     # ⭐️ 查「口径什么时候改的」，排查必备
git log -p -- sql/ctr_daily.sql           # 看某个文件的完整修改历史
git blame sql/ctr_daily.sql               # 看每一行是谁、什么时候改的
```

**对分析师最有价值的不是版本管理，是 `git log`**：指标口径什么时候变的、谁改的、为什么改——这是异常排查 Step 1 的必查项（2.5.7）。

**什么该进 Git**：SQL 脚本、Python 脚本、notebook（先清空输出）、配置文件、指标口径文档
**什么不该进**：数据文件、密码和密钥、大的中间结果

### 2.7.2 可复现

```python
# ✗ 三个不可复现的写法
df = pd.read_sql("... WHERE dt = '2026-09-01'", eng)   # 硬编码日期
sample = df.sample(1000)                                # 无种子，每次不同
df = pd.read_sql("... WHERE dt = current_date", eng)    # 今天跑和明天跑结果不同

# ✓ 对应的修法
START, END, SEED = "2026-09-01", "2026-09-07", 42
df = pd.read_sql(SQL.format(start=START, end=END), eng)
sample = df.sample(1000, random_state=SEED)
```

**可复现检查清单**：
- [ ] 所有日期、阈值、路径参数化，集中在文件开头
- [ ] 所有随机操作设 seed
- [ ] 依赖版本锁定（`requirements.txt` / `uv.lock`）
- [ ] 数据来源写清楚（哪张表、哪个时间范围、什么过滤条件）
- [ ] 中间结果保存到带时间戳的文件（`output/ctr_analysis_20260914.csv`）
- [ ] **别人只拿到你的仓库，能不能跑出同样的数？** —— 最终检验标准

### 2.7.3 调度

分析师至少要会：**配置一个定时任务、看任务日志、手动重跑、理解上下游依赖**。

```python
# Airflow 最小示例
from airflow import DAG
from airflow.operators.python import PythonOperator
import pendulum

with DAG(
    "daily_ctr_report",
    schedule="0 9 * * *",                                # 每天 9 点
    start_date=pendulum.datetime(2026, 9, 1, tz="Asia/Shanghai"),
    catchup=False,                                       # ⚠️ 不补跑历史
    default_args={"retries": 2},
) as dag:
    PythonOperator(
        task_id="gen_report",
        python_callable=gen_report,
        op_kwargs={"biz_date": "{{ ds }}"},              # ⭐️ 用调度日期，不用 current_date
    )
```

**三条纪律**：
1. **用调度框架传入的业务日期（`{{ ds }}`），绝不在代码里写 `current_date`** —— 否则重跑历史任务会写错分区（回 2.1.6）
2. **任务必须幂等**（`INSERT OVERWRITE`，不是 `INSERT INTO`）
3. **配置失败告警**，并且告警要能看出是哪个任务、哪一天失败了

### 2.7.4 命令行

```bash
# 快速看一个 CSV（不用开 Python）
head -3 data.csv                              # 看表头
wc -l data.csv                                # 行数
cut -d, -f3 data.csv | sort | uniq -c | sort -rn | head   # 第 3 列的值分布

# 日志排查
grep -i "error" app.log | tail -50
grep -c "timeout" app.log                     # 计数
awk -F',' '$3 > 1000 {sum += $3} END {print sum}' data.csv   # 条件求和

# JSON 处理
cat resp.json | jq '.data[] | {id, name}'
cat resp.json | jq -r '.data[].card_id' | sort | uniq -c

# 调接口
curl -s "https://api.example.com/metrics?date=2026-09-01" \
     -H "Authorization: Bearer $TOKEN" | jq .
```

> **最大的价值场景**：几十 MB 的日志文件，用 `grep`/`awk` 两秒出结果，用 pandas 要先加载几十秒。**探索阶段用命令行，正式分析用 Python。**

### 2.7.5 读 API 文档自己拉数

```python
import requests, time, os
import pandas as pd

def fetch_paginated(url: str, params: dict, max_pages: int = 100) -> pd.DataFrame:
    """标准的分页拉取模板：带认证、分页、限流、重试。"""
    token = os.environ["API_TOKEN"]          # ⭐️ 密钥从环境变量读，绝不硬编码
    headers = {"Authorization": f"Bearer {token}"}
    rows, page = [], 1
    while page <= max_pages:
        r = requests.get(url, params={**params, "page": page, "size": 500},
                         headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            break
        rows.extend(data)
        page += 1
        time.sleep(0.2)                       # 限流，别把人家接口打挂
    return pd.DataFrame(rows)
```

**读 API 文档时重点看五件事**：
1. **认证方式**（token 怎么拿、多久过期）
2. **分页机制**（page/size 还是 cursor？有没有总数上限？）
3. **限流规则**（QPS 限制、超了会怎样）
4. **时间参数的时区和格式**（⚠️ 又是时区，回 2.1.5）
5. **字段含义和口径**（接口返回的「UV」和数仓的「UV」是不是一回事？）

> **⚠️ 安全纪律**：token、密码、密钥一律放环境变量或密钥管理服务，**绝不写进代码或 notebook**，更不能提交到 Git。
> 一旦提交过，即使删除了，Git 历史里还在，必须走密钥轮换流程。

---

## 2.8 阶段二里程碑自测

> 这五题是阶段二的毕业考。能独立、完整地答出来，才算真正掌握了这一阶段。

### Q1：实验组转化率比对照组高 3%，p=0.04。你敢上线吗？还需要看什么？

<details><summary>参考答案</summary>

**不敢。按顺序还需要看七件事**：

**① SRM 校验**（2.4.5）—— 一票否决项。两组人数比例的卡方检验 p < 0.001 就作废，后面全都不用看了。

**② 置信区间**（2.4.7）—— p=0.04 意味着 CI 下界刚过 0。如果是 [+0.1%, +5.9%]，真实效应可能只有 0.1%，远低于业务门槛。**只报 p 值不报 CI 是不完整的汇报。**

**③ 主指标是不是事先定的**（2.4.2）—— 如果是从 20 个指标里挑出来的显著项，经 BH 校正后大概率不显著。这是 p-hacking。

**④ 有没有偷看数据**（2.4.9 陷阱 ①）—— 如果是「每天看，看到显著就停」，实际假阳性率接近 100%，这个 p 值毫无意义。

**⑤ 护栏指标**（2.4.2）—— 转化涨 3% 但加载变慢、崩溃率上升、次日留存下降，就是负收益。

**⑥ 方差算法**（2.4.9 陷阱 ⑤）—— 如果是用户级分流 + 请求级指标，且没做 delta method / bootstrap 校正，方差被低估，真实 p 可能是 0.3。判断方法：跑 AA 实验看 p 值分布是否均匀。

**⑦ 新奇效应和离群值**（陷阱 ③⑥）—— 按 `days_since_assign` 看效应是否衰减；检查是否有少数极端用户主导了差异。

**七项都过了**，才轮到业务判断：「相对提升 3%（CI 下界 X%）」值不值得承担开发和维护成本。

**一句话回答**：「p 值只是入场券。SRM 不过就作废，CI 下界够不到业务门槛就不值得上，护栏红了就不能上。」
</details>

### Q2：大盘转化率跌了 5%，给你 30 分钟，你的排查顺序是什么？

<details><summary>参考答案</summary>

严格按 2.5.7 的 SOP 走，**顺序不能换**：

**【0-3 分钟】确认这是不是异常**
看过去 30-90 天的波动区间。如果历史上转化率就在 ±5% 内波动，这可能是正常噪声。**和上周同一天比，不和昨天比**（消除周内效应）。

**【3-13 分钟】数据层排查**（六项校验，2.2.5）
- 上游分区产出了吗？行数趋势正常吗？
- 主键唯一吗？（重跑导致分母翻倍 → 转化率腰斩）
- 关键字段空值率突变了吗？
- 枚举值有新增吗？（新增了一个订单状态，没被 `CASE WHEN status='paid'` 覆盖 → 分子凭空减少）
- SQL/口径最近改过吗？（`git log`）
- 埋点/客户端发版了吗？前后端匹配率变了吗？

**关键技巧：先分别看分子和分母。** 是分子跌了还是分母涨了？这一步能直接定位一半的问题。

**【13-23 分钟】定位最小子群**
按贡献度（2.5.1，用绝对变化量不用变化率）逐层下钻：
`app_version/平台 → 渠道 → 页面/品类 → 用户分层`
目标是把「大盘跌 5%」收敛成「Android v8.2.0 的搜索页跌 40%，贡献了 82% 的跌幅」。

**【23-28 分钟】判断性质 + 对齐外部事件**
- 结构效应还是效率效应（2.5.2）？→ 决定该找投放还是找产品
- 漏斗哪一环掉的（2.5.3）？
- 把指标曲线和发版记录、实验上下线、运营活动画在同一张图上——**时间点对上是最强的证据**

**【28-30 分钟】输出结论**
包含四部分：结论 / 证据（2-3 个数字）/ 建议（谁做什么）/ 未排除项。

**新手最常犯的错**：跳过数据层直接编业务故事。花半天讲了个漂亮的故事，最后发现是上游任务重跑了。
</details>

### Q3：一个功能的曝光数（前端埋点）比服务端返回数少 15%，这个 CTR 还能不能用？

<details><summary>参考答案</summary>

**分三种情况**：

**① 分子分母同源（点击也来自前端）→ 可以用，但有前提**
丢失是随机的话，分子分母同比例缩小，CTR 不变。**但绝对曝光量不能对外报。**

**② 分子分母异源（点击来自后端）→ 不能直接用**
分母丢 15%、分子几乎不丢，CTR 会被系统性高估约 **17.6%**（= 1/0.85 - 1）。必须先按匹配率校正，或统一改成单边口径。

**③ 无论哪种情况，都必须先验证「丢失是不是随机的」**

```sql
-- 按 app_version / os / network 拆开看匹配率
```
- 如果各维度匹配率差异 < 5pp → 可认为近似随机，比率指标可用
- 如果**弱网用户匹配率明显更低** → 非随机丢失。网络好的用户被过度代表，而他们的点击行为本来就和弱网用户不同。**这种偏差不是靠「分子分母同比例」能消除的**，CTR 会系统性偏向某类人群
- 如果某个 `app_version` 匹配率特别低 → 该版本埋点有 bug，应该**排除该版本**后再算，而不是整体校正

**按 2.2.4 的分档标准，85% 属于「可用但需标注」**：
- ✅ 比率类指标（CTR）可用
- ✅ 同口径的环比、实验组 vs 对照组对比可用
- ⚠️ 绝对曝光量需按匹配率反推，且必须标注
- ❌ 不能和其他匹配率不同的页面/渠道直接比较 CTR

**标准表述**：
> 「基于前端同源口径，CTR 为 X%。前端曝光上报匹配率 85%，已验证各版本/网络类型间差异 < 3pp，无系统性偏差。趋势和组间对比可信；绝对曝光量偏低约 15%，不建议对外报。」

**最后一句很重要**：同时要推动修埋点。85% 是「凑合能用」，不是「没问题」。
</details>

### Q4：新用户留存看起来在提升，但整体留存在下降，怎么解释、怎么验证？

<details><summary>参考答案</summary>

**这是典型的辛普森悖论（2.4.9 陷阱 ④ / 2.5.8）**，最可能的解释是**用户结构变化**。

**核心机制**：整体留存 = Σ(各群体占比 × 各群体留存率)。
新用户的留存率通常远低于老用户。如果新用户占比大幅上升（比如大规模拉新、买量），即使新用户和老用户各自的留存率都在提升，加权平均仍可能下降。

**三个候选解释**：

| 解释 | 机制 | 怎么验证 |
|------|------|----------|
| **① 结构变化（最可能）** | 低留存的新用户占比上升 | 结构/效率拆解（2.5.2） |
| **② 老用户留存恶化** | 新用户在涨，老用户在掉，被平均掩盖 | 分别看新老用户的留存曲线 |
| **③ 口径/数据问题** | 「新用户」定义变了，或某批用户被重新归类 | 检查新用户定义、看分组人数是否突变 |

**验证步骤**：

**Step 1：分群看绝对量和占比**
```sql
SELECT dt,
       SUM(CASE WHEN is_new THEN 1 ELSE 0 END) AS new_cnt,
       SUM(CASE WHEN is_new THEN 0 ELSE 1 END) AS old_cnt,
       1.0*SUM(CASE WHEN is_new THEN 1 ELSE 0 END)/COUNT(*) AS new_ratio
FROM dws_user_daily GROUP BY dt ORDER BY dt;
```
如果 `new_ratio` 从 20% 涨到 50%，结构变化就是主因。

**Step 2：做结构/效率拆解（2.5.2）——这是决定性的一步**
```python
structure_efficiency_decompose(
    w0=[0.20, 0.80], r0=[0.15, 0.45],   # 期初：新用户占20%、留存15%；老用户80%、留存45%
    w1=[0.50, 0.50], r1=[0.17, 0.46],   # 期末：各自留存都涨了，但新用户占比翻倍多
)
# R0 = 0.2*0.15 + 0.8*0.45 = 39.0%
# R1 = 0.5*0.17 + 0.5*0.46 = 31.5%
# → 整体跌 7.5pp，其中结构效应 -9.0pp，效率效应 +1.2pp，交互项 +0.3pp（合计 -7.5pp ✓）
# 结论：效率在改善，但被结构恶化完全盖过
```

**Step 3：用固定结构重算（反事实）**
用**期初的用户构成权重** × **期末的各群留存率**，算出「如果结构没变，整体留存会是多少」。
上例中 = 0.2×0.17 + 0.8×0.46 = **40.2%**（比期初的 39.0% 还高）→ 证实产品侧其实在变好。

**Step 4：细分新用户来源**
新用户占比上升是从哪来的？如果是某个低质渠道大量买量，那么该谈的是投放 ROI，不是留存优化。
```sql
-- 按渠道看新用户的留存，找出拉低整体的那个渠道
```

**结论表述**：
> 「整体留存下降 7.5pp，其中结构效应 -9.0pp（新用户占比从 20% 升至 50%），效率效应 +1.2pp（新老用户留存率均有改善）。
> **产品体验在变好，下降完全由用户结构变化造成。**
> 建议：① 整体留存不再作为产品效果的主指标，改用分群留存；② 评估新增渠道的用户质量和 LTV，判断这波拉新是否划算。」

**这道题的核心能力**：把「一个坏消息」还原成「结构 + 效率」两个独立问题，并指出该找谁。
</details>

### Q5：你负责的核心指标口径要改，你需要做哪些事才能不引发全公司数据打架？

<details><summary>参考答案</summary>

口径变更是**组织协作问题**，不只是技术问题。按七步走：

**① 影响面评估（做之前）**
- 用数据血缘（2.1.7）找出所有依赖这个指标的表、看板、报告、告警
- 找出所有**人类使用者**：谁在周报里引用它、谁的 KPI 挂在它上面
- 工具找不全就 `grep` 代码仓库 + 在群里问一圈

**② 量化差异（最关键的一步）**
```sql
-- 新旧口径并排跑至少 30 天，算出差异幅度和方向
SELECT dt,
       old_metric, new_metric,
       new_metric - old_metric                          AS abs_diff,
       1.0*new_metric/NULLIF(old_metric,0) - 1          AS rel_diff
FROM (...) ORDER BY dt;
```
- 差异是**固定偏移**（每天都低 3%）还是**随时间变化**？前者好处理，后者说明两个口径在结构上不同
- **拆维度看差异**：如果只有某个渠道差异大，说明变更主要影响那个渠道，要单独通知它的负责人

**③ 写变更文档（必须有）**
```markdown
## 指标口径变更：GMV
- 变更内容：从「下单金额」改为「支付成功金额」
- 变更原因：原口径包含未支付订单，虚高
- 生效日期：2026-10-01
- 历史数据：是否回刷？（见第 ④ 步）
- 影响幅度：整体 -8.3%；自营渠道 -3.1%，第三方渠道 -15.2%
- 受影响的表/看板/报告：[清单]
- 对比数据：新旧口径 30 天并行数据 [链接]
- 负责人 / 答疑群：xxx
```

**④ 决定历史数据怎么处理**（三选一，各有代价）

| 方案 | 做法 | 优点 | 代价 |
|------|------|------|------|
| **全量回刷** | 历史数据按新口径重算 | 全时间序列可比 | 成本高；历史报告和归档数据对不上了 |
| **不回刷，断点标注** | 从生效日起用新口径 | 成本低 | 跨越生效日的同比/环比失效 |
| **双口径并存** | 新旧两列都保留一段时间 | ⭐️ 平滑过渡 | 存储和维护成本 |

**推荐双口径并存 3-6 个月**，给下游足够的迁移时间。

**⑤ 提前通知 + 设置缓冲期**
- 提前**至少 2 周**通知（重大变更 1 个月）
- 通知要**发给具体的人**，不是发个群公告就完事
- 给下游时间做适配，并提供对比数据帮他们理解差异

**⑥ 切换当天的动作**
- 在所有相关看板上打**时间标记线**（`ax.axvline`）和说明文字
- 监控告警的阈值同步调整（否则口径一变，告警全响）
- 值班答疑，准备好「为什么数字变了」的标准回答

**⑦ 沉淀到指标字典**
- 更新指标定义文档，**记录版本和生效日期**
- 理想状态：指标定义收敛到语义层（Looker LookML / dbt metrics），改一处全局生效，从根本上杜绝「两个人算出两个数」

---

**最核心的两条原则**：

> **① 口径变更的成本，90% 在沟通，不在代码。**
> 技术上改一行 SQL 就行，难的是让全公司在同一天、用同一个理解切换过去。
>
> **② 绝对不要静默改口径。**
> 静默变更是数据团队信任崩塌的第一原因。一次静默变更，会让业务方从此对所有数据都打个问号——这个信任损失要花几个月才能修复。
</details>

---

## 2.9 速查表

### 拿到一个分析任务时的自检

| 阶段 | 问自己 | 对应章节 |
|------|--------|----------|
| 取数前 | 这个指标的权威表是哪张？在哪一层？ | 2.1.3 |
| 取数前 | 维度是当前状态还是历史状态？会不会时间穿越？ | 2.1.4 |
| 取数前 | 分区字段和时间戳字段的时区一致吗？ | 2.1.5 |
| 取数后 | 六项数据校验跑了吗？ | 2.2.5 |
| 用埋点前 | 匹配率多少？丢失是随机的吗？ | 2.2.4 |
| 算 CTR 前 | 分子分母同源吗？曝光口径是什么？ | 2.2.1/2.2.2 |
| 做实验前 | 样本量够吗？主指标定了吗？随机化单元对吗？ | 2.4.2/2.4.3 |
| 看实验结果前 | SRM 过了吗？ | 2.4.5 |
| 报实验结果时 | 置信区间报了吗？护栏指标看了吗？ | 2.4.7 |
| 归因异常时 | 先排除数据问题了吗？ | 2.5.7 |
| 做下钻时 | 用的是绝对贡献度还是变化率？ | 2.5.1 |
| 整体变了但分组没变 | 做结构/效率拆解了吗？ | 2.5.2 |
| 交付前 | 别人能复现吗？口径写清楚了吗？ | 2.7.2 |

### 阶段二高频陷阱一览

| 陷阱 | 症状 | 正解 | 节 |
|------|------|------|-----|
| 用当前维度分析历史行为 | 因果颠倒的漂亮结论 | 拉链表取历史时点状态 | 2.1.4 |
| 分区时区 ≠ 时间戳时区 | 每天静默少 8 小时数据 | 分区多取一天 + 显式转时区 | 2.1.5 |
| 上游重跑导致重复 | 某天指标翻倍 | 查主键唯一性 | 2.1.6 |
| 前后端异源算 CTR | CTR 系统性高估 | 分子分母同源 | 2.2.1 |
| 曝光口径没说清 | 跨页面 CTR 不可比 | 明确面积/时长/去重三参数 | 2.2.2 |
| 非随机丢失 | 结论偏向某类用户 | 按维度拆匹配率验证 | 2.2.4 |
| merge 静默膨胀 | 金额虚高 | `validate="many_to_one"` | 2.3.2 |
| 长尾数据用 3σ 剔异常 | 误删大量真实值 | 业务规则 + 分位数截断 | 2.3.3 |
| 先算 rolling 再补日期 | 移动平均跨过缺失日 | 先 reindex 再 rolling | 2.3.4 |
| 用「有行为的用户」当分母 | 高估实验效果 | ITT 原则：用所有被分流用户 | 2.4.5 |
| 偷看数据 | 假阳性率飙到 35%+ | 事先定样本量，到点才看 | 2.4.9 |
| 用户级分流 + 请求级指标 | 方差低估 10 倍，大量假阳性 | delta method / bootstrap / 改用户级指标 | 2.4.9 |
| 只报 p 值不报 CI | 隐瞒了效应可能很小 | 必须同时报置信区间 | 2.4.7 |
| 把「不显著」说成「没效果」 | 严重的专业错误 | 看 CI 宽度判断是无效还是功效不足 | 2.4.11 |
| 下钻按变化率排序 | 抓了小渠道放过了大渠道 | 按绝对贡献度排序 | 2.5.1 |
| 整体变了就找产品 | 可能是流量结构问题 | 结构/效率拆解 | 2.5.2 |
| 留存口径不统一 | 「第 N 日」和「N 日内」差一倍 | 报数必须说明口径 | 2.5.4 |
| 最近 cohort 的 D30 不完整 | 留存看起来断崖下跌 | 未完整的 cohort 留白 | 2.5.4 |
| 静默改口径 | 全公司数据打架，信任崩塌 | 七步变更流程 | 2.8 Q5 |

### 阶段二完成标准

能独立做到这八件事，阶段二就可以打勾了：

- [ ] 拿到新表，能在 5 分钟内判断它属于哪一层、粒度是什么、时区怎么配的
- [ ] 用埋点数据前，会主动算匹配率并判断丢失是否随机
- [ ] 能独立完成一个 A/B 实验的**全流程**：设计 → 样本量 → AA → SRM → 分析 → 报告
- [ ] 看到实验结果，能在 5 分钟内说出「这个结论可不可信、为什么」
- [ ] 指标异常时，30 分钟内能给出「是数据问题还是业务问题 + 最可能的子群」
- [ ] 能把「整体变了」拆解成结构效应和效率效应，并指出该找谁
- [ ] 你的分析代码，别人 clone 下来能跑出同样的结果
- [ ] 改指标口径时，知道要通知谁、要准备什么材料

---

> 上一章：[第一章 SQL](./chapter-01-sql.md)
> 下一章：[第三章 推动决策与设计体系](./chapter-03.md)
