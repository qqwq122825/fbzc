"""用真实 Chrome 点「最后一下」：注册 → 收码 → 浏览器填码 → 点 Next → 复核。

为什么最后一步交给浏览器：
  确认页是 Bloks 渲染的，提交需要 `text_input_id` / `__hs` / `__dyn` 这类运行时值，
  纯协议还差一层（详见 BLOKS_CONFIRM.md）。浏览器点一下就都对了。

铁律：**注册、收码、点确认必须在同一个出口、同一次会话里连做**。
      该代理端口几分钟就失效，换 IP 会被直接判到 /checkpoint/。

用法:
    python3 core/browser_confirm.py --country US [--yes] [--headed]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import proxy_udeal
from core.email_pool import ApiOkReader, extract_fb_confirm
from core.legacy_reg import register as legacy_register
from core.names import generate as gen_name

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CAP = os.path.join(DATA, "capture")
APIOK_KEY = os.environ.get("APIOK_KEY", "idr_85f1ad8109b15afab74d09d4286da3b8")
PASSWORD = "Nq7#vLm2Rt9xWp4"


def ok(m): print(f"✅ {m}")
def bad(m): print(f"❌ {m}")


def wait_code(mail: ApiOkReader, email: str, tries: int = 12, gap: int = 8) -> str:
    mail.mailbox_id = base64.b64encode(email.encode()).decode()
    for i in range(tries):
        try:
            msgs = mail._list()
            if msgs:
                mid = msgs[0].get("id") or msgs[0].get("message_id")
                body = mail._detail(mid) if mid else json.dumps(msgs[0], ensure_ascii=False)
                got = extract_fb_confirm(body)
                if got.get("code"):
                    return got["code"]
        except Exception:
            pass
        time.sleep(gap)
    return ""


def click_confirm(proxy: str, cookies: dict, code: str, headed: bool = False,
                  timeout: int = 90) -> dict:
    """用 Chrome 打开确认页、填码、点 Next，返回抓到的请求与最终状态。"""
    from playwright.sync_api import sync_playwright

    p_arg = proxy or ""
    args = ["--disable-blink-features=AutomationControlled",
            "--blink-settings=imagesEnabled=false"]     # 关图，省流量
    if p_arg:
        args.append(f"--proxy-server={p_arg}")

    captured: list[dict] = []
    os.makedirs(CAP, exist_ok=True)

    with sync_playwright() as p:
        browser = None
        for kw in ({"channel": "chrome"}, {}):
            try:
                browser = p.chromium.launch(headless=not headed, args=args, **kw)
                break
            except Exception as e:
                print(f"[!] launch {kw} 失败: {str(e)[:70]}")
        if browser is None:
            raise SystemExit("没有可用浏览器")

        ctx = browser.new_context(
            viewport={"width": 412, "height": 915},
            user_agent=("Mozilla/5.0 (Linux; Android 13; SM-A536B) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36"),
            locale="en-US", timezone_id="America/New_York",
        )
        cks = [{"name": k, "value": str(v), "domain": ".facebook.com", "path": "/",
                "httpOnly": k in ("xs", "c_user"), "secure": True}
               for k, v in cookies.items()]
        ctx.add_cookies(cks)
        page = ctx.new_page()

        def on_req(r):
            if r.method == "POST" and ("bloks" in r.url or "graphql" in r.url):
                captured.append({"url": r.url, "body": (r.post_data or "")[:4000]})

        page.on("request", on_req)
        print("[*] 打开确认页…")
        last_err = ""
        for attempt in range(3):
            try:
                # 先打一个轻量页把隧道暖起来，再进确认页
                if attempt == 0:
                    try:
                        page.goto("https://m.facebook.com/robots.txt",
                                  wait_until="commit", timeout=60_000)
                    except Exception:
                        pass
                page.goto("https://m.facebook.com/confirmemail.php",
                          wait_until="commit", timeout=120_000)
                page.wait_for_timeout(4500)
                last_err = ""
                break
            except Exception as e:
                last_err = f"{type(e).__name__}: {str(e)[:60]}"
                print(f"[!] 第 {attempt+1} 次打开失败: {last_err}")
                page.wait_for_timeout(2500)
        if last_err:
            bad(f"确认页打不开（{last_err}）—— 代理隧道不稳或端口已失效")
            return {"state": "NAV_FAIL", "url": page.url, "error": last_err}
        print(f"[*] 落地: {page.url[:100]}")

        if "checkpoint" in page.url:
            bad("打开了 checkpoint —— 说明出口 IP 和注册时不一致")
            page.screenshot(path=os.path.join(CAP, f"checkpoint_{int(time.time())}.png"))
            browser.close()
            return {"state": "CHECKPOINT", "url": page.url}

        filled = False
        for sel in ("input[aria-label*='code' i]", "input[placeholder*='code' i]",
                    "input[name='code']", "input[inputmode='numeric']",
                    "input[type='tel']", "input[type='number']", "input[type='text']"):
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                el.type(code, delay=120)          # 逐字符输入，别 fill
                filled = True
                print(f"[*] 已输入验证码到 {sel}")
                break
        if not filled:
            bad("没找到验证码输入框")
            page.screenshot(path=os.path.join(CAP, f"nofield_{int(time.time())}.png"))
            browser.close()
            return {"state": "NO_INPUT", "url": page.url}

        page.wait_for_timeout(900)
        clicked = False
        for sel in ("[role='button']:has-text('Next')", "button:has-text('Next')",
                    "div[role='button']:has-text('Next')", "a:has-text('Next')",
                    "[role='button']:has-text('Continue')", "button"):
            for el in page.query_selector_all(sel):
                try:
                    if el.is_visible():
                        el.click()
                        clicked = True
                        print(f"[*] 点击 {sel}")
                        break
                except Exception:
                    continue
            if clicked:
                break
        if not clicked:
            page.keyboard.press("Enter")
            print("[*] 用 Enter 提交")

        page.wait_for_timeout(9000)
        print(f"[*] 提交后落地: {page.url[:110]}")
        shot = os.path.join(CAP, f"confirmed_{int(time.time())}.png")
        try:
            page.screenshot(path=shot)
        except Exception:
            shot = ""
        try:
            txt = page.inner_text("body")[:300]
        except Exception:
            txt = ""
        final = page.url
        browser.close()

    state = "ACTIVE" if "confirmemail" not in final and "checkpoint" not in final else \
            ("CHECKPOINT" if "checkpoint" in final else "PENDING_EMAIL")
    return {"state": state, "url": final, "text": txt, "shot": shot,
            "requests": captured}


def start_bridge(socks_endpoint: str, listen: str = "127.0.0.1:18080") -> subprocess.Popen:
    """起本地 HTTP 代理桥，把 Chrome 的 CONNECT 转到上游 SOCKS5。

    Chrome 直连 socks5:// 会因为隧道抖动报 ERR_TIMED_OUT / ERR_NETWORK_CHANGED；
    走本地桥后 Chrome 只跟 127.0.0.1 说话，稳得多（实测 curl 通过率 100%）。
    """
    bridge = os.path.join(ROOT, "tools", "socks_bridge.py")
    proc = subprocess.Popen([sys.executable, bridge, "--socks", socks_endpoint,
                             "--listen", listen],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2.0)
    return proc


def run(country: str, headed: bool) -> dict:
    rows, err = proxy_udeal.acquire(num=3, region=country.lower(), country=country,
                                    do_probe=True, workers=3)
    if err or not rows:
        bad(f"取不到出口: {err}")
        return {}
    proxy = rows[0].url
    ok(f"出口 {rows[0].egress_ip} ({rows[0].country}) {rows[0].latency_ms}ms")

    mail = ApiOkReader(APIOK_KEY, interval=8)
    email = mail.generate()
    first, last = gen_name(country, 2)
    ok(f"邮箱 {email} / 姓名 {first} {last}")

    reg = legacy_register(proxy, email, PASSWORD, first, last, 2, (13, 7, 1993), country=country)
    if not reg.get("ok"):
        bad(f"注册失败 {reg.get('status')} {str(reg.get('detail'))[:100]}")
        return {"reg": reg}
    ok(f"注册成功 c_user={reg['c_user']}")

    code = wait_code(mail, email)
    if not code:
        bad("没等到验证码")
        return {"c_user": reg["c_user"], "email": email}
    ok(f"验证码 {code}")

    # 用同一个出口 + 本地桥打开浏览器点确认
    socks_ep = proxy.replace("socks5h://", "").replace("socks5://", "")
    bridge = start_bridge(socks_ep)
    try:
        res = click_confirm("http://127.0.0.1:18080", reg["cookies"], code, headed=headed)
    finally:
        bridge.terminate()
    ok(f"浏览器结果: {res.get('state')}  {str(res.get('url'))[:70]}")
    if res.get("requests"):
        for r in res["requests"]:
            print(f"   抓包: POST {r['url'][:90]}  body={len(r['body'])}B")

    state = res.get("state", "UNKNOWN")
    with open(os.path.join(DATA, "accounts.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": int(time.time()), "channel": "legacy+browser",
                            "contact": email, "password": PASSWORD,
                            "c_user": reg["c_user"], "state": state,
                            "cookies": reg["cookies"], "proxy": proxy,
                            "profile": {"first": first, "last": last}},
                           ensure_ascii=False) + "\n")
    return {"c_user": reg["c_user"], "email": email, "state": state, "res": res}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--country", default="US")
    ap.add_argument("--headed", action="store_true", help="显示浏览器窗口（调试用）")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    if not a.yes:
        print("加 --yes 才真跑（会创建一个账号）")
        return 2
    out = run(a.country, a.headed)
    print("\n" + json.dumps({k: v for k, v in out.items() if k != "res"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
