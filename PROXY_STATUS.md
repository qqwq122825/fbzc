# 代理接入现状（白名单已加之后）

## 一、结论：白名单生效了，现在卡在「代理认证」

| 层 | 结果 | 证据 |
|---|---|---|
| 取 IP 接口 | ✅ 通了 | `num=3` 正常返回，不再出现 `白名单不存在` |
| TCP 到网关 | ✅ 通 | `create_connection` 1–2ms 成功 |
| 代理认证 | ❌ **611 Authentication Required** | 抓到原文 `HTTP/1.1 611 Authentication Required\r\nProxy-...` |
| 之后的连接 | ❌ 0 字节静默关闭 | 连上即被关闭，什么都不回（典型行限/标记） |
| 出口 IP | 103.151.173.212（HTTP 与裸 TCP 一致） | 裸 TLS 直连 ipify 读回同值，排除"NAT 两套出口" |

**611 这个码是关键**：它是「认证」错误，不是「白名单」错误。说明 `custom=29776` / `password=CbXSOw`
这一对是 **API 取 IP 用的**，**不是连代理端口用的账密**。

## 二、接口返回的两种形态（重要，之前不知道）

```bash
# re_type=1  → 纯文本，一行一个
137.184.119.209:8137
137.184.119.209:8150

# re_type=2/3/4 → JSON，并且带 origin_ip
{"msg":"success","code":200,"data":[
  {"port":"8459","ip":"146.190.52.23","origin_ip":"125.164.252.48"},
  {"port":"8999","ip":"146.190.52.23","origin_ip":"49.125.110.254"}]}
```

**`origin_ip` 才是 FB 看到的出口**，`ip` 只是网关（DigitalOcean 段）。所以：

- `re_type=1` = **机房 IP**（没有 origin_ip）→ 拿去做 FB 必死，别用
- `re_type=2/3/4` = 带住宅/移动 origin 的线路 → 用这个
- 地理对齐必须按 `origin_ip` 查，按网关 IP 查会得出错误国家（已改 `core/proxy_udeal.py`）

## 三、我试过、排除掉的可能

| 尝试 | 结果 | 说明 |
|---|---|---|
| HTTP `CONNECT` + Basic `29776:CbXSOw` | 关闭 | 账密不对 |
| HTTP `CONNECT` 不带认证 | 关闭 | 不是免认证白名单模式 |
| 绝对 URI `GET http://...` | 关闭 | 不是这种代理形态 |
| `socks5h://` 握手（含 pysocks） | `Received invalid version` | 不是 SOCKS |
| `socks4/4a` | 关闭 | 不是 SOCKS |
| `https://` 代理（TLS 到端口） | `tlsv1 alert protocol version` | 不是 TLS 代理 |
| 换 4 个网关 IP、10+ 个端口 | 全关闭 | 不是单个端口坏 |

## 四、需要你确认的两件事

1. **面板里的「代理账密」是什么？**
   通常和 API 的 `custom/password` **不是同一对**，可能在：
   - 「代理设置 / 终端账号 / 认证账号」一栏
   - 或者面板开了「白名单模式」时，代理免账密、只认 IP

2. **白名单里加的是哪个 IP？**
   要加 **`103.151.173.212`**（脚本调用方的真实出口，HTTP 与裸 TCP 已确认一致）。

拿到账密后一条命令验证，不用瞎试：

```bash
python3 tools/cred_matrix.py --from-api --num 2 --re-type 2 \
        --try "29776:CbXSOw" --try "你的代理账号:你的代理密码" --try none

# 输出 OK 的那一行就是可用组合，直接写进调度器：
#   --proxy-user '<u>' --proxy-pass '<p>'
```

## 五、冷却提醒

我这边为定位问题在短时间内打了 15+ 次握手，网关现在对 `103.151.173.212` 是**静默关闭**状态
（连 611 都不回了）。这类网关通常 10–30 分钟自动解封。**你验证时别连打**，用
`tools/cred_matrix.py` 的默认 2 秒间隔就够。

## 六、代码已经按这些发现改好了

- `core/proxy_udeal.py`
  - 解析 JSON 里的 `origin_ip`，地理对齐按它查
  - `handshake()` 单次握手分类：`OK / AUTH_REQUIRED(611) / CLOSED / TIMEOUT`
  - `diagnose()` 逐层：TCP → CONNECT 回包 → 带账密 CONNECT → 出口 IP vs origin_ip
  - `--handshake` / `--diagnose` / `--re-type` / `--proxy-user` / `--proxy-pass`
- `tools/cred_matrix.py`：账密矩阵一次性测完，OK 直接给出可用的 `--proxy-user/--proxy-pass`
