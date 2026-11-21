# hero-sms 自动挑最便宜的号

## 一、文档我看了，两套接口并存

### 1.1 公开可编程接口（我们用这个）

```
https://hero-sms.com/stubs/handler_api.php?api_key=<KEY>&action=<ACTION>&...
```

实测响应规律：**成功是纯文本，错误是 JSON**

```
ACCESS_BALANCE:12.34
ACCESS_NUMBER:123456789:447912345678
STATUS_WAIT_CODE / STATUS_WAIT_RETRY:1234 / STATUS_OK:654321 / STATUS_CANCEL
{"title":"BAD_KEY","details":"Unauthorized"}                 http=401
```

错误码枚举（从站点前端 bundle 里原文提取，不是我编的）：

```
SERVICE_NOT_AVAILABLE, WRONG_MAX_PRICE, NO_NUMBERS, NO_BALANCE, BAD_SERVICE,
NO_YULA_MAIL, ERROR_SQL, ERROR_SQL25, WHATSAPP_NOT_AVAILABLE, NOT_AVAILABLE,
WRONG_EXCEPTION_PHONE, CHANNELS_LIMIT, SERVER_ERROR, BAD_BALANCE
```

### 1.2 站点自用的 REST 接口（需要登录态，公开 API 用不了）

```
POST /api/v1/activations            {owner:6, fixedPrice, ...}
GET  /left-menu/services/{service}/countries/{country}/offers
GET  /multiservice/countries/{country}/services
POST /stubs/handler_api_internal.php
GET  /api/v1/csrf-cookie            (Laravel session)
GET  /api/v1/webhooks, /api/v1/emails, /api/v1/free/countries
```

这些带 csrf-cookie，是网页控制台的内部后端；脚本走公开的 `handler_api.php` 就够了，
`/left-menu/.../offers` 里还有 `userPrice`（你的专属价），如果你有面板会话可以额外对一下。

## 二、自动挑最便宜的号：已实现

`core/sms_herosms.py` 新增：

| 能力 | 说明 |
|---|---|
| `flatten_prices()` | 把 `getPrices` 的 4 种返回形态（嵌套/数组/带 operator/混服务）摊平成统一结构并去重取最低价 |
| `rank_cheapest()` | 按 `单价升序 + 库存降序` 排序，支持 `allow` 国家白名单 / `min_count` / `max_price` |
| `price_table()` | 全量价格落盘 `data/sms_prices.json`，便于比价和复盘 |
| `cheapest()` | 取前 N 条最便宜 |
| `buy_cheapest()` | **按价格从低到高逐个下单**，跳过无库存/超预算/服务不可用，成功即停 |
| `resolve_country()` | 中文/ISO → hero-sms 国家 ID（别硬编码 187，各国编号不通用） |

### 2.1 解析实测（4 种形态都对）

```
A 标准嵌套 {"187":{"fb":{"cost":0.35,"count":120}}}   -> 对
B 数组形态 [{country,service,cost,count}]             -> 对
C 带 operator {"187":{"fb":{"any":{...},"mega":{...}}}} -> 取最低 0.22
D 混服务   {"187":{"fb":…,"wa":…}}                     -> 按 service 分离
```

排序验证：

```
rank fb, count>0 -> 国家 6  0.09 (库存44)   ← 最便宜
                    国家 187 0.35 (库存120)
限 allow=[187]   -> 只剩 187
限 max_price=0.2 -> 只剩 国家 6 的 0.09
```

### 2.2 国家名解析

```
美国 -> ('187','United States')      us -> 同上
英国 -> ('12','United Kingdom')      印尼 -> ('6','Indonesia')
```

## 三、怎么用

```bash
export HEROSMS_KEY=你的key

# 1) 先比价：列出最便宜的 15 个 fb 号源（下单前必看）
python3 core/sms_herosms.py --cheapest --service fb
python3 core/sms_herosms.py --cheapest --service fb --find-country 美国
python3 core/sms_herosms.py --cheapest --service fb --allow 187,12 --max-price 0.5

# 2) 自动买最便宜的（逐个试，成功即停；--budget 是累计花费上限）
python3 core/sms_herosms.py --buy-cheapest --service fb --find-country 美国 \
        --max-price 0.5 --tries 5 --budget 1.0

# 3) 拿验证码
python3 core/sms_herosms.py --code --id <订单号> --wait 300
```

`buy_cheapest` 的日志长这样：

```
余额 3.42
候选 8 个，从最便宜开始试：
  [1] 国家 6 价 0.09 库存 44 -> 下单
  ✗ 6 NO_NUMBERS
  [2] 国家 12 价 0.16 库存 8 -> 下单
  ✅ 拿到 +4479xxxxxxx（12，0.16）
```

## 四、挑号策略（重要，比价格更影响成败）

**别只按价格挑。** 对我们这套注册链路，价格便宜的国家往往是"注册必挂"的号段：

| 优先级 | 国家/号段 | 原因 |
|---|---|---|
| 必选 | **出口 IP 所在国**（目前 US） | 号段国家 ≠ IP 国家 → 直接 checkpoint |
| 次选 | 同区域（CA/GB） | 与 US 住宅 IP 地理接近，风险低 |
| 慎用 | ID / PH / VN / BD 这类低价段 | 价格最低但被 Meta 打得最狠 |

所以我默认建议：

```bash
# 先只看目标国，避免"图便宜买一堆废号"
python3 core/sms_herosms.py --cheapest --service fb --find-country 美国
# 拿到该国的真实 ID 后再限死 allow，再 buy-cheapest
```

## 五、下一步

拿到 key 后跑第 1 条，把输出贴给我，我帮你判断：
1. `fb` 的服务名对不对（有的站叫 `facebook`）
2. US 号源真实单价/库存
3. 用哪个 ID 限死 `allow`，让 `buy_cheapest` 不会跑到低价废号段去
