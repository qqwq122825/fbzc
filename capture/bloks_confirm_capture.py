"""用真实浏览器抓「邮箱确认」的 Bloks 提交，顺便把请求体存下来供协议复现。

为什么需要它：确认页是 Bloks(fb_web) 渲染的，验证码提交走一条带会话 token 的
POST，纯静态推不出来。用浏览器代跑一次，把请求抓下来，之后就能脚本化。

用法:
    python3 capture/bloks_confirm_capture.py --cookies /tmp/last_reg.json --code 36609 \
            --proxy socks5://137.184.119.209:8091

输出:
    data/capture/bloks_confirm_<ts>.json   抓到的请求（method/url/headers/body/响应）
"""
from __future__ import annotations

import argparse
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data", "capture")

INTEREST = ("bloks", "ajax", "graphql", "confirmemail", "confirm", "code")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cookies", default="/tmp/last_reg.json", help="含 cookies 的 json")
    ap.add_argument("--code", default="")
    ap.add_argument("--url", default="https://m.facebook.com/confirmemail.php")
    ap.add_argument("--proxy", default="", help="socks5://host:port 或 http://host:port")
    ap.add_argument("--headless", action="store_true", default=True)
    ap.add_argument("--headed", dest="headless", action="store_false")
    ap.add_argument("--timeout", type=int, default=90)
    a = ap.parse_args()

    from playwright.sync_api import sync_playwright

    cfg = json.load(open(a.cookies, encoding="utf-8"))
    ck = cfg.get("cookies") or cfg
    code = a.code or ""

    os.makedirs(OUT_DIR, exist_ok=True)
    captured: list[dict] = []

    launch_args = ["--disable-blink-features=AutomationControlled"]
    if a.proxy:
        launch_args.append(f"--proxy-server={a.proxy}")

    with sync_playwright() as p:
        browser = None
        for kw in ({"channel": "chrome"}, {}):
            try:
                browser = p.chromium.launch(headless=a.headless, args=launch_args, **kw)
                print(f"[*] 浏览器: {kw or 'bundled chromium'}")
                break
            except Exception as e:
                print(f"[!] launch {kw} 失败: {str(e)[:80]}")
        if browser is None:
            raise SystemExit("没有可用浏览器：先跑 `playwright install chromium`")
        ctx = browser.new_context(
            viewport={"width": 412, "height": 915},
            user_agent=("Mozilla/5.0 (Linux; Android 13; SM-A536B) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36"),
            locale="en-US", timezone_id="America/New_York",
        )
        # 注入会话 cookie
        cookies = []
        for k, v in ck.items():
            if k in ("c_user", "xs", "datr", "fr", "sb", "wd", "presence"):
                cookies.append({"name": k, "value": str(v), "domain": ".facebook.com",
                                "path": "/", "httpOnly": k in ("xs", "c_user"), "secure": True})
        if cookies:
            ctx.add_cookies(cookies)

        page = ctx.new_page()

        def on_request(req):
            u = req.url
            if req.method in ("POST", "PUT") and any(k in u.lower() for k in INTEREST):
                try:
                    body = req.post_data
                except Exception:
                    body = None
                captured.append({
                    "method": req.method, "url": u,
                    "headers": {k: v for k, v in req.headers.items()},
                    "body": (body or "")[:60000],
                    "ts": time.time(),
                })

        def on_response(resp):
            u = resp.url
            if any(k in u.lower() for k in INTEREST):
                try:
                    txt = resp.text()[:1500]
                except Exception:
                    txt = ""
                for rec in reversed(captured):
                    if rec["url"] == u and "resp" not in rec:
                        rec["resp_status"] = resp.status
                        rec["resp"] = txt
                        rec["resp_headers"] = dict(resp.headers)
                        break

        page.on("request", on_request)
        page.on("response", on_response)

        print(f"[*] 打开 {a.url}")
        page.goto(a.url, wait_until="domcontentloaded", timeout=a.timeout * 1000)
        page.wait_for_timeout(4000)
        print(f"[*] 落地: {page.url}")

        # 找输入框并填码
        filled = False
        for sel in ("input[name='code']", "input[aria-label*='code' i]",
                    "input[placeholder*='code' i]", "input[inputmode='numeric']",
                    "input[type='tel']", "input[type='number']", "input[type='text']",
                    "input:not([type])"):
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.fill(code or "00000")
                    filled = True
                    print(f"[*] 填入 {sel}")
                    break
            except Exception:
                continue
        if not filled:
            print("[!] 没找到输入框，dump 一下可见 input：")
            for el in page.query_selector_all("input"):
                try:
                    print("   ", el.get_attribute("name"), el.get_attribute("type"),
                          "visible=", el.is_visible())
                except Exception:
                    pass

        page.wait_for_timeout(1200)
        try:
            vals = [(el.get_attribute("name"), el.input_value()) for el in page.query_selector_all("input")
                    if el.is_visible()]
            print(f"[*] 输入框现状: {vals}")
        except Exception:
            pass
        # 找提交按钮
        clicked = False
        for sel in ("[role='button']:has-text('Next')", "button:has-text('Next')",
                    "div[role='button']:has-text('Next')", "a:has-text('Next')",
                    "[role='button']:has-text('Continue')", "button:has-text('Continue')",
                    "[role='button']:has-text('Confirm')", "button"):
            try:
                for el in page.query_selector_all(sel):
                    if el.is_visible():
                        el.click()
                        clicked = True
                        print(f"[*] 点击 {sel}")
                        break
                if clicked:
                    break
            except Exception:
                continue
        if not clicked:
            try:
                page.keyboard.press("Enter")
                print("[*] 用 Enter 提交")
            except Exception:
                pass

        page.wait_for_timeout(10000)
        print(f"[*] 提交后落地: {page.url}")
        try:
            title = page.title()
            body_txt = page.inner_text("body")[:300]
        except Exception:
            title, body_txt = "", ""
        shot = os.path.join(OUT_DIR, f"bloks_confirm_{int(time.time())}.png")
        try:
            page.screenshot(path=shot, full_page=False)
        except Exception:
            shot = ""

        browser.close()

    result = {"url": a.url, "code": code, "final_url": "", "title": "",
              "text": "", "screenshot": shot, "requests": captured}
    try:
        result["final_url"] = page.url
        result["title"] = title
        result["text"] = body_txt
    except Exception:
        pass

    out = os.path.join(OUT_DIR, f"bloks_confirm_{int(time.time())}.json")
    json.dump(result, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n[+] 抓到 {len(captured)} 条请求 -> {out}")
    for r in captured:
        print(f"  {r['method']} {r['url'][:100]}")
        print(f"      body: {(r.get('body') or '')[:220]}")
    if shot:
        print(f"[+] 截图 {shot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
