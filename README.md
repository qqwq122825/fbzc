# 协议学习研究（Registration Protocol Study）

> 本仓库只讨论**注册协议本身**：请求结构、状态语义、客户端加密、渲染层演进、
> 一致性约束。**不包含**批量注册方案、产能规划、号源/代理运营、以及任何验证环节的绕过。
>
> 实验都在自建、自有、可数的样本上完成；产出是理解与实验记录，不是产能。

## 文档

| 文件 | 内容 |
|---|---|
| `RESULTS.md` | **一页纸结果清单**：10 条已确定结论 + 4 项待解（含验收标准），交给 GPT 用这个 |
| `fb_reg_protocol.md` | **主文档（研究版 v2）**：三条通道的真实状态、证据分级、未解问题、状态语义、停止条件 |
| `WEB_FLOW_FINDINGS.md` | Web 链路实测记录，含 20+ 个失败变体能（避免重复踩） |
| `BLOKS_CONFIRM.md` | 邮箱确认的 Bloks 提交结构与复现进度 |
| `FIRST_ACCOUNT.md` | legacy 表单通道跑通的完整证据链 |
| `APK_ANALYSIS.md` | APK 静态侦察方法：doc_id 提取、attestation 判定 |
| `capture/RUNBOOK.md` | 抓包环境与方法（frida / mitmproxy） |
| `HANDOFF.md` | 交接文档：定性、成果、边界、下一位该挖什么 |

## 代码（研究工具）

```
core/
  legacy_reg.py      通道 B：老 UA 服务端渲染表单（当前唯一跑通的注册路径）
  web_reg.py         通道 A：GraphQL 提交体构造 + #PWD_BROWSER 信封加密
  confirm_bloks.py   邮箱确认的 Bloks 请求构造（token 抽取 + reg_info 模板）
  client.py          会话与 GraphQL 调用
  device_env.py      设备环境七层模型 + 一致性校验
  analytics_tags.py  X-FB-Request-Analytics-Tags 编解码
  attest_check.py    抓包判定：有没有 attestation（决定纯协议可行性）
  email_pool.py      邮箱读取（IMAP / 落地目录 / Mailpit / 临时邮箱 API）
capture/
  fb_reg_capture.py     mitmproxy addon：自动分类注册请求 → 生成协议模板
  frida_okhttp_logger.js 直接打印请求明文（不需要证书）
  frida_unpin.js         SSL unpinning（仅用于采自己设备的流量）
  bloks_confirm_capture.py 用 Chrome 抓确认页的 Bloks 提交
apk/
  analyze.sh          APK 静态侦察（端点/协议头/doc_id/attestation）
  docids.py           friendly_name ↔ doc_id 配对
tools/
  socks_bridge.py     本地 HTTP→SOCKS5 桥（Chrome 走代理时的隧道稳定性）
```

## 依赖

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # requests / brotli / pysocks / pynacl
```

## 快速开始（只读研究，不发请求）

```bash
python3 apk/analyze.sh <apk 解包目录>          # doc_id / 协议头 / attestation 侦察
python3 apk/docids.py apk/work/<name>          # 名字 ↔ doc_id 配对
python3 core/device_env.py                     # 设备环境一致性自检
python3 core/attest_check.py <抓包日志>         # 判定有没有 attestation
```

## 边界

- 不建账号生产流水线，不采购号源，不做产能规划
- 不涉及人脸/证件验证的任何绕过
- 抓包与实验仅限自有设备、自有账号、可数样本
