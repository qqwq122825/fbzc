"""邮箱确认的 Bloks 提交（`async/wbloks/fetch`）—— 从真实浏览器抓包还原。

## 抓到的请求（来自 capture/bloks_confirm_capture.py 的一次真实点击）

```
POST https://m.facebook.com/async/wbloks/fetch/
     ?appid=com.bloks.www.bloks.caa.reg.confirmation.async
     &type=action
     &__bkv=a41584e0380212675ccae5bdaebf32e515df72d3ebef674fc81950eb183bbcc0
Content-Type: application/x-www-form-urlencoded;charset=UTF-8
Referer: https://m.facebook.com/confirmemail.php

__user=61596960383244
__a=1
__req=3
__hs=20778.BP:mtouch_pkg.2.0...0
dpr=3
__ccg=EXCELLENT
__rev=1055724655
__s=i320ap:p8m9ba:rv9drk
__hsi=7710591231292856231
__dyn=1KidAG1mws8-t0BBBwno4a2i5U4e1FwKwSwMxW0Horx67o1g8hw23E52q1ew2io0D24o1sE522G0pS0H83bw4FwmE2ewnE2Lx-…
fb_dtsg=NAfyeisgckMZbo7MU8bR6RRtwnrynlhqpEZn8sEvqnlHc9eQk0MCeVg:3:1795261445
jazoest=25818
lsd=zidKNy_UGNo68HksaZiY2l
params={"params":"{\"server_params\":{\"event_request_id\":\"…\",
        \"text_input_id\":\"8qm2kb:52\",
        \"reg_info\":\"{…first_name,last_name,contactpoint,contactpoint_type,gender,
                      user_id,confirmation_code…}\"}}"}
```

**这就是之前 `POST /confirmemail.php` 一直报 "Something Went Wrong" 的原因**：
缺的正是 `fb_dtsg` + `lsd` + `jazoest` 这三个 CSRF 值（Bloks 不吃裸表单）。

## 关键前提：必须同一个出口 IP

实测教训：
- 注册时出口是 `172.56.2.248`（T-Mobile）
- 该端口几分钟后就失效；重新取 IP 得到 `70.40.67.215`
- 用新 IP 打开确认页 → **直接跳 `/checkpoint/1501092823525282/`**

所以确认动作必须和注册在**同一个端口、同一次会话**里完成。
本模块只负责"提交"这一步，串联逻辑见 `core/full_flow.py`。

用法:
    # 只抓页面 token，验证能不能取到（不提交）
    python3 core/confirm_bloks.py --cookies /tmp/last_reg.json --proxy socks5h://ip:port --dump

    # 真提交
    python3 core/confirm_bloks.py --cookies /tmp/last_reg.json --code 36609 \
            --proxy socks5h://ip:port --reg-info reg_info.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

APPID = "com.bloks.www.bloks.caa.reg.confirmation.async"
DEFAULT_BKV = "a41584e0380212675ccae5bdaebf32e515df72d3ebef674fc81950eb183bbcc0"
CONFIRM_URL = "https://m.facebook.com/confirmemail.php"
FETCH_URL = "https://m.facebook.com/async/wbloks/fetch/"
UA = ("Mozilla/5.0 (Linux; Android 4.4.2; Nexus 5) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/34.0.1847.114 Mobile Safari/537.36")


def session(proxy: str | None, cookies: dict) -> requests.Session:
    s = requests.Session()
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    s.headers.update({"User-Agent": UA,
                      "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                      "Accept-Language": "en-US,en;q=0.9",
                      "Accept-Encoding": "gzip, deflate"})
    for k, v in (cookies or {}).items():
        s.cookies.set(k, str(v), domain=".facebook.com")
    return s


def jazoest(lsd: str) -> str:
    """jazoest = "2" + lsd 各字节之和（页面通常是现成的，这里做兜底计算）"""
    return "2" + str(sum(lsd.encode()))


def extract_tokens(html: str) -> dict:
    """从页面里抽 CSRF 三元组与 mtouch 上下文参数。"""
    out = {"lsd": "", "fb_dtsg": "", "jazoest": "", "rev": "", "hsi": "", "dyn": "", "s": ""}
    pats = {
        "lsd": [r'"lsd"\s*:\s*"([A-Za-z0-9_-]{6,40})"', r'name="lsd"\s+value="([^"]+)"',
                r'"token"\s*:\s*"([A-Za-z0-9_-]{8,40})"'],
        "fb_dtsg": [r'([A-Za-z0-9_-]{20,60}:\d{1,3}:\d{10})',
                    r'"DTSGInitialData"\s*,\s*\[\s*\]\s*,\s*\{\s*"token"\s*:\s*"([^"]+)"',
                    r'name="fb_dtsg"\s+value="([^"]+)"',
                    r'"fb_dtsg"\s*:\s*"([^"]+)"',
                    r'"dtsg"\s*:\s*"([^"]+)"'],
        "jazoest": [r'name="jazoest"\s+value="(\d+)"', r'"jazoest"\s*:\s*"(\d+)"'],
        "rev": [r'"server_revision"\s*:\s*(\d+)', r'__rev=(\d+)'],
        "hsi": [r'"hsi"\s*:\s*"?(\d+)"?', r'__hsi=(\d+)'],
        "dyn": [r'__dyn=([A-Za-z0-9_-]{20,})', r'"dyn"\s*:\s*"([^"]{20,})"'],
        "s": [r'__s=([A-Za-z0-9_:%-]{6,})'],
    }
    for k, ps in pats.items():
        for p in ps:
            m = re.search(p, html)
            if m:
                out[k] = m.group(1)
                break
    if not out["jazoest"] and out["lsd"]:
        out["jazoest"] = jazoest(out["lsd"])
    return out


def fetch_tokens(s: requests.Session, url: str = CONFIRM_URL) -> tuple[dict, str]:
    r = s.get(url, timeout=45)
    html = r.text or ""
    tok = extract_tokens(html)
    # 页面没给 fb_dtsg 时，走 mtouch 的 /ajax/dtsg/
    if not tok["fb_dtsg"]:
        for method in ("get", "post"):
            try:
                rr = getattr(s, method)("https://m.facebook.com/ajax/dtsg/", timeout=30)
                j = rr.json() if rr.text.strip().startswith("{") else {}
                cand = (j.get("dtsg") or j.get("token")
                        or (j.get("payload") or {}).get("dtsg") if isinstance(j, dict) else "")
                if cand:
                    tok["fb_dtsg"] = str(cand)
                    break
            except Exception:
                continue
    if not tok["rev"]:
        tok["rev"] = "1055724655"
    return tok, html


# 抓包还原出的 reg_info 完整字段集（缺一项都可能被服务端判为不完整）
REG_INFO_TEMPLATE = {
    "first_name": None, "last_name": None, "full_name": None,
    "contactpoint": None, "ar_contactpoint": None, "contactpoint_type": None,
    "is_using_unified_cp": None, "unified_cp_screen_variant": None,
    "is_cp_auto_confirmed": False, "is_cp_auto_confirmable": False, "is_cp_claimed": False,
    "confirmation_code": None, "birthday": None, "birthday_derived_from_age": None,
    "age_range": None, "did_use_age": None, "os_shared_age_range": None,
    "gender": None, "use_custom_gender": False, "custom_gender": None,
    "encrypted_password": None, "username": None, "username_prefill": None,
    "accounts_list_client": None, "fb_conf_source": None,
    "device_id": None, "ig4a_qe_device_id": None, "family_device_id": None,
    "fdid_available_on_start": None, "fdid_rid_available_on_start": None,
    "asdid_available_on_start": None, "user_id": None,
    "safetynet_token": None, "safetynet_response": None, "skip_slow_rel_check": False,
    "machine_id": None, "profile_photo": None,
}


def build_reg_info(first: str = "", last: str = "", contactpoint: str = "",
                   contact_type: str = "email", gender: int = 2, user_id: str = "",
                   machine_id: str = "", device_id: str = "") -> dict:
    """按抓包字段集拼 reg_info（没有的置 null，结构与真实请求一致）。"""
    ri = dict(REG_INFO_TEMPLATE)
    ri.update({
        "first_name": first, "last_name": last,
        "full_name": f"{first} {last}".strip(),
        "contactpoint": contactpoint, "contactpoint_type": contact_type,
        "gender": gender, "user_id": user_id,
        "machine_id": machine_id, "device_id": device_id,
    })
    return ri


def build_params(code: str, reg_info: dict, text_input_id: str = "8qm2kb:52") -> str:
    """还原 params 字段：外层 {"params": "<内层JSON字符串>"}"""
    inner = {
        "server_params": {
            "event_request_id": __import__("uuid").uuid4().__str__(),
            "text_input_id": text_input_id,
            "sms_retriever_started_prior_step": 0,
            "wa_timer_id": "wa_retriever",
            "reg_info": json.dumps(reg_info, ensure_ascii=False, separators=(",", ":")),
            "confirmation_code": code,
        }
    }
    return json.dumps({"params": json.dumps(inner, separators=(",", ":"))},
                      separators=(",", ":"))


def submit(s: requests.Session, tok: dict, code: str, user_id: str, reg_info: dict,
           bkv: str = DEFAULT_BKV) -> requests.Response:
    data = {
        "__user": str(user_id), "__a": "1", "__req": "3",
        "__hs": tok.get("hs", "") or "20778.BP:mtouch_pkg.2.0...0",
        "dpr": "3", "__ccg": "EXCELLENT",
        "__rev": tok.get("rev", ""), "__s": tok.get("s", ""),
        "__hsi": tok.get("hsi", ""), "__dyn": tok.get("dyn", ""),
        "fb_dtsg": tok.get("fb_dtsg", ""), "jazoest": tok.get("jazoest", ""),
        "lsd": tok.get("lsd", ""),
        "params": build_params(code, reg_info),
    }
    url = f"{FETCH_URL}?appid={APPID}&type=action&__bkv={bkv}"
    h = {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
         "Referer": CONFIRM_URL, "Origin": "https://m.facebook.com",
         "X-Requested-With": "XMLHttpRequest"}
    return s.post(url, data=data, headers=h, timeout=60, allow_redirects=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cookies", default="/tmp/last_reg.json")
    ap.add_argument("--proxy", default="")
    ap.add_argument("--code", default="")
    ap.add_argument("--reg-info", default="", help="reg_info.json；不给则用 cookies 里的 user_id 最小集")
    ap.add_argument("--bkv", default=DEFAULT_BKV)
    ap.add_argument("--dump", action="store_true", help="只抓 token 并打印，不提交")
    a = ap.parse_args()

    cfg = json.load(open(a.cookies, encoding="utf-8"))
    ck = cfg.get("cookies") or cfg
    user_id = str(ck.get("c_user", ""))
    s = session(a.proxy or None, ck)

    tok, html = fetch_tokens(s)
    print("token 抓取结果:")
    for k, v in tok.items():
        print(f"  {k:<9}{(v[:46] + '…') if len(str(v)) > 46 else v}")
    missing = [k for k in ("lsd", "fb_dtsg") if not tok.get(k)]
    if missing:
        print(f"\n[!] 缺 {missing}：这份会话可能已被 checkpoint，或页面结构变了")
    if a.dump or not a.code:
        print("\n（--dump 模式，未提交）")
        return 0

    reg_info = {}
    if a.reg_info and os.path.exists(a.reg_info):
        reg_info = json.load(open(a.reg_info, encoding="utf-8"))
    else:
        reg_info = {"user_id": user_id, "first_name": "", "last_name": "",
                    "contactpoint": "", "contactpoint_type": "email", "gender": 2}

    r = submit(s, tok, a.code, user_id, reg_info, a.bkv)
    print(f"\n提交: {r.status_code}  loc={r.headers.get('location','')[:80]}")
    print((r.text or "")[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
