"""X-FB-Request-Analytics-Tags 处理。

核心思路：**不猜结构**。
这个 header 是 base64 包的一段 protobuf-lite，字段号会随版本变。
与其从零构造，不如：
  1) 从真机抓包里取一个真实 blob
  2) decode_tag() 把它摊平成 (field_num, wire_type, value) 列表
  3) 只替换「时间戳/耗时/重试/序号」这类天然会变的 varint
  4) encode_tag() 原样拼回去

这样结构和字段顺序与服务端预期一致，只有该变的值在变。
"""
from __future__ import annotations

import base64
import struct
import time


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = 0
    val = 0
    while True:
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, i
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


def _write_varint(v: int) -> bytes:
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def decode_tag(b64: str) -> list[dict]:
    """把 base64 的 analytics tags 摊平成字段列表（无 schema，纯线格式）。"""
    raw = base64.b64decode(b64 + "=" * (-len(b64) % 4))
    fields: list[dict] = []
    i = 0
    n = len(raw)
    while i < n:
        key, i = _read_varint(raw, i)
        num, wire = key >> 3, key & 7
        rec: dict = {"field": num, "wire": wire}
        if wire == 0:
            v, i = _read_varint(raw, i)
            rec["value"] = v
        elif wire == 2:
            ln, i = _read_varint(raw, i)
            chunk = raw[i:i + ln]
            i += ln
            try:
                rec["value"] = chunk.decode("utf-8")
            except UnicodeDecodeError:
                rec["value"] = chunk.hex()
                rec["raw"] = True
        elif wire == 5:
            rec["value"] = struct.unpack("<I", raw[i:i + 4])[0]
            i += 4
        elif wire == 1:
            rec["value"] = struct.unpack("<Q", raw[i:i + 8])[0]
            i += 8
        else:
            rec["value"] = None
            rec["error"] = "unsupported wire"
            fields.append(rec)
            break
        # varint 里像时间戳/耗时的，标出来便于替换
        if isinstance(rec.get("value"), int) and rec["value"] > 1_600_000_000:
            rec["looks_like"] = "unix_ms" if rec["value"] > 1_700_000_000_000 else "unix_s"
        fields.append(rec)
    return fields


def encode_tag(fields: list[dict]) -> str:
    out = bytearray()
    for f in fields:
        key = (f["field"] << 3) | f["wire"]
        out += _write_varint(key)
        v = f.get("value")
        if f["wire"] == 0:
            out += _write_varint(int(v or 0))
        elif f["wire"] == 2:
            if f.get("raw"):
                chunk = bytes.fromhex(v or "")
            else:
                chunk = (v or "").encode()
            out += _write_varint(len(chunk))
            out += chunk
        elif f["wire"] == 5:
            out += struct.pack("<I", int(v or 0))
        elif f["wire"] == 1:
            out += struct.pack("<Q", int(v or 0))
    return base64.b64encode(bytes(out)).decode()


def retime(b64: str, now: float | None = None, jitter: float = 0.0) -> str:
    """把抓包里的时间戳字段平移到「现在」，其余字节不动。

    jitter: 0~1，给耗时字段加随机浮动（不同请求耗时不可能完全一样）。
    """
    import random

    now = now or time.time()
    fields = decode_tag(b64)
    for f in fields:
        v = f.get("value")
        if not isinstance(v, int):
            continue
        if 1_600_000_000 < v < 2_000_000_000:                     # 秒级时间戳
            f["value"] = int(now + (now - int(now)))
        elif 1_600_000_000_000 < v < 2_000_000_000_000:           # 毫秒级
            f["value"] = int(now * 1000)
        elif 0 < v < 200_000 and jitter:                          # 疑似耗时/us 差值
            f["value"] = max(1, int(v * (1 + random.uniform(-jitter, jitter))))
    return encode_tag(fields)


def describe(b64: str) -> str:
    lines = []
    for f in decode_tag(b64):
        lines.append(f"  field={f['field']:<4} wire={f['wire']} value={str(f.get('value'))[:60]}"
                     + (f"   <- {f['looks_like']}" if f.get("looks_like") else ""))
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        print(describe(sys.argv[1]))
        print("\nretimed:", retime(sys.argv[1])[:80], "...")
    else:
        demo = {"field": 1, "wire": 0, "value": 1700000000}
        blob = encode_tag([demo, {"field": 2, "wire": 2, "value": "FBAV/[VER]"}])
        print("demo:", blob)
        print(describe(blob))
