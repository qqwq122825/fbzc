"""
mitmproxy addon：抓 FB 注册链路，自动分类 -> 存 flow -> 生成协议模板
用法:
    mitmdump -s capture/fb_reg_capture.py -p 8888 --set block_global=false
输出:
    data/capture/fb_reg_<ts>.flow     原始 flow 文本
    data/protocol_template.json       去重后的「friendly_name -> 参数骨架」
"""
from __future__ import annotations

import json
import os
import re
import time
from urllib.parse import parse_qs, urlparse

from mitmproxy import http

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAP_DIR = os.path.join(ROOT, "data", "capture")
TPL_PATH = os.path.join(ROOT, "data", "protocol_template.json")

# 注册链路涉及的 host + 路径特征
HOST_HINTS = ("facebook.com", "fbcdn.net", "fb.com")
PATH_HINTS = ("/reg", "/graphql", "/auth/", "/api/v1/", "/method/", "/signup", "/confirm")

# 参数名里出现这些，说明是注册/鉴权上下文
KEY_HINTS = (
    "lsd", "jazoest", "ccp", "reg_", "doc_id", "client_doc_id",
    "fb_api_req_friendly_name", "device_id", "machine_id", "adid",
    "password", "firstname", "lastname", "contactpoint", "birthday", "gender",
    "session_key", "access_token",
)

SENSITIVE = ("password", "reg_passwd__", "contactpoint", "email", "phone", "session_key", "access_token")


def _interesting(flow: http.HTTPFlow) -> bool:
    if flow.request.pretty_host.endswith(("facebook.com", "fbcdn.net", "fb.com")) is False:
        return False
    p = flow.request.path
    if any(p.startswith(h) or h in p for h in PATH_HINTS):
        return True
    return flow.request.method == "POST" and "graph" in flow.request.pretty_host


def _mask(v: str) -> str:
    if len(v) <= 6:
        return v[:2] + "**"
    return v[:4] + "*" * (len(v) - 6) + v[-2:]


def _parse_body(flow: http.HTTPFlow) -> dict:
    raw = flow.request.get_text(strict=False) or ""
    out: dict = {}
    if not raw:
        return out
    ct = flow.request.headers.get("content-type", "")
    if "x-www-form-urlencoded" in ct or ("=" in raw and "&" in raw and not raw.lstrip().startswith("{")):
        for k, vs in parse_qs(raw, keep_blank_values=True).items():
            out[k] = _mask(vs[0]) if any(s in k.lower() for s in SENSITIVE) else vs[0]
    else:
        try:
            j = json.loads(raw)
            if isinstance(j, dict):
                out = {k: (_mask(str(v)) if any(s in k.lower() for s in SENSITIVE) else v) for k, v in j.items()}
            else:
                out = {"_json_array_len": len(j)}
        except Exception:
            out = {"_raw_len": len(raw), "_raw_head": raw[:200]}
    return out


def _friendly(flow: http.HTTPFlow, body: dict) -> str:
    return (
        body.get("fb_api_req_friendly_name")
        or flow.request.headers.get("x-fb-friendly-name", "")
        or urlparse(flow.request.path).path
    )


class FBRegCapture:
    def __init__(self) -> None:
        os.makedirs(CAP_DIR, exist_ok=True)
        self.tpl: dict = {}
        if os.path.exists(TPL_PATH):
            try:
                self.tpl = json.load(open(TPL_PATH, encoding="utf-8"))
            except Exception:
                self.tpl = {}
        self.buf: list[str] = []

    def response(self, flow: http.HTTPFlow) -> None:
        if not _interesting(flow):
            return
        body = _parse_body(flow)
        friendly = _friendly(flow, body)
        text = flow.response.get_text(strict=False) or ""
        resp_keys = sorted(set(re.findall(r'"([a-z_]{3,40})"\s*:', text)))[:60]

        rec = {
            "ts": time.time(),
            "method": flow.request.method,
            "host": flow.request.pretty_host,
            "path": flow.request.path.split("?")[0],
            "query": flow.request.path.split("?")[1] if "?" in flow.request.path else "",
            "friendly_name": friendly,
            "status": flow.response.status_code,
            "req_body_keys": body,
            "req_headers": {
                k: v for k, v in flow.request.headers.items()
                if k.lower().startswith(("x-fb", "x-tigon", "x-zero", "x-asbd", "user-agent", "content-type"))
            },
            "resp_head": text[:400],
            "resp_json_keys": resp_keys,
            "resp_len": len(text),
        }
        self.buf.append(json.dumps(rec, ensure_ascii=False))

        key = friendly or rec["path"]
        if key not in self.tpl:
            self.tpl[key] = {
                "host": rec["host"],
                "path": rec["path"],
                "method": rec["method"],
                "req_body_keys": body,
                "req_headers": rec["req_headers"],
                "resp_json_keys": resp_keys,
                "seen": 0,
            }
        self.tpl[key]["seen"] += 1

        if len(self.buf) >= 20:
            self._flush()

    def _flush(self) -> None:
        if self.buf:
            path = os.path.join(CAP_DIR, f"fb_reg_{int(time.time())}.flow")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(self.buf) + "\n")
            self.buf = []
        with open(TPL_PATH, "w", encoding="utf-8") as f:
            json.dump(self.tpl, f, ensure_ascii=False, indent=2)

    def done(self) -> None:
        self._flush()


addons = [FBRegCapture()]
