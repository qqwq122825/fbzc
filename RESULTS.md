# 结果清单（一页纸）

> 用途：把仓库交给 GPT 继续挖，你只回收"结果"。
> 上半页是**已经确定的结果**（可直接引用），下半页是**还没确定的结果**（连同验收标准一起交给对方）。

---

## 一、已确定的结果（可复核）

| # | 结论 | 证据级别 | 复现位置 |
|---|---|---|---|
| 1 | 注册存在三条通道；本次观测期（2026-11）**只有 legacy 服务端渲染表单是活的** | ✅ 实测 | `core/legacy_reg.py`、`FIRST_ACCOUNT.md` |
| 2 | legacy 表单的触发条件是**老 UA**（Android 4.4 / Chrome 34）；现代 UA 会 301 到 React 壳 | ✅ 实测 | 同上 |
| 3 | 该表单提交成功返回 302 → `/login/save-device/?login_source=account_creation`，同时下发 `c_user`+`xs` | ✅ 实测 | 同上 |
| 4 | Web Comet 的 `CAARegistrationFormDesktopQuery`（doc_id `28589959880637856`）**不需要 lsd**，裸会话直打即通过，服务端会种 `datr` | ✅ 实测 | `fb_reg_protocol.md` §1.1 |
| 5 | 钱包式密码加密：`#PWD_BROWSER:5:<key_id>:<b64>`，算法是 Curve25519+XSalsa20-Poly1305；公钥在注册页 SSR 数据的 `caa_password_encryption_data` 里，**64 位 hex** | ✅ 实测（往返验证） | `core/web_reg.py::encrypt_password` |
| 6 | 邮箱确认走 Bloks：`/async/wbloks/fetch/?appid=com.bloks.www.bloks.caa.reg.confirmation.async&type=action`；当前 DATA 结构已由纯 DSL 解释器恢复 | ✅ 单次实测 | `BLOKS_CONFIRM.md`、`tools/bloks_dsl_eval.py` |
| 7 | 当前确认 POST 返回 HTTP 200，但随后只读 `GET /` 302 到 `/checkpoint/…`；邮箱确认成功与否仍未证明 | ✅ 单次实测 | `CHECKPOINT_FINDINGS.md` |
| 8 | 历史样本中换 IP 后出现 checkpoint，但新的未换出口样本也出现 checkpoint，因此 **IP 因果未建立** | 🔶 相关性 | `fb_reg_protocol.md` §7 |
| 9 | 老表单接口对 gmail / outlook / mailinator **返回同一个错误码 1351050** ⇒ 拒绝原因不是邮箱域 | ✅ 实测 | `fb_reg_protocol.md` §2.3 |
| 10 | doc_id 可从前端 bundle 的 `*_facebookRelayOperation` 静态导出（本次提出 28 条） | ✅ 实测 | `apk/docids.py`、`data/graphql_docs.json` |

---

## 二、还没确定的结果（交给 GPT 的四项）

每项都给了**验收标准**——达到就能判定"挖到了"，不用靠感觉。

### ① 提交 mutation 的入参类型
- 现状：doc_id `27029416779977343`，任何 `input` 形态都返回 `noncoercible_variable_value`，**单字段也失败**
- 假设：bundle 导出的 doc_id 与线上实际使用的操作错位
- **验收**：用任意 doc_id + 任意 variables 让该端点返回 `data` 而非 error，并在仓库里留下可复现的请求样例

### ② Bloks 确认后的业务状态
- 现状：当前页 DATA 已恢复且 POST 返回非 500；后继状态为 checkpoint，确认结果仍未证明
- **验收**：账号所有者正常手动完成安全检查后，只读核验同一账号是否离开
  `/confirmemail.php` 与 `/checkpoint/`；不自动绕过安全检查

### ③ SSR → CSR 迁移边界
- 现状：只确认了老 UA 能拿到表单；`mbasic` / `free` / `lite` 子域在老 UA 下的行为未系统测
- **验收**：给出一张"UA × 子域 → 是否返回服务端渲染表单"的矩阵表

### ④ 一致性校验的维度与权重
- 现状：维度已列（地理/设备/会话/出口连续性/域名），**权重全是猜测**
- **验收**：给出对照实验设计（同 IP 不同 locale、同 locale 不同 IP），并至少跑出一组可复核的差异数据

---

## 三、交给 GPT 时用这段（复制即用）

```
仓库：https://github.com/qqwq122825/fbzc
先读 RESULTS.md（本文件）和 fb_reg_protocol.md 的 §0/§1.2/§5/§9，
再读 WEB_FLOW_FINDINGS.md（里面是全部失败变体，别重复踩）。

任务：只做"未确定结果"里的 ①~④，任选一个推进。
要求：
- 结论必须给出证据级别（实测/静态/未验证）
- 失败也要记录：写清试过什么、返回什么，进 WEB_FLOW_FINDINGS.md
- 不碰人脸/证件环节，不做批量与产能，不引入号源/代理运营
- 交付物：一个 markdown + 可复现的最小脚本
```

---

## 四、你回收结果时看这张表

| 项 | 交付形态 | 判定"成了"的标志 |
|---|---|---|
| ① 入参类型 | 一段可跑的请求构造 + 响应样例 | 返回 `data` 而不是 `noncoercible` |
| ② 三个运行时值 | 取法说明 + 代码 | 确认请求返回非 500，且账号状态从 PENDING 变化 |
| ③ 迁移边界 | UA × 子域矩阵表 | 表格填满、有可复核的 status/长度 |
| ④ 权重 | 实验设计 + 一组数据 | 能指出哪个维度影响最大，并附对照记录 |

> 一句话：**上半页可以直接用，下半页交给 GPT 去换。**
