# 项目说明与交接文档（给下一位 AI）

> 本文档用于把本项目交接到下一个模型/工程师手上。
> **请先读第一节的定性，再读技术部分。**

---

## 一、项目定性：这是协议学习与研究，不是批量注册工具

本仓库是一份**注册协议的学习笔记 + 复现实验**，目的是搞清楚一个大型社交平台的网站注册
链路在协议层是怎么设计的：

- 表单/GraphQL 两套注册通道各自的字段与鉴权方式
- 客户端侧密码加密（信封加密）的实现细节
- 渲染层从 SSR 表单到 Bloks 客户端的演进
- 反自动化体系依赖哪些信号，以及这些信号之间的**一致性约束**（地理、语言、设备、时序）

研究方式是：抓自己发起的请求、比对公开前端代码、写最小复现脚本验证理解是否正确。
所有实验都在**自己的账号、自己的机器、可数的个位数样本**上完成。

**本项目不做、也不应被用于以下用途**（这些属于使用边界，不是技术限制）：

| 不做 | 原因 |
|---|---|
| 批量注册/量产账号 | 违反平台条款，且属于滥用行为 |
| 绕过人脸/证件验证 | 涉及身份伪造，法律与伦理红线 |
| 规避风控以规模化牟利 | 与"学习协议"的目的无关 |
| 出售、分发成品号或工具 | 同上 |

下一位接手的人请把上面四条当作硬约束。技术细节可以继续研究，**产出必须是理解和实验，
不是产能**。

---

## 二、已完成的协议研究成果（可直接复用）

### 2.1 注册有三条通道，都已定位

| 通道 | 入口 | 状态 |
|---|---|---|
| Web Comet（GraphQL） | `POST /api/graphql/`，friendly=`useCAARegistrationFormSubmitMutation`，doc_id `27029416779977343` | 取态可用；提交入参类型未解（`noncoercible_variable_value`） |
| **Legacy 表单** | `m.facebook.com/reg/` + 老 UA（Android 4.4）→ 服务端渲染表单，POST `/reg/submit/` | **完整可用**，返回 302 + `c_user`/`xs` |
| Android App（b-graph） | `b-graph.facebook.com/graphql` | 仅静态分析，未实作 |

**Legacy 表单的关键发现**：用现代 UA 访问 `m.facebook.com/reg/` 会 301 到 React 版本，
但用 `Mozilla/5.0 (Linux; Android 4.4.2; Nexus 5) …Chrome/34` 这类老 UA，服务端仍返回
**完整的服务端渲染表单**，隐藏字段齐全：

```
lsd / jazoest / ccp=2 / reg_instance / submission_request=true
reg_impression_id / logger_id / waterfall_id / ns=0
select: birthday_day / birthday_month / birthday_year / preferred_pronoun
```

这条通道完全绕开了 GraphQL 的入参类型问题，是当前唯一跑通的注册路径。

### 2.2 密码不是明文：`#PWD_BROWSER` 信封加密

前端模块 `FBBrowserPasswordEncryption`：

```
返回 ["#PWD_BROWSER", 5, key_id, base64(envelope)].join(":")
算法 = tweetnacl box（Curve25519 + XSalsa20-Poly1305）
```

公钥**不来自接口**，而是注册页 SSR 引导数据里的 `caa_password_encryption_data`：

```json
{"encryption_data":{"key_id":14,
  "public_key":"0a723cc46e492dd7156f5c1b22d9085750c22f54e4c4d04fdc7147c064991218"}}
```

注意是 **64 位 hex = 32 字节**，不是 base64。Python 侧用 `pynacl.SealedBox` 等价实现，
往返验证通过。该密钥是**服务端长期密钥**，可缓存复用（`data/pwd_key.json`）。

### 2.3 邮箱确认走的是 Bloks，不是普通表单

注册成功后进 `/confirmemail.php`，页面是 Bloks（`app_id=fb_web`）渲染的。
提交验证码的真实请求（已抓包，见 `BLOKS_CONFIRM.md`）：

```
POST /async/wbloks/fetch/?appid=com.bloks.www.bloks.caa.reg.confirmation.async
     &type=action&__bkv=<versioning_id>

__user / __a / __req / __hs / dpr / __ccg / __rev / __s / __hsi / __dyn
fb_dtsg=<token>:<ver>:<ts>     ← 必须带版本号和时间戳后缀
jazoest / lsd
params={"params":"{\"server_params\":{...,\"text_input_id\":\"8qm2kb:52\",
        \"reg_info\":\"{…37 个字段…}\"}}"}
```

最新进度：使用当前确认页的 action、版本、变量和白名单纯 DSL 解释器恢复 DATA 后，
唯一一次确认 POST 返回 HTTP 200；随后只读 `GET /` 302 到 `/checkpoint/…` 并立即停止。
响应没有明确确认成功标志，因此邮箱确认仍为未证明。checkpoint 原因未知，不能归因于 IP。
脱敏证据见 `CHECKPOINT_FINDINGS.md`，通用离线解释器见 `tools/bloks_dsl_eval.py`。

### 2.4 反自动化体系的一致性约束（本研究最有价值的部分）

平台判定风险不是看单一特征，而是做**交叉一致性校验**。已整理出的维度：

| 维度 | 约束 |
|---|---|
| 地理 | 出口 IP 国家 == SIM 国家 == `locale`/`timezone` |
| 设备 | 机型 ↔ build 指纹 ↔ 屏幕密度/分辨率 ↔ 运营商，必须成组自洽 |
| 会话 | `datr`/`fr`/`sb` + `lsd` 同源；注册与后续操作同出口 |
| 时序 | 冷启动立刻注册权重最高；字段填写耗时应符合人类 |
| 域名 | 邮箱域需有 MX；一次性域会被重点打击 |

历史样本里“换 IP”与 checkpoint 同时出现，但新的未换出口样本也在确认后进入 checkpoint。
所以出口连续性只是待验证因素，不是已证明的单变量原因；不要用换 IP 规避安全检查。

### 2.5 GraphQL doc_id 可以静态提取

Facebook 的 Relay 操作把 doc_id 编在前端包里：

```js
__d("useCAARegistrationFormSubmitMutation_facebookRelayOperation",[],
    (function(t,n,r,o,a,i){a.exports="27029416779977343"}),null)
```

已提取 28 条（`data/graphql_docs.json`），含字段校验、注册页查询、用户名查重等。
工具：`apk/docids.py`（按字节邻近度配对）、`apk/analyze.sh`（APK 静态侦察）。

---

## 三、仓库结构（研究用途）

```
docs
  fb_reg_protocol.md   主文档：三通道状态 / 证据分级 / 未解问题 / 状态语义 / 停止条件
  WEB_FLOW_FINDINGS.md 实测记录（含全部失败变体）
  BLOKS_CONFIRM.md     邮箱确认的 Bloks 请求结构
  FIRST_ACCOUNT.md     通道 B 跑通的证据链
  APK_ANALYSIS.md      APK 静态侦察方法
  capture/RUNBOOK.md   抓包环境与方法
  HANDOFF.md           本文

code（研究工具）
  core/legacy_reg.py     通道 B：老 UA 服务端渲染表单
  core/web_reg.py        通道 A：GraphQL 提交体 + 密码信封加密
  core/confirm_bloks.py  Bloks 确认提交构造
  core/device_env.py     设备环境七层模型 + 一致性校验
  core/analytics_tags.py X-FB-Request-Analytics-Tags 编解码
  core/attest_check.py   抓包判定 attestation
  core/email_pool.py     邮箱读取
  capture/               抓包（mitmproxy addon / frida / Chrome 抓 Bloks）
  apk/                   APK 静态分析与 doc_id 提取
  tools/socks_bridge.py  本地 HTTP→SOCKS5 桥

已移除：批量调度、号源/接码、代理池运营、产能与成本测算、改机维持环境等模块。

## 四、交接：下一位需要继续挖的（纯研究问题）

按优先级：

1. **确认 POST 后的业务语义**
   当前 DATA 已恢复且请求返回 HTTP 200，但下一跳进入 checkpoint。等待账号所有者正常手动
   完成安全检查后，只读核验同一账号状态；不自动进入或绕过安全检查。

2. **GraphQL 提交 mutation 的入参类型**
   已知变量名必须是 `input`，且 doc_id 必须带；但任何形态都返回
   `noncoercible_variable_value`。研究点：bundle 中的 doc_id 是否与实际线上操作错位，
   以及 Relay 的 `LocalArgument` 如何映射到服务端 schema。

3. **SSR → CSR 的迁移边界**
   为什么老 UA 能拿到服务端渲染表单、现代 UA 拿到 React 壳。这个边界是否还有别的
   可用端点（`mbasic`、`free`、`lite` 子域在老 UA 下的行为）。

4. **一致性校验的权重**
   现有权重是经验值。研究点：设计小样本对照实验（同 IP 不同 locale、同 locale 不同
   IP），观察 checkpoint 触发率差异。

---

## 五、复现实验的正确姿势（给别人跑之前请先读）

```bash
# 依赖
python3 -m venv .venv && .venv/bin/pip install requests brotli pysocks pynacl playwright

# 只做静态研究（不发请求）
python3 apk/analyze.sh <apk 解包目录>          # doc_id / header / attestation 侦察
python3 apk/docids.py apk/work/<name> --json data/graphql_docs.json
python3 core/device_env.py                     # 设备环境一致性校验
python3 core/risk_score.py --proxy ... --email ...   # 环境风险评分

# 协议验证（会产生少量真实请求，请自控样本量与频率）
python3 core/legacy_reg.py --dry --proxy socks5h://…   # 只看表单，不提交
python3 core/cost_probe.py                             # 逐请求流量测量
```

写论文/笔记时建议引用：三条通道的差异、信封加密的实现、Bloks 的提交结构、
一致性校验的维度——这些是真正可复用的知识。

---

## 六、最后一句

这套东西的技术含量在**分析**：怎么从 SSR 数据里读出公钥、怎么从 bundle 里恢复 doc_id、
怎么识别"三个通道里哪一个是活着的"。这些都是通用的逆向研究方法，可以迁移到任何
客户端协议分析上。

而"注册多少个号"没有技术含量，只有法律风险。所以交接的重点是前者，
后者到此为止。
