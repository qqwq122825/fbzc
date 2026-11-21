# 只用虚拟邮箱，不用手机号

## 一、结论：这条路成立（有实证）

从当期 bundle 里核实：

```
data/bundles/b4.js
  contactpoint_type 枚举: "EMAIL" / "PHONE"        ← EMAIL 是合法值
  reg_data.contactpoint      = {sensitive_string_value: "<邮箱>"}
  reg_data.contactpoint_type = "EMAIL"
```

提交体里**没有任何字段强制要求手机号**，验证走邮件里的确认链接/验证码。
所以我们不需要接码平台，只要一个能收信的域名 + 一个能读信的入口。

## 二、你自建邮件服务器需要满足什么

### 2.1 收信侧（必须）

| 项 | 要求 | 说明 |
|---|---|---|
| MX | 指向你的邮件服务器 | `@ IN MX 10 mail.yourdomain.com` |
| catch-all | 任意 local part 都投递到同一信箱 | 否则每个号都要提前建账户，做不了批量 |
| IMAP 或落地目录 | 能按收件人查信 | 我们两种都支持（`--imap-*` / `--mail-sink`） |
| SMTP 出站 | **不需要** | 我们只收不发 |

### 2.2 送达率（决定成败）

| 项 | 要求 | 为什么 |
|---|---|---|
| SPF | `v=spf1 mx -all` 之类 | 不进垃圾箱的前提 |
| DKIM | 配 2048 位密钥并发布公钥 | facebookmail 的投递判定会看 |
| DMARC | `v=DMARC1; p=none; rua=...` | 先 p=none 观察，别一上来 reject |
| PTR / rDNS | 服务器 IP 反解到 mail.yourdomain.com | 住宅 IP 发信更容易被判垃圾，我们只收信问题不大 |
| 域名年龄 | **越老越好** | 新注册域名当天就用，投递率很低 |

### 2.3 域名信誉策略（重要）

- **别用一次性邮箱域名**（mailinator/guerrillamail 系）——已被大面积拉黑，命中率极低
- 新域名先做冷启动：手动在几个大站（GitHub/Reddit）注册验证能正常收信，再上量
- 同一域名并发别猛：第一天 ≤30 个地址，逐日递增
- 建议准备 **2–3 个域名轮换**，某个域名开始大面积 570 就整域停用

## 三、三种接法（选一个）

代码在 `core/email_pool.py`，一个入口三种 reader：

### A. catch-all（推荐）
```bash
python3 core/email_pool.py --mode imap \
  --host imap.yourdomain.com --user catchall@yourdomain.com --password 'PASS' \
  --domain yourdomain.com --addr '' --wait 120
# 不给 --addr 就会随机生成 fbxxxxxxxxxx@yourdomain.com 并开始等信
```

### B. plus 寻址（不想配 catch-all 时最省事）
```bash
python3 core/email_pool.py --mode imap --host imap.yourdomain.com \
  --user catchall@yourdomain.com --password 'PASS' --domain yourdomain.com \
  --plus-base catchall --wait 120
# 生成 catchall+fbXXXXXXXX@yourdomain.com，按 Delivered-To 精确匹配
```

### C. 落地目录 / Mailpit（自建开发环境最省心）
```bash
python3 core/email_pool.py --mode sink --path /var/mail/inbox --wait 60
python3 core/email_pool.py --mode mailpit --api http://127.0.0.1:8025 --wait 60
```

## 四、解析能力（已实测）

```
输入: FB 确认邮件（HTML，含 &amp; 转义、Delivered-To、quoted-printable 残留）
输出: {"link":"https://www.facebook.com/confirmemail.php?e=abc123&c=84213",
        "code":"84213","subject":"...","from":"Facebook <security@facebookmail.com>"}
```

三种 reader 的「按地址精确匹配」也验证了：
- 目标地址 → 命中自己的信
- 不存在的地址 → 超时，不会串到别人的信

## 五、接进注册链路

```bash
# 全链路：邮箱池取址 -> 提交注册 -> 等确认信 -> 提取 code/link
python3 try_register.py --stage register --country US --contact email \
  --email-domain yourdomain.com \
  --imap-host imap.yourdomain.com --imap-user catchall@yourdomain.com --imap-pass 'PASS' \
  --mail-wait 180 --yes
```

`try_register.py` 里已经接好：提交成功后如果落在 `PENDING_CONFIRM`，
自动等信 → 抽 code → 调 `cli.confirm(code)`。

## 六、这套方案的真实风险（先说清楚）

| 风险 | 表现 | 应对 |
|---|---|---|
| 域名新、投递差 | 等不到信 / 进垃圾箱 | 冷启动养域、配全 SPF/DKIM/DMARC、先手工验证可达 |
| Meta 对纯邮箱注册风控更严 | email 注册比手机更容易落 570 | 降速（每 IP ≤1–2 个/天）、住宅 IP、指纹一号一组 |
| 注册后要求补手机 | checkpoint 阶段要 binding phone | 该号当场弃；或预留少量真卡做补绑 |
| 无手机号 = 申诉困难 | 号被停基本救不回 | 只做「够用即弃」的量，别指望长期持有 |
| 域名被拉黑 | 整域所有地址都 570 | 多域轮换，命中即停该域 |

## 七、建议的最小验证顺序

```bash
# 1) 服务器还没好？先用本地 sink 把「解析 + 提交」这两段跑通
python3 try_register.py --stage email --mail-sink /tmp/mailsink --email-domain yourdomain.example

# 2) 服务器好了，先手工发一封测试信验证 IMAP 能读到
python3 core/email_pool.py --mode imap --host imap.yourdomain.com \
   --user catchall@yourdomain.com --password 'PASS' --addr test1@yourdomain.com --wait 120

# 3) 确认能读信后，跑一次真实注册（会消耗一个地址 + 一次提交）
python3 try_register.py --stage register --country US --contact email \
   --email-domain yourdomain.com --imap-host imap.yourdomain.com \
   --imap-user catchall@yourdomain.com --imap-pass 'PASS' --yes
```
