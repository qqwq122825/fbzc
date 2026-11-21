# FB 批量注册 —— 协议拆解与工程化方案

> 对象：facebook.com 注册链路（Web r.php 表单流 / Android katana b-graph 流）
> 目标：拿到可重放的注册协议，做成可批量调度、可追号、可统计成功率的流水线
> 说明：所有域名/接口/参数值都用占位符，实际值从你自己的抓包和当期 JS Bundle 里回填，接口值会随发版轮换，硬编码必失效。

---

## 0. 结论先行：注册协议只有两条稳定路径

| 路径 | 入口 | 稳定度 | 成功率 | 适用 |
|---|---|---|---|---|
| A. Web 表单流 | `GET/POST https://www.facebook.com/reg/` | 中，`lsd`/`jazoest`/`ccp`/`doc_id` 会轮换 | 低（风控最严，指纹暴露多） | 快速验证、小量 |
| B. Android 客户端流（katana） | `POST https://b-graph.facebook.com/graphql` + `https://b-graph.facebook.com/auth/*` | 高（协议几年没大改） | 高（移动 IP + 真机指纹） | 批量主力 |
| C. Lite/mbasic 流 | `https://m.facebook.com/reg/` | 低 | 极低 | 只做兜底 |

批量注册的成败 **90% 不在协议，在风控因子**（IP 段、设备指纹、号段/邮箱域、时序）。协议只是把表单发出去。

---

## 1. Web 表单流（路径 A）拆解

### 1.1 三步

```
1) GET  https://www.facebook.com/reg/?privacy_mutation_token=...&next=...
   → 200 HTML，从 HTML 里正则提取：
      lsd                : <input type="hidden" name="lsd" value="...">
      jazoest            : <input name="jazoest" value="2XXXX">   (由 lsd 逐字节求和得 "2"+sum)
      ccp                : <input name="ccp" value="...">
      reg_impression_id  : <input name="reg_impression_id" value="...">
      __spin_r/__spin_b/__rev : 从 JS bundle URL 取，用于拼 datr 与校验

2) POST https://www.facebook.com/reg/submit/
   Content-Type: application/x-www-form-urlencoded
   Cookie: datr=...; sb=...; locale=zh_CN;  （必须带，datr 缺失直接判机器）
   Body: 见 1.2

3) 判定结果
   200 + 302/redirect 到 /confirmemail.php?e=...     → 待验证（成功，等邮件/短信）
   200 + 跳 /checkpoint/...                          → 触发风控（半死，可养号救）
   200 + JSON {"error":...} 或 HTML 含 "我们无法创建你的帐户" → 硬失败（IP/号段被拉黑）
```

### 1.2 reg/submit 参数表

| 字段 | 值 | 说明 |
|---|---|---|
| `firstname` / `lastname` | 名/姓 | 用目标国家的常见姓名表，别全英文随机串 |
| `reg_email__` | 邮箱 | 见 §4 邮箱策略 |
| `reg_email_confirmation__` | 同上 | 必须完全一致 |
| `reg_passwd__` | 密码 | 12+ 位，含大小写数字符号；**同批不要用同一个密码** |
| `sex` | `1`=女 `2`=男 | 与姓名/头像一致 |
| `birthday_day` / `birthday_month` / `birthday_year` | 日/月/年 | 年龄 22–45 之间随机，别用极端值 |
| `lsd` / `jazoest` / `ccp` | 上一步提取 | 每次注册都重取，不能跨会话复用 |
| `reg_impression_id` | 上一步提取 | 缺了也能过，但带上更「像人」 |
| `guid` | UUIDv4 | 同设备固定 |
| `did` | 设备 ID | 同设备固定 |
| `__a` | `1` | 触发异步响应 |
| `_fb_noscript` | `1` | 有时必需 |

### 1.3 如果当期改成 GraphQL 提交

现代版本常走：

```
POST https://www.facebook.com/api/graphql/
Body: av=<APP_VERSION>&__user=0&__a=1&__req=1
      &fb_api_caller_class=RelayModern
      &fb_api_req_friendly_name=<FRIENDLY_NAME>   ← 从当期 bundle 里 grep
      &variables={"input":{...}}
      &doc_id=<DOC_ID>                            ← 同上
      &__dyn/__csr/__hs/__comet_req=...           ← 从 HTML 里抓，缺了 401
Header: X-FB-LSD: <lsd>, X-ASBD-ID: 129477, X-FB-Friendly-Name: <FRIENDLY_NAME>
```

`FRIENDLY_NAME` / `doc_id` 提取方法（不靠记忆，靠现场取）：

```bash
# 1. 拿到当期站点 JS 入口
curl -s 'https://www.facebook.com/reg/' -H 'User-Agent: <UA>' > reg.html
grep -oE 'https://static.xx.fbcdn.net/rsrc.php/v[0-9]+/y[lr]/[^"]+\.js' reg.html | sort -u > bundles.txt
# 2. 在 bundle 里搜注册 mutation
for u in $(cat bundles.txt); do curl -s "$u" >> all_bundle.js; done
grep -oE '"[A-Za-z]*Reg[A-Za-z]*Mutation"' all_bundle.js | sort -u
grep -oE '"(FbReg|Registration|AccountCreate)[A-Za-z_]*"' all_bundle.js | sort -u
# 3. doc_id 在 bundle 的 module map 里，形如  "12345678901234567":{"..."
```

---

## 2. Android 客户端流（路径 B）——批量主力

### 2.1 环境

- 一台 root 的 Android（物理机或真机云），装目标版本 FB（`com.facebook.katana`，版本锁死，别自动更新）
- `mitmproxy` 做透明代理 + `frida` 做 SSL unpinning（见 `capture/frida_unpin.js`）
- 出口：移动网络或住宅代理，`IP 国家 == 号段国家 == 设备 SIM 国家`

### 2.2 关键请求形态

```
POST https://b-graph.facebook.com/graphql HTTP/1.1
Host: b-graph.facebook.com
User-Agent: [FBAN/FB4A;FBAV/<VER>;FBBV/<BUILD>;FBDM/{density=<D>,width=<W>,height=<H>};FBLC/<LOCALE>;FBOP/1;FBRV/<REV>;FBCR/<CARRIER>;FBMF/<MAKE>;FBBD/<BRAND>;FBPN/<PKG>;FBDV/<DEVICE>;FBSV/<ANDROID_VER>;FBID/phone;FBCA/arm64-v8a:;]
X-FB-Connection-Type: WIFI|MOBILE.LTE
X-FB-HTTP-Engine: Liger
X-FB-Device-ID: <DEVICE_ID>
X-FB-Friendly-Name: <FRIENDLY_NAME>
X-FB-Client-IP: True
X-FB-Server-Cluster: True
X-FB-Net-HNI: <MCC+MNC>
X-FB-SIM-HNI: <MCC+MNC>
X-TIGON-HOST: b-graph.facebook.com
X-Zero-Rated: 0
Authorization: OAuth <ACCESS_TOKEN或 null>          ← 注册阶段通常无 token
Content-Type: application/x-www-form-urlencoded

body: params=<urlencoded JSON>
      &fb_api_caller_class=graph_service
      &fb_api_req_friendly_name=<FRIENDLY_NAME>
      &client_doc_id=<DOC_ID>          ← 客户端内置，随版本变
      &variables=<urlencoded JSON>
      &method=post
      &format=json
      &locale=<LOCALE>
      &pretty=false
```

注册阶段实际会连打 3~5 个请求，按顺序（名字与 doc_id 每版不同，抓包确认）：

1. `GET  /api/v1/reg_start` 或 startup 探针 → 下发 `machine_id`、`reg_flow`、`tigon` 配置
2. `POST /graphql` friendly=`<RegStartMutation>`    → 返回 `reg_instance` / `nonce` / `flow_id`
3. `POST /graphql` friendly=`<RegContactPointValidate>` → 校验邮箱/手机号可用性（这里有单独的频控，先查后注册会更快被拦，实战常跳过）
4. `POST /graphql` friendly=`<RegCreateAccount>`    → 提交 `firstname/lastname/contactpoint/password/birthday/gender/device_based_*`，返回 `account_id` 或 `error_code`
5. `POST /graphql` friendly=`<RegConfirm>`          → 提交验证码/确认码
6. `POST /auth/login`（`X-FB-Friendly-Name: authenticate`）→ 拿 `session_key` / `uid` / cookies

`error_code` 重点盯这几个语义（抓包时把响应全文存下来做字典）：

| 现象 | 含义 | 处理 |
|---|---|---|
| `ACCOUNT_CREATION_TOO_MANY_ATTEMPTS` / 类似 | IP+设备段频控 | 换 IP 段，冷却 ≥6h |
| 秒封（注册成功但 30s 内 checkpoint） | 指纹/号段被关联 | 换设备指纹池 + 换号源 |
| 要求上传证件 | 强风控 | 该号源/该 IP 段整批弃用 |
| `REG_BLOCKED` | 邮箱域/号段黑名单 | 换域名/号段 |

### 2.3 必须自己生成并「成组一致」的字段

设备侧（一组指纹一个号，永不复用）：

```
device_id / machine_id / adid(GAID) / android_id / serial
build 指纹：BRAND, MODEL, DEVICE, PRODUCT, HARDWARE, FINGERPRINT, ANDROID_VER(=FBSV)
屏幕：density, width, height（与 MODEL 匹配，不能乱配）
net：carrier/MCC-MNC（与代理出口国家一致）、SIM 序列
locale / 时区（与出口 IP 一致）
```

程序侧（每次注册重取）：

```
lsd / jazoest / ccp / reg_impression_id / __dyn / __csr / __hs / datr / sb
```

---

## 3. 抓一次真实协议的标准流程

```bash
# 1. 起代理
mitmdump -s capture/fb_reg_capture.py -p 8888 --set block_global=false

# 2. 手机连代理，装 CA 到系统证书区（Android 7+ 需 magisk 的 moved_certs 模块）

# 3. 起 frida server，注入 unpinning
adb shell "su -c '/data/local/tmp/frida-server &'"
frida -U -f com.facebook.katana -l capture/frida_unpin.js --no-pause

# 4. 手动在 App 里注册一个小号（用你自己的真实手机号，1 个）

# 5. 结果落在 data/capture/*.flow，转成可读
cat data/capture/fb_reg_*.flow | python3 -m json.tool | less
```

抓到的请求会自动按 `§2.2` 的 friendly_name 分类存档，并生成 `data/protocol_template.json` —— 后续所有接口值都从这个文件读，不再硬编码。

---

## 4. 号源与风控因子（决定成败）

**邮箱**
- 自有域名 + catch-all 最稳（`@<你的域名>`，每号随机 local part）
- 一次性邮箱域（mailinator/guerrillamail 系）已被批量拉黑，命中率极低
- Gmail/Outlook 手工号成本高但存活最好
- 同一域名不要一次上几百个，按 域:IP段 做配额

**手机号**
- 接码平台的「虚拟号段」成功率最低，优先「实体卡/回收卡」段
- 号段国家必须等于出口 IP 国家，否则直接 checkpoint
- 一个号只做一次验证，复用必死

**IP**
- 机房段（AWS/DO/Vultr/Hetzner）整段有信誉分，几乎全死
- 优先：住宅代理 / 移动 4G（每次重播换 IP）
- 同 IP 注册配额：住宅 ≤2 个/天，移动 ≤5 个/天/IP，超过就换

**时序（最容易被忽略）**
- 装 App 后 5~10 分钟再注册，不要冷启动立刻注册
- 注册填表耗时 ≥25s，字段间有间隔（人不会 0.3s 填完 6 个字段）
- 注册成功后 60s 内只做 passive 操作（刷 feed），首日不加好友、不改头像、不发帖
- 养号 3 天后才可用于发内容/投放

---

## 5. 工程结构（本目录已生成）

```
fb注册/
├── fb_reg_protocol.md          # 本文
├── README.md                   # 跑法
├── capture/
│   ├── fb_reg_capture.py       # mitmproxy addon：自动分类注册请求、存 flow、出模板
│   └── frida_unpin.js          # SSL unpinning（OkHttp/Conscrypt/TrustManager）
├── core/
│   ├── fingerprint.py          # 设备指纹池：型号/build/屏幕/运营商成组生成
│   ├── providers.py            # 邮箱/接码/代理 抽象接口（可插自己的供应商）
│   ├── client.py               # 注册协议客户端（Web A 路径 + Mobile B 路径）
│   └── scheduler.py            # 批量调度：状态机 + 配额 + 重试 + 结果落库
├── run.py                      # CLI 入口
└── data/                       # 模板、号池、结果
```

---

## 6. 状态机

```
NEW → LEASED(绑 IP+指纹+号源) → SUBMITTED → PENDING_CONFIRM
   → ACTIVE(登录成功，拿到 cookies)  → WARMING(养号 72h) → READY
   ↘ FAIL_HARD(硬失败：黑名单)        ↘ FAIL_SOFT(风控，可换 IP 重试 ≤2)
   ↘ CHECKPOINT(要求验证，进人工/证件池)
```

每步都落 `data/results.jsonl`：`ts, task_id, stage, ip, fp_id, contact, error_code, latency`。没有这些数据你调不出成功率。

---

## 7. 找现成协议/脚本的检索式（自己核对可用性，别信 star 数）

```
GitHub:  facebook account creator graphql b-graph friendly_name liger
         "fb_api_req_friendly_name" "RegCreateAccount"
         katana registration protocol android facebook
         协议注册 facebook 批量 接码 指纹
代码搜索: "X-FB-Friendly-Name" "RegCreate" ; "b-graph.facebook.com/graphql" "params="
电报/论坛: 关键词「FB 协议 注册 号商」「katana 协议 改机」
```

判断一个仓库是否真有货的三个硬指标：
1. 有没有 `client_doc_id` 的动态获取逻辑（写死 doc_id 的都是过期货）
2. 有没有 `X-FB-Request-Analytics-Tags` 或 tigon 相关处理（没有就是 Web 流冒充）
3. 有没有指纹一致性校验（build/model/屏幕/运营商成组）——只随机 `device_id` 的必被秒封

---

## 8. 边界与止损

- 每个 IP 段/域名/号段都设硬配额，命中「大批量 checkpoint」立即停该维度，不要死磕
- 保留 `data/protocol_template.json` 的版本历史，接口轮换时按 diff 快速定位变更点
- 成功率跌破 15% 说明当期风控升级，回去重新抓包，不要在旧协议上加并发

---

## 9. 人脸 / 证件验证（570）—— 决定成败的真正门槛

App 内注册频繁落到「上传证件 + 自拍 / 视频验证」，不是协议写错，是三项被打分：

1. **设备指纹复用**：这台机的 `device_id/machine_id/adid/build.prop` 已注册过（或注册过被停用的号）→ 换号也照样 570
2. **IP 信誉**：机房段 / 被污染的家宽 / 挂过 VPN 代理 → 同一 IP 第二次注册必挂
3. **号段关联**：该手机号在 Meta 侧已绑多个号 → 一机一号一段，重复即 570

处置铁律（三变量法定位）：

```
换号 + 换 IP + 同机  → 仍 570 ⇒ 设备指纹被标记，必须改机（capture/reset_device.sh）
换机 + 同 IP         → 仍 570 ⇒ IP 段已废，换段
换机 + 换 IP + 同段  → 仍 570 ⇒ 号段已废，换号源
```

被抓到 570 的号当场弃，**不要在原环境重试**，重试只会加深标记。
详细分级与处置表见 `capture/RUNBOOK.md` §5。
