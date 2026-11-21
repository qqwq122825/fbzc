#!/usr/bin/env python3
"""代理接入矩阵测试：把一个端口上「哪种认证方式能通」一次测清楚。

为什么单独做这个：代理面板的「API 取 IP 的 custom/password」和「连代理用的账密」
经常**不是同一对**。抓瞎试很容易触发网关的行限（表现为连上后 0 字节关闭）。
本脚本用一次连接一个组合、间隔固定的方式，把矩阵跑完并给出结论。

用法（在你自己的、已加白名单的机器上跑最有意义）:
    python3 tools/cred_matrix.py --endpoint 1.2.3.4:8080
    python3 tools/cred_matrix.py --from-api --num 3 --re-type 2
    python3 tools/cred_matrix.py --from-api --user 29776 --pass CbXSOw \
            --try "29776:CbXSOw" --try "email@x.com:pass" --try "none"

判定码（实测自 udealproxy 网关）:
    OK               CONNECT 200，隧道通
    AUTH_REQUIRED    HTTP/1.1 611 Authentication Required（非标准 611）→ 账密不对
    CLOSED           连上后 0 字节关闭 → IP 未进白名单 / 端口失效 / 被行限
    TIMEOUT          静默丢包
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "data")

TARGET = "api.ipify.org"


def endpoint_from_api(num: int = 3, re_type: int = 2,
                      custom: str = "29776", password: str = "CbXSOw") -> list[str]:
    import requests

    r = requests.get("http://usapi.udealproxy.com:7777/iplist",
                     params=dict(custom=custom, password=password, num=num,
                                 re_type=re_type, lb=1, region="any"), timeout=25)
    text = r.text.strip()
    eps: list[str] = []
    if text.startswith("{"):
        j = json.loads(text)
        for it in j.get("data", []):
            eps.append(f"{it['ip']}:{it['port']}")
    else:
        for line in text.splitlines():
            if ":" in line and not line.startswith(("{", "白名单")):
                eps.append(line.strip())
    return eps


def one(ep: str, user: str = "", pwd: str = "", timeout: int = 8,
        mode: str = "connect") -> tuple[str, str]:
    host, port = ep.rsplit(":", 1)
    try:
        sk = socket.create_connection((host, int(port)), timeout=timeout)
    except Exception as e:
        return "TCP_FAIL", f"{type(e).__name__}: {str(e)[:50]}"
    try:
        if mode == "connect":
            head = f"CONNECT {TARGET}:443 HTTP/1.1\r\nHost: {TARGET}:443\r\n"
        else:  # 绝对 URI 形式（少数网关只认这个）
            head = f"GET http://{TARGET}/ HTTP/1.1\r\nHost: {TARGET}\r\n"
        if user:
            b = base64.b64encode(f"{user}:{pwd}".encode()).decode()
            head += f"Proxy-Authorization: Basic {b}\r\n"
        sk.sendall((head + "\r\n").encode())
        sk.settimeout(timeout)
        data = sk.recv(400)
    except socket.timeout:
        return "TIMEOUT", "无回包"
    except Exception as e:
        return "CLOSED", f"{type(e).__name__}: {str(e)[:50]}"
    finally:
        try:
            sk.close()
        except Exception:
            pass
    if not data:
        return "CLOSED", "0 字节关闭"
    head = data[:200].decode("latin1")
    code = head.split(" ")[1] if head.startswith("HTTP/") and " " in head else "?"
    if code.startswith("2"):
        return "OK", head.replace("\r\n", " | ")[:120]
    if code in ("611", "407"):
        return "AUTH_REQUIRED", head.replace("\r\n", " | ")[:160]
    return f"HTTP_{code}", head.replace("\r\n", " | ")[:120]


def parse_try(vals: list[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for v in vals:
        if v.lower() in ("none", "", "-"):
            out.append(("", ""))
        elif ":" in v:
            u, p = v.split(":", 1)
            out.append((u, p))
        else:
            out.append((v, ""))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", action="append", default=[], help="1.2.3.4:8080，可多次给")
    ap.add_argument("--from-api", action="store_true")
    ap.add_argument("--num", type=int, default=3)
    ap.add_argument("--re-type", type=int, default=2)
    ap.add_argument("--user", default="29776")
    ap.add_argument("--pass", dest="pwd", default="CbXSOw")
    ap.add_argument("--try", dest="tries", action="append", default=[],
                    help='账密组合，如 "29776:CbXSOw"；给 none 测免认证')
    ap.add_argument("--mode", default="connect", choices=["connect", "absolute"])
    ap.add_argument("--sleep", type=float, default=2.0, help="每次尝试间隔，防止网关行限")
    a = ap.parse_args()

    eps = list(a.endpoint)
    if a.from_api or not eps:
        eps += endpoint_from_api(a.num, a.re_type, a.user, a.pwd)
    if not eps:
        print("[!] 没有端点：--endpoint 或 --from-api")
        return 2

    combos = parse_try(a.tries) if a.tries else [("none", ""), (a.user, a.pwd)]
    print(f"端点 {len(eps)} 个 × 组合 {len(combos)} 种 = {len(eps)*len(combos)} 次尝试\n")

    rows: list[str] = []
    best: tuple[str, str, str] | None = None
    for ep in eps:
        for u, p in combos:
            state, detail = one(ep, u, p, mode=a.mode)
            tag = f"{u + ':' + p if u else 'none'}"
            line = f"{ep:<22} {tag:<28} {state:<14} {detail[:80]}"
            print(line)
            rows.append(line)
            if state == "OK" and best is None:
                best = (ep, u, p)
            time.sleep(a.sleep)

    print("\n" + "=" * 60)
    if best:
        ep, u, p = best
        print(f"结论：用 {u or '免认证'} 通过了 {ep}")
        print(f"      可直接写进调度器： --proxy-user '{u}' --proxy-pass '{p}'")
    else:
        states = {r.split()[2] for r in rows if len(r.split()) > 2}
        print("结论：全部失败")
        if "AUTH_REQUIRED" in states:
            print("  → 网关明确要求认证（611），说明 custom/password 不是代理账密")
            print("    去面板找「代理账号/代理密码」或「终端账号」，用 --try 加进来")
        if "CLOSED" in states:
            print("  → 静默关闭：调用方 IP 未进白名单 / 端口失效 / 刚被行限")
            print("    把当前出口 IP 报给服务商：curl ifconfig.me")
        if "TIMEOUT" in states:
            print("  → 超时：端口不通或被防火墙拦")

    os.makedirs(DATA, exist_ok=True)
    out = os.path.join(DATA, "cred_matrix.txt")
    open(out, "w", encoding="utf-8").write("\n".join(rows) + "\n")
    print(f"\n明细已存 {out}")
    return 0 if best else 1


if __name__ == "__main__":
    raise SystemExit(main())
