"""一条龙：注册 + 收码 + 确认，全部在**同一个出口、同一个会话**里完成。

为什么必须一条龙：
  实测该代理的端口是短期的，几分钟后失效；重新取 IP 换出口后打开确认页，
  FB 会直接 302 到 `/checkpoint/...`。所以"注册完过一会儿再确认"必然失败。

流程:
  1) 取一个可用住宅出口（探活）
  2) apiok 生成临时邮箱（或外部传入）
  3) legacy 表单注册（core/legacy_reg.py）
  4) 轮询邮箱拿确认码（core/email_pool.py）
  5) 抓确认页 fb_dtsg/lsd/jazoest（core/confirm_bloks.py）
  6) POST async/wbloks/fetch 提交验证码
  7) 复核账号状态（GET / 是否还跳 confirmemail）

用法:
    python3 core/full_flow.py --country US [--yes]
    python3 core/full_flow.py --dry          # 只测到"取到邮箱 + 拿到表单"，不注册
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import proxy_udeal
from core.confirm_bloks import (build_reg_info, fetch_tokens, session as ck_session,
                               submit)
from core.email_pool import ApiOkReader, extract_fb_confirm
from core.legacy_reg import register as legacy_register
from core.names import generate as gen_name

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
APIOK_KEY = os.environ.get("APIOK_KEY", "idr_85f1ad8109b15afab74d09d4286da3b8")


def ok(msg: str) -> None:
    print(f"✅ {msg}")


def bad(msg: str) -> None:
    print(f"❌ {msg}")


def run(country: str, password: str, do_submit: bool) -> dict:
    res: dict = {}

    # 1) 出口
    rows, err = proxy_udeal.acquire(num=3, region=country.lower(), country=country,
                                    do_probe=True, workers=3)
    if err or not rows:
        bad(f"取不到 {country} 出口: {err}")
        return res
    proxy = rows[0].url
    ok(f"出口 {rows[0].egress_ip} ({rows[0].country}) {rows[0].latency_ms}ms  {proxy}")

    # 2) 邮箱
    mail = ApiOkReader(APIOK_KEY, interval=8)
    email = mail.generate()
    ok(f"邮箱 {email}")

    if not do_submit:
        print("\n--dry：到此为止（未注册）")
        return {"proxy": proxy, "email": email}

    # 3) 注册
    first, last = gen_name(country, 2)
    print(f"\n[注册] {first} {last} / {country}")
    reg = legacy_register(proxy, email, password, first, last, 2, (13, 7, 1993),
                          country=country)
    if not reg.get("ok"):
        bad(f"注册失败 {reg.get('status')} {reg.get('location')} {str(reg.get('detail'))[:120]}")
        return {"proxy": proxy, "email": email, "reg": reg}
    ok(f"注册成功 c_user={reg['c_user']}  ->  {reg['location'][:70]}")
    res.update({"proxy": proxy, "email": email, "reg": reg})

    # 4) 收码
    print("\n[收码] 轮询邮箱…")
    mail.mailbox_id = base64.b64encode(email.encode()).decode()
    code = ""
    for i in range(12):
        try:
            msgs = mail._list()
            if msgs:
                mid = msgs[0].get("id") or msgs[0].get("message_id")
                body = mail._detail(mid) if mid else json.dumps(msgs[0], ensure_ascii=False)
                got = extract_fb_confirm(body)
                if got.get("code"):
                    code = got["code"]
                    ok(f"验证码 {code}（{got.get('subject') or ''}）")
                    break
        except Exception as e:
            print(f"   poll {i+1}: {type(e).__name__}")
        time.sleep(8)
    if not code:
        bad("没等到验证码")
        return res
    res["code"] = code

    # 5) 抓确认页 token（同一出口同一会话）
    print("\n[确认] 抓页面 token")
    s = ck_session(proxy, reg["cookies"])
    tok, _ = fetch_tokens(s)
    for k in ("lsd", "fb_dtsg", "jazoest"):
        print(f"   {k:<9}{tok.get(k) or 'MISS'}")
    if not tok.get("lsd") or not tok.get("fb_dtsg"):
        bad("token 不全，确认可能失败（页面结构或会话状态问题）")

    # 6) 提交验证码
    ri = build_reg_info(first, last, email, "email", 2, reg["c_user"])
    r = submit(s, tok, code, reg["c_user"], ri)
    ok(f"提交 wbloks/fetch: {r.status_code} loc={r.headers.get('location','')[:60]}")
    print("   resp:", (r.text or "")[:300])
    res["confirm_status"] = r.status_code
    res["confirm_resp"] = (r.text or "")[:500]

    # 7) 复核
    time.sleep(3)
    rr = s.get("https://m.facebook.com/", timeout=45, allow_redirects=False)
    loc = rr.headers.get("location", "")
    state = "ACTIVE" if not loc or "confirmemail" not in loc else "PENDING_EMAIL"
    ok(f"复核状态: {state}  GET / -> {rr.status_code} {loc[:60]}")
    res["state"] = state
    res["cookies"] = reg["cookies"]

    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "accounts.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": int(time.time()), "channel": "legacy+bloks",
                            "contact": email, "c_user": reg["c_user"],
                            "state": state, "cookies": reg["cookies"], "proxy": proxy},
                           ensure_ascii=False) + "\n")
    print("已写 data/accounts.jsonl")
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--country", default="US")
    ap.add_argument("--password", default="Nq7#vLm2Rt9xWp4")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--yes", action="store_true", help="真的注册并确认")
    a = ap.parse_args()
    if not a.yes and not a.dry:
        print("加 --yes 才会真跑（会创建一个账号）")
        return 2
    out = run(a.country, a.password, do_submit=not a.dry)
    print("\n结果:", json.dumps({k: (v if k != "reg" and k != "cookies" else "...") for k, v in out.items()},
                              ensure_ascii=False)[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
