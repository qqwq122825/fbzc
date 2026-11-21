"""hero-sms 接码协议客户端（sms-activate 兼容接口）。

端点（实测可达）:
    https://hero-sms.com/stubs/handler_api.php
    ?api_key=<KEY>&action=<ACTION>&...

实测的返回形态（关键：错误是 JSON，成功是纯文本）:
    {"title":"BAD_KEY","details":"Unauthorized"}                 http=401   key 错
    {"title":"UNPROCESSABLE_ENTITY","info":{"field":"api_key"}}   http=422   缺 key
    {"title":"ROUTE_NOT_FOUND"}                                   http=404   路径错
    成功（纯文本，形如）:
        ACCESS_BALANCE:12.34
        ACCESS_NUMBER:123456789:447912345678
        STATUS_WAIT_CODE
        STATUS_WAIT_RETRY:1234
        STATUS_OK:654321
        NO_NUMBERS / NO_BALANCE / WRONG_SERVICE / BAD_ACTION

    python3 core/sms_herosms.py --key KEY --balance
    python3 core/sms_herosms.py --key KEY --prices --country 187
    python3 core/sms_herosms.py --key KEY --buy --country 187 [--max-price 0.5]
    python3 core/sms_herosms.py --key KEY --code --id 123456789 --wait 300
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
BASE = "https://hero-sms.com/stubs/handler_api.php"
SPEND_LOG = os.path.join(DATA, "sms_spend.jsonl")

STATUS_CODES = {1: "已发送/待验证", 3: "重试", 6: "完成", 8: "取消"}

# 常见国家的别名（中文/ISO → hero-sms 里的英文名），用于 resolve_country
COUNTRY_ALIAS = {
    "美国": "united states", "us": "united states", "usa": "united states", "187": "united states",
    "英国": "united kingdom", "uk": "united kingdom", "gb": "united kingdom",
    "德国": "germany", "de": "germany",
    "法国": "france", "fr": "france",
    "日本": "japan", "jp": "japan",
    "新加坡": "singapore", "sg": "singapore",
    "印尼": "indonesia", "印度尼西亚": "indonesia", "id": "indonesia",
    "菲律宾": "philippines", "ph": "philippines",
    "越南": "vietnam", "vn": "vietnam",
    "泰国": "thailand", "th": "thailand",
    "马来西亚": "malaysia", "my": "malaysia",
    "印度": "india", "in": "india",
    "巴西": "brazil", "br": "brazil",
    "俄罗斯": "russia", "ru": "russia",
    "土耳其": "turkey", "tr": "turkey",
    "加拿大": "canada", "ca": "canada",
}


class SmsError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass
class Order:
    order_id: str
    phone: str
    service: str
    country: str
    cost: float = 0.0
    created: float = 0.0


# ---------------------------------------------------------------- 价格解析
def flatten_prices(raw) -> list[dict]:
    """把 getPrices 的返回摊平成统一结构，兼容三种实测/文档形态：

      A) {"187":{"fb":{"cost":0.35,"count":1234,"physicalCount":900}}}
      B) {"187":{"fb":{"cost":0.35,"count":1234}}}          同 A 的变体
      C) {"187":{"fb":{"cost":0.35,"count":1234,"operators":{"any":{...}}}}}
      D) [{country:..,service:..,cost:..,count:..}]          数组形态
    统一成: [{"country":cid,"service":"fb","cost":0.35,"count":1234}]
    """
    rows: list[dict] = []

    def add(cid, sid, node):
        if not isinstance(node, dict):
            return
        cost = node.get("cost", node.get("price"))
        count = node.get("count", node.get("physicalCount", 0))
        try:
            cost = float(cost)
        except (TypeError, ValueError):
            return
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 0
        rows.append({"country": str(cid), "service": str(sid),
                     "cost": round(cost, 4), "count": count})

    if isinstance(raw, list):
        for it in raw:
            if isinstance(it, dict) and "country" in it:
                add(it.get("country"), it.get("service", ""), it)
        return rows

    if not isinstance(raw, dict):
        return rows

    for cid, cnode in raw.items():
        if not isinstance(cnode, dict):
            continue
        for sid, snode in cnode.items():
            if isinstance(snode, dict) and ("cost" in snode or "price" in snode):
                add(cid, sid, snode)
            elif isinstance(snode, dict):
                for op, onode in snode.items():     # 运营商维度
                    add(cid, sid, onode if isinstance(onode, dict) else {})
    # 去重（同 country+service 取最低价）
    best: dict[tuple, dict] = {}
    for r in rows:
        k = (r["country"], r["service"])
        if k not in best or r["cost"] < best[k]["cost"]:
            best[k] = r
    return list(best.values())


def rank_cheapest(rows: list[dict], service: str = "fb",
                  allow: list[str] | None = None, min_count: int = 1,
                  max_price: float | None = None) -> list[dict]:
    out = [r for r in rows if r["service"] == service and r["count"] >= min_count]
    if allow:
        out = [r for r in out if r["country"] in allow]
    if max_price is not None:
        out = [r for r in out if r["cost"] <= max_price]
    return sorted(out, key=lambda r: (r["cost"], -r["count"]))


class HeroSMS:
    def __init__(self, api_key: str, base: str = BASE, timeout: int = 30) -> None:
        self.key = api_key
        self.base = base
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*"})

    # ------------------------------------------------------------ 低层
    def call(self, action: str, **params) -> str:
        q = {"api_key": self.key, "action": action}
        q.update({k: v for k, v in params.items() if v not in (None, "")})
        r = self.s.get(self.base, params=q, timeout=self.timeout)
        text = (r.text or "").strip()

        # JSON 形态 = 错误
        if text.startswith("{"):
            try:
                j = json.loads(text)
            except Exception:
                raise SmsError("BAD_RESPONSE", text[:200])
            title = str(j.get("title") or j.get("code") or "")
            detail = str(j.get("details") or j.get("message") or "")
            field = ((j.get("info") or {}).get("field") or "") if isinstance(j.get("info"), dict) else ""
            raise SmsError(title or "UNKNOWN_JSON", f"{detail} {('field=' + field) if field else ''}".strip())

        up = text.upper()
        for bad in ("NO_NUMBERS", "NO_BALANCE", "BAD_KEY", "BAD_ACTION", "WRONG_SERVICE",
                    "WRONG_COUNTRY", "BANNED", "NO_ACCESS", "WRONG_MAX_PRICE", "WRONG_OPERATOR",
                    "ERROR_SQL", "BAD_STATUS", "NO_ACTIVATION"):
            if up.startswith(bad):
                raise SmsError(bad, text)
        return text

    # ------------------------------------------------------------ 账户
    def balance(self) -> float:
        t = self.call("getBalance")
        m = re.search(r"ACCESS_BALANCE:([\d.]+)", t)
        if not m:
            raise SmsError("BAD_BALANCE_FORMAT", t)
        return float(m.group(1))

    def countries(self) -> dict:
        t = self.call("getCountries")
        try:
            return json.loads(t)
        except Exception:
            raise SmsError("BAD_COUNTRIES", t[:200])

    def prices(self, service: str = "fb", country: str | None = None) -> dict:
        t = self.call("getPrices", service=service, country=country)
        try:
            return json.loads(t)
        except Exception:
            raise SmsError("BAD_PRICES", t[:200])

    def price_table(self, service: str = "fb") -> list[dict]:
        """全量价格表（摊平 + 去重），落盘 data/sms_prices.json。"""
        raw = self.prices(service, None)
        rows = flatten_prices(raw)
        os.makedirs(DATA, exist_ok=True)
        json.dump({"ts": int(time.time()), "service": service, "rows": rows},
                  open(os.path.join(DATA, "sms_prices.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        return rows

    def resolve_country(self, name: str) -> list[tuple[str, str]]:
        """按国家名/ISO 找 hero-sms 的国家 ID。返回 [(id, 名称), ...]

        hero-sms 的 country 是**数字 ID**（不同服务商编号不通用），
        所以别硬编码 187，一律用这个函数从 getCountries 里查。
        """
        want = (name or "").strip().lower()
        want = COUNTRY_ALIAS.get(want, want)
        if not want:
            return []
        c = self.countries()
        hits: list[tuple[str, str]] = []
        items = c.items() if isinstance(c, dict) else enumerate(c)
        for k, v in items:
            label = str(v if isinstance(v, str) else (v.get("name") if isinstance(v, dict) else v))
            if want == str(k).lower() or want in label.lower() or want == label.lower():
                hits.append((str(k), label))
        return hits

    def cheapest(self, service: str = "fb", allow: list[str] | None = None,
                 min_count: int = 1, max_price: float | None = None,
                 top: int = 15) -> list[dict]:
        return rank_cheapest(self.price_table(service), service, allow, min_count, max_price)[:top]

    # ------------------------------------------------------------ 号码
    def get_number(self, service: str = "fb", country: str = "187",
                   operator: str | None = None, max_price: float | None = None) -> Order:
        t = self.call("getNumber", service=service, country=country,
                      operator=operator, maxPrice=max_price)
        m = re.search(r"ACCESS_NUMBER:(\d+):(\+?\d+)", t)
        if not m:
            if t.upper().startswith("ACCESS_NUMBER"):
                m2 = re.search(r"ACCESS_NUMBER:(\d+):(\d+)", t)
                if m2:
                    m = m2
        if not m:
            raise SmsError("BAD_NUMBER_FORMAT", t)
        return Order(order_id=m.group(1), phone=m.group(2), service=service,
                     country=str(country), cost=float(max_price or 0), created=time.time())

    def status(self, order_id: str) -> tuple[str, str]:
        """-> (state, value)；state ∈ WAIT_CODE / WAIT_RETRY / WAIT_RESEND / OK / CANCEL / UNKNOWN"""
        t = self.call("getStatus", id=order_id)
        up = t.upper()
        if up.startswith("STATUS_WAIT_CODE"):
            return "WAIT_CODE", ""
        if up.startswith("STATUS_WAIT_RETRY"):
            return "WAIT_RETRY", t.split(":", 1)[1] if ":" in t else ""
        if up.startswith("STATUS_WAIT_RESEND"):
            return "WAIT_RESEND", ""
        if up.startswith("STATUS_OK"):
            return "OK", t.split(":", 1)[1] if ":" in t else ""
        if up.startswith("STATUS_CANCEL"):
            return "CANCEL", ""
        return "UNKNOWN", t

    def set_status(self, order_id: str, status: int) -> str:
        return self.call("setStatus", id=order_id, status=status)

    def wait_code(self, order_id: str, timeout: int = 300, interval: int = 5,
                  on_tick=None) -> str | None:
        """轮询拿验证码。收到即 setStatus(6)=完成；超时则由调用方 cancel。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            state, val = self.status(order_id)
            if on_tick:
                on_tick(state, val)
            if state == "OK":
                try:
                    self.set_status(order_id, 6)
                except SmsError:
                    pass
                return val
            if state == "CANCEL":
                return None
            if state in ("WAIT_RETRY", "WAIT_RESEND") and val:
                pass  # 服务商在催上一步没提交验证码；继续等
            time.sleep(interval)
        return None

    def buy_cheapest(self, service: str = "fb", allow: list[str] | None = None,
                     max_price: float | None = None, tries: int = 5,
                     min_count: int = 1, budget: float | None = None,
                     log=print) -> Order | None:
        """按价格从低到高逐个尝试下单，跳过无库存/超预算/服务不可用。

        预算语义：budget = 累计花费上限（不是单号上限）。超了就停。
        """
        bal = None
        try:
            bal = self.balance()
            log(f"余额 {bal}")
        except SmsError as e:
            log(f"余额查询失败 {e.code}")

        ranked = self.cheapest(service, allow, min_count, max_price, top=max(tries * 3, 10))
        if not ranked:
            log(f"没有满足条件的 {service} 号源（allow={allow} max_price={max_price}）")
            return None

        log(f"候选 {len(ranked)} 个，从最便宜开始试：")
        spent = 0.0
        for i, r in enumerate(ranked):
            if i >= tries:
                break
            cid, cost = r["country"], r["cost"]
            if budget is not None and spent + cost > budget:
                log(f"  超预算（{spent:.2f}+{cost:.2f} > {budget:.2f}），停")
                break
            if bal is not None and bal - spent < cost:
                log(f"  余额不足（{bal - spent:.2f} < {cost:.2f}），停")
                break
            try:
                log(f"  [{i + 1}] 国家 {cid} 价 {cost} 库存 {r['count']} -> 下单")
                o = self.get_number(service, cid, None, max_price=max_price or cost)
                o.cost = cost
                spent += cost
                self.log_spend(o, f"cheapest(country={cid},cost={cost})")
                log(f"  ✅ 拿到 {o.phone}（{cid}，{cost}）")
                return o
            except SmsError as e:
                log(f"  ✗ {cid} {e.code}")
                if e.code in ("NO_BALANCE", "BAD_KEY", "BAD_BALANCE"):
                    return None
                continue
        return None

    def cancel(self, order_id: str) -> str:
        try:
            return self.set_status(order_id, 8)
        except SmsError as e:
            return f"[cancel failed] {e}"

    # ------------------------------------------------------------ 记账
    def log_spend(self, order: Order, note: str = "") -> None:
        os.makedirs(DATA, exist_ok=True)
        with open(SPEND_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": int(time.time()), "order": order.order_id, "phone": order.phone,
                "service": order.service, "country": order.country,
                "cost": order.cost, "note": note,
            }, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- CLI
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=os.environ.get("HEROSMS_KEY", ""), help="也可用环境变量 HEROSMS_KEY")
    ap.add_argument("--service", default="fb")
    ap.add_argument("--country", default="187", help="国家ID（187=美国常见编号，用 --countries 查）")
    ap.add_argument("--operator", default=None)
    ap.add_argument("--max-price", type=float, default=None)
    ap.add_argument("--id", default="")
    ap.add_argument("--wait", type=int, default=300)
    ap.add_argument("--allow", default="", help="只在这些国家里挑，逗号分隔，如 187,12")
    ap.add_argument("--min-count", type=int, default=1)
    ap.add_argument("--budget", type=float, default=None, help="累计花费上限")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--find-country", default="", help="按名字找国家ID，如 united states / 美国")
    ap.add_argument("--tries", type=int, default=5, help="买最便宜时最多试几个国家")
    for a in ("balance", "countries", "prices", "cheapest", "buy", "buy-cheapest",
              "status", "code", "finish", "cancel", "all"):
        ap.add_argument(f"--{a}", action="store_true")
    args = ap.parse_args()

    if not args.key:
        print("[!] 缺 api_key：--key 或 export HEROSMS_KEY=...")
        return 2
    cli = HeroSMS(args.key)

    try:
        if args.balance or args.all:
            print(f"余额: {cli.balance()}")

        if args.countries or args.all:
            c = cli.countries()
            rows = [(k, v) for k, v in c.items()] if isinstance(c, dict) else list(enumerate(c))
            print(f"国家 {len(rows)} 个，前 20:")
            for k, v in rows[:20]:
                print(f"  {k}: {v}")

        if args.prices or args.all:
            p = cli.prices(args.service, args.country)
            print(f"{args.service} / {args.country} 价格表:")
            try:
                node = p[args.country][args.service] if args.country in p else p
            except Exception:
                node = p
            print(json.dumps(node, ensure_ascii=False)[:1200])

        allow = [x.strip() for x in args.allow.split(",") if x.strip()] or None
        if args.find_country:
            hits = cli.resolve_country(args.find_country)
            print(f"'{args.find_country}' 匹配 {len(hits)} 个: {hits[:8]}")
            if hits and not allow:
                allow = [hits[0][0]]
                print(f"自动采用国家 ID {allow}")

        if args.cheapest or args.all:
            rows = cli.cheapest(args.service, allow, args.min_count,
                                args.max_price, args.top)
            if not rows:
                print(f"没有满足条件的 {args.service} 号源")
            else:
                print(f"\n最便宜的 {args.service} 号源（共 {len(rows)} 条，按单价升序）:")
                print(f"{'国家':<8}{'单价':>8}{'库存':>10}")
                for r in rows:
                    print(f"{r['country']:<8}{r['cost']:>8}{r['count']:>10}")
                b = rows[0]
                print(f"\n最便宜: 国家 {b['country']} 单价 {b['cost']} 库存 {b['count']}")

        if args.buy_cheapest:
            o = cli.buy_cheapest(args.service, allow, args.max_price, args.tries,
                                 args.min_count, args.budget)
            if o is None:
                return 1
            print(f"下单成功 id={o.order_id} phone={o.phone}")

        if args.buy:
            o = cli.get_number(args.service, args.country, args.operator, args.max_price)
            print(f"下单: id={o.order_id} phone={o.phone}")
            cli.log_spend(o, "buy")

        if args.code and args.id:
            code = cli.wait_code(args.id, args.wait,
                                 on_tick=lambda s, v: print(f"  .. {s} {v}"))
            print(f"验证码: {code}")
            return 0 if code else 1

        if args.finish and args.id:
            print(cli.set_status(args.id, 6))
        if args.cancel and args.id:
            print(cli.cancel(args.id))
        if args.status and args.id:
            print(cli.status(args.id))
    except SmsError as e:
        print(f"[SmsError] {e.code}  {e.detail}")
        hints = {
            "BAD_KEY": "api_key 错，去 hero-sms 面板复制",
            "NO_BALANCE": "余额不足，先充值",
            "NO_NUMBERS": "该服务/国家当前无号，换 country 或稍后重试（fb 的号源波动很大）",
            "WRONG_SERVICE": "service 名不对，试 fb / facebook",
            "WRONG_COUNTRY": "country 编号不对，用 --countries 查",
            "WRONG_MAX_PRICE": "maxPrice 低于市场价，去掉或调高",
        }
        if e.code in hints:
            print(f"          -> {hints[e.code]}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
