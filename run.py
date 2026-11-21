"""CLI: python3 run.py --mode web --country US --count 5 [--dry]

--dry 只跑本地装配（指纹 + 号源 + 代理租用），不发任何网络请求，用于验证流水线。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.fingerprint import make as make_fp
from core.providers import ManualSmsProvider, ProxyPool
from core.scheduler import Scheduler, make_task

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _email_provider():
    try:
        from core.providers import CatchAllProvider
        return CatchAllProvider()
    except Exception as e:
        print(f"[warn] 未配置 data/email.json，邮箱池不可用: {e}")
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["web", "mobile"], default="web")
    ap.add_argument("--country", default="US")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--channel", choices=["protocol", "device", "auto"], default="protocol",
                    help="protocol=纯 HTTP 复现 b-graph；device=真机/云真机 bridge；auto=按实测成功率自动切")
    ap.add_argument("--dry", action="store_true", help="只装配，不发请求")
    args = ap.parse_args()

    pool = ProxyPool()
    if not pool.items:
        print("[warn] data/proxies.txt 为空或不存在；正式跑前先灌 IP")

    sch = Scheduler(proxy_pool=pool, email_provider=_email_provider(),
                    sms_provider=ManualSmsProvider(), mode=args.mode, channel=args.channel)

    tasks = [make_task(args.country) for _ in range(args.count)]

    if args.dry:
        for t in tasks:
            fp = make_fp(t.country, seed=t.task_id)
            print(json.dumps({
                "task": t.task_id, "country": t.country,
                "name": f"{t.firstname} {t.lastname}",
                "birthday": t.bday, "pw_len": len(t.password),
                "fp": {k: fp.to_dict()[k] for k in
                       ("brand", "model", "android_ver", "carrier_name", "locale", "device_id", "adid")},
                "ua": fp.user_agent()[:160],
            }, ensure_ascii=False, indent=2))
        print(f"\n装配完成 {len(tasks)} 条，未发请求。")
        return 0

    print(f"[channel] {args.channel} -> {sch.channels}")
    summary = sch.run(tasks)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"结果: {os.path.join(DATA, 'results.jsonl')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
