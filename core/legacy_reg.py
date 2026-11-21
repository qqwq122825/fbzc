"""legacy 表单注册通道（m.facebook.com/reg/ 服务端渲染表单）—— **实测能出号**。

为什么这条路能通（2026-11 实测）：
  Web Comet 的提交 mutation 入参类型静态推不出来（`noncoercible_variable_value`），
  但用老客户端 UA 访问 m.facebook.com/reg/，服务端仍然返回**老式 HTML 表单**，
  字段齐全：lsd / jazoest / ccp / reg_instance / reg_impression_id / logger_id / waterfall_id，
  提交后返回 302 到 `login/save-device/?login_source=account_creation`，
  并且直接下发 `c_user` + `xs` —— 也就是说**号创建成功了**。

实测记录：
  GET  m.facebook.com/reg/           200, 102KB, 33 个 input + 4 个 select
  POST m.facebook.com/reg/submit/…   302 -> /login/save-device/?login_source=account_creation
      cookies: datr / fr / c_user=61596960383244 / sb / xs
      GET /  -> 302 /confirmemail.php （等邮箱验证码，正常）
      邮箱收到: "FB-36609 is your confirmation code"  (code=36609)

用法:
    python3 core/legacy_reg.py --dry                 # 只抓表单、打印要提交的字段
    python3 core/legacy_reg.py --email a@b.com --proxy socks5h://ip:port [--yes]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from html import unescape

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

# 关键：老客户端 UA 才会拿到服务端渲染表单
UA_LEGACY = ("Mozilla/5.0 (Linux; Android 4.4.2; Nexus 5) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/34.0.1847.114 Mobile Safari/537.36")

REG_URL = "https://m.facebook.com/reg/"
SUBMIT_PATH = "/reg/submit/"

from core.names import generate as gen_name   # 真实姓名库：频率加权 + 性别一致 + 批内去重


def _session(proxy: str | None) -> requests.Session:
    s = requests.Session()
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    s.headers.update({
        "User-Agent": UA_LEGACY,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Upgrade-Insecure-Requests": "1",
    })
    return s


def fetch_form(s: requests.Session) -> tuple[str, list[tuple[str, str]]]:
    """返回 (action_url, 有序字段表)。字段表保留隐藏项与结构项。"""
    r = s.get(REG_URL, timeout=45)
    html = r.text or ""
    m = re.search(r"<form[^>]*action=\"([^\"]+)\"", html)
    if not m:
        raise RuntimeError(f"没拿到表单（status={r.status_code}, len={len(html)}）——UA 或 IP 被拒")
    action = unescape(m.group(1))
    form = re.search(r"<form[^>]*>(.*?)</form>", html, re.S)
    body = form.group(1) if form else html

    fields: list[tuple[str, str]] = []
    for im in re.finditer(r"<input([^>]*)>", body):
        attrs = im.group(1)
        nm = re.search(r'name="([^"]*)"', attrs)
        vl = re.search(r'value="([^"]*)"', attrs)
        tp = re.search(r'type="([^"]*)"', attrs)
        if not nm:
            continue
        name = unescape(nm.group(1))
        val = unescape(vl.group(1)) if vl else ""
        typ = unescape(tp.group(1)) if tp else "text"
        if name == "sex" and typ == "radio":
            continue           # 由调用方显式赋值
        if name in ("firstname", "lastname", "reg_email__", "reg_email_confirmation__",
                    "reg_passwd__", "age_step_input", "custom_gender"):
            val = ""           # 清空用户输入项
        fields.append((name, val))

    # select 项补默认值
    for sm in re.finditer(r"<select([^>]*)>(.*?)</select>", body, re.S):
        nm = re.search(r'name="([^"]*)"', sm.group(1))
        if not nm:
            continue
        name = unescape(nm.group(1))
        if name not in ("birthday_day", "birthday_month", "birthday_year", "preferred_pronoun"):
            continue
        opts = re.findall(r'<option[^>]*value="([^"]*)"', sm.group(2))
        fields.append((name, opts[0] if opts else ""))
    return action, fields


def register(proxy: str | None, email: str, password: str, first: str = "", last: str = "",
             sex: int = 2, bday: tuple[int, int, int] = (13, 7, 1993),
             timeout: int = 60, country: str = "US") -> dict:
    """走 legacy 表单注册。返回 {ok, status, c_user, cookies, location, detail}"""
    s = _session(proxy)
    action, fields = fetch_form(s)
    if not (first and last):
        g_first, g_last = gen_name(country or "US", sex)
        first = first or g_first
        last = last or g_last

    fill = {
        "firstname": first, "lastname": last,
        "reg_email__": email, "reg_email_confirmation__": email,
        "reg_passwd__": password, "sex": str(sex),
        "birthday_day": str(bday[0]), "birthday_month": str(bday[1]), "birthday_year": str(bday[2]),
        "preferred_pronoun": "", "did_use_age": "", "age_step_input": "",
        "custom_gender": "", "use_custom_gender": "", "guid": "", "ns": "0",
        "submission_request": "true",
    }
    payload = dict(fields)
    payload.update(fill)

    # 模拟填表节奏
    time.sleep(random.uniform(1.5, 3.5))

    h = {"Content-Type": "application/x-www-form-urlencoded",
         "Origin": "https://m.facebook.com",
         "Referer": "https://m.facebook.com/reg/",
         "Accept": "text/html,*/*;q=0.8"}
    url = "https://m.facebook.com" + action if action.startswith("/") else action
    r = s.post(url, data=payload, headers=h, timeout=timeout, allow_redirects=False)
    loc = r.headers.get("location", "")
    ck = s.cookies.get_dict()

    ok = bool(ck.get("c_user")) or "save-device" in loc or "account_creation" in loc
    return {
        "ok": ok,
        "status": r.status_code,
        "c_user": ck.get("c_user", ""),
        "location": loc,
        "cookies": ck,
        "detail": (r.text or "")[:300],
        "profile": {"first": first, "last": last, "email": email, "sex": sex, "bday": list(bday)},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default="")
    ap.add_argument("--email", default="")
    ap.add_argument("--password", default="Nq7#vLm2Rt9xWp4")
    ap.add_argument("--first", default="")
    ap.add_argument("--last", default="")
    ap.add_argument("--sex", type=int, default=2, choices=[1, 2])
    ap.add_argument("--day", type=int, default=13)
    ap.add_argument("--month", type=int, default=7)
    ap.add_argument("--year", type=int, default=1993)
    ap.add_argument("--country", default="US", help="姓名/生日按这个国家的真实分布生成")
    ap.add_argument("--dry", action="store_true", help="只抓表单打印字段，不提交")
    ap.add_argument("--yes", action="store_true", help="确认提交（会真的创建一个号）")
    a = ap.parse_args()

    if a.dry:
        s = _session(a.proxy or None)
        action, fields = fetch_form(s)
        print(f"action: {action[:120]}")
        print(f"字段数: {len(fields)}")
        for k, v in fields:
            print(f"  {k:<28}{(v[:40] + '…') if len(v) > 40 else v}")
        print("\n提示：加 --email + --yes 才真正提交")
        return 0

    if not a.email:
        print("[!] 需要 --email（或用 --dry 先看表单）")
        return 2
    if not a.yes:
        print("[!] 加 --yes 才会真的提交（会创建一个真实账号）")
        return 2

    res = register(a.proxy or None, a.email, a.password, a.first, a.last, a.sex,
                   (a.day, a.month, a.year), country=a.country)
    print(json.dumps({k: v for k, v in res.items() if k != "cookies"}, ensure_ascii=False, indent=2))
    print("cookies:", list(res["cookies"].keys()))
    if res["ok"]:
        os.makedirs(DATA, exist_ok=True)
        with open(os.path.join(DATA, "accounts.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": int(time.time()), "channel": "legacy_form",
                                "contact": a.email, "c_user": res["c_user"],
                                "cookies": res["cookies"], "proxy": a.proxy,
                                "state": "PENDING_EMAIL"}, ensure_ascii=False) + "\n")
        print("\n✅ 已创建，写入 data/accounts.jsonl")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
