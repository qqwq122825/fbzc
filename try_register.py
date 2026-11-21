#!/usr/bin/env python3
"""分层试跑：先用 Python 把「IP 池 / 接码 / 设备环境 / 注册」四层逐个打通，再合起来跑。

为什么分层：一次性跑全链路，失败时你不知道是 IP 脏、号段废、指纹不一致、还是协议过期。
每层都有独立的通过标准，先各自绿了再合体。

    # 1) 预检（不花钱、不发注册请求）
    python3 try_register.py --herosms-key KEY --stage preflight

    # 2) 只取 IP 池并写盘
    python3 try_register.py --stage proxy --proxy-num 20 --want-country US

    # 3) 只查接码账户（余额 + fb 价格，不下单）
    python3 try_register.py --herosms-key KEY --stage sms --want-country 187

    # 4) 真跑一单（会花一次接码钱 + 发一次注册）
    python3 try_register.py --herosms-key KEY --stage register --contact phone --yes
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import proxy_udeal
from core.device_env import DeviceEnv
from core.providers import ProxyPool

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PROTO = os.path.join(DATA, "protocol_template.json")

OK, NO, WARN = "✅", "❌", "⚠️ "


def line(ok: bool | None, title: str, detail: str = "") -> None:
    mark = OK if ok is True else (NO if ok is False else WARN)
    print(f"{mark} {title}" + (f"  —  {detail}" if detail else ""))


# ---------------------------------------------------------------- 1. 设备环境
def stage_env(country: str) -> tuple[DeviceEnv | None, bool]:
    print("\n[1] 设备环境")
    env = DeviceEnv.make(country)
    good, issues = env.health(country)
    line(good, f"一致性别校验（{country} / {env.brand} {env.model}）",
         "全部自洽" if good else f"{len(issues)} 处矛盾")
    for i in issues:
        print(f"     - {i}")
    if good:
        p = env.save()
        print(f"     机型 {env.model} / MCC {env.mcc_mnc} / {env.locale} / {env.timezone}")
        print(f"     device_id {env.device_id}  adid {env.adid[:8]}…  -> {os.path.basename(p)}")
    return env, good


# ---------------------------------------------------------------- 2. IP 池
def stage_proxy(num: int, want: str, workers: int, write: bool) -> tuple[list, bool]:
    print("\n[2] 代理池（udealproxy）")
    rows, err = proxy_udeal.acquire(num=num, country=want or None, workers=workers)
    if err:
        line(False, "取 IP 失败", err)
        return [], False
    alive = [r for r in rows if r.ok]
    line(bool(alive), f"探活 {len(rows)} 条", f"存活 {len(alive)}")
    best = sorted(alive, key=lambda r: r.latency_ms)[:5]
    for r in best:
        print(f"     {r.country or '??':<3} {r.egress_ip:<16} {r.latency_ms:>4}ms  {r.isp[:24]}")
    if not alive:
        return [], False
    if want:
        good = [r for r in alive if r.country == want.upper()]
        line(bool(good), f"地理对齐 {want}", f"匹配 {len(good)}/{len(alive)}")
        if not good:
            return [], False
    if write:
        p = proxy_udeal.write_pool(rows)
        print(f"     已写 {p}")
    return alive, True


# ---------------------------------------------------------------- 3. 接码
def stage_sms(key: str, country: str, service: str) -> tuple[bool, dict]:
    print("\n[3] 接码（hero-sms）")
    from core.sms_herosms import HeroSMS, SmsError

    cli = HeroSMS(key)
    info: dict = {}
    try:
        bal = cli.balance()
        info["balance"] = bal
        line(True, "余额可用", f"{bal}")
    except SmsError as e:
        line(False, "余额查询失败", f"{e.code} {e.detail}")
        return False, info

    try:
        p = cli.prices(service, country)
        node = p
        for k in (country, service):
            if isinstance(node, dict) and k in node:
                node = node[k]
        cost = node.get("cost") if isinstance(node, dict) else None
        cnt = node.get("count") if isinstance(node, dict) else None
        info["cost"], info["count"] = cost, cnt
        enough = cost is None or float(cost) <= info.get("balance", 0)
        line(enough, f"{service} / 国家{country} 有号",
             f"单价 {cost} / 库存 {cnt} / 余额 {info.get('balance')}")
        return enough, info
    except SmsError as e:
        hint = {
            "NO_NUMBERS": "该国家当前没有 fb 号源，换 country 编号（用 --list-countries 查）",
            "WRONG_SERVICE": "service 名不对，试 fb / facebook",
            "WRONG_COUNTRY": "country 编号不对",
        }.get(e.code, "")
        line(False, f"{service} / 国家{country} 价格/库存查询失败", f"{e.code} {hint}")
        return False, info


# ---------------------------------------------------------------- 3.5 邮箱池
def build_pool(args):
    from core.email_pool import DirSink, EmailPool, ImapReader, MailpitReader

    if args.mail_sink:
        reader = DirSink(args.mail_sink); kind = "sink"
    elif args.mailpit:
        reader = MailpitReader(args.mailpit); kind = "mailpit"
    elif args.imap_host and args.imap_user and args.imap_pass:
        reader = ImapReader(args.imap_host, args.imap_user, args.imap_pass, args.imap_port)
        kind = "imap"
    else:
        return None, ""
    return EmailPool(reader, args.email_domain or "yourdomain.example",
                     plus_base=args.plus_base), kind


def stage_email(args) -> tuple[str, bool]:
    print("\n[3.5] 邮箱池")
    pool, kind = build_pool(args)
    if pool is None:
        line(False, "邮箱池未配置",
             "给 --imap-host/--imap-user/--imap-pass，或 --mail-sink <目录>，或 --mailpit <api>")
        return "", False
    addr = args.email or pool.acquire()
    line(True, f"地址已就绪（{kind}）", addr)
    if args.stage == "email" and args.mail_wait:
        print(f"     等信 {args.mail_wait}s（往这个地址发一封测试信即可）…")
        got = pool.wait_code(addr, args.mail_wait)
        line(got.get("ok", False), "解析结果", f"code={got.get('code')} link={str(got.get('link'))[:50]}")
        return addr, bool(got.get("ok"))
    return addr, True


# ---------------------------------------------------------------- 4. 注册
def stage_register(args, env: DeviceEnv, proxies: list, phone: str = "") -> bool:
    print("\n[4] 注册")
    from core.client import RegClient

    if args.no_proxy:
        proxy = None
        line(None, "已指定 --no-proxy", "只测装配；真发会暴露本机 IP")
    elif proxies:
        px = sorted(proxies, key=lambda r: r.latency_ms)[0]
        if px.country and px.country != env.country_iso:
            line(False, "IP/指纹国家冲突", f"IP {px.country} != env {env.country_iso}，别发")
            return False
        proxy = px.url
        print(f"     出口 {px.egress_ip} ({px.country}) {px.latency_ms}ms")
    else:
        proxy = None
        line(None, "无可用代理", "直连会立刻暴露本机 IP，仅限联调")

    channel = args.channel
    if channel == "mobile" and not os.path.exists(PROTO):
        line(False, "缺 data/protocol_template.json",
             "mobile 通道要先抓一次真机注册（capture/RUNBOOK.md 第 2 节）")
        return False

    cli = RegClient(env=env, proxy=proxy)
    contact = phone or args.email
    pool = None
    if not contact and args.contact == "email":
        pool, kind = build_pool(args)
        if pool is not None:
            contact = pool.acquire()
            print(f"     邮箱池({kind}) 分配: {contact}")
    if not contact:
        line(False, "缺联系方式", "配邮箱池（--imap-* / --mail-sink / --mailpit）或给 --email")
        return False

    if not args.yes:
        print(f"{WARN} 未加 --yes，只做装配演练（不发注册请求）")
        print(f"     将提交: {env.user_agent()[:80]}...")
        print(f"     contact={contact} country={env.country_iso} proxy={(proxy or 'direct')[:40]}")
        return True

    t0 = time.time()
    if channel == "mobile":
        res = cli.mobile_register(args.first, args.last, contact, args.password,
                                  args.sex, (args.day, args.month, args.year),
                                  contact_type="phone" if phone else "email")
    else:
        res = cli.web_register(args.first, args.last, contact, args.password,
                               args.sex, (args.day, args.month, args.year),
                               use_email=not bool(phone))
    dt = time.time() - t0
    print(f"     {res.stage} code={res.error_code} http={res.raw_status} {dt:.1f}s")
    print(f"     detail: {res.detail[:200]}")
    if res.ok:
        with open(os.path.join(DATA, "accounts.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": int(time.time()), "contact": contact, "password": args.password,
                "stage": res.stage, "account_id": res.account_id,
                "cookies": res.cookies, "country": env.country_iso,
                "proxy": proxy, "fp_id": env.fp_id,
            }, ensure_ascii=False) + "\n")
        line(True, "注册请求成功", "已写 data/accounts.jsonl")
        if pool is not None and res.stage == "PENDING_CONFIRM":
            print(f"     等确认信（{args.mail_wait}s）…")
            got = pool.wait_code(contact, args.mail_wait)
            if got.get("ok"):
                line(True, "收到确认邮件", f"code={got.get('code')} link={str(got.get('link'))[:56]}")
                if got.get("code"):
                    cres = cli.confirm(got["code"])
                    line(cres.ok, "提交确认码", f"{cres.stage} {cres.error_code}")
            else:
                line(False, "没等到确认信", str(got.get("reason")))
    else:
        hint = {
            "CHECKPOINT_REQUIRED": "落 570：换指纹+换IP+换号段，别原地重试",
            "CREATE_REJECTED": "被拒：优先怀疑 IP 段或号段",
            "NO_TEMPLATE": "缺模板，先抓包",
        }.get(res.error_code, "")
        line(False, "注册失败", hint or res.error_code)
    return res.ok


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="FB 注册链路的 Python 分层试跑")
    ap.add_argument("--stage", default="preflight",
                    choices=["preflight", "env", "proxy", "sms", "email", "register"])
    ap.add_argument("--country", default="US", help="目标国家（指纹/locale/时区都跟它走）")
    ap.add_argument("--want-country", default="", help="IP 池要过滤的出口国家；sms 阶段是国家编号")
    ap.add_argument("--proxy-num", type=int, default=10)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--herosms-key", default=os.environ.get("HEROSMS_KEY", ""))
    ap.add_argument("--service", default="fb")
    ap.add_argument("--channel", default="web", choices=["web", "mobile"])
    ap.add_argument("--contact", default="phone", choices=["phone", "email"])
    ap.add_argument("--email", default="", help="直接给一个地址；不给则由邮箱池生成")
    ap.add_argument("--email-domain", default="")
    ap.add_argument("--plus-base", default="", help="plus 寻址的本地部分，如 catchall")
    ap.add_argument("--imap-host", default="")
    ap.add_argument("--imap-user", default="")
    ap.add_argument("--imap-pass", default="")
    ap.add_argument("--imap-port", type=int, default=993)
    ap.add_argument("--mail-sink", default="", help="邮件落地目录（自建服务器/Maildir）")
    ap.add_argument("--mailpit", default="", help="Mailpit/MailHog API，如 http://127.0.0.1:8025")
    ap.add_argument("--mail-wait", type=int, default=180)
    ap.add_argument("--phone", default="", help="已有号码可直接给，跳过购买")
    ap.add_argument("--first", default="Michael")
    ap.add_argument("--last", default="Wilson")
    ap.add_argument("--password", default="Nq7#vLm2Rt9xWp4")
    ap.add_argument("--sex", type=int, default=2, choices=[1, 2])
    ap.add_argument("--day", type=int, default=13)
    ap.add_argument("--month", type=int, default=7)
    ap.add_argument("--year", type=int, default=1993)
    ap.add_argument("--no-proxy", action="store_true", help="跳过代理（只测装配，别真发）")
    ap.add_argument("--yes", action="store_true", help="真的发注册请求（不加只演练）")
    args = ap.parse_args()

    print("=" * 68)
    print(f"FB 注册链路试跑  stage={args.stage}  country={args.country}")
    print("=" * 68)

    env = None
    proxies: list = []
    ok_env = ok_proxy = ok_sms = False

    if args.stage in ("preflight", "env"):
        env, ok_env = stage_env(args.country)

    if args.stage in ("preflight", "proxy"):
        proxies, ok_proxy = stage_proxy(args.proxy_num, args.want_country or args.country,
                                        args.workers, not args.no_write)

    if args.stage in ("preflight", "sms"):
        if args.herosms_key:
            sms_country = args.want_country if args.stage == "sms" and args.want_country else "187"
            ok_sms, _ = stage_sms(args.herosms_key, sms_country, args.service)
        else:
            line(None, "未给 --herosms-key", "跳过接码检查")

    if args.stage == "email":
        addr, ok_mail = stage_email(args)
        return 0 if ok_mail else 1

    if args.stage == "register":
        if env is None:
            env, ok_env = stage_env(args.country)
        if not proxies and not args.no_proxy:
            proxies, ok_proxy = stage_proxy(args.proxy_num, args.country, args.workers,
                                            not args.no_write)
        stage_register(args, env, proxies, phone=args.phone)
        return 0

    if args.stage == "preflight":
        print("\n" + "=" * 68)
        line(ok_env, "设备环境")
        line(ok_proxy, "IP 池")
        line(ok_sms if args.herosms_key else None, "接码")
        verdict = ok_env and ok_proxy and (ok_sms or not args.herosms_key)
        print(f"\n{'可以进 register 阶段' if verdict else '先修上面标 ❌ 的项，别急着发注册'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
