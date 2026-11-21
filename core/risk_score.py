"""注册前风险打分 + 注册后状态判定。

回答两个问题：
  1. **提交之前**：这个 IP / 邮箱域 / 节奏，大概会不会撞上 checkpoint（含人脸/证件）
  2. **提交之后**：这个号是 ACTIVE、要验证、还是要证件——不用等出事才发现

重要前提（必须先说清楚）：
  人脸/证件验证（ID verification）**不会出现在注册提交那一步**。
  它是 checkpoint，发生在「首次进 feed / 改资料 / 加好友 / 换设备或换 IP 登录」时。
  所以"注册成功"≠"号可用"，必须做注册后探测。

用法:
    # 只评估环境（不提交任何东西）
    python3 core/risk_score.py --proxy socks5h://ip:port --email a@yourdomain.com

    # 注册后判定账号状态（用注册时的 cookies + 同一出口）
    python3 core/post_check.py --cookies '{"datr":"...","c_user":"..."}' --proxy socks5h://ip:port
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# 已知被 Meta 重点打击的一次性邮箱域（命中直接扣分）
DISPOSABLE = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
    "yopmail.com", "sharklasers.com", "trashmail.com", "getnada.com",
    "uselesss.org", "maildrop.cc", "dispostable.com", "temp-mail.org",
}


# ---------------------------------------------------------------- IP 侧
def ip_profile(proxy: str, timeout: int = 20) -> dict:
    out: dict = {"ip": "", "country": "", "asn": "", "org": "", "hosting": None,
                 "proxy_flag": None, "mobile": None, "latency_ms": 0}
    px = {"http": proxy, "https": proxy} if proxy else None
    t0 = time.time()
    try:
        r = requests.get("https://api.ipify.org?format=json", proxies=px, timeout=timeout)
        out["ip"] = (r.json() or {}).get("ip", "")
        out["latency_ms"] = int((time.time() - t0) * 1000)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:60]}"
        return out
    if not out["ip"]:
        return out
    try:
        j = requests.get(
            f"http://ip-api.com/json/{out['ip']}"
            "?fields=countryCode,isp,org,as,hosting,proxy,mobile",
            timeout=timeout).json()
        out.update({
            "country": (j.get("countryCode") or "").upper(),
            "org": j.get("org") or j.get("isp") or "",
            "asn": j.get("as") or "",
            "hosting": j.get("hosting"),
            "proxy_flag": j.get("proxy"),
            "mobile": j.get("mobile"),
        })
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- 邮箱侧
def mx_of(domain: str) -> list[str]:
    for cmd in (["dig", "+short", "MX", domain], ["host", "-t", "MX", domain]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
            if r.returncode == 0 and r.stdout.strip():
                return [l.strip() for l in r.stdout.splitlines() if l.strip()][:5]
        except Exception:
            continue
    return []


def email_profile(addr: str) -> dict:
    domain = addr.split("@")[-1].lower() if "@" in addr else ""
    mx = mx_of(domain) if domain else []
    return {
        "domain": domain,
        "mx": mx,
        "has_mx": bool(mx),
        "disposable": domain in DISPOSABLE,
        "free_mail": domain in ("gmail.com", "outlook.com", "hotmail.com", "yahoo.com",
                                "icloud.com", "proton.me"),
        "catchall_like": bool(domain) and domain not in DISPOSABLE
                        and domain not in ("gmail.com", "outlook.com"),
    }


# ---------------------------------------------------------------- 打分
def score(ip: dict, mail: dict, velocity: dict | None = None) -> dict:
    """0（最危险）~ 100（最干净）。扣分项按实测权重排序。"""
    s = 100
    why: list[str] = []

    # —— IP：权重最高
    if ip.get("hosting") is True:
        s -= 45; why.append("-45 IP 属于机房/云厂商段（hosting=true）")
    if ip.get("proxy_flag") is True:
        s -= 25; why.append("-25 IP 被标记为代理/VPN")
    if ip.get("mobile") is True:
        s += 5; why.append("+5 移动网络出口（最优质）")
    if ip.get("latency_ms", 0) > 3000:
        s -= 5; why.append("-5 出口延迟 >3s，请求容易超时/重试")
    org = (ip.get("org") or "").lower()
    for bad in ("digitalocean", "vultr", "linode", "hetzner", "ovh", "amazon",
                "google cloud", "microsoft", "alibaba", "tencent"):
        if bad in org:
            s -= 40; why.append(f"-40 出口属于云厂商 ASN（{bad}）")
            break
    for good in ("comcast", "at&t", "verizon", "charter", "centurylink", "cox",
                 "t-mobile", "vodafone", "telekom", "ntt", "kddi"):
        if good in org:
            s += 8; why.append(f"+8 出口是居民宽带运营商（{good}）")
            break

    # —— 邮箱
    if not mail.get("has_mx"):
        s -= 35; why.append("-35 邮箱域没有 MX（注册必报 EMAIL_DOMAIN_NOT_MX=7）")
    if mail.get("disposable"):
        s -= 30; why.append("-30 一次性邮箱域，Meta 重点打击")
    if mail.get("free_mail"):
        s -= 12; why.append("-12 公共邮箱域（gmail/outlook），成功率高但易被要求补绑")
    if mail.get("catchall_like"):
        s += 6; why.append("+6 自有域名（catch-all），最稳")

    # —— 节奏
    v = velocity or {}
    if v.get("ip_today", 0) >= 3:
        s -= 20; why.append(f"-20 该 IP 今天已注册 {v.get('ip_today')} 个")
    if v.get("domain_today", 0) >= 30:
        s -= 15; why.append(f"-15 该邮箱域今天已注册 {v.get('domain_today')} 个")
    if v.get("last_gap_s", 9999) < 60:
        s -= 10; why.append(f"-10 距上一单仅 {v.get('last_gap_s')}s，节奏太快")

    s = max(0, min(100, s))
    if s >= 80:
        band, expect = "LOW", "预计正常出号，570 概率 <10%"
    elif s >= 60:
        band, expect = "MEDIUM", "预计 20–40% 会要二次验证"
    elif s >= 40:
        band, expect = "HIGH", "预计 50–70% 会 checkpoints（含证件/自拍）"
    else:
        band, expect = "EXTREME", "基本必挂，先换 IP/域名再谈注册"
    return {"score": s, "band": band, "expect": expect, "reasons": why,
            "ip": ip, "email": mail, "velocity": v}


# ---------------------------------------------------------------- 注册后判定
def post_check(cookies: dict, proxy: str | None = None, timeout: int = 30) -> dict:
    """用注册时的 cookies + 同一出口探测账号真实状态。

    判定顺序（从软到硬）：
      1. GET /              看是否被 302 到 /checkpoint/ 或 /login/
      2. GET /me            看落点（profile / login / checkpoint）
      3. POST /api/graphql  me 查询，看是否 requires_reauth
    """
    s = requests.Session()
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    s.headers.update({"User-Agent": UA, "Accept": "text/html,*/*;q=0.8",
                      "Accept-Language": "en-US,en;q=0.9"})
    for k, v in (cookies or {}).items():
        s.cookies.set(k, v, domain=".facebook.com")

    out: dict = {"state": "UNKNOWN", "steps": []}

    def step(name, fn):
        try:
            r = fn()
            loc = r.headers.get("location", "")
            body = (r.text or "")
            out["steps"].append({"step": name, "status": r.status_code,
                                 "loc": loc[:80], "len": len(body)})
            return r, loc, body
        except Exception as e:
            out["steps"].append({"step": name, "error": f"{type(e).__name__}: {str(e)[:60]}"})
            return None, "", ""

    r, loc, body = step("GET /", lambda: s.get("https://www.facebook.com/",
                                               timeout=timeout, allow_redirects=False))
    if "checkpoint" in loc.lower() or "checkpoint" in body[:4000].lower():
        out["state"] = "CHECKPOINT"
        out["detail"] = "会话一进来就被要求验证"
        return out
    if "/login" in loc.lower():
        out["state"] = "SESSION_DEAD"
        out["detail"] = "cookies 无效（注册其实没成功，或已被踢）"
        return out

    r, loc, body = step("GET /me", lambda: s.get("https://www.facebook.com/me",
                                                  timeout=timeout, allow_redirects=False))
    if "checkpoint" in loc.lower():
        out["state"] = "CHECKPOINT"
        out["detail"] = "/me 被拦到 checkpoint（这是人脸/证件最常出现的位置）"
        return out
    if "/login" in loc.lower():
        out["state"] = "SESSION_DEAD"
        return out
    if r is not None and r.status_code in (200, 302):
        out["state"] = "ACTIVE"
        out["detail"] = "会话可用，未触发验证"

    # GraphQL 复核
    gql_body = {"av": "0", "__user": "0", "__a": "1", "fb_api_caller_class": "RelayModern",
                "fb_api_req_friendly_name": "CometMeQuery",
                "variables": "{}", "server_timestamps": "true"}
    r2, loc2, b2 = step("POST /api/graphql (me)", lambda: s.post(
        "https://www.facebook.com/api/graphql/", data=gql_body,
        headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=timeout))
    if b2 and ("requires_reauth" in b2 or "checkpoint" in b2.lower()):
        out["state"] = "CHECKPOINT"
        out["detail"] = "GraphQL 返回 requires_reauth/checkpoint"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default="")
    ap.add_argument("--email", default="probe@yourdomain.example")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    ip = ip_profile(a.proxy) if a.proxy else {}
    mail = email_profile(a.email)
    res = score(ip, mail)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    print(f"IP       {ip.get('ip') or '-'}  {ip.get('country') or '?'}  "
          f"{(ip.get('org') or '')[:40]}")
    print(f"         hosting={ip.get('hosting')} proxy={ip.get('proxy_flag')} "
          f"mobile={ip.get('mobile')} {ip.get('latency_ms')}ms")
    print(f"邮箱     {a.email}  MX={'有' if mail['has_mx'] else '无'}  "
          f"一次性={mail['disposable']} 自有域={mail['catchall_like']}")
    print(f"\n风险分   {res['score']}/100   [{res['band']}]  {res['expect']}")
    for w in res["reasons"]:
        print(f"   {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
