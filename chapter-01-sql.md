# 第一章 SQL

> 这一项的权重超过其他所有技术项之和。
>
> 原因很简单：分析师 80% 的时间在和数据打交道，而 SQL 是唯一一个「不会就寸步难行、会了就能独立干活」的技能。Python 可以慢慢学，BI 工具可以现学现用，但 SQL 写错一个 JOIN，你后面所有的分析、结论、汇报全是错的，而且你不会知道。
>
> 本章方言以 **Trino / Presto** 为主（大数据场景最常见），关键差异处会标注 Hive/Spark SQL 和 MySQL 的写法。

---

## 1.0 贯穿全章的示例数据

后面所有例子都基于这四张表。**建议先花两分钟把它们的粒度记住**——「这张表一行代表什么」是 SQL 里最重要的问题。

### `dim_user` 用户维度表 — 粒度：一个用户一行

| user_id | reg_date   | channel | city |
|---------|------------|---------|------|
| 1       | 2026-01-05 | organic | 上海 |
| 2       | 2026-02-11 | ads_a   | 北京 |
| 3       | 2026-02-11 | ads_b   | 上海 |
| 4       | 2026-03-02 | organic | NULL |

> user_4 注册了但从没下过单；city 是 NULL——这两个特征后面会反复用到。

### `dwd_order` 订单主表 — 粒度：一个订单一行

| order_id | user_id | pay_time            | status | amount | dt         |
|----------|---------|---------------------|--------|--------|------------|
| O1       | 1       | 2026-09-01 10:00:00 | paid   | 200    | 2026-09-01 |
| O2       | 1       | 2026-09-03 22:30:00 | refund | 150    | 2026-09-03 |
| O3       | 2       | 2026-09-03 09:15:00 | paid   | 500    | 2026-09-03 |
| O4       | 3       | 2026-09-05 16:40:00 | paid   | 200    | 2026-09-05 |

> `dt` 是分区字段。真实订单总额 = 200 + 150 + 500 + 200 = **1050**。记住这个数，1.3 节会用它演示「膨胀」。

### `dwd_order_item` 订单明细表 — 粒度：一个订单的一个商品一行

| order_id | item_id | sku   | qty | item_amount |
|----------|---------|-------|-----|-------------|
| O1       | I1      | SKU_A | 1   | 120         |
| O1       | I2      | SKU_B | 2   | 80          |
| O2       | I3      | SKU_C | 1   | 150         |
| O3       | I4      | SKU_A | 5   | 500         |
| O4       | I5      | SKU_B | 1   | 200         |

> **注意 O1 有两行**。这是 1.3 节所有麻烦的起点。

### `dwd_event` 埋点事件表 — 粒度：一次事件一行

| user_id | event_name | event_time          | props (JSON)                          | dt         |
|---------|------------|---------------------|---------------------------------------|------------|
| 1       | view       | 2026-09-01 09:58:00 | `{"page":"home","tags":["a","b"]}`    | 2026-09-01 |
| 1       | add_cart   | 2026-09-01 09:59:00 | `{"sku":"SKU_A","price":120}`         | 2026-09-01 |
| 1       | pay        | 2026-09-01 10:00:00 | `{"order_id":"O1"}`                   | 2026-09-01 |
| 2       | view       | 2026-09-03 09:10:00 | `{"page":"search","tags":["b"]}`      | 2026-09-03 |
| 2       | pay        | 2026-09-03 09:15:00 | `{"order_id":"O3"}`                   | 2026-09-03 |
| 3       | view       | 2026-09-05 16:30:00 | `{"page":"home","tags":[]}`           | 2026-09-05 |
| 3       | pay        | 2026-09-05 16:40:00 | `{"order_id":"O4"}`                   | 2026-09-05 |

---

## 1.1 基础语法

### 1.1.1 先记执行顺序，不是书写顺序

这是初学者最大的认知盲区。SQL 的**书写顺序**和**执行顺序**完全不同：

```
书写顺序： SELECT → FROM → WHERE → GROUP BY → HAVING → ORDER BY → LIMIT
执行顺序： FROM → WHERE → GROUP BY → HAVING → SELECT → ORDER BY → LIMIT
            ①      ②       ③          ④        ⑤       ⑥         ⑦
```

记住执行顺序，下面三件事立刻就通了：

**① 为什么 WHERE 里不能用聚合函数？**

```sql
-- ✗ 报错：WHERE 在 GROUP BY 之前执行，那时还没有 SUM
SELECT user_id, SUM(amount) AS total
FROM dwd_order
WHERE SUM(amount) > 300
GROUP BY user_id;

-- ✓ 正确：聚合后的过滤用 HAVING
SELECT user_id, SUM(amount) AS total
FROM dwd_order
GROUP BY user_id
HAVING SUM(amount) > 300;
```

**② 为什么 WHERE 里不能用 SELECT 定义的别名？**

```sql
-- ✗ 报错：WHERE 在 SELECT 之前执行，别名 pay_date 还不存在
SELECT order_id, date(pay_time) AS pay_date
FROM dwd_order
WHERE pay_date = DATE '2026-09-03';

-- ✓ 正确：WHERE 里重写表达式
SELECT order_id, date(pay_time) AS pay_date
FROM dwd_order
WHERE date(pay_time) = DATE '2026-09-03';
```

**③ 为什么 ORDER BY 里可以用别名？**

因为 ORDER BY 在 SELECT 之后执行，别名已经存在了。

```sql
-- ✓ 可以
SELECT user_id, SUM(amount) AS total
FROM dwd_order
GROUP BY user_id
ORDER BY total DESC;
```

### 1.1.2 WHERE vs HAVING：过滤时机决定结果

这两个的区别不是「一个过滤行、一个过滤组」这么抽象，看一个具体对比：

```sql
-- A：只统计已支付订单，每个用户的支付金额
SELECT user_id, SUM(amount) AS paid_amount
FROM dwd_order
WHERE status = 'paid'          -- 先扔掉退款单，再聚合
GROUP BY user_id;
```
结果：
| user_id | paid_amount |
|---------|-------------|
| 1       | 200         |
| 2       | 500         |
| 3       | 200         |

```sql
-- B：统计所有订单，但只保留有退款的用户
SELECT user_id, SUM(amount) AS all_amount
FROM dwd_order
GROUP BY user_id
HAVING SUM(CASE WHEN status = 'refund' THEN 1 ELSE 0 END) > 0;  -- 聚合后再筛组
```
结果：
| user_id | all_amount |
|---------|------------|
| 1       | 350        |

**一句话判断**：条件只和「单行」有关 → WHERE；条件和「整组的汇总值」有关 → HAVING。

> **性能提醒**：能放 WHERE 的绝不放 HAVING。WHERE 提前过滤能减少进入 shuffle 的数据量，HAVING 是全量聚合完才过滤。

### 1.1.3 GROUP BY 的常见误区

**误区一：SELECT 里出现了没在 GROUP BY 里的非聚合列**

```sql
-- ✗ Trino/Postgres 直接报错；MySQL 在 ONLY_FULL_GROUP_BY 关闭时会
--   随机返回一个 city，你根本发现不了错在哪
SELECT user_id, city, SUM(amount)
FROM dwd_order o JOIN dim_user u USING (user_id)
GROUP BY user_id;

-- ✓ 要么加进 GROUP BY
GROUP BY user_id, city
-- ✓ 要么明确用聚合函数表达你想要哪个
SELECT user_id, MAX(city) AS city, SUM(amount) ...
```

**误区二：以为 GROUP BY 后行数 = 原表行数**

GROUP BY 之后，**一行代表一个组**。这是「粒度变化」，后面 JOIN 时会非常关键。

### 1.1.4 ORDER BY 与 LIMIT

```sql
-- 取金额最高的 2 个订单
SELECT order_id, amount
FROM dwd_order
ORDER BY amount DESC
LIMIT 2;
```

三个必须知道的点：

1. **没有 ORDER BY 的 LIMIT 结果是不确定的**。分布式引擎每次返回的行可能不同，不要用它做「抽样看看」以外的事。
2. **ORDER BY 会触发全局排序**，在大表上极贵。要「每组 Top N」请用窗口函数（1.7 节），不要用 ORDER BY + LIMIT 硬凑。
3. **NULL 的排序位置各引擎不同**。Trino/Postgres 默认 `NULLS LAST`（升序时），MySQL 默认 NULL 排最前。跨库迁移时这是静默的坑，需要显式写：

```sql
ORDER BY city ASC NULLS LAST
```

### ⚠️ 本节自检

```sql
-- 这条 SQL 想「找出支付金额超过 300 的用户」，错在哪？
SELECT user_id, SUM(amount) AS total
FROM dwd_order
WHERE status = 'paid' AND total > 300
GROUP BY user_id;
```
<details><summary>答案</summary>

`total` 是 SELECT 阶段才产生的别名，且是聚合结果，WHERE 阶段两样都不存在。应改为：

```sql
SELECT user_id, SUM(amount) AS total
FROM dwd_order
WHERE status = 'paid'
GROUP BY user_id
HAVING SUM(amount) > 300;
```
</details>

---

## 1.2 各类 JOIN 的语义差异

### 1.2.1 四种 JOIN 一次看懂

用 `dim_user`（4 行）JOIN `dwd_order`（4 行，但只涉及 user 1/2/3），`ON u.user_id = o.user_id`：

| JOIN 类型 | 保留谁 | 结果行数 | 说明 |
|-----------|--------|----------|------|
| `INNER JOIN` | 两边都有 | 4 | user_4 没订单，被丢掉 |
| `LEFT JOIN` | 左表全留 | 5 | user_4 保留，订单字段全 NULL |
| `RIGHT JOIN` | 右表全留 | 4 | 订单都有对应用户，等同 INNER |
| `FULL OUTER JOIN` | 两边全留 | 5 | 左右缺失的都补 NULL |
| `CROSS JOIN` | 笛卡尔积 | 16 | 4 × 4，无 ON 条件 |

```sql
-- LEFT JOIN 的结果（重点看 user_4）
SELECT u.user_id, o.order_id, o.amount
FROM dim_user u
LEFT JOIN dwd_order o ON u.user_id = o.user_id;
```

| user_id | order_id | amount |
|---------|----------|--------|
| 1       | O1       | 200    |
| 1       | O2       | 150    |
| 2       | O3       | 500    |
| 3       | O4       | 200    |
| 4       | NULL     | NULL   |

### 1.2.2 最经典的坑：LEFT JOIN 被 WHERE 变回 INNER JOIN

这个错误每个分析师都犯过，而且**不报错、结果看着正常**。

```sql
-- ✗ 意图：统计每个用户的已支付订单数（含 0 单用户）
SELECT u.user_id, COUNT(o.order_id) AS paid_cnt
FROM dim_user u
LEFT JOIN dwd_order o ON u.user_id = o.user_id
WHERE o.status = 'paid'        -- ← 灾难在这里
GROUP BY u.user_id;
```

**为什么错**：LEFT JOIN 给 user_4 补了一行全 NULL，然后 `WHERE o.status = 'paid'` 判断 `NULL = 'paid'` → 结果是 NULL（不是 true）→ 这行被过滤掉。user_4 消失了，LEFT JOIN 白做。

结果：
| user_id | paid_cnt |
|---------|----------|
| 1 | 1 |
| 2 | 1 |
| 3 | 1 |

user_4 不见了。

**两种正确写法**：

```sql
-- ✓ 写法一：把过滤条件放进 ON（在 JOIN 时就限定右表）
SELECT u.user_id, COUNT(o.order_id) AS paid_cnt
FROM dim_user u
LEFT JOIN dwd_order o
  ON u.user_id = o.user_id
 AND o.status = 'paid'         -- ← 放 ON 里
GROUP BY u.user_id;

-- ✓ 写法二：先把右表过滤好再 JOIN
SELECT u.user_id, COUNT(o.order_id) AS paid_cnt
FROM dim_user u
LEFT JOIN (
  SELECT * FROM dwd_order WHERE status = 'paid'
) o ON u.user_id = o.user_id
GROUP BY u.user_id;
```

结果（正确）：
| user_id | paid_cnt |
|---------|----------|
| 1 | 1 |
| 2 | 1 |
| 3 | 1 |
| 4 | 0 |

**记住这条规则**：

> **对于 LEFT JOIN，右表的过滤条件写在 ON 里；左表的过滤条件写在 WHERE 里。**
> 对于 INNER JOIN，写哪里结果都一样（优化器会自己下推）。

### 1.2.3 ON 和 WHERE 的本质区别

- `ON` 决定**哪些行能配上对**（配不上的，LEFT JOIN 仍会保留左行并补 NULL）
- `WHERE` 决定**JOIN 完成后哪些行能留下**

顺序是：先按 ON 配对 → 补 NULL → 再用 WHERE 筛。

**唯一合法地在 WHERE 里引用右表的场景**：反连接（找「没有 XX 的」）

```sql
-- 找出从未下过单的用户
SELECT u.user_id
FROM dim_user u
LEFT JOIN dwd_order o ON u.user_id = o.user_id
WHERE o.order_id IS NULL;      -- ← 这里的 IS NULL 是有意为之
```
结果：user_4。这叫 **anti-join 模式**，是刻意利用「补 NULL」的行为，不是 bug。

### 1.2.4 CROSS JOIN 的正当用途

`CROSS JOIN` 不是只会出事故，它有个高频正当用途：**生成完整的维度骨架**。

```sql
-- 要求：输出 9 月 1-5 日 × 所有渠道 的订单数，没有数据的格子补 0
WITH dates AS (
  SELECT d FROM UNNEST(SEQUENCE(DATE '2026-09-01', DATE '2026-09-05', INTERVAL '1' DAY)) AS t(d)
),
channels AS (
  SELECT DISTINCT channel FROM dim_user
),
skeleton AS (
  SELECT d, channel FROM dates CROSS JOIN channels   -- 5 × 3 = 15 个格子
)
SELECT s.d, s.channel, COUNT(o.order_id) AS order_cnt
FROM skeleton s
LEFT JOIN dwd_order o ON o.dt = s.d
LEFT JOIN dim_user u ON u.user_id = o.user_id AND u.channel = s.channel
GROUP BY s.d, s.channel
ORDER BY s.d, s.channel;
```

没有这个骨架，日期断档的那天会直接从结果里消失，画出来的折线图会「跳过」那一天，看起来一切正常——这是可视化里最隐蔽的错误之一。

### ⚠️ 本节自检

```sql
-- 这条 SQL 想统计「上海用户的订单数（含无订单用户）」，结果对吗？
SELECT u.user_id, COUNT(o.order_id) AS cnt
FROM dim_user u
LEFT JOIN dwd_order o ON u.user_id = o.user_id
WHERE u.city = '上海'
GROUP BY u.user_id;
```
<details><summary>答案</summary>

**对的**。`u.city` 是**左表**字段，左表的行本来就都在，WHERE 过滤左表不会破坏 LEFT JOIN 语义。只有过滤**右表**字段才会退化成 INNER JOIN。

（附带一提：user_4 的 city 是 NULL，`NULL = '上海'` 为 NULL，所以它会被排除——这是 NULL 的问题，不是 JOIN 的问题，见 1.9 节。）
</details>

---

## 1.3 JOIN 粒度控制 ⭐️ 本章最重要的一节

> 如果这一章你只记一件事，记这个：**JOIN 之前，先问「两边各自的主键是什么、是否唯一」。**

### 1.3.1 亲眼看一次「膨胀」

我们已知真实订单总额 = **1050**（O1:200 + O2:150 + O3:500 + O4:200）。

现在加一个需求：「按 SKU 看订单总额」，于是 JOIN 明细表：

```sql
-- ✗ 看起来完全合理，结果是错的
SELECT SUM(o.amount) AS total_amount
FROM dwd_order o
JOIN dwd_order_item i ON o.order_id = i.order_id;
```

**结果：1250**，而不是 1050。多了 200。

原因：O1 在明细表里有两行（I1、I2），JOIN 后 O1 变成了两行，它的 `amount=200` 被加了两遍。

| order_id | item_id | o.amount ← 被重复带出来 |
|----------|---------|--------|
| O1 | I1 | 200 |
| O1 | I2 | **200 ← 重复** |
| O2 | I3 | 150 |
| O3 | I4 | 500 |
| O4 | I5 | 200 |

合计 1250。

### 1.3.2 为什么 `SUM(DISTINCT)` 不是解药

很多人第一反应是加 DISTINCT：

```sql
-- ✗ 更危险的错误
SELECT SUM(DISTINCT o.amount) FROM ...
```

`SUM(DISTINCT amount)` 去重的是**金额值**，不是订单。这批数据里 O1 和 O4 的金额都是 200，会被当成同一个值只算一次：

`{200, 150, 500}` → **850**。

比刚才错得更离谱，而且方向相反——你可能会误以为「加了 DISTINCT 变小了，说明修对了」。

> **DISTINCT 从来不是粒度问题的解药，它只是把「多算」换成「少算」。**

### 1.3.3 三种正确姿势

**姿势一：先聚合，再 JOIN（最推荐）**

把右表压到和左表相同的粒度再 JOIN。

```sql
-- ✓ 先把 item 聚合成「一个订单一行」
WITH item_agg AS (
  SELECT
    order_id,
    COUNT(*)        AS item_cnt,
    SUM(qty)        AS total_qty,
    SUM(item_amount) AS item_amount_sum
  FROM dwd_order_item
  GROUP BY order_id                -- ← 粒度对齐到 order_id
)
SELECT
  SUM(o.amount)    AS total_amount,   -- 1050 ✓
  SUM(a.item_cnt)  AS total_items
FROM dwd_order o
LEFT JOIN item_agg a ON o.order_id = a.order_id;
```

**姿势二：JOIN 后用条件聚合去重**

```sql
-- ✓ 只在每个订单的第一行上取 amount
SELECT
  SUM(CASE WHEN rn = 1 THEN o_amount ELSE 0 END) AS total_amount
FROM (
  SELECT
    o.amount AS o_amount,
    ROW_NUMBER() OVER (PARTITION BY o.order_id ORDER BY i.item_id) AS rn
  FROM dwd_order o
  JOIN dwd_order_item i ON o.order_id = i.order_id
) t;
```

**姿势三：明细指标用明细表的字段，主表指标用主表**

```sql
-- ✓ 按 SKU 看金额，就用 item_amount（明细粒度的字段），不要用 o.amount
SELECT i.sku, SUM(i.item_amount) AS sku_amount
FROM dwd_order o
JOIN dwd_order_item i ON o.order_id = i.order_id
WHERE o.status = 'paid'
GROUP BY i.sku;
```

> **判断口诀**：JOIN 后的结果表，一行代表什么？如果代表「订单-商品」，那就只能对「订单-商品」级别的度量求和（`item_amount`、`qty`），不能对「订单」级别的度量求和（`amount`、`运费`、`优惠券`）。

### 1.3.4 JOIN 前的唯一性自检（养成肌肉记忆）

**每次 JOIN 之前，花 30 秒跑这个：**

```sql
-- 检查 dwd_order_item 的 order_id 是否唯一
SELECT
  COUNT(*)                  AS total_rows,
  COUNT(DISTINCT order_id)  AS distinct_keys,
  COUNT(*) - COUNT(DISTINCT order_id) AS dup_rows
FROM dwd_order_item;
```
| total_rows | distinct_keys | dup_rows |
|------------|---------------|----------|
| 5 | 4 | 1 |

`dup_rows > 0` → 这一侧是「多」，JOIN 会膨胀，必须先聚合。

**进一步：看重复得多严重**

```sql
SELECT order_id, COUNT(*) AS cnt
FROM dwd_order_item
GROUP BY order_id
HAVING COUNT(*) > 1
ORDER BY cnt DESC
LIMIT 10;
```

### 1.3.5 JOIN 后的行数校验（必须做）

```sql
-- LEFT JOIN 之后，结果行数应该 >= 左表行数；
-- 如果 > 左表行数，说明右表不唯一，发生了膨胀
SELECT
  (SELECT COUNT(*) FROM dwd_order)                       AS left_rows,
  (SELECT COUNT(*) FROM dwd_order o
     LEFT JOIN dwd_order_item i ON o.order_id = i.order_id) AS joined_rows;
```
| left_rows | joined_rows |
|-----------|-------------|
| 4 | 5 |

5 > 4 → 膨胀了，停下来检查。

### 1.3.6 三表以上 JOIN 的连环膨胀

最可怕的情况是多张「一对多」表串起来：

```sql
-- ✗ 订单(4) JOIN 明细(5) JOIN 事件(7) → 行数可能爆到几十甚至上千倍
FROM dwd_order o
JOIN dwd_order_item i ON o.order_id = i.order_id
JOIN dwd_event e ON e.user_id = o.user_id
```

O1 有 2 个明细 × user_1 有 3 个事件 = O1 单独就变成 6 行。这种「乘法效应」在生产数据上能把一张千万行的表 JOIN 成百亿行，任务直接 OOM。

**唯一正确做法：每张「多」表先各自聚合到共同粒度，再依次 JOIN。**

```sql
-- ✓
WITH item_agg AS (
  SELECT order_id, SUM(item_amount) AS item_amt, COUNT(*) AS item_cnt
  FROM dwd_order_item GROUP BY order_id
),
event_agg AS (
  SELECT user_id, COUNT(*) AS event_cnt
  FROM dwd_event GROUP BY user_id
)
SELECT o.order_id, o.amount, ia.item_cnt, ea.event_cnt
FROM dwd_order o
LEFT JOIN item_agg  ia ON o.order_id = ia.order_id
LEFT JOIN event_agg ea ON o.user_id  = ea.user_id;
```
每次 JOIN 右侧都是唯一键，行数恒等于 4。

### ⚠️ 本节自检

你写了一个报表，昨天 GMV 是 1000 万，今天代码没改但跑出来 1800 万。列出你的排查顺序。

<details><summary>答案思路</summary>

1. **先看行数**：JOIN 后的行数今天是不是变了？和左表行数比。
2. **查唯一性**：右表的 JOIN key 今天是不是出现了重复？（上游可能重跑导致数据重复写入、或新增了一个维度导致维度表变成多行）
3. **定位膨胀的 key**：`GROUP BY join_key HAVING COUNT(*) > 1 ORDER BY 2 DESC`，看是哪些 key 重复了。
4. **确认是数据问题还是 SQL 问题**：如果 SQL 没改，99% 是上游数据的粒度变了（最常见：维度表加了拉链、或者上游任务重跑没做幂等）。
</details>

---

## 1.4 聚合函数与 `COUNT(DISTINCT x)` 的性能代价

### 1.4.1 先分清三个 COUNT

```sql
SELECT
  COUNT(*)                AS cnt_star,      -- 行数，NULL 也算
  COUNT(city)             AS cnt_col,       -- city 非 NULL 的行数
  COUNT(DISTINCT city)    AS cnt_distinct   -- city 的不同取值个数，不含 NULL
FROM dim_user;
```
| cnt_star | cnt_col | cnt_distinct |
|----------|---------|--------------|
| 4 | 3 | 2 |

- `COUNT(*)` = 4（user_4 也算）
- `COUNT(city)` = 3（user_4 的 city 是 NULL，不计）
- `COUNT(DISTINCT city)` = 2（上海、北京；NULL 不算一类）

> **业务后果**：如果你用 `COUNT(city)` 当「用户数」，city 为空的用户就凭空消失了。统计用户数永远用 `COUNT(*)` 或 `COUNT(DISTINCT user_id)`，不要用业务字段。

### 1.4.2 为什么 `COUNT(DISTINCT)` 这么贵

理解这一点需要知道分布式引擎怎么算聚合：

**普通 `COUNT(*)` / `SUM()`——可以局部聚合**
```
节点A: 100 万行 → 局部算出 100万
节点B: 100 万行 → 局部算出 100万
节点C: 100 万行 → 局部算出 100万
                   ↓ 只传 3 个数字
              汇总节点: 300万
```
网络只传了 3 个数。非常快。

**`COUNT(DISTINCT user_id)`——不能局部聚合**
```
节点A 的 user_id 可能和节点B 重复，不去重就不能局部计数
       ↓
必须把所有 user_id 按哈希重新分发（shuffle）到固定节点
       ↓
网络传输量 ≈ 整列数据量，且容易在某几个节点堆积
```

所以：**`COUNT(DISTINCT)` 的代价 ≈ 一次全量 shuffle**，数据量越大越慢，而且是非线性变慢。

### 1.4.3 多个 `COUNT(DISTINCT)` 的爆炸

```sql
-- ✗ 这种 SQL 在大表上是任务杀手
SELECT
  dt,
  COUNT(DISTINCT user_id)   AS uv,
  COUNT(DISTINCT order_id)  AS orders,
  COUNT(DISTINCT sku)       AS skus,
  COUNT(DISTINCT city)      AS cities
FROM big_table
GROUP BY dt;
```

部分引擎（如 Hive 的某些执行计划）会为**每一个** distinct 单独做一次数据膨胀+shuffle，4 个 distinct 就是 4 倍数据量。

**优化方案一：用近似去重（Trino / Spark）**

```sql
-- ✓ 误差约 ±2%，速度提升一个数量级
SELECT
  dt,
  approx_distinct(user_id)  AS uv,      -- Trino
  approx_distinct(order_id) AS orders
FROM big_table
GROUP BY dt;

-- Spark SQL / Hive: approx_count_distinct(user_id)
-- ClickHouse:       uniq(user_id) / uniqCombined(user_id)
```

> **什么时候能用近似**：看趋势、看大盘、做探索性分析 → 完全可以。
> **什么时候绝对不能**：对外报数、财务口径、A/B 实验结论、KPI 考核 → 必须精确。

**优化方案二：两段式聚合（精确且快）**

```sql
-- ✓ 先在明细层去重，再计数——把 distinct 变成普通 count
WITH deduped AS (
  SELECT DISTINCT dt, user_id FROM big_table    -- 这一步只 shuffle 两列
)
SELECT dt, COUNT(*) AS uv
FROM deduped
GROUP BY dt;
```

**优化方案三：预聚合成中间表**

日活这类天天要算的指标，别每次从明细算。建一张 `dws_user_active_daily`（粒度：日期 × 用户），下游直接 `COUNT(*)`。

**优化方案四：Bitmap（ClickHouse / Doris / StarRocks）**

用户 ID 映射成整数后存 bitmap，去重计数和「多日留存交集」都能变成位运算，是留存分析的标准解法。

### 1.4.4 其他聚合函数的 NULL 行为（高频踩坑）

```sql
SELECT
  SUM(amount)   AS s,    -- 忽略 NULL
  AVG(amount)   AS a,    -- 忽略 NULL：分母是非 NULL 的行数！
  MAX(amount)   AS mx,   -- 忽略 NULL
  COUNT(amount) AS c
FROM dwd_order;
```

**`AVG` 的陷阱**：假设有 100 行，其中 40 行 amount 是 NULL。
- `AVG(amount)` 的分母是 **60**，不是 100
- 如果业务上 NULL 应该算 0（比如「没有优惠券 = 优惠 0 元」），那 AVG 就算高了

```sql
-- ✓ 显式表达你想要的口径
AVG(COALESCE(amount, 0))              -- NULL 当 0，分母 100
SUM(amount) / COUNT(*)                -- 同上，更直白
AVG(amount)                           -- NULL 不参与，分母 60
```

**空结果集的陷阱**：

```sql
-- 如果 WHERE 没匹配到任何行
SELECT SUM(amount) FROM dwd_order WHERE dt = '2099-01-01';
-- 返回：NULL（不是 0！）

SELECT COUNT(*) FROM dwd_order WHERE dt = '2099-01-01';
-- 返回：0
```
下游拿 NULL 去做除法或比较，会得到一连串 NULL，报表上显示空白。习惯性包一层：

```sql
SELECT COALESCE(SUM(amount), 0) AS total FROM ...
```

---

## 1.5 CASE WHEN：条件聚合与行转列

`CASE WHEN` 是分析师使用频率最高的函数，没有之一。

### 1.5.1 条件聚合（最重要的用法）

需求：一条 SQL 同时算出「订单数、支付单数、退款单数、支付金额、支付率」。

```sql
SELECT
  dt,
  COUNT(*)                                                AS order_cnt,
  SUM(CASE WHEN status = 'paid'   THEN 1 ELSE 0 END)      AS paid_cnt,
  SUM(CASE WHEN status = 'refund' THEN 1 ELSE 0 END)      AS refund_cnt,
  SUM(CASE WHEN status = 'paid'   THEN amount ELSE 0 END) AS paid_amount,
  -- 支付率：注意分母要转 double，否则整数除法得 0
  1.0 * SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) / COUNT(*) AS paid_rate
FROM dwd_order
GROUP BY dt
ORDER BY dt;
```
| dt | order_cnt | paid_cnt | refund_cnt | paid_amount | paid_rate |
|----|-----------|----------|------------|-------------|-----------|
| 2026-09-01 | 1 | 1 | 0 | 200 | 1.0 |
| 2026-09-03 | 2 | 1 | 1 | 500 | 0.5 |
| 2026-09-05 | 1 | 1 | 0 | 200 | 1.0 |

> **为什么这比写 3 条 SQL 再 JOIN 好**：只扫一次表；三个指标天然对齐同一批数据，不会出现「A 报表和 B 报表口径不一致」。

### 1.5.2 `SUM(CASE...)` vs `COUNT(CASE...)` 的区别

```sql
SUM(CASE WHEN status='paid' THEN 1 ELSE 0 END)   -- 写 0，靠加法
COUNT(CASE WHEN status='paid' THEN 1 END)         -- 不写 ELSE，靠 COUNT 跳过 NULL
```

两者结果相同。但下面这个**是错的**：

```sql
-- ✗ COUNT 的是「非 NULL 的个数」，ELSE 0 让不满足条件的也变成非 NULL
COUNT(CASE WHEN status='paid' THEN 1 ELSE 0 END)   -- 永远等于 COUNT(*)
```

**建议统一用 `SUM(CASE WHEN ... THEN 1 ELSE 0 END)`**，语义最清楚，不容易写错。

### 1.5.3 条件去重计数

```sql
-- 各渠道的「下过单的用户数」
SELECT
  u.channel,
  COUNT(DISTINCT u.user_id)                                        AS total_users,
  COUNT(DISTINCT CASE WHEN o.status='paid' THEN u.user_id END)     AS paid_users
  -- ↑ 注意：这里必须不写 ELSE，让不满足条件的返回 NULL 被 DISTINCT 跳过
FROM dim_user u
LEFT JOIN dwd_order o ON u.user_id = o.user_id
GROUP BY u.channel;
```

### 1.5.4 行转列（透视）

需求：把「每天每种状态的订单数」从长表变成宽表。

```sql
SELECT
  dt,
  SUM(CASE WHEN status = 'paid'   THEN 1 ELSE 0 END) AS paid,
  SUM(CASE WHEN status = 'refund' THEN 1 ELSE 0 END) AS refund
FROM dwd_order
GROUP BY dt;
```

> **原理**：行转列 = `GROUP BY 要保留的维度` + `每个目标列写一个条件聚合`。
> 列是**写死**的，所以只适用于取值有限且稳定的字段（状态、平台、性别）。如果取值会变（SKU、城市），应该保持长表，交给 BI 工具去透视。

### 1.5.5 分桶 / 分层

```sql
-- 把订单金额分档，看分布
SELECT
  CASE
    WHEN amount IS NULL   THEN '未知'
    WHEN amount <  100    THEN '01_0-100'
    WHEN amount <  300    THEN '02_100-300'
    WHEN amount <  1000   THEN '03_300-1000'
    ELSE                       '04_1000+'
  END AS amount_bucket,
  COUNT(*)     AS order_cnt,
  SUM(amount)  AS total_amount
FROM dwd_order
GROUP BY 1
ORDER BY 1;
```

三个实践细节：

1. **CASE WHEN 是从上往下短路匹配的**，第一个满足的分支生效，所以区间条件不用写 `AND amount >= 100`。
2. **给桶名加数字前缀**（`01_`、`02_`），否则排序会变成字典序：`0-100` → `100-300` → `1000+` → `300-1000`，图表上的顺序全乱。
3. **NULL 一定要显式处理并放第一个分支**。否则 `amount < 100` 对 NULL 返回 NULL（非 true），会一路落到 `ELSE` 里，被错分成「1000+」。这是最阴的坑。

### ⚠️ 本节自检

```sql
-- 想算「支付转化率」，为什么结果永远是 0？
SELECT
  SUM(CASE WHEN status='paid' THEN 1 ELSE 0 END) / COUNT(*) AS rate
FROM dwd_order;
```
<details><summary>答案</summary>

整数除以整数 = 整数除法，1/4 被截断成 0。修法：

```sql
1.0 * SUM(CASE WHEN status='paid' THEN 1 ELSE 0 END) / COUNT(*)
-- 或
CAST(SUM(...) AS DOUBLE) / COUNT(*)
-- 或 Trino 里直接
SUM(CASE WHEN status='paid' THEN 1e0 ELSE 0e0 END) / COUNT(*)
```
</details>

---

## 1.6 子查询与 CTE

### 1.6.1 四种子查询

**① 标量子查询**（返回一个值）
```sql
-- 每个订单金额和全站均值的差
SELECT order_id, amount,
       amount - (SELECT AVG(amount) FROM dwd_order) AS diff_from_avg
FROM dwd_order;
```

**② IN 子查询**（返回一列）
```sql
SELECT * FROM dim_user
WHERE user_id IN (SELECT user_id FROM dwd_order WHERE status = 'paid');
```

**③ EXISTS 子查询**（返回是否存在）
```sql
SELECT * FROM dim_user u
WHERE EXISTS (SELECT 1 FROM dwd_order o WHERE o.user_id = u.user_id);
```

**④ FROM 子查询 / 派生表**
```sql
SELECT channel, AVG(user_amount) AS avg_per_user
FROM (
  SELECT u.channel, u.user_id, SUM(o.amount) AS user_amount
  FROM dim_user u LEFT JOIN dwd_order o ON u.user_id = o.user_id
  GROUP BY u.channel, u.user_id
) t
GROUP BY channel;
```

> **`IN` vs `EXISTS` vs `JOIN`**：现代引擎的优化器基本都会把三者改写成同一个执行计划，性能差异不大。但 **`NOT IN` 是例外**——它遇到 NULL 会全盘返回空，见 1.9.3 节。**养成用 `NOT EXISTS` 或 `LEFT JOIN ... IS NULL` 代替 `NOT IN` 的习惯。**

### 1.6.2 用 CTE 把长 SQL 拆成可读的段落

对比一下同一个需求的两种写法。

**需求**：找出「9 月支付金额排名前 2 的渠道」下的「支付金额最高的用户」。

**✗ 嵌套写法**（读的人要从最里层往外剥）
```sql
SELECT * FROM (
  SELECT channel, user_id, amt,
         ROW_NUMBER() OVER (PARTITION BY channel ORDER BY amt DESC) rn
  FROM (
    SELECT u.channel, u.user_id, SUM(o.amount) amt
    FROM dwd_order o JOIN dim_user u ON o.user_id = u.user_id
    WHERE o.dt BETWEEN '2026-09-01' AND '2026-09-30' AND o.status='paid'
    GROUP BY u.channel, u.user_id
  ) a
  WHERE channel IN (
    SELECT channel FROM (
      SELECT u.channel, SUM(o.amount) c_amt,
             ROW_NUMBER() OVER (ORDER BY SUM(o.amount) DESC) c_rn
      FROM dwd_order o JOIN dim_user u ON o.user_id = u.user_id
      WHERE o.dt BETWEEN '2026-09-01' AND '2026-09-30' AND o.status='paid'
      GROUP BY u.channel
    ) b WHERE c_rn <= 2
  )
) c WHERE rn = 1;
```

**✓ CTE 写法**（从上往下顺着读，每段一个意思，且消除了重复代码）
```sql
WITH
-- ① 基础数据：9 月已支付订单 + 用户渠道
base AS (
  SELECT u.channel, u.user_id, o.amount
  FROM dwd_order o
  JOIN dim_user u ON o.user_id = u.user_id
  WHERE o.dt BETWEEN DATE '2026-09-01' AND DATE '2026-09-30'
    AND o.status = 'paid'
),
-- ② 渠道级汇总，取 Top 2
top_channel AS (
  SELECT channel
  FROM (
    SELECT channel, SUM(amount) AS amt,
           ROW_NUMBER() OVER (ORDER BY SUM(amount) DESC) AS rn
    FROM base GROUP BY channel
  ) t
  WHERE rn <= 2
),
-- ③ 用户级汇总（只保留 Top 2 渠道）
user_amt AS (
  SELECT b.channel, b.user_id, SUM(b.amount) AS amt
  FROM base b
  JOIN top_channel tc ON b.channel = tc.channel
  GROUP BY b.channel, b.user_id
)
-- ④ 每个渠道取金额第一的用户
SELECT channel, user_id, amt
FROM (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY channel ORDER BY amt DESC) AS rn
  FROM user_amt
) t
WHERE rn = 1;
```

**CTE 的四个好处**：
1. **可读**：每个 CTE 一个名字，就是一句注释
2. **可复用**：`base` 被用了两次，不用写两遍
3. **可调试**：想验证中间结果，把最后的 SELECT 换成 `SELECT * FROM base LIMIT 10` 就行
4. **可 review**：同事能逐段确认你的口径

### 1.6.3 CTE 的两个注意点

**① CTE 不一定被物化（可能被重复计算）**

Trino/Spark 里 CTE 默认是「逻辑别名」，被引用两次可能就执行两次。如果这个 CTE 很重（比如扫了几十亿行），要显式落地：

```sql
-- Spark
CACHE TABLE base AS SELECT ...;

-- 通用做法：写成临时表
CREATE TEMPORARY TABLE base AS SELECT ...;

-- Postgres 11+ 可以显式指定
WITH base AS MATERIALIZED (SELECT ...)
```

**② CTE 层数别超过 5-6 层**

超过就说明这个逻辑应该沉淀成中间表（DWS 层），而不是每次在查询里现算。

---

## 1.7 窗口函数 ⭐️ 分析师日常高频

### 1.7.1 窗口函数 vs GROUP BY

一句话：**GROUP BY 会把多行压成一行；窗口函数保留所有行，额外算一列。**

```sql
-- GROUP BY：4 行 → 3 行
SELECT user_id, SUM(amount) FROM dwd_order GROUP BY user_id;

-- 窗口函数：4 行 → 4 行，每行都带上「这个用户的总额」
SELECT order_id, user_id, amount,
       SUM(amount) OVER (PARTITION BY user_id) AS user_total
FROM dwd_order;
```
| order_id | user_id | amount | user_total |
|----------|---------|--------|------------|
| O1 | 1 | 200 | 350 |
| O2 | 1 | 150 | 350 |
| O3 | 2 | 500 | 500 |
| O4 | 3 | 200 | 200 |

这就能直接算「这笔订单占该用户总消费的比例」——用 GROUP BY 要 JOIN 回去，用窗口函数一行搞定。

### 1.7.2 语法骨架

```sql
函数名(参数) OVER (
  PARTITION BY 分组字段      -- 可选：按什么分组（类比 GROUP BY，但不压缩行）
  ORDER BY   排序字段        -- 可选：组内怎么排序
  ROWS/RANGE BETWEEN ... AND ...   -- 可选：窗口框架，算到哪几行
)
```

### 1.7.3 `ROW_NUMBER / RANK / DENSE_RANK`

三者的区别只在**遇到并列时怎么办**。假设三笔订单金额是 200、200、150：

| amount | ROW_NUMBER | RANK | DENSE_RANK |
|--------|------------|------|------------|
| 200 | 1 | 1 | 1 |
| 200 | 2 | **1** | **1** |
| 150 | 3 | **3** | **2** |

- `ROW_NUMBER`：强行给唯一序号，并列时顺序随机
- `RANK`：并列同名次，后面**跳号**（1,1,3）
- `DENSE_RANK`：并列同名次，后面**不跳号**（1,1,2）

**用途 A：每组 Top N**

```sql
-- 每个用户金额最高的 1 笔订单
SELECT user_id, order_id, amount
FROM (
  SELECT user_id, order_id, amount,
         ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY amount DESC) AS rn
  FROM dwd_order
) t
WHERE rn = 1;
```

> **注意**：窗口函数不能直接写在 WHERE 里（WHERE 在 SELECT 之前执行），必须套一层子查询/CTE 再过滤。这是最常见的报错。

**用途 B：去重取最新一条（极高频）**

场景：上游任务重跑，同一个 order_id 写了多条，要取最新的那条。

```sql
SELECT *
FROM (
  SELECT *,
         ROW_NUMBER() OVER (
           PARTITION BY order_id           -- 按业务主键分组
           ORDER BY update_time DESC       -- 最新的排第一
         ) AS rn
  FROM dwd_order_raw
) t
WHERE rn = 1;
```

> **为什么不用 `DISTINCT`**：DISTINCT 要求整行完全一样才去重，而重复记录往往只是 update_time 不同。`ROW_NUMBER` 能按你指定的主键去重并保留你想要的那一条。

**用途 C：排名带并列**

```sql
-- 渠道 GMV 排名（并列算同名次）
SELECT channel, gmv,
       DENSE_RANK() OVER (ORDER BY gmv DESC) AS rk
FROM (SELECT u.channel, SUM(o.amount) AS gmv
      FROM dwd_order o JOIN dim_user u USING(user_id)
      GROUP BY u.channel) t;
```

### 1.7.4 `LAG / LEAD`：看前一行 / 后一行

```sql
LAG(字段, 偏移量, 默认值)  OVER (PARTITION BY ... ORDER BY ...)
LEAD(字段, 偏移量, 默认值) OVER (PARTITION BY ... ORDER BY ...)
```

**用途 A：算环比 / 日环比**

```sql
SELECT
  dt,
  gmv,
  LAG(gmv, 1) OVER (ORDER BY dt)                     AS prev_gmv,
  gmv - LAG(gmv, 1) OVER (ORDER BY dt)               AS diff,
  -- 环比增长率，注意分母为 0 和 NULL 的保护
  CASE WHEN LAG(gmv,1) OVER (ORDER BY dt) > 0
       THEN 1.0 * gmv / LAG(gmv,1) OVER (ORDER BY dt) - 1
  END AS wow_rate
FROM (SELECT dt, SUM(amount) AS gmv FROM dwd_order GROUP BY dt) t
ORDER BY dt;
```

> **陷阱**：`LAG(gmv,1) OVER (ORDER BY dt)` 取的是「结果集里的上一行」，**不是「日历上的前一天」**。如果某天没有订单，那天在结果集里根本不存在，LAG 会跳过它去拿更早的一天，算出来的「环比」实际是跨了两天。
> **解法**：先用 1.2.4 节的日期骨架把缺失日期补齐（补 0），再做 LAG。

**用途 B：算相邻事件的时间差**

```sql
-- 每个用户相邻两次事件的间隔（秒）
SELECT
  user_id, event_name, event_time,
  LAG(event_time) OVER (PARTITION BY user_id ORDER BY event_time) AS prev_time,
  date_diff('second',
            LAG(event_time) OVER (PARTITION BY user_id ORDER BY event_time),
            event_time) AS gap_sec
FROM dwd_event;
```
| user_id | event_name | event_time | prev_time | gap_sec |
|---------|------------|------------|-----------|---------|
| 1 | view | 09:58:00 | NULL | NULL |
| 1 | add_cart | 09:59:00 | 09:58:00 | 60 |
| 1 | pay | 10:00:00 | 09:59:00 | 60 |

**用途 C：会话切分（session 化）**

「间隔超过 30 分钟算一次新会话」——这是埋点分析的经典需求：

```sql
WITH gaps AS (
  SELECT user_id, event_time,
         date_diff('minute',
           LAG(event_time) OVER (PARTITION BY user_id ORDER BY event_time),
           event_time) AS gap_min
  FROM dwd_event
),
flags AS (
  SELECT *,
         -- 间隔 > 30 分钟 或 是第一条（gap 为 NULL）→ 标记为新会话起点
         CASE WHEN gap_min IS NULL OR gap_min > 30 THEN 1 ELSE 0 END AS is_new_session
  FROM gaps
)
SELECT *,
       -- 累加标记，得到会话序号
       SUM(is_new_session) OVER (PARTITION BY user_id ORDER BY event_time) AS session_seq
FROM flags;
```

这个「标记 + 累加」的模式非常通用，值得背下来。

### 1.7.5 `SUM / AVG OVER`：累计值与移动平均

关键在 **窗口框架（frame）**：

```sql
ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW   -- 从第一行到当前行 → 累计
ROWS BETWEEN 6 PRECEDING       AND CURRENT ROW     -- 当前行往前 7 行 → 7 日移动
ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING  -- 整个分区 → 总计
```

**用途 A：累计值（MTD / 累计 GMV）**

```sql
SELECT dt, gmv,
       SUM(gmv) OVER (ORDER BY dt
                      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cum_gmv
FROM (SELECT dt, SUM(amount) AS gmv FROM dwd_order GROUP BY dt) t;
```
| dt | gmv | cum_gmv |
|----|-----|---------|
| 2026-09-01 | 200 | 200 |
| 2026-09-03 | 650 | 850 |
| 2026-09-05 | 200 | 1050 |

> 简写：`SUM(gmv) OVER (ORDER BY dt)` 在有 ORDER BY 时默认框架就是 `RANGE UNBOUNDED PRECEDING AND CURRENT ROW`，等价于累计。**但建议显式写出框架**，避免引擎默认值差异。

**用途 B：7 日移动平均（平滑掉周内波动，看真实趋势）**

```sql
SELECT dt, gmv,
       AVG(gmv) OVER (ORDER BY dt ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS ma7
FROM daily_gmv;
```

> 业务数据有强烈的「周内效应」（周末和工作日差异大），直接看日线会误判趋势。**汇报日级趋势时，同时画 MA7 是基本素养。**

**用途 C：算占比（不用 JOIN 回去）**

```sql
SELECT
  channel, gmv,
  1.0 * gmv / SUM(gmv) OVER () AS pct_of_total   -- OVER() 空括号 = 全体
FROM channel_gmv;
```

### 1.7.6 `FIRST_VALUE / LAST_VALUE` 与 frame 的大坑

**用途：取每个用户的首单 / 末单信息**

```sql
SELECT DISTINCT
  user_id,
  FIRST_VALUE(order_id) OVER (PARTITION BY user_id ORDER BY pay_time) AS first_order,
  FIRST_VALUE(amount)   OVER (PARTITION BY user_id ORDER BY pay_time) AS first_amount
FROM dwd_order;
```

**⚠️ `LAST_VALUE` 的默认行为是错的（几乎所有人第一次都会踩）**

```sql
-- ✗ 你以为拿到的是「该用户最后一单」，实际拿到的是「当前行自己」
LAST_VALUE(order_id) OVER (PARTITION BY user_id ORDER BY pay_time)
```

原因：有 ORDER BY 时，默认框架是「从分区开头到**当前行**」，所以「最后一行」就是当前行。

**三种正确写法**：

```sql
-- ✓ 写法一：显式指定框架到分区末尾
LAST_VALUE(order_id) OVER (
  PARTITION BY user_id ORDER BY pay_time
  ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
)

-- ✓ 写法二（推荐）：倒序 + FIRST_VALUE，不容易写错
FIRST_VALUE(order_id) OVER (PARTITION BY user_id ORDER BY pay_time DESC)

-- ✓ 写法三：ROW_NUMBER 取 rn=1
```

> **建议直接放弃 `LAST_VALUE`，统一用「倒序 + FIRST_VALUE」或「ROW_NUMBER」**。少一个出错机会。

### 1.7.7 一个综合实战：首单复购分析

```sql
WITH order_seq AS (
  SELECT
    user_id, order_id, pay_time, amount,
    ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY pay_time)          AS order_no,
    LEAD(pay_time) OVER (PARTITION BY user_id ORDER BY pay_time)        AS next_pay_time,
    COUNT(*)      OVER (PARTITION BY user_id)                            AS total_orders
  FROM dwd_order
  WHERE status = 'paid'
)
SELECT
  user_id,
  MAX(CASE WHEN order_no = 1 THEN pay_time END)  AS first_pay_time,
  MAX(CASE WHEN order_no = 1 THEN amount   END)  AS first_amount,
  MAX(total_orders)                               AS order_cnt,
  MAX(total_orders) > 1                           AS is_repurchase,
  -- 首单到次单的间隔天数
  MAX(CASE WHEN order_no = 1
           THEN date_diff('day', pay_time, next_pay_time) END) AS days_to_2nd
FROM order_seq
GROUP BY user_id;
```

一条 SQL 同时产出：首单时间、首单金额、总单数、是否复购、复购间隔。这就是窗口函数的威力。

### ⚠️ 本节自检

```sql
-- 想取「每个用户最后一次事件的名称」，为什么结果不对？
SELECT DISTINCT user_id,
  LAST_VALUE(event_name) OVER (PARTITION BY user_id ORDER BY event_time) AS last_event
FROM dwd_event;
```
<details><summary>答案</summary>

默认窗口框架是 `UNBOUNDED PRECEDING AND CURRENT ROW`，"最后一行" = 当前行，所以每行拿到的是它自己的 event_name，加上 DISTINCT 后会返回该用户的**所有**事件名，而不是一行。

修法：`FIRST_VALUE(event_name) OVER (PARTITION BY user_id ORDER BY event_time DESC)`
</details>

---

## 1.8 日期时间处理

时间是数据分析里出错率最高的字段类型，没有之一。

### 1.8.1 日期截断：按天/周/月聚合

```sql
-- Trino / Presto
date_trunc('day',   ts)     -- 2026-09-03 22:30:00 → 2026-09-03 00:00:00
date_trunc('week',  ts)     -- 截到周一
date_trunc('month', ts)     -- 截到当月 1 号
date(ts)                    -- 直接转成 DATE 类型

-- Hive / Spark SQL
to_date(ts)                              -- 转日期
trunc(ts, 'MM')                          -- 截到月
date_format(ts, 'yyyy-MM')               -- 转字符串（注意结果是 string，不能做日期运算）

-- MySQL
DATE(ts)
DATE_FORMAT(ts, '%Y-%m-01')
```

**按周聚合时务必确认「周从周几开始」**。Trino 的 `date_trunc('week')` 从周一开始，有些引擎从周日开始，差一天会让所有周环比对不上。

### 1.8.2 时区：最隐蔽的错误来源

**典型灾难场景**：分区字段 `dt` 是**北京时间的日期**，而时间戳字段 `event_time` 存的是 **UTC**。

```sql
-- ✗ 想查 9 月 3 日（北京时间）全天的数据
SELECT * FROM dwd_event
WHERE dt = DATE '2026-09-03'
  AND event_time >= TIMESTAMP '2026-09-03 00:00:00'
  AND event_time <  TIMESTAMP '2026-09-04 00:00:00';
```
`dt` 按北京时间切，`event_time` 是 UTC，两个条件取交集后，你实际只拿到了北京时间 **08:00 到 24:00** 的数据，**丢了早上 8 小时**。而且结果看起来很正常，数字只是「偏低了一点」。

**正确做法**：

```sql
-- ✓ 把时间戳先转到目标时区再比较
SELECT *
FROM dwd_event
WHERE dt BETWEEN DATE '2026-09-02' AND DATE '2026-09-04'    -- 分区多取一天做缓冲
  AND event_time AT TIME ZONE 'Asia/Shanghai' >= TIMESTAMP '2026-09-03 00:00:00'
  AND event_time AT TIME ZONE 'Asia/Shanghai' <  TIMESTAMP '2026-09-04 00:00:00';
```

```sql
-- Trino 其他时区函数
from_unixtime(ts_sec)                                -- 秒级时间戳 → timestamp
from_unixtime(ts_ms / 1000)                          -- 毫秒级要先除 1000
at_timezone(ts, 'Asia/Shanghai')
to_unixtime(ts)

-- Hive / Spark
from_utc_timestamp(ts, 'Asia/Shanghai')
to_utc_timestamp(ts, 'Asia/Shanghai')
```

**三条纪律**：
1. 拿到一张新表，**第一件事是确认每个时间字段的时区和单位**（秒还是毫秒）
2. 分区字段的时区和时间戳字段的时区**经常不一致**，必须分别确认
3. 跨时区口径要写进指标文档；「日活」在不同时区下是不同的数

### 1.8.3 日期序列生成与补零

**为什么要补零**：没有数据的日期在结果里会直接消失，折线图会把两个不连续的点连起来，看不出「那天挂了」。

```sql
-- Trino：生成日期序列
SELECT d
FROM UNNEST(SEQUENCE(DATE '2026-09-01', DATE '2026-09-07', INTERVAL '1' DAY)) AS t(d);
```

```sql
-- ✓ 完整的补零模板
WITH date_spine AS (
  SELECT d FROM UNNEST(
    SEQUENCE(DATE '2026-09-01', DATE '2026-09-07', INTERVAL '1' DAY)
  ) AS t(d)
),
daily AS (
  SELECT dt, SUM(amount) AS gmv, COUNT(*) AS cnt
  FROM dwd_order
  WHERE dt BETWEEN DATE '2026-09-01' AND DATE '2026-09-07'
  GROUP BY dt
)
SELECT
  s.d                          AS dt,
  COALESCE(dl.gmv, 0)          AS gmv,     -- ← 补 0，不是 NULL
  COALESCE(dl.cnt, 0)          AS cnt
FROM date_spine s
LEFT JOIN daily dl ON s.d = dl.dt
ORDER BY s.d;
```
结果（注意 09-02、09-04、09-06、09-07 被补出来了）：
| dt | gmv | cnt |
|----|-----|-----|
| 2026-09-01 | 200 | 1 |
| 2026-09-02 | 0 | 0 |
| 2026-09-03 | 650 | 2 |
| 2026-09-04 | 0 | 0 |
| 2026-09-05 | 200 | 1 |
| 2026-09-06 | 0 | 0 |
| 2026-09-07 | 0 | 0 |

> 其他引擎的日期序列写法：
> - Spark：`explode(sequence(to_date('2026-09-01'), to_date('2026-09-07'), interval 1 day))`
> - Hive：常用 `posexplode(split(space(n), ' '))` 生成数字序列再做日期加法
> - 通用兜底：建一张 `dim_date` 日期维度表（强烈推荐，一劳永逸）

### 1.8.4 日期计算

```sql
-- Trino
date_diff('day',  d1, d2)        -- d2 - d1 的天数
date_add('day', 7, d)            -- 加 7 天
d - INTERVAL '7' DAY
current_date, current_timestamp

-- 常见需求：算次日留存
SELECT
  reg.dt AS reg_date,
  COUNT(DISTINCT reg.user_id) AS reg_uv,
  COUNT(DISTINCT CASE WHEN date_diff('day', reg.dt, act.dt) = 1
                      THEN act.user_id END) AS d1_retained
FROM reg_table reg
LEFT JOIN active_table act ON reg.user_id = act.user_id
GROUP BY reg.dt;
```

**跨天 / 跨月计算的坑**：
- 「上个月同期」不能简单减 30 天（月份长度不同）
- 「同比」要注意闰年、春节等节假日错位（2 月 CNY 在不同年份的公历日期不同，同比会失真）

---

## 1.9 NULL 的陷阱

NULL 不是「空字符串」也不是「0」，它是**「未知」**。所有反直觉的行为都源于这个定义。

### 1.9.1 `NULL` 参与的比较结果是 `NULL`，不是 `false`

```sql
SELECT
  NULL = NULL     AS a,   -- NULL（不是 true！）
  NULL != NULL    AS b,   -- NULL
  NULL = 1        AS c,   -- NULL
  NULL + 1        AS d,   -- NULL
  NULL || 'abc'   AS e;   -- NULL（Trino；某些引擎会返回 'abc'）
```

SQL 的 WHERE 只保留结果为 **true** 的行，NULL 和 false 一样被过滤掉。

```sql
-- ✗ 想找出 city 为空的用户，一行都查不到
SELECT * FROM dim_user WHERE city = NULL;

-- ✓ 必须用 IS NULL
SELECT * FROM dim_user WHERE city IS NULL;
```

**更隐蔽的版本**：

```sql
-- ✗ 想找「不是上海的用户」，user_4（city 为 NULL）不会出现
SELECT * FROM dim_user WHERE city != '上海';
-- 返回：user_2（北京）。user_4 被静默丢弃！

-- ✓ 明确表达意图
SELECT * FROM dim_user WHERE city != '上海' OR city IS NULL;
-- 或
SELECT * FROM dim_user WHERE COALESCE(city, '未知') != '上海';
-- 或 Trino 的空安全比较
SELECT * FROM dim_user WHERE city IS DISTINCT FROM '上海';
```

> **这是最常见的「数据凭空消失」原因**。任何时候写 `!=` / `<>` / `NOT LIKE`，都要先想一下：这一列有 NULL 吗？

### 1.9.2 聚合函数对 NULL 的处理

| 函数 | 对 NULL 的行为 | 空表时返回 |
|------|----------------|-----------|
| `COUNT(*)` | 计入 | 0 |
| `COUNT(col)` | **不计入** | 0 |
| `COUNT(DISTINCT col)` | **不计入** | 0 |
| `SUM(col)` | 忽略 | **NULL** |
| `AVG(col)` | 忽略（分母也减少） | **NULL** |
| `MAX/MIN(col)` | 忽略 | **NULL** |

见 1.4.4 节的详细说明。核心两条：
- **算用户数用 `COUNT(*)` 或 `COUNT(DISTINCT user_id)`，不要用业务字段**
- **`SUM` 在空结果集返回 NULL 不是 0**，用 `COALESCE(SUM(x), 0)` 包住

### 1.9.3 `NOT IN` 的致命陷阱

```sql
-- 假设 blacklist 表里有一行 user_id 是 NULL
SELECT * FROM dim_user
WHERE user_id NOT IN (SELECT user_id FROM blacklist);
-- 返回：0 行（不管 dim_user 有多少数据）
```

**为什么**：`user_id NOT IN (1, 2, NULL)` 展开成
`user_id != 1 AND user_id != 2 AND user_id != NULL`
最后一项永远是 NULL，整个 AND 链最好也只能是 NULL，永远不为 true。

**三种安全写法**：

```sql
-- ✓ 写法一：NOT EXISTS（推荐，NULL 安全）
SELECT * FROM dim_user u
WHERE NOT EXISTS (SELECT 1 FROM blacklist b WHERE b.user_id = u.user_id);

-- ✓ 写法二：LEFT JOIN + IS NULL
SELECT u.* FROM dim_user u
LEFT JOIN blacklist b ON u.user_id = b.user_id
WHERE b.user_id IS NULL;

-- ✓ 写法三：非要用 NOT IN，就先过滤掉 NULL
SELECT * FROM dim_user
WHERE user_id NOT IN (SELECT user_id FROM blacklist WHERE user_id IS NOT NULL);
```

> **直接把「不用 NOT IN」写进个人规范**，这个坑一年能救你好几次。

### 1.9.4 JOIN key 里的 NULL

```sql
-- NULL 永远匹配不上 NULL，含 NULL 的 key 会全部落空
FROM a JOIN b ON a.city = b.city     -- a.city 为 NULL 的行全丢
```

如果业务上 NULL 应该匹配，需要显式处理：

```sql
ON COALESCE(a.city, '__NULL__') = COALESCE(b.city, '__NULL__')
-- 或 Trino/Postgres
ON a.city IS NOT DISTINCT FROM b.city
```

> ⚠️ 但要小心：这么做会让**所有 NULL 互相匹配**，如果 NULL 很多会造成严重膨胀（回到 1.3 节的问题）。多数情况下更好的做法是先把 NULL 过滤掉或填充成有意义的值。

### 1.9.5 处理 NULL 的工具函数

```sql
COALESCE(a, b, c)       -- 返回第一个非 NULL 的值
NULLIF(a, b)            -- a = b 时返回 NULL，常用于防止除零：x / NULLIF(y, 0)
IF(cond, a, b)          -- Hive/Spark/Trino
CASE WHEN x IS NULL THEN ... END

-- 防除零的标准写法
SELECT 1.0 * paid_cnt / NULLIF(total_cnt, 0) AS rate FROM ...
-- total_cnt 为 0 时返回 NULL 而不是报错
```

### 1.9.6 空字符串 ≠ NULL

```sql
SELECT
  '' = NULL        AS a,   -- NULL
  '' IS NULL       AS b,   -- false
  length('')       AS c;   -- 0
```

导入数据时，有的链路把缺失值写成 `''`，有的写成 `'null'` 字符串，有的写成真 NULL。**做数据质量检查时三种都要查**：

```sql
SELECT
  SUM(CASE WHEN city IS NULL       THEN 1 ELSE 0 END) AS null_cnt,
  SUM(CASE WHEN city = ''          THEN 1 ELSE 0 END) AS empty_cnt,
  SUM(CASE WHEN lower(city) IN ('null','none','nan','\N') THEN 1 ELSE 0 END) AS fake_null_cnt
FROM dim_user;
```

---

## 1.10 查询优化

分析师不需要成为性能专家，但要能做到：**不写出拖垮集群的 SQL，且知道慢在哪。**

### 1.10.1 分区裁剪（收益最大的一条）

分区表按 `dt` 物理分目录存储。`WHERE dt = '2026-09-01'` 能让引擎只读一个目录，而不是全表。

```sql
-- ✗ 灾难：不带分区条件，全表扫描
SELECT COUNT(*) FROM dwd_event WHERE user_id = 1;

-- ✓ 一定带上分区条件
SELECT COUNT(*) FROM dwd_event
WHERE dt BETWEEN DATE '2026-09-01' AND DATE '2026-09-07'
  AND user_id = 1;
```

**让分区裁剪失效的三种写法（重点记）**：

```sql
-- ✗ 对分区列做函数：引擎无法反推该读哪些分区
WHERE date_format(dt, 'yyyy-MM') = '2026-09'
WHERE substr(dt, 1, 7) = '2026-09'
WHERE year(dt) = 2026 AND month(dt) = 9

-- ✓ 改成对分区列的直接范围比较
WHERE dt >= DATE '2026-09-01' AND dt < DATE '2026-10-01'
```

```sql
-- ✗ 分区列参与 OR 且另一侧是非分区列
WHERE dt = DATE '2026-09-01' OR user_id = 1

-- ✗ 分区列类型隐式转换（分区是 string，你传 date）
WHERE dt = DATE '2026-09-01'   -- 若 dt 是 string 类型，可能全表扫
-- ✓ 类型对齐
WHERE dt = '2026-09-01'
```

**养成习惯**：写完 SQL 先自问「这条会扫多少分区」。不确定就用 `EXPLAIN` 看，或先跑一个 `SELECT COUNT(*)` 试探。

### 1.10.2 不要 `SELECT *`

在列式存储（Parquet / ORC）里，只读你要的列，IO 是按列计费的。

```sql
-- ✗ 一张 200 列的宽表，SELECT * 要读全部 200 列
SELECT * FROM dwd_event WHERE dt = DATE '2026-09-01';

-- ✓ 只读 3 列，IO 可能只有 1/50
SELECT user_id, event_name, event_time FROM dwd_event WHERE dt = DATE '2026-09-01';
```

例外：探索新表时 `SELECT * ... LIMIT 10` 看结构是完全合理的。

### 1.10.3 JOIN 的优化

**① 大表 JOIN 小表 → 广播（broadcast / map join）**

小表被复制到每个节点，避免大表 shuffle。多数引擎会自动判断，但你可以帮它：

```sql
-- 确保小表在 JOIN 右侧（部分引擎对顺序敏感）
FROM big_fact_table f
JOIN small_dim_table d ON f.key = d.key

-- Hive/Spark 可以给 hint
SELECT /*+ BROADCAST(d) */ ...
SELECT /*+ MAPJOIN(d) */ ...
```

**② 先过滤再 JOIN**

```sql
-- ✗ 先 JOIN 十亿行再过滤
FROM big_a a JOIN big_b b ON a.id = b.id
WHERE a.dt = DATE '2026-09-01' AND b.status = 'paid'

-- ✓ 各自先过滤，JOIN 的数据量小几个数量级
FROM (SELECT * FROM big_a WHERE dt = DATE '2026-09-01') a
JOIN (SELECT * FROM big_b WHERE dt = DATE '2026-09-01' AND status='paid') b
  ON a.id = b.id
```
> 现代优化器大多会自动做谓词下推，但涉及 OUTER JOIN、UDF、复杂表达式时经常推不动，手动写更保险。

**③ 先聚合再 JOIN**

见 1.3.3 节。既能防膨胀，又能大幅减少 JOIN 的数据量，一举两得。

### 1.10.4 数据倾斜：识别与处理

**症状**：任务 99% 的 task 几分钟跑完，剩下 1-2 个跑几小时，或者直接 OOM。

**原因**：某个 JOIN key 或 GROUP BY key 的值极度集中（比如 `user_id = 0`、`device_id = 'unknown'`、爬虫 IP），所有数据被哈希到同一个节点。

**第一步：确认倾斜**

```sql
-- 看 key 的分布，前几名占比是否异常
SELECT user_id, COUNT(*) AS cnt
FROM dwd_event
WHERE dt = DATE '2026-09-01'
GROUP BY user_id
ORDER BY cnt DESC
LIMIT 20;
```
如果 Top1 的量是中位数的几千倍，就是倾斜。

**第二步：处理**

```sql
-- 方案 A：如果热点 key 是脏数据（user_id = 0 / NULL / 'unknown'），直接过滤
WHERE user_id IS NOT NULL AND user_id NOT IN (0, -1)

-- 方案 B：加盐打散（热点 key 拆成 N 份分散计算，再汇总）
--   左表：给 key 加随机后缀
SELECT concat(cast(user_id AS varchar), '_', cast(cast(rand()*10 AS int) AS varchar)) AS salted_key, ...
--   右表：把每行复制 10 份，分别带 0-9 后缀
SELECT concat(cast(user_id AS varchar), '_', cast(s AS varchar)) AS salted_key, ...
FROM dim_user CROSS JOIN UNNEST(SEQUENCE(0, 9)) AS t(s)

-- 方案 C：热点 key 单独处理，最后 UNION ALL
--   热点走 broadcast join，非热点走正常 shuffle join
```

方案 B/C 写起来麻烦，**日常分析优先用方案 A**（脏数据过滤）；只有在生产 ETL 里才值得上加盐。

### 1.10.5 其他常见提速手段

```sql
-- ① UNION ALL 代替 UNION（UNION 会去重，等于额外一次 shuffle）
SELECT ... UNION ALL SELECT ...

-- ② 探索阶段先加 LIMIT / 抽样
WHERE dt = DATE '2026-09-01' AND rand() < 0.01     -- 1% 抽样
TABLESAMPLE BERNOULLI (1)                          -- Trino 语法

-- ③ 重复用到的中间结果落成临时表，而不是每次重算
CREATE TABLE tmp.my_base AS SELECT ...;

-- ④ ORDER BY 只在最后一步、数据量小的时候做
```

### 1.10.6 学会看 `EXPLAIN`

```sql
EXPLAIN SELECT ... ;               -- 看逻辑计划
EXPLAIN ANALYZE SELECT ... ;       -- 真跑一遍，看每个算子的实际行数和耗时（Trino/Postgres）
```

重点看三样：
1. **扫描的分区数 / 行数**：远超预期 → 分区裁剪没生效
2. **JOIN 类型**：是 broadcast 还是 partitioned（repartition）？大表被 broadcast 会 OOM
3. **各 stage 的行数变化**：某一步行数暴涨 → 膨胀（回 1.3 节）

---

## 1.11 半结构化数据：JSON 与数组

埋点表的 `props` 字段几乎都是 JSON，这块不会就读不了埋点。

### 1.11.1 JSON 解析

```sql
-- Trino / Presto
json_extract_scalar(props, '$.page')              -- 取标量，返回 varchar
json_extract(props, '$.tags')                     -- 取 JSON 对象/数组，返回 json
json_extract_scalar(props, '$.user.profile.age')  -- 嵌套路径
CAST(json_extract_scalar(props, '$.price') AS DOUBLE)  -- 记得转类型

-- Hive / Spark SQL
get_json_object(props, '$.page')
json_tuple(props, 'page', 'sku')                  -- 一次取多个字段，比多次 get_json_object 快

-- Spark 3 结构化解析（性能最好）
from_json(props, 'page STRING, sku STRING, price DOUBLE')
```

**实例**：统计各页面的浏览量

```sql
SELECT
  json_extract_scalar(props, '$.page') AS page,
  COUNT(*)                             AS pv,
  COUNT(DISTINCT user_id)              AS uv
FROM dwd_event
WHERE dt = DATE '2026-09-01'
  AND event_name = 'view'
GROUP BY 1
ORDER BY pv DESC;
```

**三个实践要点**：

1. **`json_extract_scalar` 的返回值永远是字符串**，要比大小/求和必须显式 CAST：
   ```sql
   -- ✗ 字符串比较：'9' > '100' 是 true
   WHERE json_extract_scalar(props, '$.price') > '100'
   -- ✓
   WHERE CAST(json_extract_scalar(props, '$.price') AS DOUBLE) > 100
   ```
2. **字段不存在时返回 NULL**，不会报错——这意味着埋点改名后你的报表会静默变成 0，必须配监控。
3. **JSON 解析很贵**。同一个字段在 SELECT 和 WHERE 里都用到时，先在子查询里解析一次：
   ```sql
   SELECT page, COUNT(*) FROM (
     SELECT json_extract_scalar(props, '$.page') AS page, user_id
     FROM dwd_event WHERE dt = DATE '2026-09-01'
   ) t WHERE page IS NOT NULL GROUP BY page;
   ```
4. **脏 JSON 会让整个查询失败**。Trino 里可以用 `try(...)` 兜底：
   ```sql
   try(CAST(json_extract_scalar(props, '$.price') AS DOUBLE))
   ```

### 1.11.2 数组展开

```sql
-- Trino：UNNEST
SELECT e.user_id, t.tag
FROM dwd_event e
CROSS JOIN UNNEST(
  CAST(json_extract(e.props, '$.tags') AS ARRAY(VARCHAR))
) AS t(tag)
WHERE e.dt = DATE '2026-09-01';
```

user_1 的 `tags = ["a","b"]`，展开后：
| user_id | tag |
|---------|-----|
| 1 | a |
| 1 | b |
| 2 | b |

```sql
-- Hive / Spark：LATERAL VIEW EXPLODE
SELECT e.user_id, t.tag
FROM dwd_event e
LATERAL VIEW EXPLODE(split(get_json_object(e.props,'$.tags'), ',')) t AS tag;

-- 带序号
LATERAL VIEW POSEXPLODE(arr) t AS pos, val
```

**⚠️ 两个必须知道的点**：

**① 展开就是膨胀，回到 1.3 节的问题**

一行变多行之后，原表的度量字段不能再直接 SUM。

```sql
-- ✗ 一个事件有 3 个 tag，展开后这个事件的 amount 会被算 3 遍
SELECT tag, SUM(e.amount) FROM ... CROSS JOIN UNNEST(...) GROUP BY tag;

-- ✓ 计数类指标没问题（本来就是想按 tag 计数）
SELECT tag, COUNT(*) AS tag_pv, COUNT(DISTINCT e.user_id) AS tag_uv
FROM ... GROUP BY tag;
```

**② 空数组会让整行消失**

`CROSS JOIN UNNEST` 对空数组 `[]` 或 NULL 的行**不产生任何输出**，整行被丢掉（user_3 的 `tags` 是 `[]`，它不会出现在上面的结果里）。

```sql
-- ✓ 要保留空数组的行，用 LEFT JOIN UNNEST
FROM dwd_event e
LEFT JOIN UNNEST(CAST(json_extract(e.props,'$.tags') AS ARRAY(VARCHAR))) AS t(tag) ON TRUE
-- Hive/Spark 对应 OUTER LATERAL VIEW
LATERAL VIEW OUTER EXPLODE(arr) t AS tag
```

> 这个坑很隐蔽：你算「带标签的事件占比」时，分母用了展开后的表，空标签的事件已经没了，分母偏小，占比虚高。

### 1.11.3 MAP 类型

```sql
-- Trino
props_map['page']                                   -- 取值
element_at(props_map, 'page')                       -- 安全取值，key 不存在返回 NULL
map_keys(props_map), map_values(props_map)
SELECT k, v FROM t CROSS JOIN UNNEST(props_map) AS m(k, v)   -- 展开成 KV 两列
```

### 1.11.4 数组常用函数（Trino）

```sql
cardinality(arr)                   -- 长度
contains(arr, 'a')                 -- 是否包含
array_agg(x)                       -- 聚合成数组（GROUP BY 里把多行收成一行）
array_distinct(arr)
array_intersect(a, b)              -- 交集，做留存/共现分析很好用
filter(arr, x -> x > 10)           -- lambda 过滤
transform(arr, x -> x * 2)         -- lambda 映射
reduce(arr, 0, (s, x) -> s + x, s -> s)  -- 归约求和
```

**实用例子：把每个用户的事件路径拼成一条链**

```sql
SELECT
  user_id,
  array_join(array_agg(event_name ORDER BY event_time), ' -> ') AS path
FROM dwd_event
WHERE dt = DATE '2026-09-01'
GROUP BY user_id;
```
| user_id | path |
|---------|------|
| 1 | view -> add_cart -> pay |

这就是**路径分析**的基础，能直接拿去统计最常见的转化路径。

---

## 1.12 章末综合练习

用本章的示例表（`dim_user` / `dwd_order` / `dwd_order_item` / `dwd_event`）完成，答案思路在折叠里。

**Q1（基础 + JOIN）** 统计每个渠道的：用户数、下单用户数、下单率。要求包含 0 单的渠道和用户。

<details><summary>思路</summary>

```sql
SELECT
  u.channel,
  COUNT(DISTINCT u.user_id) AS users,
  COUNT(DISTINCT o.user_id) AS order_users,
  1.0 * COUNT(DISTINCT o.user_id) / NULLIF(COUNT(DISTINCT u.user_id), 0) AS order_rate
FROM dim_user u
LEFT JOIN dwd_order o ON u.user_id = o.user_id AND o.status = 'paid'
GROUP BY u.channel;
```
关键点：① `LEFT JOIN` 保住 0 单用户；② `status` 条件放 **ON** 不放 WHERE；③ 分母用 `NULLIF` 防除零；④ `COUNT(DISTINCT o.user_id)` 里 NULL 不计，天然得到「有单用户数」。
</details>

**Q2（粒度）** 统计每个 SKU 的销售额和「该 SKU 的订单里，订单平均总额」。

<details><summary>思路</summary>

```sql
WITH order_sku AS (
  SELECT DISTINCT i.sku, o.order_id, o.amount   -- 先去重到「订单-SKU」粒度
  FROM dwd_order o JOIN dwd_order_item i ON o.order_id = i.order_id
),
sku_amt AS (
  SELECT sku, SUM(item_amount) AS sku_sales FROM dwd_order_item GROUP BY sku
)
SELECT s.sku, s.sku_sales, AVG(os.amount) AS avg_order_amount
FROM sku_amt s JOIN order_sku os ON s.sku = os.sku
GROUP BY s.sku, s.sku_sales;
```
关键点：SKU 销售额用**明细粒度**字段 `item_amount`；订单总额是**订单粒度**字段，必须先按 `(sku, order_id)` 去重才能求平均。
</details>

**Q3（窗口）** 找出每个用户的首单和末单金额，以及首末单间隔天数。

<details><summary>思路</summary>

```sql
SELECT DISTINCT
  user_id,
  FIRST_VALUE(amount) OVER (PARTITION BY user_id ORDER BY pay_time)      AS first_amt,
  FIRST_VALUE(amount) OVER (PARTITION BY user_id ORDER BY pay_time DESC) AS last_amt,
  date_diff('day',
    MIN(pay_time) OVER (PARTITION BY user_id),
    MAX(pay_time) OVER (PARTITION BY user_id))                            AS span_days
FROM dwd_order WHERE status = 'paid';
```
关键点：末单用「倒序 + FIRST_VALUE」，不用 `LAST_VALUE`。
</details>

**Q4（NULL）** 统计「非上海用户」的订单数。为什么直接 `WHERE city != '上海'` 会漏数据？

<details><summary>思路</summary>

user_4 的 city 是 NULL，`NULL != '上海'` 结果是 NULL 不是 true，会被 WHERE 过滤掉。
应写成 `WHERE COALESCE(city, '未知') != '上海'` 或 `WHERE city IS DISTINCT FROM '上海'`。
</details>

**Q5（日期 + 补零）** 输出 2026-09-01 至 2026-09-07 每天的 GMV 和 3 日移动平均，无数据的日期补 0。

<details><summary>思路</summary>

先用 `SEQUENCE` 生成日期骨架 `LEFT JOIN` 日聚合并 `COALESCE(gmv, 0)`，**然后**再套
`AVG(gmv) OVER (ORDER BY dt ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)`。
顺序很重要：必须先补零再算移动平均，否则窗口会跨过缺失日期，算出来的是「最近有数据的 3 天」而不是「最近 3 个自然日」。
</details>

**Q6（JSON + 数组）** 统计每个 tag 的曝光 UV，并说明为什么不能直接用展开后的表算「总曝光 UV」。

<details><summary>思路</summary>

```sql
SELECT t.tag, COUNT(DISTINCT e.user_id) AS uv
FROM dwd_event e
CROSS JOIN UNNEST(CAST(json_extract(e.props,'$.tags') AS ARRAY(VARCHAR))) AS t(tag)
WHERE e.dt BETWEEN DATE '2026-09-01' AND DATE '2026-09-05' AND e.event_name = 'view'
GROUP BY t.tag;
```
不能用展开表算总 UV 的原因有两个：① `tags` 为空数组的事件（user_3）被 `CROSS JOIN UNNEST` 丢弃了，分母偏小；② 即使 UV 有 DISTINCT 保护，PV 类指标会按 tag 个数翻倍。总量指标必须回原表算。
</details>

---

## 1.13 速查表

### 写每条 SQL 前的三问

1. **粒度**：我 JOIN 的两张表，各自一行代表什么？key 唯一吗？
2. **NULL**：我用到的字段有 NULL 吗？`!=` / `NOT IN` / 聚合会不会静默丢数据？
3. **分区**：这条会扫多少分区？分区条件写对了吗？

### 交付前的四项自检

```sql
-- ① 行数是否符合预期（JOIN 后是否膨胀）
SELECT COUNT(*) FROM your_result;

-- ② 总量是否对得上已知口径（和大盘报表交叉验证）
SELECT SUM(metric) FROM your_result;

-- ③ 有没有莫名的 NULL
SELECT COUNT(*) - COUNT(key_col) AS null_key FROM your_result;

-- ④ 时间范围是否完整（有没有缺失日期）
SELECT MIN(dt), MAX(dt), COUNT(DISTINCT dt) FROM your_result;
```

### 高频陷阱一览

| 陷阱 | 症状 | 正解 |
|------|------|------|
| LEFT JOIN 被 WHERE 打回 INNER | 「0 值」的行消失 | 右表条件写进 `ON` |
| 一对多 JOIN 膨胀 | 金额虚高 | 先聚合到同粒度再 JOIN |
| `SUM(DISTINCT)` 补救 | 金额变小但仍然错 | DISTINCT 不解决粒度问题 |
| `!=` 遇 NULL | 数据静默丢失 | `COALESCE` 或 `IS DISTINCT FROM` |
| `NOT IN` 遇 NULL | 返回 0 行 | 用 `NOT EXISTS` |
| 整数除法 | 比率永远是 0 | `1.0 *` 或 `CAST(... AS DOUBLE)` |
| `LAST_VALUE` 默认框架 | 拿到当前行 | 倒序 + `FIRST_VALUE` |
| `LAG` 跨过缺失日期 | 环比算成跨两天 | 先补齐日期骨架 |
| 分区列被函数包住 | 查询慢到超时 | 改成范围比较 |
| `CROSS JOIN UNNEST` 丢空数组 | 分母偏小、占比虚高 | `LEFT JOIN UNNEST ... ON TRUE` |
| 分区时区 ≠ 时间戳时区 | 每天少 8 小时数据 | 分别确认时区，分区多取一天缓冲 |
| `COUNT(业务字段)` 当用户数 | 少算了字段为空的用户 | `COUNT(DISTINCT user_id)` |

### 完成标准

能独立做到下面五件事，1.1 SQL 这一节就可以打勾了：

- [ ] 拿到一个业务问题，能自己找到表、确认粒度、写出正确的 SQL
- [ ] JOIN 之前会主动检查唯一性，JOIN 之后会主动校验行数
- [ ] 能用窗口函数解决「每组 Top N / 去重取最新 / 环比 / 累计 / 移动平均」
- [ ] 知道自己写的 SQL 会扫多少数据，慢了能说出慢在哪
- [ ] 别人的 SQL 结果和你对不上时，能定位到是口径差异还是代码 bug

---

> 下一节：1.2 Excel / 表格（待写）
> 下一章：[第二章 定位问题与验证方案](./chapter-02.md)
