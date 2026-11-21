"""判定网关：纯协议注册到底能不能成立。

用法:
    python3 core/attest_check.py data/capture/live_123.log
    python3 core/attest_check.py data/capture/*.flow

判定逻辑（唯一的决定性证据）：
    注册请求里有没有「设备完整性证明」类字段/header。
      有 -> 纯协议必挂（服务端要一个只有真机能出的 token），只能真机/云真机取 token
      无 -> 纯协议成立，成功率取决于 IP + 号段 + 指纹自洽
"""
from __future__ import annotations

import glob
import json
import re
import sys

STRONG = {
    "hardware_attestation": [r"attestation", r"play_?integrity", r"integrity_?token",
                             r"safetynet", r"x-fb-attestation", r"device_?attestation",
                             r"attestation_?result", r"key_?attestation"],
    "biometric_or_doc":     [r"id_?verification", r"selfie", r"document_?upload",
                             r"video_?selfie", r"liveness"],
}
WEAK = {
    "analytics_tags": [r"x-fb-request-analytics-tags"],
    "server_issued_state": [r"reg_instance", r"nonce", r"flow_?id", r"session_?id",
                            r"machine_?id", r"client_doc_id"],
    "captcha": [r"recaptcha", r"funcaptcha", r"arkose", r"captcha_?token"],
}
EMU = [r"goldfish", r"ranchu", r"qemu", r"generic_x86", r"vbox", r"bluestacks",
       r"nox", r"ldplayer", r"ro\.kernel\.qemu"]


def scan(text: str) -> dict:
    low = text.lower()
    hits: dict[str, list[str]] = {}
    for cat, pats in {**STRONG, **WEAK}.items():
        got = sorted({m.group(0) for p in pats for m in re.finditer(p, low)})
        if got:
            hits[cat] = got
    emu = sorted({m.group(0) for p in EMU for m in re.finditer(p, low)})
    return {"hits": hits, "emulator_markers": emu}


def verdict(res: dict) -> tuple[str, str, list[str]]:
    hits = res["hits"]
    reasons: list[str] = []
    strong = [k for k in STRONG if k in hits]
    if "hardware_attestation" in strong:
        reasons.append("注册链路出现设备完整性证明字段 → 服务端要求真机硬件证明")
        reasons.append("纯 Python 发不出该 token；必须真机取 token 或走设备通道")
        return "BLOCKED_FOR_PURE_PROTOCOL", "无把握（除非拿到 token 注入通道）", reasons
    if "biometric_or_doc" in strong:
        reasons.append("链路里已出现 570/证件/自拍 相关字段 → 说明这份抓包的环境已被标记")
        reasons.append("这份日志里的成功链路是被污染样本，先改机重抓再看结论")
        return "CONTAMINATED_SAMPLE", "样本无效，重抓", reasons
    if "captcha" in hits:
        reasons.append("注册端点要求 captcha token（recaptcha/arkose）→ 需要打码通道")
        reasons.append("协议可写，但要接 OCR/打码，成本在验证码不在协议")
        return "NEEDS_CAPTCHA_CHANNEL", "有把握（需打码通道）", reasons
    if "server_issued_state" in hits:
        reasons.append("存在服务端下发状态（reg_instance/nonce/machine_id）→ 需按序取，不能跳步")
        return "FEASIBLE", "有把握（按序取态即可）", reasons
    reasons.append("未发现完整性证明/生物识别字段")
    reasons.append("降级：可能这份日志没覆盖到注册提交那一步，补抓 RegCreateAccount 再判")
    return "LIKELY_FEASIBLE", "有把握（需补抓关键请求确认）", reasons


def main(argv: list[str]) -> int:
    paths: list[str] = []
    for a in argv[1:]:
        paths.extend(glob.glob(a))
    if not paths:
        print(__doc__)
        return 2
    agg = {"hits": {}, "emulator_markers": []}
    for p in paths:
        try:
            text = open(p, encoding="utf-8", errors="ignore").read()
        except Exception as e:
            print(f"[skip] {p}: {e}")
            continue
        r = scan(text)
        for k, v in r["hits"].items():
            agg["hits"].setdefault(k, [])
            agg["hits"][k] = sorted(set(agg["hits"][k]) | set(v))
        agg["emulator_markers"] = sorted(set(agg["emulator_markers"]) | set(r["emulator_markers"]))
        print(f"[scan] {p}  hits={list(r['hits'])}")

    code, conf, reasons = verdict(agg)
    out = {
        "verdict": code,
        "confidence": conf,
        "reasons": reasons,
        "hits": agg["hits"],
        "emulator_markers": agg["emulator_markers"],
        "files": paths,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if agg["emulator_markers"]:
        print("\n[!] 检测到模拟器痕迹：" + ", ".join(agg["emulator_markers"]))
        print("    这一份的失败原因可能是模拟器本身，不是协议——换真机重抓再判定。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
