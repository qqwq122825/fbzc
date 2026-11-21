"""供应商抽象：邮箱 / 接码 / 代理。把你自己买的池子接到这三个接口上。"""
from __future__ import annotations

import abc
import itertools
import json
import os
import random
import time
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


# ---------------------------------------------------------------- 邮箱
class EmailProvider(abc.ABC):
    @abc.abstractmethod
    def acquire(self, tag: str) -> tuple[str, str]:
        """返回 (address, handle)"""

    @abc.abstractmethod
    def fetch_code(self, handle: str, timeout: int = 180) -> str | None:
        ...


class CatchAllProvider(EmailProvider):
    """自有域名 + catch-all + IMAP。

    配置：data/email.json
        {"domain": "example.com", "imap_host": "imap.example.com", "user": "...", "password": "..."}
    """

    def __init__(self) -> None:
        cfg = json.load(open(os.path.join(DATA, "email.json"), encoding="utf-8"))
        self.cfg = cfg
        self._n = itertools.count(int(cfg.get("start", 1)))

    def acquire(self, tag: str) -> tuple[str, str]:
        local = f"{tag}{random.randint(10000, 99999)}{next(self._n)}"
        addr = f"{local}@{self.cfg['domain']}"
        return addr, addr

    def fetch_code(self, handle: str, timeout: int = 180) -> str | None:
        import email
        import imaplib
        import re

        c = self.cfg
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                m = imaplib.IMAP4_SSL(c["imap_host"])
                m.login(c["user"], c["password"])
                m.select("INBOX")
                typ, data = m.search(None, f'(TO "{handle}")')
                ids = data[0].split()
                for i in reversed(ids[-3:]):
                    typ, msg = m.fetch(i, "(RFC822)")
                    mail = email.message_from_bytes(msg[0][1])
                    body = ""
                    if mail.is_multipart():
                        for part in mail.walk():
                            if part.get_content_type() == "text/html":
                                body += part.get_payload(decode=True).decode("utf-8", "ignore")
                    else:
                        body = mail.get_payload(decode=True).decode("utf-8", "ignore")
                    mm = re.search(r"\b(\d{5,8})\b", body)
                    if mm:
                        m.logout()
                        return mm.group(1)
                m.logout()
            except Exception:
                pass
            time.sleep(6)
        return None


# ---------------------------------------------------------------- 接码
class SmsProvider(abc.ABC):
    @abc.abstractmethod
    def acquire(self, country: str, service: str = "fb") -> tuple[str, str]:
        """返回 (phone_e164, order_id)"""

    @abc.abstractmethod
    def fetch_code(self, order_id: str, timeout: int = 300) -> str | None:
        ...

    def release(self, order_id: str) -> None:
        return None


class ManualSmsProvider(SmsProvider):
    """手工号池兜底：data/phones.txt 一行一个 +86xxxxxxxxxxx，自备收码渠道。"""

    def __init__(self) -> None:
        p = os.path.join(DATA, "phones.txt")
        self.pool = [l.strip() for l in open(p, encoding="utf-8")] if os.path.exists(p) else []
        self._i = 0

    def acquire(self, country: str, service: str = "fb") -> tuple[str, str]:
        if self._i >= len(self.pool):
            raise RuntimeError("phone pool exhausted")
        num = self.pool[self._i]
        self._i += 1
        return num, num

    def fetch_code(self, order_id: str, timeout: int = 300) -> str | None:
        raise NotImplementedError("接入你自己的收码渠道；或在 data/sms_codes.json 轮询")


# ---------------------------------------------------------------- 代理
@dataclass
class Proxy:
    url: str            # http://user:pass@host:port
    country: str
    kind: str = "residential"   # residential | mobile | datacenter
    used_today: int = 0
    last_used: float = 0.0

    def quota(self) -> int:
        return {"residential": 2, "mobile": 5, "datacenter": 1}[self.kind]


class ProxyPool:
    """data/proxies.txt: 一行一个，格式  url,country,kind"""

    def __init__(self, path: str | None = None) -> None:
        self.items: list[Proxy] = []
        p = path or os.path.join(DATA, "proxies.txt")
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [x.strip() for x in line.split(",")]
                self.items.append(Proxy(parts[0], parts[1] if len(parts) > 1 else "US",
                                        parts[2] if len(parts) > 2 else "residential"))

    def lease(self, country: str | None = None) -> Proxy | None:
        cands = [p for p in self.items if p.used_today < p.quota() and (country is None or p.country == country)]
        if not cands:
            return None
        cands.sort(key=lambda p: (p.used_today, p.last_used))
        p = cands[0]
        p.used_today += 1
        p.last_used = time.time()
        return p

    def rollover(self) -> None:
        for p in self.items:
            p.used_today = 0
