"""udealproxy IP 池客户端 + 探活 + 地理对齐。

对方接口（GET，明文返回，非 JSON）:
    http://usapi.udealproxy.com:7777/iplist
        ?custom=29776&password=***&num=200&re_type=1&lb=1&region=any

实测返回的两种形态：
    成功: 一行一个 ip:port（或 ip:port:user:pass / user:pass@ip:port）
    失败: 纯文本中文错误，例：
          白名单不存在103.151.173.212     <- 调用方出口 IP 没进白名单（最常见）
          参数错误                        <- 缺 custom/password
          404 page not found              <- 路径写错

所以本模块第一件事不是解析，是**错误分类**：把这类纯文本错误翻成可执行的动作。

用法:
    python3 core/proxy_udeal.py --num 10 --probe --country US
    python3 core/proxy_udeal.py --num 200 --probe --write      # 写 data/proxies.txt 给调度器用
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass, field

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
LIST_URL = "http://usapi.udealproxy.com:7777/iplist"

ERRORS = [
    (r"白名单不存在([0-9.]+)", "WHITELIST_MISSING",
     "调用方出口 IP {ip} 不在白名单。去面板把 {ip} 加进 API 白名单（或把脚本放到白名单机器上跑）"),
    (r"参数错误", "BAD_PARAMS", "缺 custom / password，或参数名写错"),
    (r"余额不足|余额为零|过期|到期", "NO_BALANCE", "账户余额/套餐到期，去面板充值"),
    (r"频繁|过快|限流", "RATE_LIMITED", "取 IP 太频繁，降低频率"),
]


@dataclass
class ProxyRow:
    ip: str
    port: int
    username: str = ""
    password: str = ""
    scheme: str = "http"
    origin_ip: str = ""      # 服务商声明的「真实出口」（re_type=2/3/4 才有）
    mode: str = ""           # 探活命中的组合: http+auth / socks5+none ...
    ok: bool = False
    egress_ip: str = ""
    country: str = ""
    city: str = ""
    isp: str = ""
    latency_ms: int = 0
    error: str = ""
    banned_until: float = 0.0
    used: int = 0

    @property
    def url(self) -> str:
        auth = f"{self.username}:{self.password}@" if self.username else ""
        return f"{self.scheme}://{auth}{self.ip}:{self.port}"

    def alive(self) -> bool:
        return self.ok and time.time() > self.banned_until

    def ban(self, seconds: int = 3600, why: str = "") -> None:
        self.banned_until = time.time() + seconds
        if why:
            self.error = why

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d.pop("banned_until", None)
        d["url"] = self.url
        return d


def fetch_raw(num: int = 10, region: str = "any", re_type: int = 1, lb: int = 1,
              custom: str = "29776", password: str = "CbXSOw", timeout: int = 25) -> str:
    params = {"custom": custom, "password": password, "num": num,
              "re_type": re_type, "lb": lb, "region": region}
    r = requests.get(LIST_URL, params=params, timeout=timeout)
    r.encoding = r.encoding or "utf-8"
    return r.text.strip()


def classify_error(text: str) -> tuple[str, str]:
    """把对方的纯文本错误翻成 (code, 动作)。非错误返回 ("", "")。"""
    for pat, code, hint in ERRORS:
        m = re.search(pat, text)
        if m:
            ip = m.group(1) if m.groups() else ""
            return code, hint.format(ip=ip or "[CALLER_IP]")
    return "", ""


def parse_list(text: str, auth_user: str = "", auth_pass: str = "") -> list[ProxyRow]:
    """兼容 4 种行格式；非 ip:port 形状的行直接忽略。"""
    rows: list[ProxyRow] = []
    # 万一哪天返回 json
    if text.startswith(("[", "{")):
        try:
            j = json.loads(text)
            items = j if isinstance(j, list) else (j.get("data") or j.get("list") or [])
            for it in items:
                if isinstance(it, str):
                    rows.extend(parse_list(it, auth_user, auth_pass))
                elif isinstance(it, dict):
                    ip = str(it.get("ip") or it.get("host") or "")
                    port = it.get("port") or 0
                    if ip and port:
                        rows.append(ProxyRow(ip, int(port),
                                             str(it.get("username") or auth_user),
                                             str(it.get("password") or auth_pass),
                                             str(it.get("scheme") or "http"),
                                             origin_ip=str(it.get("origin_ip") or "")))
            return rows
        except Exception:
            pass

    for line in re.split(r"[\r\n,;]+", text):
        line = line.strip()
        if not line or not re.search(r"\d", line):
            continue
        scheme = "http"
        m = re.match(r"^(socks5h?|socks4|https?)://(.+)$", line, re.I)
        if m:
            scheme, line = m.group(1).lower(), m.group(2)
        user = auth_user
        pw = auth_pass
        if "@" in line:
            cred, hostpart = line.split("@", 1)
            if ":" in cred:
                user, pw = cred.split(":", 1)
            line = hostpart
        parts = re.split(r"[:\s]+", line)
        if len(parts) >= 4:                       # ip:port:user:pass
            user, pw = parts[2], parts[3]
        if len(parts) < 2:
            continue
        ip, port = parts[0], parts[1]
        if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip) or not port.isdigit():
            continue
        rows.append(ProxyRow(ip, int(port), user, pw, scheme))
    return rows


def geo(ip: str, timeout: int = 8) -> dict:
    for url in (f"http://ip-api.com/json/{ip}?fields=status,countryCode,city,isp",
                f"https://ipinfo.io/{ip}/json"):
        try:
            r = requests.get(url, timeout=timeout)
            j = r.json()
            if j.get("status") == "fail":
                continue
            return {
                "country": (j.get("countryCode") or j.get("country") or "").upper(),
                "city": j.get("city") or "",
                "isp": j.get("isp") or j.get("org") or "",
            }
        except Exception:
            continue
    return {"country": "", "city": "", "isp": ""}


def _try_url(row: ProxyRow, scheme: str, auth: bool, timeout: int) -> tuple[bool, str, str]:
    """返回 (成功, 出口IP, 错误)"""
    cred = f"{row.username}:{row.password}@" if (auth and row.username) else ""
    url = f"{scheme}://{cred}{row.ip}:{row.port}"
    try:
        r = requests.get("https://api.ipify.org?format=json",
                         proxies={"http": url, "https": url}, timeout=timeout)
        ip = (r.json() or {}).get("ip", "")
        return (bool(ip), ip, "" if ip else "empty egress")
    except Exception as e:
        return (False, "", type(e).__name__ + ": " + str(e)[:70])


AUTH_RE = re.compile(rb"HTTP/1\.[01] (\d{3})[^\r\n]*")


def handshake(row: ProxyRow, timeout: int = 8, use_auth: bool = False) -> tuple[str, str]:
    """单次握手判定（不重试，避免行限）。返回 (状态, 说明)。

    实测本账号：**SOCKS5 + IP 白名单（免账密）**
      SOCKS5 问候 \x05\x01\x00  ->  \x05\x00   = 免认证通过
                                ->  \x05\x02   = 要求账密
                                ->  \x05\xff   = 无可用认证方式
      HTTP CONNECT 打到 SOCKS 口    ->  非 SOCKS 响应/关闭（协议选错，不是账密错）
    """
    import base64
    import socket

    def _tcp():
        return socket.create_connection((row.ip, row.port), timeout=timeout)

    # ---- L1 SOCKS5 问候
    try:
        sk = _tcp()
    except Exception as e:
        return "TCP_FAIL", f"{type(e).__name__}: {str(e)[:60]}"
    try:
        method = 2 if (use_auth and row.username) else 0
        if method == 2:
            sk.sendall(bytes([5, 2, 0, 2]))
        else:
            sk.sendall(bytes([5, 1, 0]))
        sk.settimeout(timeout)
        d = sk.recv(16)
    except socket.timeout:
        d = b""
    except Exception:
        d = b""
    finally:
        try:
            sk.close()
        except Exception:
            pass
    if d[:2] == b"\x05\x00":
        return "OK", "SOCKS5 免认证（\x05\x00）"
    if d[:2] == b"\x05\x02":
        return "AUTH_REQUIRED", "SOCKS5 要求账密（\x05\x02）"
    if d[:2] == b"\x05\xff":
        return "NO_METHOD", "SOCKS5 无可用认证方式（\x05\xff）"

    # ---- L2 不是 SOCKS，再试 HTTP CONNECT
    try:
        sk = _tcp()
    except Exception as e:
        return "TCP_FAIL", f"{type(e).__name__}: {str(e)[:60]}"
    try:
        hdr = "CONNECT api.ipify.org:443 HTTP/1.1\r\nHost: api.ipify.org:443\r\n"
        if use_auth and row.username:
            b = base64.b64encode(f"{row.username}:{row.password}".encode()).decode()
            hdr += f"Proxy-Authorization: Basic {b}\r\n"
        sk.sendall((hdr + "\r\n").encode())
        sk.settimeout(timeout)
        data = sk.recv(400)
    except socket.timeout:
        return "TIMEOUT", "无回包"
    except Exception as e:
        return "CLOSED", f"{type(e).__name__}: {str(e)[:60]}"
    finally:
        try:
            sk.close()
        except Exception:
            pass
    if not data:
        return "CLOSED", "0 字节关闭"
    head = data[:120].decode("latin1").replace("\r\n", " | ")
    m = AUTH_RE.match(data)
    code = m.group(1).decode() if m else "?"
    if code.startswith("2"):
        return "OK", head
    if code in ("611", "407"):
        return "AUTH_REQUIRED", head
    return f"HTTP_{code}", head


def diagnose(row: ProxyRow, timeout: int = 10) -> list[tuple[str, bool, str]]:
    """逐层诊断：TCP -> 首字节 -> CONNECT/握手 -> 出口 IP。给服务商报障用这份。"""
    import socket

    steps: list[tuple[str, bool, str]] = []
    # L1 TCP
    t0 = time.time()
    try:
        sk = socket.create_connection((row.ip, row.port), timeout=timeout)
        steps.append(("TCP 连接", True, f"{(time.time()-t0)*1000:.0f}ms"))
    except Exception as e:
        steps.append(("TCP 连接", False, f"{type(e).__name__}: {str(e)[:60]}"))
        return steps
    # L2 首字节：发 CONNECT 后能否收到任何回包
    try:
        sk.sendall(f"CONNECT api.ipify.org:443 HTTP/1.1\r\nHost: api.ipify.org:443\r\n\r\n".encode())
        sk.settimeout(timeout)
        data = sk.recv(200)
        if data:
            steps.append(("HTTP CONNECT 回包", True, data[:60].decode("latin1").replace("\r\n", " | ")))
        else:
            steps.append(("HTTP CONNECT 回包", False, "服务端直接关闭（0 字节）——典型 IP ACL 静默拒绝"))
    except Exception as e:
        steps.append(("HTTP CONNECT 回包", False, f"{type(e).__name__}: {str(e)[:60]}"))
    finally:
        try:
            sk.close()
        except Exception:
            pass
    # L2b 带账密的握手
    state, detail = handshake(row, timeout, use_auth=True)
    steps.append((f"CONNECT + Basic({row.username}:****)", state == "OK", f"{state} {detail}"))
    if state == "AUTH_REQUIRED":
        steps.append(("结论", False, "服务端明确要求代理认证：custom/password 不是代理账密，或需改用白名单模式"))

    # L3 组合探活
    hit = ""
    for scheme in ("socks5h", "http"):
        for auth in (False, True):
            ok, ip, err = _try_url(row, scheme, auth, timeout)
            if ok:
                row.ok, row.egress_ip, row.mode = True, ip, f"{scheme}+{'auth' if auth else 'none'}"
                hit = f"{scheme}+{'auth' if auth else 'none'} -> {ip}"
                break
            steps.append((f"探活 {scheme}+{'auth' if auth else 'none'}", False, err))
        if hit:
            break
    if hit:
        steps.append(("组合探活", True, hit))
        if row.origin_ip:
            steps.append(("出口 vs origin_ip", row.egress_ip == row.origin_ip,
                          f"egress={row.egress_ip} origin={row.origin_ip}"
                          + ("" if row.egress_ip == row.origin_ip else "  ← 不一致，IP 池路由异常")))
    return steps


def probe(row: ProxyRow, timeout: int = 8, do_geo: bool = True) -> ProxyRow:
    t0 = time.time()
    errs: list[str] = []
    for scheme in ("socks5h", "http"):
        for auth in (False, True):
            ok, ip, err = _try_url(row, scheme, auth, timeout)
            if ok:
                row.ok, row.egress_ip = True, ip
                row.mode = f"{scheme}+{'auth' if auth else 'none'}"
                row.latency_ms = int((time.time() - t0) * 1000)
                break
            errs.append(f"{scheme}{'/auth' if auth else ''}: {err}")
        if row.ok:
            break
    if not row.ok:
        row.error = " | ".join(errs)[:160]
        row.latency_ms = int((time.time() - t0) * 1000)
        return row
    if do_geo:
        g = geo(row.origin_ip or row.egress_ip or row.ip)
        row.country, row.city, row.isp = g["country"], g["city"], g["isp"]
    return row


def probe_all(rows: list[ProxyRow], workers: int = 16, do_geo: bool = True) -> list[ProxyRow]:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        return list(ex.map(lambda r: probe(r, do_geo=do_geo), rows))


def write_pool(rows: list[ProxyRow], path: str | None = None,
               kind: str = "residential") -> str:
    """写成调度器吃的格式: url,country,kind"""
    p = path or os.path.join(DATA, "proxies.txt")
    os.makedirs(DATA, exist_ok=True)
    alive = [r for r in rows if r.ok and r.country]
    for r in alive:
        if r.mode.startswith("socks5"):
            r.scheme = "socks5h"
    with open(p, "w", encoding="utf-8") as f:
        f.write("# auto-generated by core/proxy_udeal.py\n")
        f.write(f"# {time.strftime('%F %T')}  alive={len(alive)}/{len(rows)}\n")
        for r in alive:
            f.write(f"{r.url},{r.country},{kind}\n")
    js = os.path.join(DATA, "proxies.json")
    json.dump([r.as_dict() for r in rows], open(js, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    return p


def acquire(num: int = 10, region: str = "any", country: str | None = None,
            do_probe: bool = True, workers: int = 16,
            custom: str = "29776", password: str = "CbXSOw",
            re_type: int = 2, auth_user: str = "", auth_pass: str = "") -> tuple[list[ProxyRow], str]:
    """完整取池：拉取 -> 错误分类 -> 探活 -> 可选按国家过滤。返回 (rows, 错误动作)"""
    text = fetch_raw(num=num, region=region, re_type=re_type, custom=custom, password=password)
    code, hint = classify_error(text)
    if code:
        return [], f"[{code}] {hint}"
    rows = parse_list(text, auth_user=auth_user, auth_pass=auth_pass)
    if not rows:
        return [], f"[EMPTY] 返回内容无法解析，原文前 200 字: {text[:200]!r}"
    if do_probe:
        rows = probe_all(rows, workers=workers)
    if country:
        keep = [r for r in rows if r.country == country.upper()]
        if not keep:
            got = sorted({r.country for r in rows if r.country})
            return [], f"[GEO_MISMATCH] 没有 {country} 出口；本次实际地区: {got or '未知'}"
        rows = keep
    return rows, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--num", type=int, default=10)
    ap.add_argument("--region", default="us", help="us / gb / de / jp / any")
    ap.add_argument("--country", default="")
    ap.add_argument("--custom", default="29776")
    ap.add_argument("--password", default="CbXSOw")
    ap.add_argument("--probe", action="store_true", help="逐条探活 + 查地理")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--write", action="store_true", help="写 data/proxies.txt / proxies.json")
    ap.add_argument("--raw", action="store_true", help="只打印接口原文，不解析")
    ap.add_argument("--re-type", type=int, default=2, help="1=机房(勿用) 2/3/4=住宅线路")
    ap.add_argument("--diagnose", action="store_true", help="逐层诊断失败原因（报障用）")
    ap.add_argument("--proxy-user", default="", help="代理账密用户名（默认用 custom）")
    ap.add_argument("--proxy-pass", default="", help="代理账密密码（默认用 password）")
    ap.add_argument("--handshake", action="store_true", help="只做单次握手判定（不触发限行）")
    a = ap.parse_args()

    if a.raw:
        print(fetch_raw(a.num, a.region, re_type=a.re_type, custom=a.custom, password=a.password))
        return 0

    if a.handshake:
        text = fetch_raw(a.num, a.region, re_type=a.re_type, custom=a.custom, password=a.password)
        code, hint = classify_error(text)
        if code:
            print(f"[{code}] {hint}")
            return 1
        pu = a.proxy_user or a.custom
        pp = a.proxy_pass or a.password
        rows = parse_list(text, auth_user=pu, auth_pass=pp)
        print(f"取到 {len(rows)} 条，单次握手判定（不重试，避免行限）\n")
        for r in rows[:4]:
            for ua in (True, False):
                st, det = handshake(r, use_auth=ua)
                print(f"{r.ip}:{r.port} origin={r.origin_ip or '-':<16} "
                      f"{'auth' if ua else 'none':<5} -> {st:<14} {det[:95]}")
        return 0

    if a.diagnose:
        text = fetch_raw(a.num, a.region, re_type=a.re_type, custom=a.custom, password=a.password)
        code, hint = classify_error(text)
        if code:
            print(f"[{code}] {hint}")
            return 1
        pu = a.proxy_user or a.custom
        pp = a.proxy_pass or a.password
        rows = parse_list(text, auth_user=pu, auth_pass=pp)
        if a.handshake:
            for r in rows[:3]:
                for ua in (True, False):
                    st, det = handshake(r, use_auth=ua)
                    print(f"{r.ip}:{r.port} origin={r.origin_ip or '-':<16} "
                          f"{'auth' if ua else 'none':<5} -> {st:<14} {det[:90]}")
            return 0
        print(f"取到 {len(rows)} 条，逐层诊断前 {min(3, len(rows))} 条\n")
        os.makedirs(os.path.join(DATA, "diag"), exist_ok=True)
        out = os.path.join(DATA, "diag", f"proxy_diag_{int(time.time())}.txt")
        lines: list[str] = []
        for r in rows[:3]:
            lines.append(f"===== {r.ip}:{r.port}  origin={r.origin_ip or '-'}")
            for name, ok, detail in diagnose(r):
                lines.append(f"  {'OK  ' if ok else 'FAIL'} {name:<26} {detail}")
            lines.append("")
        report = "\n".join(lines)
        print(report)
        open(out, "w", encoding="utf-8").write(report)
        print(f"[+] 诊断报告: {out}")
        return 0

    rows, err = acquire(a.num, a.region, a.country or None, a.probe, a.workers,
                        a.custom, a.password, a.re_type, a.proxy_user, a.proxy_pass)
    if err:
        print(err)
        return 1
    ok = [r for r in rows if r.ok]
    print(f"取到 {len(rows)} 条，存活 {len(ok)} 条")
    print(f"{'状态':<5}{'出口IP':<17}{'国家':<5}{'城市':<14}{'延迟':>7}  url")
    for r in rows[:40]:
        st = "OK" if r.ok else "DEAD"
        print(f"{st:<5}{r.egress_ip or r.ip:<17}{r.country or '-':<5}{r.city[:13]:<14}"
              f"{r.latency_ms:>5}ms  {r.url}")
    if a.write:
        p = write_pool(rows)
        print(f"\n[+] 已写 {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
