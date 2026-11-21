"""注册协议客户端：Web 表单流 + Android b-graph 流。

设计原则：
1) 所有接口值（lsd/jazoest/ccp/doc_id/friendly_name/headers）都从「现场」取，
   不在代码里写死 —— 写死的一定会随发版过期。
2) 两个流共用同一套结果判定，返回统一的 RegResult。
3) 代理/指纹/号源由外部注入，客户端不自己管配额。
"""
from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import requests

from .device_env import DeviceEnv
from .fingerprint import Fingerprint

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

WEB_BASE = "https://www.facebook.com"
WEB_REG = f"{WEB_BASE}/reg/"
WEB_SUBMIT = f"{WEB_BASE}/reg/submit/"
WEB_GRAPHQL = f"{WEB_BASE}/api/graphql/"
MBASE = "https://m.facebook.com"
BGRAPH = "https://b-graph.facebook.com"

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/[CHROME_VER].0.0.0 Safari/537.36"
)


def browser_headers(ua: str) -> dict:
    """完整浏览器头。实测：**头不全 FB 直接回 400**（thin 头必挂），全套头才 200。

    另注：Accept-Encoding 带 br，必须装 brotli，否则 requests 会把 brotli 字节
    当 latin1 文本，页面能"拿到"但是乱码，token 全 MISS。
    """
    ver = re.search(r"Chrome/(\d+)", ua)
    v = ver.group(1) if ver else "122"
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "sec-ch-ua": f'"Chromium";v="{v}", "Not(A:Brand";v="24", "Google Chrome";v="{v}"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    }


# --------------------------------------------------------------- 结果
@dataclass
class RegResult:
    ok: bool
    stage: str                 # SUBMITTED | PENDING_CONFIRM | ACTIVE | FAIL_SOFT | FAIL_HARD | CHECKPOINT
    error_code: str = ""
    account_id: str = ""
    cookies: dict = field(default_factory=dict)
    detail: str = ""
    raw_status: int = 0
    latency: float = 0.0

    def as_row(self) -> dict:
        return {
            "ok": self.ok, "stage": self.stage, "error_code": self.error_code,
            "account_id": self.account_id, "detail": self.detail[:300],
            "status": self.raw_status, "latency": round(self.latency, 2),
        }


def _classify(status: int, text: str, final_url: str = "") -> tuple[str, str]:
    t = (text or "")[:200000]
    low = t.lower()
    payload = None
    if t.strip().startswith("{"):
        try:
            payload = json.loads(t)
        except Exception:
            payload = None

    if payload and isinstance(payload, dict):
        err = payload.get("error") or payload.get("errorSummary") or {}
        code = ""
        if isinstance(err, dict):
            code = str(err.get("code") or err.get("error_code") or err.get("description") or "")
        if code:
            if any(k in code.upper() for k in ("TOO_MANY", "RATE", "LIMIT", "BLOCKED")):
                return "FAIL_SOFT", code
            return "FAIL_HARD", code
        if payload.get("account_id") or payload.get("uid") or payload.get("session_key"):
            return "ACTIVE", ""

    if "checkpoint" in final_url or "/checkpoint/" in low:
        return "CHECKPOINT", "CHECKPOINT_REQUIRED"
    if "confirmemail" in final_url or "confirmemail" in low or "confirm" in final_url:
        return "PENDING_CONFIRM", ""
    if "confirm" in low and ("code" in low or "验证码" in t):
        return "PENDING_CONFIRM", ""
    if re.search(r"(无法创建|couldn.t create|帐户出错|something went wrong|暂时无法)", low):
        return "FAIL_SOFT", "CREATE_REJECTED"
    if status in (200, 302):
        return "SUBMITTED", ""
    return "FAIL_SOFT", f"HTTP_{status}"


# --------------------------------------------------------------- 客户端
class RegClient:
    def __init__(self, fp: Fingerprint | None = None, proxy: str | None = None,
                 timeout: int = 30, env: DeviceEnv | None = None) -> None:
        self.env = env
        if fp is None and env is None:
            raise ValueError("必须给 fp 或 env 之一")
        self.fp = fp or env          # DeviceEnv 有 user_agent()/headers()，可直接当 fp 用
        self.timeout = timeout
        self.s = requests.Session()
        if proxy:
            self.s.proxies = {"http": proxy, "https": proxy}
        self.template: dict = {}
        tpl = os.path.join(DATA, "protocol_template.json")
        if os.path.exists(tpl):
            try:
                self.template = json.load(open(tpl, encoding="utf-8"))
            except Exception:
                self.template = {}

    # ---------------- 通用工具
    def _human_pause(self, lo: float = 0.6, hi: float = 2.4) -> None:
        time.sleep(random.uniform(lo, hi))

    def _type_text(self, text: str) -> None:
        """模拟逐字段输入：按字符数换算停顿，避免 0.2s 填完整张表。"""
        time.sleep(min(1.2, 0.02 * len(text) + random.uniform(0.15, 0.5)))

    # ---------------- 路径 A：Web 表单
    def bootstrap_web(self) -> dict:
        """拿注册页 token。三步不能省：先访问首页拿 datr/fr/sb，再带全套头取 /reg/。

        实测（走住宅 SOCKS5）：
          thin 头 -> 400（1542B 错误页）
          full 头 -> 200（337KB）+ datr/fr/sb
          br 未解压 -> token 全 MISS（乱码）
        """
        ua = DEFAULT_UA.replace("[CHROME_VER]", random.choice(["121", "122", "123"]))
        self.s.headers.update(browser_headers(ua))
        out: dict = {"_ua": ua, "_html_len": 0, "_status": 0, "_errors": []}

        try:
            r0 = self.s.get(WEB_BASE + "/", timeout=self.timeout)
            out["_home_status"] = r0.status_code
        except Exception as e:
            out["_errors"].append(f"home: {type(e).__name__}: {str(e)[:60]}")

        try:
            r = self.s.get(WEB_REG, timeout=self.timeout)
        except Exception as e:
            out["_errors"].append(f"reg: {type(e).__name__}: {str(e)[:60]}")
            return out
        html = r.text or ""
        self.last_html = html
        out.update({"_status": r.status_code, "_html_len": len(html),
                    "_final_url": r.url, "_cookies": list(self.s.cookies.get_dict().keys())})

        pats = {
            "lsd": [r'"token":"([A-Za-z0-9_-]{10,60})"',
                    r'name="lsd"\s+value="([^"]*)"',
                    r'"LSD",\["([^"]+)"'],
            "jazoest": [r"jazoest=(\d+)", r'name="jazoest"\s+value="(\d+)"'],
            "ccp": [r'name="ccp"\s+value="([^"]*)"'],
            "reg_impression_id": [r'name="reg_impression_id"\s+value="([^"]*)"'],
            "__spin_r": [r'"__spin_r":(\d+)', r'"server_revision":(\d+)'],
            "__rev": [r'"server_revision":(\d+)'],
        }
        for key, ps in pats.items():
            val = ""
            for p in ps:
                m = re.search(p, html)
                if m:
                    val = m.group(1)
                    break
            out[key] = val

        # jazoest 缺了就按 lsd 自己算：'2' + 各字节之和
        if not out.get("jazoest") and out.get("lsd"):
            out["jazoest"] = "2" + str(sum(out["lsd"].encode()))

        self.boot = out
        if not out.get("lsd"):
            out["_errors"].append("lsd MISS：页面结构变了或响应不是 200 的完整版")
        return out

    def bootstrap_lean(self) -> dict:
        """精简取态：**不打任何页面**，直接打 GraphQL。

        实测依据（core/cost_probe.py）：
          完整流程 705 KB/号（首页 377KB + 注册页 336KB）
          精简流程 9.3 KB/号（只有 GraphQL），节省 98.7%
          并且 GraphQL 不需要 lsd —— 服务端会在响应里种 datr
        代价：拿不到页面里的密码加密公钥，那玩意是全局的（key_id 14），
              按 data/pwd_key.json 缓存复用即可。
        """
        from .web_reg import doc_id_for
        friendly = "CAARegistrationFormDesktopQuery"
        body = {"av": "0", "__user": "0", "__a": "1", "__req": "1", "__hs": "0",
                "__rev": "0", "__comet_req": "0",
                "fb_api_caller_class": "RelayModern",
                "fb_api_req_friendly_name": friendly,
                "variables": '{"args":{},"ig_age_data":null,"next_uri":"/","scale":1}',
                "server_timestamps": "true", "doc_id": doc_id_for(friendly)}
        h = {"Content-Type": "application/x-www-form-urlencoded",
             "X-FB-Friendly-Name": friendly, "Origin": WEB_BASE,
             "Referer": WEB_REG, "Accept": "*/*"}
        out: dict = {"_mode": "lean", "_errors": []}
        try:
            r = self.s.post(WEB_GRAPHQL, data=body, headers=h, timeout=self.timeout)
            cl = r.headers.get("content-length")
            out.update({"_status": r.status_code,
                        "_bytes": int(cl) if cl and cl.isdigit() else len(r.content or b""),
                        "_cookies": list(self.s.cookies.get_dict().keys())})
            j = r.json()
            reg = (j.get("data") or {}).get("caa_registration") or {}
            out["reg_instance"] = (reg.get("constants") or {}).get("reg_instance", "")
            out["constants"] = reg.get("constants") or {}
            if not out["reg_instance"]:
                out["_errors"].append((r.text or "")[:200])
        except Exception as e:
            out["_errors"].append(f"{type(e).__name__}: {str(e)[:60]}")
        self.boot = out
        return out

    def web_register(
        self,
        firstname: str,
        lastname: str,
        contact: str,
        password: str,
        sex: int,
        bday: tuple[int, int, int],
        use_email: bool = True,
    ) -> RegResult:
        t0 = time.time()
        boot = self.bootstrap_web()
        if not boot.get("lsd"):
            return RegResult(False, "FAIL_SOFT", "NO_LSD", detail="bootstrap 未取到 lsd，可能被墙/被反爬", latency=time.time() - t0)

        payload: dict[str, Any] = {
            "firstname": firstname,
            "lastname": lastname,
            "reg_passwd__": password,
            "sex": str(sex),
            "birthday_day": str(bday[0]),
            "birthday_month": str(bday[1]),
            "birthday_year": str(bday[2]),
            "lsd": boot["lsd"],
            "jazoest": boot["jazoest"],
            "ccp": boot["ccp"],
            "reg_impression_id": boot["reg_impression_id"],
            "guid": self.fp.device_id,
            "did": self.fp.device_id,
            "__a": "1",
            "_fb_noscript": "1",
        }
        if use_email:
            payload["reg_email__"] = contact
            payload["reg_email_confirmation__"] = contact
        else:
            payload["reg_email__"] = contact

        headers = {
            "User-Agent": boot["_ua"],
            "Content-Type": "application/x-www-form-urlencoded",
            "X-FB-LSD": boot["lsd"],
            "X-FB-Friendly-Name": "useFbSignupMutation",
            "Origin": WEB_BASE,
            "Referer": WEB_REG,
            "Accept": "*/*",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }
        # 依次「填写」字段，制造真实耗时
        for k in ("firstname", "lastname", "reg_email__", "reg_passwd__"):
            if k in payload:
                self._type_text(str(payload[k]))
        self._human_pause(0.4, 1.2)

        r = self.s.post(WEB_SUBMIT, data=payload, headers=headers,
                        timeout=self.timeout, allow_redirects=True)
        stage, code = _classify(r.status_code, r.text, r.url)
        return RegResult(
            ok=stage in ("SUBMITTED", "PENDING_CONFIRM", "ACTIVE"),
            stage=stage, error_code=code,
            cookies=self.s.cookies.get_dict(),
            detail=(r.text or "")[:500],
            raw_status=r.status_code,
            latency=time.time() - t0,
        )

    # ---------------- 路径 B：移动端 b-graph
    def _mobile_headers(self, friendly: str, extra: dict | None = None,
                        analytics_tags: str = "") -> dict:
        if self.env is not None:
            h = self.env.headers(friendly, analytics_tags)
        else:
            h = self.fp.headers()
            h["X-FB-Friendly-Name"] = friendly
            if analytics_tags:
                h["X-FB-Request-Analytics-Tags"] = analytics_tags
        if extra:
            h.update(extra)
        return h

    def mobile_call(self, entry: dict, variables: dict, friendly: str | None = None,
                    analytics_tags: str = "") -> requests.Response:
        """按 protocol_template.json 里的一条记录重放请求，variables 做占位替换。"""
        host = entry.get("host", "b-graph.facebook.com")
        path = entry.get("path", "/graphql")
        fn = friendly or entry.get("friendly_name", "")
        body_tpl = entry.get("req_body_keys", {}) or {}

        params: dict[str, str] = {}
        for k, v in body_tpl.items():
            if isinstance(v, str):
                params[k] = v
            else:
                params[k] = json.dumps(v, ensure_ascii=False)
        params.setdefault("locale", self.fp.locale)
        params.setdefault("format", "json")
        if "variables" in params or "variables" in body_tpl:
            params["variables"] = json.dumps(self._merge_vars(params.get("variables", "{}"), variables), ensure_ascii=False)
        elif variables:
            params["variables"] = json.dumps(variables, ensure_ascii=False)

        url = f"https://{host}{path}"
        h = self._mobile_headers(fn, analytics_tags=analytics_tags)
        return self.s.post(url, data=params, headers=h, timeout=self.timeout)

    @staticmethod
    def _merge_vars(raw: str, extra: dict) -> dict:
        try:
            base = json.loads(raw) if raw else {}
        except Exception:
            base = {}
        def deep(d: dict, e: dict) -> dict:
            for k, v in e.items():
                if isinstance(v, dict) and isinstance(d.get(k), dict):
                    deep(d[k], v)
                else:
                    d[k] = v
            return d
        return deep(base, extra)

    def mobile_register(
        self,
        firstname: str,
        lastname: str,
        contact: str,
        password: str,
        sex: int,
        bday: tuple[int, int, int],
        contact_type: str = "email",
    ) -> RegResult:
        """依赖 data/protocol_template.json（先用 capture/fb_reg_capture.py 抓一次真机注册）。"""
        t0 = time.time()
        if not self.template:
            return RegResult(False, "FAIL_HARD", "NO_TEMPLATE",
                             detail="缺 data/protocol_template.json：先跑 capture 抓一次注册", latency=0)

        sequence = sorted(self.template.items(), key=lambda kv: kv[1].get("seen", 0))
        # 按抓包顺序登记（模板里每个 friendly_name 自带 order 时优先用）
        sequence.sort(key=lambda kv: kv[1].get("order", 0))

        common = self.env.form_common() if self.env is not None else {}
        variables = {
            "input": {
                "first_name": firstname,
                "last_name": lastname,
                "contact_point": contact,
                "password": password,
                "gender": "FEMALE" if sex == 1 else "MALE",
                "birthday": {"day": bday[0], "month": bday[1], "year": bday[2]},
                "device_id": self.fp.device_id,
                "machine_id": self.fp.machine_id,
                "adid": self.fp.adid,
                "locale": self.fp.locale,
                "timezone": self.fp.timezone,
                "contact_type": contact_type,
                **common,
            }
        }
        last = None
        stage, code = "FAIL_SOFT", "NO_STEP"
        for fn, entry in sequence:
            if not fn or entry.get("method", "POST") != "POST":
                continue
            self._human_pause(0.5, 2.0)
            tags = ""
            raw_tags = (entry.get("req_headers") or {}).get("X-FB-Request-Analytics-Tags", "")
            if raw_tags:
                try:
                    from .analytics_tags import retime
                    tags = retime(raw_tags, jitter=0.2)
                except Exception:
                    tags = ""
            last = self.mobile_call(entry, variables, friendly=fn, analytics_tags=tags)
            stage, code = _classify(last.status_code, last.text, last.url)
            if stage in ("FAIL_HARD", "CHECKPOINT"):
                break

        return RegResult(
            ok=stage in ("SUBMITTED", "PENDING_CONFIRM", "ACTIVE"),
            stage=stage, error_code=code,
            cookies=self.s.cookies.get_dict(),
            detail=(last.text if last is not None else "")[:500],
            raw_status=last.status_code if last is not None else 0,
            latency=time.time() - t0,
        )

    # ---------------- 验证码/邮件确认
    def confirm(self, code: str, endpoint: str | None = None) -> RegResult:
        t0 = time.time()
        url = endpoint or f"{MBASE}/confirm/"

        r = self.s.post(url, data={"code": code, "c": code}, headers=self._mobile_headers("reg_confirm"),
                        timeout=self.timeout, allow_redirects=True)
        stage, ec = _classify(r.status_code, r.text, r.url)
        return RegResult(ok=stage in ("ACTIVE", "PENDING_CONFIRM"), stage=stage, error_code=ec,
                         cookies=self.s.cookies.get_dict(), detail=r.text[:300],
                         raw_status=r.status_code, latency=time.time() - t0)
