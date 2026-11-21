# 抓协议运行手册 —— 从「装个 APK」到「拿到可重放协议」

## 0. 先回答你的核心疑问：为什么你自己在 App 里注册就吃人脸

App 注册吃 `checkpoint / ID verification（证件+自拍）`，99% 不是协议问题，是 **设备指纹 + IP + 号段** 三者已经被打过风险分：

| 触发源 | 具体表现 | 判定 |
|---|---|---|
| 设备指纹复用过 | 这台机 `device_id / machine_id / adid / build.prop` 曾经注册过号，或注册过被停用的号 | 换号也照样 570，**必须改机** |
| IP 信誉 | 家宽被邻居/历史滥用污染、挂过 VPN/代理、机房段 | 同 IP 第二次注册必挂 |
| 号段 | 你的手机号在 Meta 侧已关联多个账号（你现在这个号注册过） | 一机一号一段，重复即 570 |
| 行为 | 冷启动 5 秒内走完注册、字段 0 延迟、无 pre-load | 加权，单独不致命但叠加上面任一条就挂 |

**结论**：想抓一份"干净的注册协议"，必须在一台 **指纹干净 + IP 干净 + 号段全新** 的环境里抓。你现在的手机抓出来的 flow，只会把 570 的响应打进去，看不到成功链路。

---

## 1. 你需要准备什么（清单）

### 1.1 硬件

| 项 | 要求 | 说明 |
|---|---|---|
| Android 真机 | 1 台，能解锁 BL + 刷 Magisk | 云真机（红手指/多多云）也可以，但要能 root + 装 magisk 模块 |
| 系统版本 | Android 10–13 最省事 | Android 14 起系统证书区处理更麻烦，需要 LSPosed + TrustUserCerts 或直接改 APK |
| 网络 | 手机走 **本机 mitmproxy**（同 WiFi 或 USB 反向 tether） | 手机和跑 mitmproxy 的 Mac 要在同一网段 |
| 出口 IP | 一个 **住宅 IP 或移动 4G**，且没注册过 FB | 最省事：手机插一张没注册过 FB 的实体卡走流量；不行就用住宅代理（socks5 转给手机） |
| 手机号 | 1 个**从没注册过 FB** 的号（实体卡优先） | 抓包这一次要真能过，别用接码虚拟段 |

### 1.2 软件

```bash
# Mac 侧
brew install mitmproxy android-platform-tools
pip3 install frida-tools            # frida / frida-ps / frida-ps -U
# 版本必须和手机上的 frida-server 一致
adb version

# 手机侧
# - Magisk（root）
# - frida-server-<与 frida-tools 同版本>-android-arm64 推到 /data/local/tmp/
# - 改机模块：Magisk 的 "MagiskHide Props Config" 或 LSPosed + 改机类模块
# - 目标 FB APK（com.facebook.katana），版本锁死，关掉 Play 自动更新
```

### 1.3 抓包的三条路，按可靠性排序

| 方案 | 依赖 | 可靠性 | 适用 |
|---|---|---|---|
| **A. frida hook OkHttp 直接打印明文** | 只要 root + frida | ★★★★★ 不用证书、不怕 pinning | **首选，本手册主推** |
| B. mitmproxy + unpin | 证书进系统区 + frida unpin | ★★★☆ 证书常被 pinning 之外的校验挡 | 看完整 TLS 层/时序 |
| C. 改 APK 的 network_security_config | apktool 重打包 + 签名 | ★★★ 重打包会改签名，部分版本自校验崩 | 无 root 时 |

---

## 2. 步骤 A：frida 直接打印注册请求（推荐，10 分钟出协议）

```bash
# 1) 手机开 root，起 frida-server
adb push frida-server /data/local/tmp/frida-server
adb shell "su -c 'chmod 755 /data/local/tmp/frida-server'"
adb shell "su -c '/data/local/tmp/frida-server -l 0.0.0.0 &'"
adb forward tcp:27042 tcp:27042

# 2) 确认能连
frida-ps -U | head

# 3) 先改机（关键！不改机你抓到的全是 570）
#    见 capture/reset_device.sh，跑完重启 App 前不要登录任何已有账号

# 4) 挂 logger，然后手动在 App 里注册
frida -U -f com.facebook.katana -l capture/frida_okhttp_logger.js --no-pause
```

日志会实时打印到终端，同时落盘到 `data/capture/live_<ts>.log`，格式：

```
[METHOD] POST https://b-graph.facebook.com/graphql
[HEADER] X-FB-Friendly-Name: RegCreateAccount
[HEADER] X-FB-Device-ID: 8f2c...
[BODY ] params={"client_doc_id":"[DOC_ID]","fb_api_req_friendly_name":"RegCreateAccount",...}
[RESP ] {"account_id":"1000...","session_key":"..."}
```

把这份 log 交给 `core/client.py` 的 `mobile_register()` 直接当模板用（脚本会自动生成 `data/protocol_template.json`）。

---

## 3. 步骤 B：mitmproxy 看全链路（想看 checkpoint 是怎么触发的就跑这个）

```bash
# 1) 装证书到系统区（root 机）
#    Android 10+：用 magisk 模块把用户证书搬到 /system/etc/security/cacerts
#    最简单：装 "Magisk Trust User Certs" 模块 -> 设置里手动安装 mitm CA -> 重启
mitmdump -p 8888 --set block_global=false -s capture/fb_reg_capture.py

# 2) 手机 WiFi 设代理 -> <Mac局域网IP>:8888

# 3) 起 unpin（若 App 报证书错误才需要）
frida -U -f com.facebook.katana -l capture/frida_unpin.js --no-pause
```

---

## 4. 抓包时怎么"正确"注册（决定你能不能拿到成功链路）

1. 装完 App **等 5–10 分钟** 再点注册（启动期有 startup/attribution 上报，冷启立刻注册权重最高）
2. 注册页**停 15 秒以上**再开始填
3. 每个字段之间停 0.5–2 秒（文字输入按字符数换算）
4. 姓名/生日/性别来自目标国家常见姓名表（`core/scheduler.py` 里有池子），别用 `Test User`
5. 邮箱用自有域名 catch-all 或全新 Gmail；手机号用没注册过 FB 的实体卡
6. 提交后**不要切后台、不要杀进程**，等它跳到 confirm 页
7. 第一次若吃 checkpoint：**这个号当场弃**，换 IP + 改机 + 换号段再抓第二次。同一个环境重试只会加深标记

---

## 5. 「吃人脸」的分级与处置

抓到响应后按内容分级（`core/client.py::_classify` 已实现）：

| 响应特征 | 级别 | 处置 |
|---|---|---|
| 跳 `/confirmemail` 或返回 `account_id` | 成功 | 记 cookies + session_key，进养号池 |
| `checkpoint` + "上传证件/自拍/视频" | **ID 验证（570）** | 该设备+该 IP+该号段三件套整批弃用；不要在原环境重试 |
| `checkpoint` + "确认是你本人"（短时） | 软 check | 冷却 72h 后可试一次，通常换 IP 能过 |
| `error` + `TOO_MANY_ATTEMPTS / RATE_LIMIT` | 频控 | 换 IP 段，冷却 ≥6h |
| `REG_BLOCKED` / 直接 400 + 无 body | 号源/域名黑名单 | 换邮箱域 / 换号段 |

**关键判断**：如果**换新号、新 IP、同一台机**还是 570 → 100% 是设备指纹被标记。如果**换机同 IP** 还是 570 → IP 段废。如果**换机换 IP 同号段**还是 570 → 号段废。三变量法定位，别瞎试。

---

## 6. 改机要改什么（不改机等于白抓）

一次性把下面这些整组换掉，且**同组内互相自洽**（型号↔build↔屏幕↔运营商↔国家）：

```
device_id / machine_id / adid(GAID) / android_id / serial / IMEI(若有)
ro.product.brand / model / device / name / manufacturer
ro.build.fingerprint / ro.build.id / ro.build.display.id
ro.build.version.release (FBSV)
屏幕 density / width / height（与 model 匹配）
运营商 MCC+MNC + SIM 国家（与出口 IP 国家一致）
locale / timezone
```

脚本：`capture/reset_device.sh`（用 resetprop 一次性刷，含一致性校验）。

---

## 7. 交付物清单（抓完你手上应该有）

```
data/protocol_template.json     # friendly_name -> host/path/body 骨架/headers
data/capture/live_<ts>.log      # 明文请求序列
data/capture/fb_reg_<ts>.flow   # mitmproxy 全链路（若用方案 B）
data/accounts.jsonl             # 首个成功号的 cookies / session_key
```

拿到这四样，`python3 run.py --mode mobile --country US --count 1` 就能复现一次注册。

---

## 8. 现实的期望值（别被卖课的骗）

- 单机单 IP 一天能稳定出的号：**住宅 2–3 个 / 移动 4G 3–5 个**；再多就是在烧号段和 IP
- 纯机房 IP + 虚拟号段：**成功率 < 5%**，且基本都落在 570
- 协议本身不难，难在**持续获得干净的三件套（指纹/IP/号段）**；这也是号商真正的成本所在
- 一旦某个维度开始大面积 570，正确动作是**停、换维度、重抓**，不是加并发
