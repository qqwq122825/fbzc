# 注册链路协议研究（研究版 v2）

> **定性**：本文是协议**研究笔记**，记录"某个大型社交平台的网站注册链路是怎么设计的"，
> 以及我们通过抓包/静态分析**验证过的部分**和**仍未解开的部分**。
>
> **不包含**批量注册方案、产能规划、风控规避策略。原先 v1 里的"工程化方案"与
> 成功率表格已移除——那些数字没有统计支撑，属于传闻，见 §8。
>
> 证据分级（全文统一）：
> - ✅ **实测**：真实请求往返得到的结果，可在本仓库复现
> - 🔶 **静态**：从公开前端代码 / SSR 数据里读出来的结构，未单独验证
> - ❓ **未验证**：流传说法、经验推断，本项目没有数据支撑

---

## 0. 三条通道的真实状态

| 通道 | 入口 | 状态 | 证据 |
|---|---|---|---|
| **A. Web Comet（GraphQL）** | `POST /api/graphql/`，friendly `useCAARegistrationFormSubmitMutation`，doc_id `27029416779977343` | 取态可用；**提交未解**（`noncoercible_variable_value`） | ✅ 取态实测 / ✅ 失败实测 |
| **B. Legacy 服务端渲染表单** | `m.facebook.com/reg/` + 老 UA（Android 4.4）→ `POST /reg/submit/` | **完整跑通**：302 → `login/save-device/?login_source=account_creation` + `c_user`/`xs` | ✅ 实测 |
| **C. Android App（b-graph）** | `b-graph.facebook.com/graphql` | 仅静态分析，未实作 | 🔶 静态 |

**重要**：v1 里那张"稳定度 / 成功率"表（Web 低、App 高、Lite 极低）**没有数据支撑**，
v2 删除。真实情况是：通道是否可用取决于**当期服务端留了哪个入口**，
本次观测期（2026-11）活着的只有 B。

---

## 1. 通道 A：Web Comet GraphQL

### 1.1 已确认的事实

```
POST https://www.facebook.com/api/graphql/
  fb_api_caller_class=RelayModern
  fb_api_req_friendly_name=CAARegistrationFormDesktopQuery
  doc_id=28589959880637856
  variables={"args":{},"ig_age_data":null,"next_uri":"/","scale":1}
→ 200，返回 caa_registration.constants.reg_instance
```

✅ 实测结论：

- **不需要 `lsd`**：裸会话（无 cookie、无页面）直打即可通过，服务端会种 `datr`
- **必须带 `doc_id`**：去掉会返回 `error 1675002`
- 变量名必须是 `input`（去掉包裹 → `missing_required_variable_value`）

### 1.2 仍未解的部分

提交 mutation（doc_id `27029416779977343`）任何入参形态都返回
`noncoercible_variable_value`，已排除的变体：

| 变体 | 结果 |
|---|---|
| `input` 传字符串 / 数字 / 布尔 / 数组 | noncoercible |
| `input` 只放一个 `client_mutation_id` | noncoercible（**单字段也失败**）|
| `reg_data` 当对象 / 当 JSON 字符串 / 拍平 | noncoercible |
| snake_case ↔ camelCase | noncoercible |
| 去掉 doc_id | `1675002`（说明 doc_id 必需）|

**单字段也 noncoercible** 说明该 input 类型里不存在这些字段名 ⇒ bundle 导出的
doc_id 与线上实际使用的操作**不是同一个**（前端会同时发多版 op）。

❓ 待研究：Relay 的 `LocalArgument` 如何映射到服务端 schema；能否从别的入口拿到活着的 doc_id。

---

## 2. 通道 B：Legacy 表单（本次唯一跑通的路径）

### 2.1 触发条件（关键发现）

```
现代 UA 访问 m.facebook.com/reg/  → 301 到 www 的 React 版本（无可用表单）
老 UA（Mozilla/5.0 (Linux; Android 4.4.2; Nexus 5) … Chrome/34.0.1847.114）
                                  → 200，服务端渲染表单，约 102 KB
```

### 2.2 表单字段（✅ 实测，从 HTML 直接读）

```
隐藏：lsd / jazoest / ccp=2 / reg_instance / submission_request=true
      reg_impression_id / logger_id / waterfall_id / ns=0 / app_id / helper
结构：field_names[] × 5（firstname / birthday_wrapper / reg_email__ / sex / reg_passwd__）
输入：firstname / lastname / reg_email__ / reg_passwd__ / sex(1|2|-1) / custom_gender
选择：birthday_day / birthday_month / birthday_year / preferred_pronoun
提交：POST /reg/submit/?privacy_mutation_token=…&multi_step_form=1&skip_suma=0&shouldForceMTouch=1
```

### 2.3 响应语义（✅ 实测）

| 现象 | 含义 |
|---|---|
| 302 → `/login/save-device/?login_source=account_creation` + `c_user`/`xs` | 账号已创建 |
| `error=1357005 "Your Request Couldn't be Processed"` | 缺 CSRF（对照实验：去掉 lsd 即出现） |
| `error=1351050 "Registration Error"` | 业务层拒绝；**换 gmail / outlook / mailinator 结果相同** ⇒ 与邮箱域无关，是老接口对新客户端形态的统一拒绝 |

### 2.4 注意

这条通道的存在本身是**服务端为老客户端保留的兼容层**，不是"漏洞"。
它随时可能被关掉——这正是本研究的价值点：**记录兼容层何时消失**。

---

## 3. 通道 C：Android App（仅静态）

🔶 静态提取到的信息：`b-graph.facebook.com/graphql` 的请求头组合
（`X-FB-HTTP-Engine: Liger`、`X-FB-Net-HNI`、`X-FB-Device-Group` 等）、
`client_doc_id` 由客户端内置。**未做任何实机验证**。

---

## 4. 密码信封加密（✅ 实测可复现）

前端模块 `FBBrowserPasswordEncryption`：

```
返回 ["#PWD_BROWSER", 5, key_id, base64(envelope)].join(":")
算法 = tweetnacl box（Curve25519 + XSalsa20-Poly1305）
```

公钥**不来自接口**，而是注册页 SSR 引导数据：

```json
"caa_password_encryption_data":{"encryption_data":{
  "key_id":14,
  "public_key":"0a723cc46e492dd7156f5c1b22d9085750c22f54e4c4d04fdc7147c064991218"}}
```

注意：**64 位 hex = 32 字节**，不是 base64。Python 侧用 `pynacl.SealedBox` 等价实现，
往返验证通过（`core/web_reg.py::encrypt_password`）。该密钥是服务端长期密钥，可缓存复用
（`data/pwd_key.json`）。

❓ 未解：`key_id` 的轮换周期。

---

## 5. 邮箱确认：Bloks 提交（结构已知，参数未完全对齐）

🔶 抓到的真实请求形态（详见 `BLOKS_CONFIRM.md`）：

```
POST /async/wbloks/fetch/?appid=com.bloks.www.bloks.caa.reg.confirmation.async
     &type=action&__bkv=<versioning_id>

__user / __a / __req / __hs / dpr / __ccg / __rev / __s / __hsi / __dyn
fb_dtsg=<token>:<ver>:<ts>     ← 必须带版本号与时间戳后缀
jazoest / lsd
params={"params":"{\"server_params\":{…,\"text_input_id\":\"8qm2kb:52\",
        \"reg_info\":\"{…37 个字段…}\"}}"}
```

✅ 实测的复现进度：

| 阶段 | 结果 |
|---|---|
| 裸 `POST /confirmemail.php` | `1357004 Sorry, something went wrong` |
| 补 `lsd` + `jazoest` | 仍 `1357004` |
| 补 `fb_dtsg`（带 `:ver:ts`） | `1357004` 消失 → **HTTP 500** |

⇒ **CSRF 已通过**，卡在 `params` 的精确结构。缺三个运行时值：
`text_input_id`（组件实例 ID）、`__hs`、`__dyn`。

❓ 待研究：这三个值能否从 SSR 的 Lispy 树静态推出，还是必须由 JS 运行时生成。

---

## 6. 一致性约束模型（本研究最可复用的部分）

平台的风险判定不是单点特征，而是**交叉一致性**。已识别的维度：

| 维度 | 约束 | 证据 |
|---|---|---|
| 地理 | 出口 IP 国家 == SIM 国家 == `locale` / `timezone` | 🔶 字段语义推断 |
| 设备 | 机型 ↔ build 指纹 ↔ 屏幕密度/分辨率 ↔ 运营商，必须成组自洽 | 🔶 字段语义推断 |
| 会话 | `datr`/`fr`/`sb` 与后续请求同源 | ✅ 换 session 后状态不一致 |
| 出口连续性 | 注册与后续操作**必须同出口**：换 IP 访问确认页直接跳 `/checkpoint/` | ✅ 见 §7 |
| 域名 | 邮箱域需有 MX（否则 `USER_REGISTER_EMAIL_DOMAIN_NOT_MX=7`） | 🔶 错误码枚举 |

❓ **权重未知**：各维度在打分里占多少、阈值在哪，本项目**没有**做过对照实验，
任何"某项占 90%"的说法都是猜测。

---

## 7. 会话语义：出口连续性（✅ 最硬的结论之一）

```
注册出口 172.56.2.248（T-Mobile）
  → 该端口几分钟后失效
重新取 IP 得到 70.40.67.215
  → 打开确认页 302 到 /checkpoint/1501092823525282/
```

推论：**"先批量注册、回头统一验证"在物理上不成立**——不是因为风控策略，
而是因为会话与出口被绑定，而这类代理的端口是短期的。

这条结论对**正面用途**同样成立：任何需要"注册后立即操作"的自动化测试，
都必须保证出口在会话周期内稳定。

---

## 8. 需要明确否定的传闻（本项目无数据支撑）

v1 文档里出现过的这些说法，**全部属于经验传闻，应视为 ❓ 未验证**：

| 说法 | 状态 |
|---|---|
| "Web 通道成功率低、App 通道高" | ❓ 无统计 |
| "纯机房 IP 成功率 <5%" | ❓ 无统计 |
| "住宅 2–3 个/天、移动 3–5 个/天" | ❓ 无统计（且属产能规划，不在本研究范围）|
| "风控 90% 取决于 IP" | ❓ 权重未测 |
| "模拟器必死" | 🔶 结构性推断（Play Integrity 等），未做实验对照 |

需要这些数字的人应自己设计对照实验（同 IP 不同 locale、同 locale 不同 IP 等），
而不是引用二手结论。实验设计思路见 `FACE_RISK.md` §5。

---

## 9. 研究层面的停止条件

出现以下任一情况，说明研究对象已变化，应停下重新观察而不是加大投入：

1. 通道 B 的老 UA 也返回 React 壳（兼容层关闭）
2. 抓不到 `caa_password_encryption_data`（加密方案换代）
3. 确认页从 Bloks 换成别的渲染层（对照 `app_id` 是否变化）
4. 连续多次实验都落在 `/checkpoint/`（会话/出口语义变了）
5. 任何一步开始要求"人脸/证件"——这是**研究边界**，不是待攻克的难题

---

## 10. 敏感数据处理

`.gitignore` 排除 `data/` 全目录，因为其中包含：

```
data/accounts.jsonl    c_user / cookies(xs,datr) / 测试密码
data/proxies.txt       代理出口与账密
data/devices/          设备指纹
data/pwd_key.json      会话相关
```

文档里出现的 `c_user`、临时邮箱、以及已失效的 `lsd` / `fb_dtsg` 抓包值，
均为**本次实验的一次性产物**，不代表任何可复用凭据。

---

## 11. 复现与证据索引

| 想看什么 | 看哪里 |
|---|---|
| 通道 B 的完整字段与响应 | `core/legacy_reg.py`、`FIRST_ACCOUNT.md` |
| Bloks 确认的原始抓包 | `capture/bloks_confirm_capture.py` + `data/capture/bloks_confirm_template.json` |
| 所有失败变体（避免重复踩） | `WEB_FLOW_FINDINGS.md` |
| 密码加密实现 | `core/web_reg.py` |
| 一致性校验模型 | `core/device_env.py`、`FACE_RISK.md` |
| doc_id 静态提取 | `apk/docids.py`、`data/graphql_docs.json` |
| 流量成本测量 | `core/cost_probe.py`、`COST_MODEL.md` |
