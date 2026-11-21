"""从解包后的 APK 里挖 GraphQL 「friendly_name <-> doc_id」对应关系。

原理：FB 把 GraphQL 查询编译进 dex 的字符串表 / assets。
      名字和 doc_id 常常落在同一段二进制窗口内（dex string data 相邻，或 assets 的 map 里）。
      所以做法是「按字节邻近度配对」，再按兴趣加权，而不是靠正则一次命中。

用法:
    python3 apk/docids.py apk/work/<apk名>            # 扫描并打印候选
    python3 apk/docids.py apk/work/<apk名> --json data/graphql_docs.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

NAME_RE = re.compile(rb"[A-Z][A-Za-z0-9_]{5,70}")
DIGIT_RE = re.compile(rb"(?<![0-9])([0-9]{17,20})(?![0-9])")

# 注册链路关心的关键词（命中加分）
INTEREST = [
    "Reg", "Register", "Signup", "SignUp", "AccountCreate", "CreateAccount",
    "AccountCreation", "Registration", "Auth", "Login", "Confirm", "ContactPoint",
    "Checkpoint", "DeviceBased", "Nonce", "Start", "Submit",
]
NOISE = re.compile(rb"^(android|java|com_|org_|kotlin|google|facebook_|R\$|BuildConfig)")


def score_name(name: str) -> int:
    s = 0
    for k in INTEREST:
        if k.lower() in name.lower():
            s += 3
    if name.endswith(("Mutation", "Query")):
        s += 4
    if name.isupper():
        s -= 2
    if len(name) > 60:
        s -= 2
    return s


def scan_dex_windows(path: str, window: int = 240, limit: int = 4000) -> list[dict]:
    """在每个 doc_id 前后 window 字节里找 identifier，按距离配对。"""
    data = open(path, "rb").read()
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for m in DIGIT_RE.finditer(data):
        doc_id = m.group(1).decode()
        lo = max(0, m.start() - window)
        hi = min(len(data), m.end() + window)
        chunk = data[lo:hi]
        for nm in NAME_RE.finditer(chunk):
            name = nm.group(0).decode("utf-8", "ignore")
            if NOISE.match(nm.group(0)):
                continue
            sc = score_name(name)
            if sc < 3:
                continue
            dist = abs((lo + nm.start()) - m.start())
            key = (name, doc_id)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "name": name, "doc_id": doc_id, "distance": dist,
                "score": sc, "file": os.path.basename(path),
            })
            if len(out) >= limit:
                return out
    return out


def scan_assets(base: str) -> list[dict]:
    """assets 里的 .graphql / json map：文件名当 friendly_name，内容里找 id。"""
    out: list[dict] = []
    for p in glob.glob(os.path.join(base, "**", "assets", "**", "*"), recursive=True):
        if not os.path.isfile(p):
            continue
        low = p.lower()
        stem = os.path.splitext(os.path.basename(p))[0]
        if low.endswith((".graphql", ".graphqls")):
            txt = open(p, encoding="utf-8", errors="ignore").read()
            ids = re.findall(r"\b(\d{17,20})\b", txt)
            out.append({"name": stem, "doc_id": ids[0] if ids else "", "source": "asset-graphql",
                        "file": os.path.basename(p), "score": score_name(stem) + 5})
        elif low.endswith(".json") and ("graphql" in low or "query" in low or "doc" in low):
            try:
                j = json.load(open(p, encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            stack = [j]
            while stack:
                cur = stack.pop()
                if isinstance(cur, dict):
                    nm = cur.get("name") or cur.get("query_name") or cur.get("friendly_name")
                    did = cur.get("doc_id") or cur.get("id")
                    if nm and did and re.fullmatch(r"\d{17,20}", str(did)):
                        out.append({"name": str(nm), "doc_id": str(did), "source": "asset-json",
                                    "file": os.path.basename(p), "score": score_name(str(nm)) + 8})
                    stack.extend(cur.values())
                elif isinstance(cur, list):
                    stack.extend(cur)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("unpacked", help="apk 解包目录")
    ap.add_argument("--json", default="", help="输出 json 路径")
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args(argv[1:])
    if not os.path.isdir(args.unpacked):
        print(f"[!] 目录不存在: {args.unpacked}")
        return 2

    rows: list[dict] = []
    dexes = glob.glob(os.path.join(args.unpacked, "**", "classes*.dex"), recursive=True)
    print(f"[*] dex {len(dexes)} 个")
    for d in dexes:
        got = scan_dex_windows(d)
        rows.extend(got)
        print(f"    {os.path.basename(d)}: {len(got)} 对候选")

    assets = scan_assets(args.unpacked)
    rows.extend(assets)
    if assets:
        print(f"[*] assets 命中 {len(assets)} 条（优先级最高）")

    # 去重 + 排序：名字优先，距离越近越可信
    best: dict[str, dict] = {}
    for r in rows:
        cur = best.get(r["name"])
        if cur is None or (r["score"], -r["distance"] if "distance" in r else 0) > \
                          (cur["score"], -cur.get("distance", 0)):
            best[r["name"]] = r
    ranked = sorted(best.values(), key=lambda r: (-r["score"], r.get("distance", 0)))

    if not ranked:
        print("[!] 没有命中。多半是：doc_id 在 native 库 / 查询被编译成二进制 proto。")
        print("    下一步：抓包（capture/RUNBOOK.md），doc_id 直接从请求里读，比静态挖更快。")
        return 0

    print(f"\n{'score':>5}  {'distance':>8}  {'name':<52} doc_id")
    for r in ranked[:args.top]:
        print(f"{r['score']:>5}  {r.get('distance', 0):>8}  {r['name'][:52]:<52} {r['doc_id']}")

    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        json.dump(ranked, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n[+] 写入 {args.json}（{len(ranked)} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
