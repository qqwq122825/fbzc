"""虚拟邮箱池：自建邮件服务器 + catch-all / plus 寻址 / 本地 sink，全程不用手机号。

为什么这条路成立（已从 bundle 核实）：
    data/bundles/b4.js  contactpoint_type 枚举里 **有 "EMAIL"**
    提交体 reg_data.contactpoint = {sensitive_string_value: "<邮箱>"}
                 reg_data.contactpoint_type = "EMAIL"
    → 注册不需要手机号，验证走邮件里的确认链接/验证码

三种接法，按你服务器形态选一个：

  A. CatchAll   自有域名配 catch-all（任意 local part 都收）
                取号：随机 local@yourdomain   读取：IMAP 按收件人搜
  B. Plus      一个真实邮箱开 plus 寻址（user+tag@domain）
                取号：user+<随机tag>@domain   读取：IMAP 按 Delivered-To 搜
  C. Sink       自建服务器把邮件落到目录 / 或跑 Mailpit、MailHog 之类
                取号：任意 local@domain        读取：扫目录文件 或 Mailpit HTTP API

CLI:
    # 自检：用内置的 FB 确认邮件样本验解析
    python3 core/email_pool.py --selftest

    # 等一封信（你自己往这个地址发一封测试信即可）
    python3 core/email_pool.py --mode imap --host imap.yourdomain.com \
        --user catchall@yourdomain.com --password 'PASS' --addr test1@yourdomain.com --wait 120

    # Mailpit / 本地 sink
    python3 core/email_pool.py --mode sink --path /var/mail/inbox --addr a@b.com --wait 60
    python3 core/email_pool.py --mode mailpit --api http://127.0.0.1:8025 --addr a@b.com --wait 60
"""
from __future__ import annotations

import argparse
import base64
import email
import glob
import html as htmllib
import imaplib
import json
import os
import random
import re
import string
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

# ---------------------------------------------------------------- FB 邮件解析
CODE_PATTERNS = [
    r"\b(\d{5,8})\b",
    r"code[^0-9]{0,20}(\d{4,8})",
    r"验证码[^0-9]{0,10}(\d{4,8})",
    r"確認碼[^0-9]{0,10}(\d{4,8})",
]
LINK_PATTERNS = [
    r'https?://(?:www\.|m\.|web\.)?facebook\.com/[^\s"\'<>]*confirm[^\s"\'<>]*',
    r'https?://(?:www\.)?facebook\.com/[^\s"\'<>]*\?c=\d+[^\s"\'<>]*',
    r'https?://fb\.me/[^\s"\'<>]+',
    r'https?://[^\s"\'<>]*facebook\.com/[^\s"\'<>]*(?:code|verify)[^\s"\'<>]*',
]


def extract_fb_confirm(raw: str) -> dict:
    """从邮件原文里抽确认链接 / 验证码。返回 {link, code, subject, from}"""
    text = raw or ""
    # HTML 实体 + 常见的 quoted-printable 残留
    text = htmllib.unescape(text)
    text = text.replace("=\r\n", "").replace("=\n", "")
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.S | re.I)

    link = ""
    for p in LINK_PATTERNS:
        m = re.search(p, text, re.I)
        if m:
            link = m.group(0).rstrip(").,\"'")
            break

    code = ""
    plain = re.sub(r"<[^>]+>", " ", text)
    for p in CODE_PATTERNS:
        for m in re.finditer(p, plain):
            cand = m.group(1)
            # 排除像年份、金额、Facebook 内部 ID 的数字
            if cand.startswith(("19", "20")) and len(cand) == 4:
                continue
            if len(cand) >= 13:
                continue
            code = cand
            break
        if code:
            break

    subj = ""
    m = re.search(r"^Subject:\s*(.+)$", raw or "", re.M | re.I)
    if m:
        subj = m.group(1).strip()[:120]
    frm = ""
    m = re.search(r"^From:\s*(.+)$", raw or "", re.M | re.I)
    if m:
        frm = m.group(1).strip()[:120]
    return {"link": link, "code": code, "subject": subj, "from": frm}


# ---------------------------------------------------------------- 地址生成
def gen_local(tag: str = "fb", n: int = 8) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return f"{tag}{''.join(random.choice(alphabet) for _ in range(n))}"


# ---------------------------------------------------------------- 读取器
class EmailReader:
    def wait(self, address: str, timeout: int = 180, match_from: str = "facebook") -> str | None:
        raise NotImplementedError


class ImapReader(EmailReader):
    """catch-all 与 plus 寻址通用。按收件人/plus 标签匹配，取最新一封。

    config: {"host","port"(993),"user","password","mailbox"(INBOX),"ssl":True}
    """

    def __init__(self, host: str, user: str, password: str, port: int = 993,
                 mailbox: str = "INBOX", ssl: bool = True) -> None:
        self.host, self.port, self.user, self.password = host, port, user, password
        self.mailbox, self.ssl = mailbox, ssl

    def _conn(self):
        m = imaplib.IMAP4_SSL(self.host, self.port) if self.ssl else imaplib.IMAP4(self.host, self.port)
        m.login(self.user, self.password)
        m.select(self.mailbox, readonly=True)
        return m

    @staticmethod
    def _decode(msg) -> str:
        """把整封邮件摊平成「头 + 正文」的文本，html 与 text 都拼上。"""
        out: list[str] = []
        for k in ("From", "To", "Delivered-To", "Subject", "Date"):
            v = msg.get(k)
            if v:
                out.append(f"{k}: {v}")
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                if ctype not in ("text/html", "text/plain"):
                    continue
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                charset = part.get_content_charset() or "utf-8"
                try:
                    out.append(payload.decode(charset, "ignore"))
                except Exception:
                    out.append(payload.decode("utf-8", "ignore"))
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                out.append(payload.decode(msg.get_content_charset() or "utf-8", "ignore"))
        return "\n".join(out)

    def find(self, address: str, timeout: int = 60) -> str | None:
        local, _, domain = address.partition("@")
        tag = local.split("+", 1)[1] if "+" in local else local
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                m = self._conn()
                # 先按 plus 标签搜，再退回按收件人全文搜
                ids: list[bytes] = []
                for crit in (f'(HEADER Delivered-To "{address}")',
                             f'(TO "{address}")',
                             f'(TEXT "{tag}")'):
                    typ, data = m.search(None, crit)
                    if typ == "OK" and data and data[0]:
                        ids = data[0].split()
                        if ids:
                            break
                for i in reversed(ids[-3:]):
                    typ, msg_data = m.fetch(i, "(RFC822)")
                    if typ != "OK" or not msg_data:
                        continue
                    raw = msg_data[0][1]
                    text = self._decode(email.message_from_bytes(raw))
                    if "facebook" in text.lower() or extract_fb_confirm(text)["link"]:
                        m.logout()
                        return text
                m.logout()
            except Exception:
                pass
            time.sleep(5)
        return None

    def wait(self, address: str, timeout: int = 180, match_from: str = "facebook") -> str | None:
        return self.find(address, timeout=timeout)


class DirSink(EmailReader):
    """自建服务器把邮件落成文件（Maildir/mbox/eml/单文件目录）时用这个。"""

    def __init__(self, path: str) -> None:
        self.path = path

    def _candidates(self) -> list[str]:
        if os.path.isfile(self.path):
            return [self.path]
        pats = ["*.eml", "*.txt", "new/*", "cur/*", "*.mbox", "*"]
        out: list[str] = []
        for p in pats:
            out += [f for f in glob.glob(os.path.join(self.path, p)) if os.path.isfile(f)]
        return sorted(set(out), key=lambda f: os.path.getmtime(f), reverse=True)

    def wait(self, address: str, timeout: int = 180, match_from: str = "facebook") -> str | None:
        local = address.split("@")[0]
        deadline = time.time() + timeout
        seen = 0
        while time.time() < deadline:
            for f in self._candidates()[:60]:
                try:
                    if os.path.getsize(f) < 200:
                        continue
                    raw = open(f, encoding="utf-8", errors="ignore").read()
                except Exception:
                    continue
                if address not in raw and local not in raw:
                    continue
                if match_from and match_from not in raw.lower() and not extract_fb_confirm(raw)["link"]:
                    continue
                return raw
            seen += 1
            time.sleep(5)
        return None


class MailpitReader(EmailReader):
    """Mailpit / MailHog（本地开发邮件服务器）的 HTTP API。

    Mailpit:  GET /api/v1/search?query=to:addr   -> {"messages":[{ID:...}]}
              GET /api/v1/message/{ID}           -> 含 HTML/Text
    MailHog:  GET /api/v2/search?kind=to&query=addr -> {"items":[{Raw:{To,Data}}]}
    """

    def __init__(self, api: str = "http://127.0.0.1:8025") -> None:
        self.api = api.rstrip("/")

    def _get(self, path: str):
        import requests

        r = requests.get(self.api + path, timeout=15)
        r.raise_for_status()
        return r.json()

    def wait(self, address: str, timeout: int = 180, match_from: str = "facebook") -> str | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msgs = []
                try:  # Mailpit
                    j = self._get(f"/api/v1/search?query={address}")
                    msgs = j.get("messages") or []
                    for it in msgs[:3]:
                        mid = it.get("ID") or it.get("Id")
                        if not mid:
                            continue
                        d = self._get(f"/api/v1/message/{mid}")
                        text = "\n".join(str(d.get(k) or "") for k in ("HTML", "Text", "Subject", "From"))
                        if text.strip():
                            return text
                except Exception:
                    pass
                try:  # MailHog
                    j = self._get(f"/api/v2/search?kind=to&query={address}")
                    for it in (j.get("items") or [])[:3]:
                        raw = (it.get("Raw") or {})
                        data = raw.get("Data") or ""
                        if data:
                            return base64.b64decode(data).decode("utf-8", "ignore") if _is_b64(data) else data
                except Exception:
                    pass
            except Exception:
                pass
            time.sleep(5)
        return None


def _is_b64(s: str) -> bool:
    try:
        base64.b64decode(s, validate=True)
        return len(s) % 4 == 0
    except Exception:
        return False


class ApiOkReader(EmailReader):
    """apiok.us 临时邮箱（实测已打通，域名 uselesss.org）。

    实测响应形态（都是 HTTP 200 + JSON）:
        /generate/v1?apikey&type=email
          {"code":0,"result":{"id":"bXRtbHR4dEB1c2VsZXNzcy5vcmc=","email":"mtmltxt@uselesss.org"},"credits":0.0}
          ↑ id 是 **邮箱地址的 base64**，不是消息 ID
        /messages/v1?apikey&id=<邮箱的base64>
          {"code":0,"result":{"messages":[]}}
          ↑ 只认 base64 的 id；直接传地址会返回 {"code":1001,"msg":"id error."}
        /message/detail/v1?apikey&id=<消息ID>

    错误: {"code":1001,"msg":"apikey required."} / {"code":1005,"msg":"Credits not enough."}
    限速: 1 qps / 5 qpm → 轮询 ≥3s
    """

    BASE = "https://apiok.us/api/cbea"

    def __init__(self, apikey: str, domain: str = "uselesss.org", interval: float = 3.0) -> None:
        self.key = apikey
        self.domain = domain
        self.interval = interval
        self.mailbox_id = ""

    def _get(self, path: str, **params) -> dict:
        import requests

        params["apikey"] = self.key
        r = requests.get(f"{self.BASE}{path}", params=params, timeout=25)
        try:
            j = r.json()
        except Exception:
            raise RuntimeError(f"非 JSON 响应 {r.status_code}: {(r.text or '')[:120]}")
        code = j.get("code")
        if code not in (0, None, "0"):
            raise RuntimeError(f"apiok code={code} msg={j.get('msg')} credits={j.get('credits')}")
        return j

    # ---------------- 发地址
    def generate(self) -> str:
        j = self._get("/generate/v1", type="email")
        res = j.get("result") or {}
        self.mailbox_id = res.get("id") or ""
        addr = res.get("email") or ""
        if not addr:
            txt = json.dumps(j, ensure_ascii=False)
            m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", txt)
            addr = m.group(0) if m else ""
        if not addr:
            raise RuntimeError(f"generate 里没有邮箱: {json.dumps(j, ensure_ascii=False)[:200]}")
        if not self.mailbox_id:
            self.mailbox_id = base64.b64encode(addr.encode()).decode()
        return addr

    # ---------------- 收件
    def _list(self) -> list[dict]:
        if not self.mailbox_id:
            raise RuntimeError("还没有邮箱：先 generate()")
        j = self._get("/messages/v1", id=self.mailbox_id)
        res = j.get("result") or {}
        for key in ("messages", "list", "items", "data"):
            v = res.get(key) if isinstance(res, dict) else None
            if isinstance(v, list):
                return v
        return res if isinstance(res, list) else []

    def _detail(self, mid) -> str:
        j = self._get("/message/detail/v1", id=mid)
        return json.dumps(j.get("result") or j, ensure_ascii=False)

    def wait(self, address: str, timeout: int = 180, match_from: str = "facebook") -> str | None:
        if not self.mailbox_id:
            self.mailbox_id = base64.b64encode(address.encode()).decode()
        deadline = time.time() + timeout
        seen: set = set()
        while time.time() < deadline:
            try:
                for it in self._list():
                    mid = (it.get("id") or it.get("ID") or it.get("mid")
                           or it.get("message_id") or it.get("mail_id"))
                    if mid in seen:
                        continue
                    seen.add(mid)
                    body = self._detail(mid) if mid is not None else json.dumps(it, ensure_ascii=False)
                    blob = json.dumps(it, ensure_ascii=False) + " " + body
                    if match_from and match_from not in blob.lower() and not extract_fb_confirm(blob)["link"]:
                        continue
                    return blob
            except Exception:
                pass
            time.sleep(max(2.0, self.interval))
        return None


# ---------------------------------------------------------------- 池
class EmailPool:
    """统一入口：给地址 -> 等信 -> 抽 code/link。"""

    def __init__(self, reader: EmailReader, domain: str, tag: str = "fb",
                 plus_base: str = "") -> None:
        self.reader = reader
        self.domain = domain
        self.tag = tag
        self.plus_base = plus_base      # 非空则走 plus 寻址：plus_base 是本地部分
        self.bound: dict[str, str] = {}
        self.used: set[str] = set()

    def acquire(self) -> str:
        if self.plus_base:
            addr = f"{self.plus_base}+{gen_local(self.tag, 8)}@{self.domain}"
        else:
            addr = f"{gen_local(self.tag, 10)}@{self.domain}"
        self.used.add(addr)
        return addr

    def wait_code(self, address: str, timeout: int = 180) -> dict:
        raw = self.reader.wait(address, timeout=timeout)
        if not raw:
            return {"ok": False, "reason": "timeout", "code": "", "link": ""}
        got = extract_fb_confirm(raw)
        got["ok"] = bool(got.get("link") or got.get("code"))
        if not got["ok"]:
            got["reason"] = "no_confirm_token"
            got["raw_head"] = (raw or "")[:200]
        return got

    # ---------------- 持久化
    def save(self, path: str | None = None) -> str:
        os.makedirs(DATA, exist_ok=True)
        p = path or os.path.join(DATA, "email_pool.json")
        json.dump({"domain": self.domain, "plus_base": self.plus_base,
                   "used": sorted(self.used)},
                  open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return p


# ---------------------------------------------------------------- CLI
SAMPLE = """From: Facebook <security@facebookmail.com>
To: fb1234567890@yourdomain.example
Delivered-To: fb1234567890@yourdomain.example
Subject: Finish creating your Facebook account

<html><body>
<h2>Hi Michael,</h2>
<p>Please confirm your account.</p>
<p><a href="https://www.facebook.com/confirmemail.php?e=abc123&amp;c=84213">Confirm your email</a></p>
<p>Or enter this code: 84213</p>
</body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="imap",
                    choices=["imap", "sink", "mailpit", "apiok", "selftest"])
    ap.add_argument("--apikey", default=os.environ.get("APIOK_KEY", ""))
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--host", default="")
    ap.add_argument("--port", type=int, default=993)
    ap.add_argument("--user", default="")
    ap.add_argument("--password", default="")
    ap.add_argument("--mailbox", default="INBOX")
    ap.add_argument("--path", default="")
    ap.add_argument("--api", default="http://127.0.0.1:8025")
    ap.add_argument("--domain", default="")
    ap.add_argument("--plus-base", default="")
    ap.add_argument("--addr", default="")
    ap.add_argument("--wait", type=int, default=120)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest or a.mode == "selftest":
        got = extract_fb_confirm(SAMPLE)
        print("解析样本邮件:")
        print(json.dumps(got, ensure_ascii=False, indent=2))
        ok = bool(got["code"] and got["link"])
        print(f"\n{'✅ 解析正确' if ok else '❌ 解析失败'}  code={got['code']} link={got['link'][:60]}")
        return 0 if ok else 1

    if a.mode == "apiok":
        if not a.apikey:
            print("[!] apiok 模式需要 --apikey（或 export APIOK_KEY=...）")
            return 2
        reader = ApiOkReader(a.apikey, interval=a.interval)
        try:
            addr = a.addr or reader.generate()
        except RuntimeError as e:
            print(f"[apiok] {e}")
            return 1
        print(f"临时邮箱: {addr}")
        got_raw = reader.wait(addr, a.wait)
        got = extract_fb_confirm(got_raw or "")
        print(json.dumps(got, ensure_ascii=False, indent=2))
        return 0 if got.get("ok") else 1

    if a.mode == "imap":
        if not (a.host and a.user and a.password):
            print("[!] imap 模式需要 --host --user --password")
            return 2
        reader = ImapReader(a.host, a.user, a.password, a.port, a.mailbox)
    elif a.mode == "sink":
        if not a.path:
            print("[!] sink 模式需要 --path")
            return 2
        reader = DirSink(a.path)
    else:
        reader = MailpitReader(a.api)

    pool = EmailPool(reader, a.domain or "yourdomain.example", plus_base=a.plus_base)
    addr = a.addr or pool.acquire()
    print(f"地址: {addr}\n等待邮件（最多 {a.wait}s）…")
    got = pool.wait_code(addr, a.wait)
    print(json.dumps(got, ensure_ascii=False, indent=2))
    print(f"\n验证码: {got.get('code') or '-'}   链接: {got.get('link') or '-'}")
    return 0 if got.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
