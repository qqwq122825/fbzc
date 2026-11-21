"""设备指纹池：型号 / build / 屏幕 / 运营商 必须成组一致，不能随机拼装。"""
from __future__ import annotations

import hashlib
import random
import uuid
from dataclasses import dataclass, asdict, field

# 型号库：(BRAND, MODEL, DEVICE, PRODUCT, HARDWARE, ANDROID_VER, density, width, height)
DEVICE_POOL = [
    ("samsung", "SM-A536B", "a53x", "a53xeea", "exynos1280", "13", "450", "1080", "2340"),
    ("samsung", "SM-G991B", "o1s", "o1sxeea", "exynos2100", "13", "480", "1080", "2400"),
    ("Xiaomi", "M2101K7AG", "curtana", "curtana_global", "qcom", "12", "440", "1080", "2400"),
    ("Xiaomi", "2201117TG", "veux", "veux_global", "qcom", "13", "440", "1080", "2400"),
    ("OnePlus", "LE2123", "OnePlus9Pro", "OnePlus9Pro_EEA", "qcom", "13", "560", "1440", "3216"),
    ("motorola", "moto g(60)", "elish", "elish_g", "qcom", "12", "400", "1080", "2460"),
    ("HUAWEI", "ELS-NX9", "ELLE", "ELS-N29", "kirin990", "12", "456", "1200", "2640"),
    ("OPPO", "CPH2451", "OP5556L1", "CPH2451EEA", "mt6877", "13", "450", "1080", "2412"),
    ("realme", "RMX3630", "RE58C2", "RMX3630EEA", "mt6833", "13", "411", "1080", "2400"),
    ("google", "Pixel 7", "panther", "panther", "gs201", "14", "420", "1080", "2400"),
]

# 运营商 (MCC+MNC, 名称) —— 必须与出口 IP 国家一致
CARRIERS = {
    "US": [("310260", "T-Mobile"), ("311480", "Verizon"), ("310410", "AT&T")],
    "GB": [("23415", "Vodafone UK"), ("23430", "EE"), ("23410", "O2-UK")],
    "DE": [("26201", "Telekom.de"), ("26202", "Vodafone.de"), ("26203", "o2 - de")],
    "JP": [("44010", "NTT DOCOMO"), ("44020", "SoftBank"), ("44050", "KDDI")],
    "SG": [("52501", "SingTel"), ("52503", "M1"), ("52505", "StarHub")],
}

LOCALES = {
    "US": ("en_US", "America/New_York"),
    "GB": ("en_GB", "Europe/London"),
    "DE": ("de_DE", "Europe/Berlin"),
    "JP": ("ja_JP", "Asia/Tokyo"),
    "SG": ("en_SG", "Asia/Singapore"),
}

FB_VER = ("FBAV/", "FBBV/", "FBRV/")


@dataclass
class Fingerprint:
    fp_id: str
    country: str
    brand: str
    model: str
    device: str
    product: str
    hardware: str
    android_ver: str
    density: str
    width: str
    height: str
    carrier_mcc_mnc: str
    carrier_name: str
    locale: str
    timezone: str
    device_id: str
    machine_id: str
    adid: str
    android_id: str
    serial: str
    fingerprint_build: str = ""
    leaked: bool = False
    used: int = 0

    # ---- 派生 ----
    def user_agent(self, fb_version: str = "FBAV/[APP_VER];FBBV/[BUILD];FBRV/[REV]") -> str:
        return (
            f"[FBAN/FB4A;{fb_version};"
            f"FBDM/{{density={self.density},width={int(self.width) // 2},height={int(self.height) // 2}}};"
            f"FBLC/{self.locale};FBOP/1;FBCR/{self.carrier_name};FBMF/{self.brand};FBBD/{self.brand};"
            f"FBPN/com.facebook.katana;FBDV/{self.model};FBSV/{self.android_ver};FBID/phone;FBCA/arm64-v8a:;]"
        )

    def headers(self) -> dict:
        return {
            "User-Agent": self.user_agent(),
            "X-FB-Device-ID": self.device_id,
            "X-FB-Net-HNI": self.carrier_mcc_mnc,
            "X-FB-SIM-HNI": self.carrier_mcc_mnc,
            "X-FB-HTTP-Engine": "Liger",
            "X-FB-Client-IP": "True",
            "X-FB-Server-Cluster": "True",
            "X-Zero-Rated": "0",
            "Accept-Encoding": "gzip, deflate",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def to_dict(self) -> dict:
        return asdict(self)


def _h(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def make(country: str, seed: str | None = None) -> Fingerprint:
    seed = seed or uuid.uuid4().hex
    rnd = random.Random(seed)
    brand, model, device, product, hw, av, dens, w, h_ = rnd.choice(DEVICE_POOL)
    mcc, cname = rnd.choice(CARRIERS.get(country, CARRIERS["US"]))
    loc, tz = LOCALES.get(country, LOCALES["US"])
    device_id = _h(seed, "dev")[:16]
    machine_id = _h(seed, "mach")[:32]
    adid = str(uuid.UUID(_h(seed, "adid")[:32]))
    android_id = _h(seed, "aid")[:16]
    serial = _h(seed, "ser")[:12].upper()
    build = (
        f"{brand}/{model}/{device}:{av}/TP1A.220624.014/{_h(seed, 'build')[:8]}:user/release-keys"
    )
    return Fingerprint(
        fp_id=_h(seed, "fp")[:12], country=country, brand=brand, model=model, device=device,
        product=product, hardware=hw, android_ver=av, density=dens, width=w, height=h_,
        carrier_mcc_mnc=mcc, carrier_name=cname, locale=loc, timezone=tz,
        device_id=device_id, machine_id=machine_id, adid=adid, android_id=android_id,
        serial=serial, fingerprint_build=build,
    )


def load_or_gen(fp_id: str, country: str) -> Fingerprint:
    """同一 seed 可复现同一指纹；落库后按 fp_id 取回，保证「一号一机」可追溯。"""
    return make(country, seed=fp_id)


if __name__ == "__main__":
    fp = make("US")
    print(fp.user_agent())
    for k, v in fp.headers().items():
        print(f"{k}: {v}")
