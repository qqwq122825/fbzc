"""双通道路由：协议通道 vs 设备通道。

为什么必须双通道：
  - 协议通道（纯 HTTP 复现 b-graph）：成本极低、可并发，但服务端一旦上调完整性校验就整批归零
  - 设备通道（真机/云真机 + frida 驱动）：成本高、并发低，但抗风控、能出 attestation token
  正确做法不是押一个，是「协议优先、设备兜底」，按实测成功率自动切换。

设备通道约定（本机跑一个 bridge 代理，接口固定）：
    POST http://127.0.0.1:8765/register
      {"country":"US","firstname":"..","lastname":"..","contact":"..",
       "password":"..","sex":1,"birthday":[1,1,1995],"fp_id":".."}
    200 {"ok":true,"stage":"ACTIVE","account_id":"..","cookies":{...},"error_code":""}
"""
from __future__ import annotations

import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
STATE = os.path.join(DATA, "channel_state.json")
BRIDGE_CFG = os.path.join(DATA, "device_bridge.json")


class ChannelHealth:
    def __init__(self, path: str = STATE) -> None:
        self.path = path
        self.data: dict[str, dict] = {}
        if os.path.exists(path):
            try:
                self.data = json.load(open(path, encoding="utf-8"))
            except Exception:
                self.data = {}

    def record(self, channel: str, ok: bool) -> None:
        d = self.data.setdefault(channel, {"ok": 0, "fail": 0, "last": 0.0})
        d["ok" if ok else "fail"] += 1
        d["last"] = time.time()
        self.save()

    def rate(self, channel: str, min_n: int = 5, prior: float = 0.5) -> float:
        d = self.data.get(channel) or {"ok": 0, "fail": 0}
        n = d["ok"] + d["fail"]
        if n < min_n:
            return prior
        return d["ok"] / n

    def save(self) -> None:
        os.makedirs(DATA, exist_ok=True)
        json.dump(self.data, open(self.path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    def report(self) -> str:
        rows = []
        for k, v in self.data.items():
            n = v["ok"] + v["fail"]
            rows.append(f"{k}: ok={v['ok']} fail={v['fail']} rate={(v['ok'] / n if n else 0):.0%}")
        return " | ".join(rows) or "(no data)"


def pick(health: ChannelHealth, allowed: list[str], threshold: float = 0.25) -> str:
    """协议优先；协议掉到阈值以下自动切设备。"""
    if len(allowed) == 1:
        return allowed[0]
    order = ["protocol", "device"]
    allowed = [c for c in order if c in allowed]
    p = health.rate("protocol")
    d = health.rate("device")
    if "protocol" in allowed and p >= threshold:
        return "protocol"
    if "device" in allowed and d >= p:
        return "device"
    return allowed[0]


class DeviceBridge:
    """把注册动作丢给本机的设备通道 agent（真机群控 / 云真机 / 改机机）。"""

    def __init__(self, url: str | None = None, timeout: int = 300) -> None:
        cfg = {}
        if os.path.exists(BRIDGE_CFG):
            try:
                cfg = json.load(open(BRIDGE_CFG, encoding="utf-8"))
            except Exception:
                cfg = {}
        self.url = url or cfg.get("url", "http://127.0.0.1:8765/register")
        self.timeout = timeout

    def register(self, payload: dict) -> dict:
        import requests

        try:
            r = requests.post(self.url, json=payload, timeout=self.timeout)
            return r.json()
        except Exception as e:
            return {"ok": False, "stage": "FAIL_SOFT", "error_code": "BRIDGE_UNREACHABLE",
                    "detail": f"{e}；先把设备通道 agent 起在 {self.url}"}
