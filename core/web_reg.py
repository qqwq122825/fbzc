"""Web 注册 GraphQL 通道（CAARegistrationFormSubmitMutation）。

全部字段来自对当期 bundle 的静态提取，不是猜的：
    data/bundles/b4.js
      __d("useCAARegistrationFormSubmitMutation_facebookRelayOperation",[],...
          a.exports="27029416779977343")           <- doc_id
    data/bundles/b4.js 的 CAARegistrationFormDesktopReducer.getMutationPayload：
      {client_mutation_id, machine_id, next_uri,
       reg_data:{birthday_age?, birthday_day, birthday_month(0基+1), birthday_year,
                 contactpoint:{sensitive_string_value}, contactpoint_type,
                 custom_gender, did_use_age, firstname:{sensitive_string_value},
                 fullname:{sensitive_string_value}, ig_age_block_data, lastname:{...},
                 preferred_pronoun, reg_passwd__:{sensitive_string_value},
                 sex, use_custom_gender, username:{sensitive_string_value}},
       sk_pipa_consent_given, waterfall_id}
    data/bundles/b1.js 的 FBBrowserPasswordEncryption：
      返回  ["#PWD_BROWSER", 5, key_id, base64(envelope)].join(":")
      envelope = tweetnacl box（Curve25519 + XSalsa20-Poly1305），
      内层：自己生成临时 keypair，用「服务器公钥」做 box，前置自己公钥，
            再做 blake2b 派生的 secretbox 包裹 —— Python 侧用 pynacl 的 SealedBox 等价实现。

用法:
    python3 core/web_reg.py --dry                       # 只生成 payload，不发
    python3 core/web_reg.py --dry --email a@b.com
    python3 core/web_reg.py --send --key-id <id> --public-key <b64> --email a@b.com
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import re
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

KEY_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "pwd_key.json")

DOCS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "graphql_docs.json")

# 联系点类型枚举（convertContactpointTypetoGraphql）
CONTACT_TYPE = {"email": "EMAIL", "phone": "PHONE"}
# XFBCAARegGenderUtils.fromJSEnum
GENDER_ENUM = {1: "FEMALE", 2: "MALE"}


def load_key_cache(path: str | None = None) -> dict:
    p = path or KEY_CACHE
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_key_cache(key_id: str, public_key: str, path: str | None = None) -> str:
    p = path or KEY_CACHE
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump({"key_id": str(key_id), "public_key": public_key,
               "ts": int(time.time())}, open(p, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    return p


def load_docs() -> dict:
    if os.path.exists(DOCS):
        return json.load(open(DOCS, encoding="utf-8"))
    return {}


def doc_id_for(name: str) -> str:
    return load_docs().get(name, "")


# ---------------------------------------------------------------- 密码加密
def decode_pubkey(public_key: str) -> bytes:
    """服务端公钥有两种编法，自动识别：
       - SSR 引导数据里是 **64 位 hex**（例 0a723cc4…1218，32 字节）
       - 某些接口给 base64（44 字符，含 =）
    """
    k = (public_key or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{64}", k):
        return bytes.fromhex(k)
    raw = base64.b64decode(k + "=" * (-len(k) % 4))
    if len(raw) != 32:
        try:
            raw = bytes.fromhex(k)
        except Exception:
            pass
    return raw


def extract_key_from_html(html: str) -> tuple[str, str]:
    """从注册页 SSR 引导数据里取密码加密公钥。

    原文（/tmp/reg3.html 实测）：
      "caa_password_encryption_data":{"encryption_data":{"key_id":14,
        "public_key":"0a723cc46e492dd7156f5c1b22d9085750c22f54e4c4d04fdc7147c064991218"}}
    这两项来自 CAARegistrationFormDesktopQuery（doc_id 28589959880637856）。
    """
    m = re.search(r'"caa_password_encryption_data"\s*:\s*\{.*?"encryption_data"\s*:\s*\{'
                  r'"key_id"\s*:\s*(\d+)\s*,\s*"public_key"\s*:\s*"([0-9a-fA-F]{64})"',
                  html, re.S)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r'"key_id"\s*:\s*(\d+)\s*,\s*"public_key"\s*:\s*"([^"]+)"', html, re.S)
    return (m.group(1), m.group(2)) if m else ("", "")


def encrypt_password(password: str, public_key_b64: str, key_id: str = "1",
                     version: int = 5, tag: str = "#PWD_BROWSER") -> str:
    """复现 FBBrowserPasswordEncryption.encryptPassword。

    输出： ``#PWD_BROWSER:5:<key_id>:<base64>``
    内层用 SealedBox（Curve25519 + XSalsa20-Poly1305，接收方公钥加密、自己可解），
    与原实现在密码学上等价：只有持私钥的服务端能解开，且带完整性校验。
    """
    from nacl.public import PublicKey, SealedBox
    from nacl.encoding import RawEncoder

    pk_raw = decode_pubkey(public_key_b64)
    if len(pk_raw) != 32:
        raise ValueError(f"public_key 应为 32 字节 Curve25519，实际 {len(pk_raw)}")
    box = SealedBox(PublicKey(pk_raw))
    env = box.encrypt(password.encode(), encoder=RawEncoder)
    return ":".join([tag, str(version), str(key_id), base64.b64encode(env).decode()])


# ---------------------------------------------------------------- payload
def build_payload(first: str, last: str, contact: str, password_enc: str,
                  sex: int, bday: tuple[int, int, int], contact_type: str = "email",
                  machine_id: str = "", next_uri: str = "/", waterfall_id: str = "") -> dict:
    """完全按 bundle 里 getMutationPayload 的结构拼。"""
    return {
        "client_mutation_id": str(uuid.uuid4()),
        "machine_id": machine_id or uuid.uuid4().hex[:15],
        "next_uri": next_uri,
        "reg_data": {
            "birthday_day": int(bday[0]),
            "birthday_month": int(bday[1]),          # bundle 里是 month+1 之后的 1 基值
            "birthday_year": int(bday[2]),
            "contactpoint": {"sensitive_string_value": contact},
            "contactpoint_type": CONTACT_TYPE.get(contact_type, "EMAIL"),
            "custom_gender": "",
            "did_use_age": False,
            "firstname": {"sensitive_string_value": first},
            "fullname": {"sensitive_string_value": ""},
            "ig_age_block_data": None,
            "lastname": {"sensitive_string_value": last},
            "preferred_pronoun": None,
            "reg_passwd__": {"sensitive_string_value": password_enc},
            "sex": GENDER_ENUM.get(sex, "MALE"),
            "use_custom_gender": False,
            "username": {"sensitive_string_value": ""},
        },
        "sk_pipa_consent_given": False,
        "waterfall_id": waterfall_id or uuid.uuid4().hex,
    }


def build_form(payload: dict, lsd: str, friendly: str = "useCAARegistrationFormSubmitMutation",
               doc_id: str = "") -> dict:
    """拼成 /api/graphql/ 的 form 体。"""
    body = {
        "av": "0",
        "__user": "0",
        "__a": "1",
        "__req": "1",
        "__hs": "0",
        "__rev": "1055718978",
        "__comet_req": "0",
        "fb_api_caller_class": "RelayModern",
        "fb_api_req_friendly_name": friendly,
        "variables": json.dumps({"input": payload}, separators=(",", ":")),
        "server_timestamps": "true",
        "lsd": lsd,
    }
    did = doc_id or doc_id_for(friendly)
    if did:
        body["doc_id"] = did
    return body


# ---------------------------------------------------------------- CLI
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", default="Michael")
    ap.add_argument("--last", default="Wilson")
    ap.add_argument("--email", default="trial@yourdomain.example")
    ap.add_argument("--phone", default="")
    ap.add_argument("--password", default="Nq7#vLm2Rt9xWp4")
    ap.add_argument("--sex", type=int, default=2, choices=[1, 2])
    ap.add_argument("--day", type=int, default=13)
    ap.add_argument("--month", type=int, default=7)
    ap.add_argument("--year", type=int, default=1993)
    ap.add_argument("--key-id", default="")
    ap.add_argument("--public-key", default="", help="服务器公钥 b64（32B Curve25519）")
    ap.add_argument("--lsd", default="[LSD_FROM_BOOTSTRAP]")
    ap.add_argument("--from-page", action="store_true", help="直接从注册页 SSR 数据抓 key_id/public_key")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--proxy", default="")
    a = ap.parse_args()

    docs = load_docs()
    print(f"doc 表: {len(docs)} 条")
    for k in ("useCAARegistrationFormSubmitMutation", "useCAARegistrationFieldValidationQuery",
              "CAARegistrationFormDesktopQuery"):
        print(f"  {docs.get(k, 'MISS'):<20} {k}")

    if a.from_page:
        from core.client import RegClient
        from core.device_env import DeviceEnv
        px = a.proxy or next((l.split(",")[0] for l in open(
            os.path.join(os.path.dirname(DOCS), "proxies.txt"), encoding="utf-8")
            if l.startswith("socks5h")), "")
        cli = RegClient(env=DeviceEnv.make("US"), proxy=px or None, timeout=40)
        boot = cli.bootstrap_web()
        html = open("/tmp/reg3.html", encoding="utf-8").read() if boot.get("_html_len") == 0 else ""
        key_id, pub = extract_key_from_html(getattr(cli, "last_html", "") or html)
        if not key_id:
            # 兜底：本机缓存页
            for cand in ("/tmp/reg3.html",):
                if os.path.exists(cand):
                    key_id, pub = extract_key_from_html(open(cand, encoding="utf-8").read())
                    if key_id:
                        break
        print(f"\n从页面拿到: key_id={key_id} public_key={pub[:24]}…（{len(pub)} 字符）")
        a.key_id, a.public_key = key_id, pub
        if boot.get("lsd"):
            a.lsd = boot["lsd"]
            print(f"同时拿到 lsd={a.lsd}  jazoest={boot.get('jazoest')}  cookies={boot.get('_cookies')}")

    if a.public_key:
        enc = encrypt_password(a.password, a.public_key, a.key_id or "1")
        print(f"\nenc_password: {enc[:70]}…  (len={len(enc)})")
    else:
        enc = "#PWD_BROWSER:5:[KEY_ID]:[BASE64]"
        print("\n未给 --public-key，enc_password 用占位：密码加密需要服务端公钥")

    contact = a.phone or a.email
    payload = build_payload(a.first, a.last, contact, enc, a.sex, (a.day, a.month, a.year),
                            contact_type="phone" if a.phone else "email")
    body = build_form(payload, a.lsd)
    print("\n=== POST https://www.facebook.com/api/graphql/")
    print(json.dumps(json.loads(body["variables"]), ensure_ascii=False, indent=2)[:1400])
    print(f"\ndoc_id={body.get('doc_id')}  friendly={body['fb_api_req_friendly_name']}")

    if a.send:
        import requests

        from core.client import WEB_GRAPHQL, browser_headers
        if "[LSD_FROM_BOOTSTRAP]" in a.lsd:
            print("\n[!] 真发需要真 lsd：先跑 RegClient.bootstrap_web()")
            return 2
        s = requests.Session()
        if a.proxy:
            s.proxies = {"http": a.proxy, "https": a.proxy}
        s.headers.update(browser_headers(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"))
        h = {"Content-Type": "application/x-www-form-urlencoded",
             "X-FB-LSD": a.lsd, "X-FB-Friendly-Name": body["fb_api_req_friendly_name"],
             "Origin": "https://www.facebook.com", "Referer": "https://www.facebook.com/reg/",
             "Accept": "*/*", "Sec-Fetch-Site": "same-origin",
             "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}
        r = s.post(WEB_GRAPHQL, data=body, headers=h, timeout=40)
        print(f"\n响应 {r.status_code}: {r.text[:400]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
