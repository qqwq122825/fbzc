"""纯协议场景下的「设备环境」构造器。

核心原则：**协议注册模拟设备，不是"造得像"，是"造得一致 + 造得持久"。**
  一致  = 型号 ↔ build ↔ 屏幕 ↔ 运营商 ↔ SIM 国家 ↔ locale ↔ 时区 ↔ 出口 IP 全部互相对得上
  持久  = 一组 ID 一号一环境，注册后登录/养号继续用这一组，中途换就等于自曝

服务端不看你"是不是真机"，它在做一致性交叉校验。任何一处的矛盾都是一个加权信号，
叠到阈值以上就是 570。所以这里做了 validate()，把矛盾在发请求之前全部打出来。

用法：
    env = DeviceEnv.make("US")
    env.validate(ip_country="US")        # -> []  才允许发请求
    env.save()                            # -> data/devices/<fp_id>.json
    client = RegClient(env=env, proxy=...)
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import uuid
from dataclasses import dataclass, asdict, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DEV_DIR = os.path.join(DATA, "devices")

# brand|model|device|product|manufacturer|release|sdk|density|w|h|build_id|hardware
MODELS = [
    ("samsung", "SM-A536B", "a53x", "a53xeea", "samsung", "13", "33", "450", "1080", "2340",
     "TP1A.220624.014", "exynos1280"),
    ("samsung", "SM-G991B", "o1s", "o1sxeea", "samsung", "13", "33", "480", "1080", "2400",
     "TP1A.220624.014", "exynos2100"),
    ("Xiaomi", "M2101K7AG", "curtana", "curtana_global", "Xiaomi", "12", "31", "440", "1080", "2400",
     "SKQ1.211006.001", "qcom"),
    ("Xiaomi", "2201117TG", "veux", "veux_global", "Xiaomi", "13", "33", "440", "1080", "2400",
     "TKQ1.221013.002", "qcom"),
    ("OnePlus", "LE2123", "OnePlus9Pro", "OnePlus9Pro_EEA", "OnePlus", "13", "33", "560", "1440", "3216",
     "TP1A.220905.001", "qcom"),
    ("motorola", "moto g(60)", "elish", "elish_g", "motorola", "12", "31", "400", "1080", "2460",
     "S3RQS32.20-42-10", "qcom"),
    ("OPPO", "CPH2451", "OP5556L1", "CPH2451EEA", "OPPO", "13", "33", "450", "1080", "2412",
     "TP1A.220905.001", "mt6877"),
    ("realme", "RMX3630", "RE58C2", "RMX3630EEA", "realme", "13", "33", "411", "1080", "2400",
     "TP1A.220905.001", "mt6833"),
    ("google", "Pixel 7", "panther", "panther", "Google", "14", "34", "420", "1080", "2400",
     "AP1A.240305.019", "gs201"),
    ("HUAWEI", "ELS-NX9", "ELLE", "ELS-N29", "HUAWEI", "12", "31", "456", "1200", "2640",
     "ELS-N29 12.0.0.290", "kirin990"),
]

# 国家 -> (MCC 前缀, 允许的 MCCMNC 列表[(mccmnc, carrier)], locale, timezone)
COUNTRIES = {
    "US": (("310", "311"), [("310260", "T-Mobile"), ("310410", "AT&T"), ("311480", "Verizon")],
           ("en_US", "America/New_York")),
    "GB": ("234", [("23415", "Vodafone UK"), ("23430", "EE"), ("23410", "O2-UK")],
           ("en_GB", "Europe/London")),
    "DE": ("262", [("26201", "Telekom.de"), ("26202", "Vodafone.de"), ("26207", "o2 - de")],
           ("de_DE", "Europe/Berlin")),
    "JP": ("440", [("44010", "NTT DOCOMO"), ("44020", "SoftBank"), ("44050", "KDDI")],
           ("ja_JP", "Asia/Tokyo")),
    "SG": ("525", [("52501", "SingTel"), ("52503", "M1"), ("52505", "StarHub")],
           ("en_SG", "Asia/Singapore")),
    "FR": ("208", [("20801", "Orange F"), ("20810", "SFR"), ("20820", "Bouygues")],
           ("fr_FR", "Europe/Paris")),
}

TZ_OFFSET = {
    "America/New_York": -5, "America/Chicago": -6, "America/Los_Angeles": -8,
    "Europe/London": 0, "Europe/Berlin": 1, "Europe/Paris": 1,
    "Asia/Tokyo": 9, "Asia/Singapore": 8,
}

FB_APP = {
    "ver": "[APP_VER]", "build": "[BUILD]", "rev": "[REV]",
    "pkg": "com.facebook.katana",
}


def _h(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


@dataclass
class DeviceEnv:
    # ---- 身份（一号一组，永不跨号复用）----
    fp_id: str
    device_id: str
    machine_id: str
    adid: str
    android_id: str
    serial: str
    install_id: str
    family_device_id: str
    session_id: str
    process_uuid: str
    # ---- 画像 ----
    brand: str
    model: str
    device: str
    product: str
    manufacturer: str
    hardware: str
    release: str
    sdk: str
    build_id: str
    build_fingerprint: str
    density: str
    width: str
    height: str
    # ---- 通信 ----
    mcc_mnc: str
    carrier: str
    sim_country: str
    phone_country_iso: str
    network_type: str
    # ---- 语言/时间 ----
    locale: str
    language: str
    country_iso: str
    timezone: str
    tz_offset: int
    # ---- 状态 ----
    registered: bool = False
    account_id: str = ""
    used: int = 0

    # ------------------------------------------------------------ 派生
    def user_agent(self) -> str:
        return (
            f"[FBAN/FB4A;FBAV/{FB_APP['ver']};FBBV/{FB_APP['build']};"
            f"FBDM/{{density={self.density},width={int(self.width) // 2},height={int(self.height) // 2}}};"
            f"FBLC/{self.locale};FBOP/1;FBRV/{FB_APP['rev']};FBCR/{self.carrier};"
            f"FBMF/{self.manufacturer};FBBD/{self.brand};FBPN/{FB_APP['pkg']};"
            f"FBDV/{self.model};FBSV/{self.release};FBID/phone;FBCA/arm64-v8a:;]"
        )

    def headers(self, friendly: str = "[FRIENDLY_NAME]", analytics_tags: str = "") -> dict:
        """顺序照真机 Liger 的实际发送顺序写（顺序本身也是特征，别用 set/排序）。"""
        h = {
            "User-Agent": self.user_agent(),
            "X-FB-Connection-Type": self.network_type,
            "X-FB-HTTP-Engine": "Liger",
            "X-FB-Client-IP": "True",
            "X-FB-Server-Cluster": "True",
            "X-TIGON-HOST": "b-graph.facebook.com",
            "X-FB-Net-HNI": self.mcc_mnc,
            "X-FB-SIM-HNI": self.mcc_mnc,
            "X-FB-Device-Group": _h(self.family_device_id)[:16],
            "X-FB-Device-ID": self.device_id,
            "X-FB-Friendly-Name": friendly,
            "X-Zero-Rated": "0",
            "X-FB-Quality-Session-ID": self.session_id,
            "Accept-Encoding": "gzip, deflate",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        if analytics_tags:
            h["X-FB-Request-Analytics-Tags"] = analytics_tags
        return h

    def form_common(self) -> dict:
        """请求体里那批「环境类」公共字段。"""
        return {
            "device_id": self.device_id,
            "machine_id": self.machine_id,
            "adid": self.adid,
            "android_id": self.android_id,
            "family_device_id": self.family_device_id,
            "install_id": self.install_id,
            "session_id": self.session_id,
            "process_uuid": self.process_uuid,
            "locale": self.locale,
            "timezone": self.timezone,
            "sim_country": self.sim_country,
            "sim_operator": self.mcc_mnc,
            "connection_type": self.network_type,
        }

    # ------------------------------------------------------------ 一致性校验
    def validate(self, ip_country: str | None = None) -> list[str]:
        issues: list[str] = []
        tbl = {(m[0], m[1]): m for m in MODELS}
        row = tbl.get((self.brand, self.model))
        if row is None:
            issues.append(f"型号不在库内: {self.brand}/{self.model}（库外型号无法校验屏幕/指纹一致性）")
        else:
            _, _, dev, prod, mfg, rel, sdk, dens, w, h, bid, hw = row
            for name, got, want in (
                ("device", self.device, dev), ("product", self.product, prod),
                ("manufacturer", self.manufacturer, mfg), ("release", self.release, rel),
                ("sdk", self.sdk, sdk), ("density", self.density, dens),
                ("width", self.width, w), ("height", self.height, h), ("build_id", self.build_id, bid),
                ("hardware", self.hardware, hw),
            ):
                if got != want:
                    issues.append(f"{name} 与型号 {self.model} 不匹配: {got} != {want}")

        fp = self.build_fingerprint
        for part in (self.brand, self.model, self.device, self.release):
            if part not in fp:
                issues.append(f"build_fingerprint 缺 {part}")

        if self.country_iso not in COUNTRIES:
            issues.append(f"未知国家: {self.country_iso}")
        else:
            prefix, mncs, (loc, tz) = COUNTRIES[self.country_iso]
            prefixes = (prefix,) if isinstance(prefix, str) else tuple(prefix)
            if not self.mcc_mnc.startswith(prefixes):
                issues.append(f"MCCMNC {self.mcc_mnc} 与 SIM 国家 {self.sim_country} 不匹配（应 {'/'.join(prefixes)}xx）")
            if self.mcc_mnc not in [m for m, _ in mncs]:
                issues.append(f"MCCMNC {self.mcc_mnc} 不在该国运营商表，可能是编的")
            if self.locale != loc:
                issues.append(f"locale {self.locale} 与 {self.country_iso} 惯用 {loc} 不符")
            if self.timezone != tz:
                issues.append(f"timezone {self.timezone} 与 {self.country_iso} 惯用 {tz} 不符")

        if self.sim_country != self.country_iso:
            issues.append(f"sim_country {self.sim_country} != country_iso {self.country_iso}")
        if self.tz_offset != TZ_OFFSET.get(self.timezone):
            issues.append(f"tz_offset {self.tz_offset} 与 {self.timezone} 不符")

        if ip_country and ip_country != self.sim_country:
            issues.append(f"出口 IP 国家 {ip_country} != SIM 国家 {self.sim_country} —— 这是最贵的一处矛盾")

        for name, val, ln in (("device_id", self.device_id, 16), ("machine_id", self.machine_id, 32),
                              ("android_id", self.android_id, 16), ("serial", self.serial, 12)):
            if not re_hex(val):
                issues.append(f"{name} 不是 hex: {val[:12]}...")
            elif len(val) != ln:
                issues.append(f"{name} 长度 {len(val)} != {ln}")
        for name, val in (("adid", self.adid), ("install_id", self.install_id),
                          ("session_id", self.session_id), ("process_uuid", self.process_uuid)):
            try:
                uuid.UUID(val)
            except Exception:
                issues.append(f"{name} 不是合法 UUID: {val}")
        ids = [self.device_id, self.machine_id, self.android_id, self.adid]
        if len(set(ids)) != len(ids):
            issues.append("身份 ID 之间有重复，必须彼此不同")
        return issues

    def health(self, ip_country: str | None = None) -> tuple[bool, list[str]]:
        issues = self.validate(ip_country)
        return (len(issues) == 0, issues)

    # ------------------------------------------------------------ 持久化
    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: str | None = None) -> str:
        os.makedirs(DEV_DIR, exist_ok=True)
        p = path or os.path.join(DEV_DIR, f"{self.fp_id}.json")
        json.dump(self.to_dict(), open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return p

    @classmethod
    def load(cls, fp_id: str) -> "DeviceEnv":
        return cls(**json.load(open(os.path.join(DEV_DIR, f"{fp_id}.json"), encoding="utf-8")))

    # ------------------------------------------------------------ 构造
    def rotate(self) -> "DeviceEnv":
        """换号时换整组环境：新 profile + 新 ID。旧 env 标记 retired，不再复用。"""
        return DeviceEnv.make(self.country_iso, seed=uuid.uuid4().hex)

    def rotate_ids_keep_profile(self, seed: str | None = None) -> "DeviceEnv":
        """同一机型换一套 ID（模拟"重装 App"）。build 不变、身份全换——这是唯一安全的换 ID 姿势。"""
        d = self.to_dict()
        s = seed or uuid.uuid4().hex
        d.update({
            "fp_id": _h(self.fp_id, s, "fp")[:12],
            "device_id": _h(s, "dev")[:16],
            "machine_id": _h(s, "mach")[:32],
            "adid": str(uuid.UUID(_h(s, "adid")[:32])),
            "android_id": _h(s, "aid")[:16],
            "serial": _h(s, "ser")[:12].upper(),
            "install_id": str(uuid.UUID(_h(s, "inst")[:32])),
            "family_device_id": str(uuid.UUID(_h(s, "fam")[:32])),
            "session_id": str(uuid.UUID(_h(s, "sess")[:32])),
            "process_uuid": str(uuid.UUID(_h(s, "proc")[:32])),
            "registered": False, "account_id": "", "used": 0,
        })
        return DeviceEnv(**d)

    @classmethod
    def make(cls, country: str, seed: str | None = None) -> "DeviceEnv":
        if country not in COUNTRIES:
            raise ValueError(f"未支持国家 {country}，可选 {list(COUNTRIES)}")
        seed = seed or uuid.uuid4().hex
        rnd = random.Random(seed)
        brand, model, dev, prod, mfg, rel, sdk, dens, w, h, bid, hw = rnd.choice(MODELS)
        _p, mncs, (loc, tz) = COUNTRIES[country]
        mcc, carrier = rnd.choice(mncs)
        lang = loc.split("_")[0]
        if rnd.random() < 0.25:
            net = "MOBILE.LTE"
        elif rnd.random() < 0.5:
            net = "WIFI"
        else:
            net = "MOBILE.4G"
        return cls(
            fp_id=_h(seed, "fp")[:12],
            device_id=_h(seed, "dev")[:16],
            machine_id=_h(seed, "mach")[:32],
            adid=str(uuid.UUID(_h(seed, "adid")[:32])),
            android_id=_h(seed, "aid")[:16],
            serial=_h(seed, "ser")[:12].upper(),
            install_id=str(uuid.UUID(_h(seed, "inst")[:32])),
            family_device_id=str(uuid.UUID(_h(seed, "fam")[:32])),
            session_id=str(uuid.UUID(_h(seed, "sess")[:32])),
            process_uuid=str(uuid.UUID(_h(seed, "proc")[:32])),
            brand=brand, model=model, device=dev, product=prod, manufacturer=mfg,
            hardware=hw, release=rel, sdk=sdk, build_id=bid,
            build_fingerprint=f"{brand}/{model}/{dev}:{rel}/{bid}/{_h(seed, 'b')[:8]}:user/release-keys",
            density=dens, width=w, height=h,
            mcc_mnc=mcc, carrier=carrier, sim_country=country, phone_country_iso=country,
            network_type=net,
            locale=loc, language=lang, country_iso=country, timezone=tz,
            tz_offset=TZ_OFFSET[tz],
        )


def re_hex(s: str) -> bool:
    try:
        int(s, 16)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    env = DeviceEnv.make("US")
    ok, issues = env.health("US")
    print(env.user_agent())
    print()
    for k, v in env.headers("RegCreateAccount").items():
        print(f"{k}: {v}")
    print()
    print("consistent:", ok)
    for i in issues:
        print("  !!", i)
    print("saved:", env.save())
