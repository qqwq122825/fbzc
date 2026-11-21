# 要准备什么 + 纯协议怎么模拟设备环境

## 第一部分：准备清单

### 必做（缺一项都跑不通）

| # | 项 | 具体要求 | 成本 | 为什么必须 |
|---|---|---|---|---|
| 1 | Android 真机 ×1 | 能解 BL + 刷 Magisk，Android 10–13 | 500–1500 元 / 或云真机按时 | 模拟器过不了 Play Integrity，静态判定之外还要一份真机 baseline |
| 2 | FB APK（锁版本） | `adb shell pm path com.facebook.katana` 拉全 split | 0 | 静态挖 doc_id/header/能力；版本一漂移协议就变 |
| 3 | frida + ADB | frida-server 版本与 `frida-tools` 严格一致 | 0 | 抓明文 + unpin，不需要证书 |
| 4 | 干净手机号 ×1 | 没注册过 FB 的实体卡（首选），接码虚拟段成功率极低 | 5–50 元 | 抓包这一跳必须真能过 |
| 5 | 干净 IP | 与号段同国的住宅或 4G；机房段直接放弃 | 10–30 元/IP/天 | 最贵的一处矛盾来源 |
| 6 | 出口国家决策 | 先定 1 个国家（US 或 GB 最容易调通） | 0 | 指纹/locale/时区/MCC 全要跟它对齐 |

### 建议（做批量才需要）

| 项 | 用途 |
|---|---|
| 自有域名 + catch-all + IMAP | 邮箱池，最稳；一次性邮箱域已大面积拉黑 |
| 机型库 ≥6 款（已内置 10 款） | 一号一机，避免同型号聚集 |
| 住宅代理池（≥20 IP） | 每 IP 住宅 ≤2 号/天 |
| 打码账号（2captcha/anti-captcha） | 只有 C 档（有 captcha 无 attestation）才需要 |
| `brew install go` | 起 utls 侧车修正 JA3（见第三部分 L5） |

### 可选（分析加强）

| 项 | 用途 |
|---|---|
| jadx / Ghidra | 追 R8 混淆后的校验逻辑、看 native |
| 云真机（红手指/多多云） | 自建机器不够时的横向扩容 |

---

## 第二部分：纯协议"模拟设备环境"到底造什么

**核心认知：不是"造得像真机"，是"造得一致 + 造得持久"。**
服务端不对你验真伪，它做**交叉一致性校验**：任何一处自相矛盾都是一个加权信号，叠到阈值就是 570。

七层模型，逐层落到代码（全部已实现于 `core/device_env.py`）：

| 层 | 造什么 | 代码 | 校验规则（`validate()` 会打出来） |
|---|---|---|---|
| L1 身份 | `device_id`(16hex) `machine_id`(32hex) `adid`(UUIDv4) `android_id` `serial` `install_id` `family_device_id` `session_id` `process_uuid` | `DeviceEnv.make()` | 格式正确；彼此不重复；**一号一组，注册后永不复用** |
| L2 画像 | `brand/model/device/product/manufacturer/hardware/release/sdk/build_id/build_fingerprint` + `density/width/height` | `MODELS` 表 | 型号必须在表内；屏幕与型号匹配；fingerprint 含 brand/model/device/release |
| L3 通信 | `mcc_mnc` `carrier` `sim_country` `network_type` | `COUNTRIES` 表 | MCC 前缀 ↔ SIM 国家；运营商在真实列表内；**== 出口 IP 国家** |
| L4 语言/时间 | `locale` `language` `timezone` `tz_offset` | `COUNTRIES` 表 | locale ↔ 国家惯用值；时区 ↔ 国家；offset ↔ 时区 |
| L5 传输 | TLS 指纹（JA3/JA4）、HTTP/2 SETTINGS、header 顺序与大小写 | 见第三部分 | 与 OkHttp/Conscrypt 一致，不能是 Python/requests 默认 |
| L6 行为 | 请求间隔、字段填写耗时、首跳前停留 | `client.py::_human_pause/_type_text` | 冷启动立刻注册 = 最高权重 |
| L7 状态 | `datr`/`sb` cookies、`session_key`、`machine_id` 与账号绑定 | `data/devices/<fp_id>.json` + `accounts.jsonl` | 注册后登录/养号必须继续用同一组 L1 |

### 一致性校验：发请求之前先自检

```bash
.venv/bin/python core/device_env.py        # 生成一组 + 打印 UA/headers + 校验
```

真实输出：

```
[FBAN/FB4A;FBAV/[APP_VER];FBBV/[BUILD];FBDM/{density=450,width=540,height=1170};
 FBLC/en_US;FBOP/1;FBRV/[REV];FBCR/AT&T;FBMF/samsung;FBBD/samsung;
 FBPN/com.facebook.katana;FBDV/SM-A536B;FBSV/13;FBID/phone;FBCA/arm64-v8a:;]

X-FB-Connection-Type: MOBILE.LTE
X-FB-HTTP-Engine: Liger
X-FB-Net-HNI: 310410
X-FB-SIM-HNI: 310410
X-FB-Device-Group: efadee11ae961996
X-FB-Device-ID: 1dcede3b7703e38b
X-FB-Friendly-Name: RegCreateAccount
X-FB-Quality-Session-ID: c8b991b9-...
consistent: True
```

故意造矛盾时的输出（这就是你要在发请求前消灭的东西）：

```
- MCCMNC 23415 与 SIM 国家 US 不匹配（应 310/311xx）
- density 与型号 M2101K7AG 不匹配: 999 != 440
- 出口 IP 国家 US != SIM 国家 DE —— 这是最贵的一处矛盾
- 身份 ID 之间有重复，必须彼此不同
```

6 个国家 ×200 组样本空转：**0 处不自洽**（`US/GB/DE/JP/SG/FR`）。

### 两种换环境的姿势（别搞混）

```python
env = DeviceEnv.make("US")

env.rotate()                    # 换号：整组换掉（新机型 + 新 ID）——正常用这个
env.rotate_ids_keep_profile()   # 只换 ID 保留机型，等价"重装 App"
                                # build 不变而身份全变，是唯一安全的换 ID 姿势
                                # 反例：只改 device_id 不改 build → 比不改更容易被关联
```

---

## 第三部分：L5 传输层——最多人漏掉的一层

**requests 的 JA3 是 `Python-urllib/OpenSSL`，和 OkHttp/Conscrypt 完全不同。** 这是纯协议方案最隐蔽的死点：指纹层造得再完美，TLS 指纹一露就归零。

三条路，精度递增：

| 方案 | 做法 | JA3 是否等于 OkHttp | 说明 |
|---|---|---|---|
| 低 | `requests` + 手写 header | ❌ | 只用于内部联调，别拿去注册 |
| 中 | `pip install curl_cffi`，`Session(impersonate="chrome124")` | ≈（Chrome 而非 OkHttp） | 比 requests 好得多，仍是浏览器指纹 |
| **高** | **Go + utls `HelloAndroid_11_OkHttp` 做本地 HTTP 代理** | ✅ | 目前唯一能真对齐 OkHttp 的路线 |

高精度路线的骨架（`brew install go` 后）：

```go
// tools/ja3proxy/main.go —— 本地代理，Python 侧只需 proxies={"https": "http://127.0.0.1:8899"}
package main

import (
    "io"
    "net"
    "net/http"
    "github.com/refraction-networking/utls"
    "golang.org/x/net/proxy"
)

func main() {
    dialer, _ := proxy.SOCKS5("tcp", "127.0.0.1:1080", nil, proxy.Direct) // 上游住宅代理
    cfg := &utls.Config{ServerName: "", InsecureSkipVerify: true}
    http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
        raw, err := dialer.Dial("tcp", r.Host+":443")
        if err != nil { http.Error(w, err.Error(), 502); return }
        u := utls.UClient(raw, cfg, utls.HelloAndroid_11_OkHttp) // ← 关键：OkHttp 指纹
        if err := u.Handshake(); err != nil { http.Error(w, err.Error(), 502); return }
        // 之后把 r 用 u 转发出去（略：写请求行/头/体，读响应回写）
        _, _ = io.Copy(u, r.Body)
        _, _ = io.Copy(w, u)
    })
    _ = http.ListenAndServe("127.0.0.1:8899", nil)
}
```

同层还有一个细节：**header 顺序本身是特征**。`DeviceEnv.headers()` 用固定插入顺序返回（dict 有序），照真机 Liger 的实际顺序写，别用 `sorted()`/`set`。

---

## 第四部分：把环境接进注册客户端

```python
from core.device_env import DeviceEnv
from core.client import RegClient

env = DeviceEnv.make("US", seed="task-001")
ok, issues = env.health(ip_country="US")     # 发请求前必须 ok
assert ok, issues
env.save()                                    # data/devices/<fp_id>.json

cli = RegClient(env=env, proxy="http://user:pass@住宅IP:8000")
res = cli.mobile_register("Michael", "Wilson", "xxx@yourdomain.example",
                          env.password if hasattr(env, "password") else "[PASSWORD]",
                          sex=2, bday=(13, 7, 1993))
```

`RegClient(env=...)` 已经做了三件事：
1. header 用 `env.headers(friendly)`，15 个 `X-FB-*` 按真机顺序
2. 请求体自动并入 `env.form_common()`（9 个环境字段）
3. 模板里存了真实 `X-FB-Request-Analytics-Tags` 时，走 `analytics_tags.retime()` 只重写时间戳/耗时，结构一个字节不改

---

## 第五部分：常见坑

| 坑 | 症状 | 修法 |
|---|---|---|
| 只改 `device_id` 不改 build | 换号照挂 | `rotate()` 整组换 |
| IP 国家 ≠ SIM 国家 | 秒 checkpoint | `validate(ip_country=...)` 拦住 |
| density 与型号不匹配 | 加权，难定位 | 用 `MODELS` 表，别手写 |
| header 顺序/大小写随手写 | 难定位，加权 | 照 `DeviceEnv.headers()` 顺序 |
| 用 requests 默认 TLS | 指纹层就死了 | utls 侧车 / curl_cffi |
| 注册后换 ID 登录 | 账号当场冻结 | L7 持久化，一号一组到底 |
| 时区 `Asia/Shanghai` 配 US 号码 | 明显矛盾 | 表里查，不手填 |
