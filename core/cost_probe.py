"""流量成本实测：一次注册到底跑多少 KB，5GB 住宅流量能撑多少个号。

背景：住宅代理按流量计费（常见 ¥40/5GB ≈ ¥8/GB）。模拟器那条路会拖图片/字体/JS，
单个号几百 KB 到几 MB；纯协议只发必要请求，可以把成本压到几 KB。

本脚本实测三条链路的**线上真实传输字节**（按 content-length，即压缩后）：
    A. 模拟器/浏览器完整加载（首页 + 注册页 + 静态资源）
    B. 我们的旧流程（首页 + 注册页 + GraphQL）
    C. 精简流程（不打页面，直接 GraphQL —— 已实测可行）

用法:
    python3 core/cost_probe.py                 # 用 data/proxies.txt 里第一个 socks5h 出口
    python3 core/cost_probe.py --proxy socks5h://ip:port --price-per-gb 8
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.client import WEB_BASE, WEB_GRAPHQL, WEB_REG, browser_headers
from core.device_env import DeviceEnv
from core.web_reg import (build_form, build_payload, doc_id_for,
                          encrypt_password, extract_key_from_html)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")


def wire(r: requests.Response) -> int:
    cl = r.headers.get("content-length")
    return int(cl) if cl and cl.isdigit() else len(r.content or b"")


def session(proxy: str | None) -> requests.Session:
    s = requests.Session()
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    s.headers.update(browser_headers(UA))
    return s


def gql(s, friendly: str, variables: dict, lsd: str = "", rev: str = "0") -> requests.Response:
    body = {"av": "0", "__user": "0", "__a": "1", "__req": "1", "__hs": "0", "__rev": rev,
            "__comet_req": "0", "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": friendly,
            "variables": __import__("json").dumps(variables, separators=(",", ":")),
            "server_timestamps": "true", "doc_id": doc_id_for(friendly)}
    if lsd:
        body["lsd"] = lsd
    h = {"Content-Type": "application/x-www-form-urlencoded", "X-FB-Friendly-Name": friendly,
         "Origin": WEB_BASE, "Referer": WEB_REG, "Accept": "*/*"}
    if lsd:
        h["X-FB-LSD"] = lsd
    return s.post(WEB_GRAPHQL, data=body, headers=h, timeout=60)


def flow_full(proxy):
    """B: 首页 + 注册页 + 一条 GraphQL"""
    s = session(proxy)
    used = []
    r1 = s.get(WEB_BASE + "/", timeout=45); used.append(("首页 HTML", wire(r1)))
    r2 = s.get(WEB_REG, timeout=45); used.append(("注册页 HTML", wire(r2)))
    lsd = ""
    m = re.search(r'"token":"([A-Za-z0-9_-]{10,60})"', r2.text or "")
    if m:
        lsd = m.group(1)
    r3 = gql(s, "CAARegistrationFormDesktopQuery",
             {"args": {}, "ig_age_data": None, "next_uri": "/", "scale": 1}, lsd)
    used.append(("GraphQL 取态", wire(r3)))
    return used


def flow_lean(proxy):
    """C: 不打页面，直接 GraphQL（服务端会种 datr）"""
    s = session(proxy)
    used = []
    r1 = gql(s, "CAARegistrationFormDesktopQuery",
             {"args": {}, "ig_age_data": None, "next_uri": "/", "scale": 1})
    used.append(("GraphQL 取态", wire(r1)))
    return used, s, r1


def flow_lean_attempt(proxy):
    """C+: 取态 + 字段校验 + 提交尝试（完整一次注册的协议流量）"""
    used, s, r1 = flow_lean(proxy)
    addr = "costprobe@uselesss.org"
    v = {"input": {"contactpoint": {"sensitive_string_value": addr},
                   "contactpoint_type": "EMAIL",
                   "fetch_username_suggestions": False,
                   "field_name": "contactpoint",
                   "firstname": {"sensitive_string_value": "Michael"},
                   "fullname": {"sensitive_string_value": ""},
                   "lastname": {"sensitive_string_value": "Wilson"},
                   "username": {"sensitive_string_value": ""}}, "scale": 1}
    r2 = gql(s, "useCAARegistrationFieldValidationQuery", v)
    used.append(("字段校验", wire(r2)))

    kid, pub = extract_key_from_html("")  # 精简流程拿不到页面里的 key，用占位
    enc = "#PWD_BROWSER:5:14:[B64]" if not pub else encrypt_password("Nq7#vLm2Rt9xWp4", pub, kid)
    p = build_payload("Michael", "Wilson", addr, enc, 1, (13, 7, 1993))
    body = build_form(p, "", "useCAARegistrationFormSubmitMutation")
    h = {"Content-Type": "application/x-www-form-urlencoded",
         "X-FB-Friendly-Name": "useCAARegistrationFormSubmitMutation",
         "Origin": WEB_BASE, "Referer": WEB_REG, "Accept": "*/*"}
    r3 = s.post(WEB_GRAPHQL, data=body, headers=h, timeout=60)
    used.append(("提交 mutation", wire(r3)))
    return used


def report(name: str, used: list[tuple[str, int]], price_per_gb: float) -> int:
    total = sum(b for _, b in used)
    print(f"\n=== {name}   合计 {total:,} B ({total/1024:.1f} KB)")
    for step, b in used:
        print(f"    {step:<16}{b:>10,} B")
    gb = 5.0
    n = int(gb * 1024 * 1024 * 1024 / total) if total else 0
    print(f"    5GB 可撑 ≈ {n:,} 个号   每号流量成本 ≈ ¥{price_per_gb * total / (1024**3):.6f}")
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default="")
    ap.add_argument("--price-per-gb", type=float, default=8.0, help="¥/GB，40元/5GB ≈ 8")
    a = ap.parse_args()
    px = a.proxy
    if not px:
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "proxies.txt")
        if os.path.exists(p):
            px = next((l.split(",")[0] for l in open(p, encoding="utf-8")
                       if l.startswith("socks5h")), "")
    print(f"出口: {px or '直连'}")
    print(f"计费: ¥{a.price_per_gb}/GB\n（所有数字都是线上压缩后的传输字节 content-length）")

    full = flow_full(px)
    report("B 旧流程（首页 + 注册页 + GraphQL）", full, a.price_per_gb)

    used, _, _ = flow_lean(px)
    lead = report("C 精简（只 GraphQL 取态）", used, a.price_per_gb)

    attempt = flow_lean_attempt(px)
    total = report("C+ 完整一次注册尝试（取态 + 校验 + 提交）", attempt, a.price_per_gb)

    print("\n" + "=" * 62)
    print(f"旧流程 {sum(b for _,b in full)/1024:.0f} KB/号  ->  "
          f"{int(5*1024**3/sum(b for _,b in full)):,} 号/5GB")
    print(f"精简流程 {total/1024:.1f} KB/号  ->  {int(5*1024**3/total):,} 号/5GB")
    print(f"节省 {(1-total/sum(b for _,b in full))*100:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
