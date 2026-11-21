# 协议学习研究（Registration Protocol Study）

> 本仓库用于**学习与研究**网站注册链路的协议设计：请求结构、鉴权方式、客户端加密、
> 渲染层演进、反自动化信号的一致性约束。**不用于批量注册，也不涉及绕过身份验证。**
> 接手前请先读 [HANDOFF.md](</Users/xxx/Documents/fb注册/HANDOFF.md>)。

# 目录与技术索引

## 目录

```
EMAIL_MODE.md                ★ 只用虚拟邮箱不用手机号：服务器要求、三种接法、风险评估
SMS_CHEAPEST.md              ★ hero-sms 自动挑最便宜号：文档结论、4 种价格形态、买号策略
WEB_FLOW_FINDINGS.md         ★ Web 注册链路已挖到可直接发请求：doc_id / 提交体 / 密码信封加密
PROXY_STATUS.md               代理接入现状：白名单已通、卡在 611 代理认证、re_type/origin_ip 真相
BLOKS_CONFIRM.md              ★ 邮箱确认的 Bloks 提交（抓到了完整请求结构）
FIRST_ACCOUNT.md              ★ 第一次真实注册成功的完整记录（legacy 表单通道）
FACE_RISK.md                  ★ 人脸/证件何时出现、提交前打分、提交后探测
TRYING.md                    先试 Python：分层跑法、两个待解锁（白名单 / api_key）
try_register.py              ★ 分层试跑入口：preflight / env / proxy / sms / register
PREP.md                      准备清单 + 纯协议模拟设备环境的七层模型 + JA3 修法
APK_ANALYSIS.md              ★ 要不要分析 fb.apk：10 问对应 APK 里哪里、命令、静态能/不能答什么
FEASIBILITY.md               纯协议可行性评估：A/B/C 三档、模拟器为何必死、四阶段路线
apk/
  analyze.sh                 ★ 一条命令扫 APK：端点/协议头/doc_id/attestation/绑定 → 出 verdict
  docids.py                  ★ 名字↔doc_id 按字节邻近度配对（实测可用）
  work/ report/              解包与报告输出
fb_reg_protocol.md           协议拆解（Web 表单流 / Android b-graph 流 / 参数表 / 风控因子）
capture/
  RUNBOOK.md                 ★ 抓包运行手册：设备清单、改机、frida、人脸验证(570)分级处置
  frida_okhttp_logger.js     ★ 首选：frida 直接打印注册请求明文，不用证书不怕 pinning
  frida_unpin.js             mitmproxy 方案用的 SSL unpinning
  fb_reg_capture.py          mitmproxy addon：自动分类注册请求 -> data/protocol_template.json
  reset_device.sh            ★ 改机脚本：整组指纹自洽替换 + 备份 + 回滚
core/
  email_pool.py              ★ 虚拟邮箱池：catch-all/plus/落地目录/Mailpit + FB 确认邮件解析
  web_reg.py                 ★ 按 bundle 原文拼 CAARegistrationFormSubmitMutation 提交体 + #PWD_BROWSER 加密
  proxy_udeal.py             ★ udealproxy 取池 + 错误分类 + 探活 + 地理对齐 + 写 data/proxies.txt
  sms_herosms.py             ★ hero-sms 接码协议（getNumber/getStatus/setStatus/getPrices）
  device_env.py              ★ 七层设备环境构造 + 一致性校验（发请求前自检）
  attest_check.py            ★ 闸门判定：扫抓包，输出 FEASIBLE / BLOCKED_FOR_PURE_PROTOCOL
  analytics_tags.py          X-FB-Request-Analytics-Tags 解/编 + 时间戳重写（不猜结构）
  channels.py                双通道路由 + 设备 bridge 接口
  fingerprint.py             设备指纹池（型号/build/屏幕/运营商成组，不可随机拼）
  providers.py               邮箱 / 接码 / 代理 三个抽象接口
  client.py                  注册协议客户端（web_register / mobile_register / confirm）
  scheduler.py               状态机 + 配额 + 熔断 + 结果落库
tools/cred_matrix.py         ★ 代理账密矩阵测试（一次测清哪种组合能通）
tools/ja3proxy/              Go+utls 本地代理，把 TLS 指纹换成 OkHttp（HelloAndroid_11_OkHttp）
run.py                       CLI 入口
data/                        模板、号池、结果（已给 .sample）
```

## 依赖（一次性）

```bash
python3 -m venv .venv && .venv/bin/pip install requests brotli pysocks pynacl
```

`brotli` 不装会拿到乱码页（token 全 MISS）；`pysocks` 用于 `socks5h://`；`pynacl` 用于密码信封加密。

## 现在就能跑的三条

```bash
python3 try_register.py --stage preflight --country US --proxy-num 10 --herosms-key KEY
python3 try_register.py --stage proxy --proxy-num 20 --want-country US
python3 core/sms_herosms.py --key KEY --balance
```

细节见 TRYING.md。

## 快速开始

```bash
python3 -m venv .venv && .venv/bin/pip install requests
cp data/proxies.txt.sample data/proxies.txt      # 灌你的 IP（住宅/移动优先）
cp data/email.json.sample data/email.json        # 自有域名 catch-all + IMAP
cp data/phones.txt.sample data/phones.txt        # 手工号池兜底

# 1) 只装配，不发请求 —— 先确认指纹/姓名/生日/UA 都自洽
.venv/bin/python run.py --mode mobile --country US --count 3 --dry

# 2) 抓一次真机协议（见 capture/RUNBOOK.md 第 2 节）
adb push capture/reset_device.sh /data/local/tmp/
adb shell "su -c 'sh /data/local/tmp/reset_device.sh --profile 1 --country US'"
adb reboot
adb shell "su -c '/data/local/tmp/frida-server &'"
frida -U -f com.facebook.katana -l capture/frida_okhttp_logger.js --no-pause

# 3) 闸门判定：纯协议到底可不可行
.venv/bin/python core/attest_check.py data/capture/live_*.log

# 4) 抓到的 log 转成模板后正式跑（auto = 协议优先、掉线切设备）
.venv/bin/python run.py --mode mobile --channel auto --country US --count 1
```

## 起点判断（重要）

你在 App 里自己注册就吃人脸 → 说明 **当前设备的指纹 / IP / 号段 已被打标**。
在同一个环境里继续抓，只会抓到 570 的响应。正确顺序是：

1. 先 `reset_device.sh` 改机（整组指纹换掉，不是只换 device_id）
2. 换出口 IP（住宅/移动，且国家 = 指纹国家 = 号段国家）
3. 换一个没注册过 FB 的号
4. 再抓。三变量法定位是哪一项在挂（见 RUNBOOK §5）

## 空转约束

- 默认 `--dry` 不发请求；真实跑请自行确认号源/代理配额
- `scheduler.py` 连续 10 次且成功率 < 15% 自动熔断，防止把号段和 IP 烧穿
- 每步都写 `data/results.jsonl`，成功号写 `data/accounts.jsonl`（含 cookies）
