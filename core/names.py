"""真实姓名池：按国家 + 性别 + 真实频率生成，保证批内不重复。

为什么不能随便拼：
  1. 姓名与 `sex` 必须一致（Michael + female 的直接矛盾）
  2. 姓名与国家/locale 要匹配（US 号填 "Yuki Tanaka" 是矛盾信号）
  3. 批内不能出现重复组合（同 IP/同域名下两个 Michael Wilson 很扎眼）
  4. 不要用明显的测试名（Test User / John Doe / 名人名）

数据来源：各国常见姓名榜（近似频率权重），不是随机字母拼的。

用法:
    python3 core/names.py --country US --sex 2 --count 10
    python3 core/names.py --country GB --sex 1 --count 5 --json
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys

# 名字附带近似频率权重（越大越常见），用于分布更像真实人口
MALE = {
    "US": [("James", 32), ("Michael", 30), ("Robert", 29), ("John", 28), ("David", 27),
           ("William", 24), ("Richard", 21), ("Joseph", 21), ("Thomas", 20), ("Christopher", 19),
           ("Charles", 18), ("Daniel", 18), ("Matthew", 17), ("Anthony", 16), ("Mark", 15),
           ("Donald", 14), ("Steven", 14), ("Andrew", 13), ("Paul", 13), ("Joshua", 12),
           ("Kenneth", 12), ("Kevin", 11), ("Brian", 11), ("George", 10), ("Timothy", 10),
           ("Ronald", 9), ("Jason", 9), ("Edward", 8), ("Jeffrey", 8), ("Ryan", 8),
           ("Jacob", 7), ("Gary", 7), ("Nicholas", 7), ("Eric", 6), ("Jonathan", 6),
           ("Stephen", 6), ("Larry", 5), ("Justin", 5), ("Scott", 5), ("Brandon", 5)],
    "GB": [("Oliver", 20), ("Harry", 18), ("Jack", 18), ("George", 17), ("Noah", 15),
           ("Charlie", 15), ("Jacob", 14), ("Thomas", 14), ("Oscar", 13), ("William", 13),
           ("James", 12), ("Henry", 12), ("Leo", 11), ("Alfie", 10), ("Freddie", 10),
           ("Archie", 9), ("Theo", 8), ("Arthur", 8), ("Ollie", 7), ("Edward", 7)],
    "DE": [("Lukas", 18), ("Leon", 16), ("Finn", 15), ("Jonas", 15), ("Paul", 14),
           ("Felix", 13), ("Maximilian", 13), ("Elias", 12), ("Noah", 12), ("Ben", 11),
           ("Jonas", 11), ("Tim", 10), ("Jan", 10), ("Niklas", 9), ("Julian", 9),
           ("Moritz", 8), ("David", 8), ("Simon", 7), ("Tobias", 7), ("Philipp", 6)],
    "JP": [("Haruto", 20), ("Ren", 18), ("Yuto", 17), ("Sota", 16), ("Kaito", 15),
           ("Riku", 14), ("Takumi", 13), ("Daiki", 12), ("Sora", 11), ("Yuki", 10),
           ("Hiroto", 9), ("Kenta", 9), ("Sho", 8), ("Naoki", 8), ("Tsubasa", 7)],
    "SG": [("Wei Jie", 12), ("Jun Hao", 12), ("Kai Wen", 10), ("Marcus", 10), ("Ryan", 9),
           ("Daryl", 9), ("Shawn", 8), ("Zhi Hao", 8), ("Ethan", 7), ("Nicholas", 7)],
}

FEMALE = {
    "US": [("Mary", 30), ("Patricia", 26), ("Jennifer", 25), ("Linda", 24), ("Elizabeth", 22),
           ("Barbara", 21), ("Susan", 20), ("Jessica", 20), ("Sarah", 19), ("Karen", 18),
           ("Nancy", 17), ("Lisa", 17), ("Betty", 16), ("Margaret", 15), ("Sandra", 15),
           ("Ashley", 14), ("Kimberly", 13), ("Emily", 13), ("Donna", 12), ("Michelle", 12),
           ("Carol", 11), ("Amanda", 11), ("Dorothy", 10), ("Melissa", 10), ("Deborah", 9),
           ("Stephanie", 9), ("Rebecca", 8), ("Sharon", 8), ("Laura", 8), ("Cynthia", 7),
           ("Kathleen", 7), ("Amy", 7), ("Angela", 6), ("Shirley", 6), ("Anna", 6),
           ("Brenda", 5), ("Pamela", 5), ("Emma", 5), ("Nicole", 5), ("Helen", 5)],
    "GB": [("Olivia", 20), ("Amelia", 19), ("Isla", 18), ("Ava", 17), ("Ivy", 15),
           ("Freya", 14), ("Lily", 14), ("Florence", 13), ("Mia", 12), ("Willow", 11),
           ("Rosie", 10), ("Sophia", 10), ("Grace", 9), ("Poppy", 9), ("Emily", 8),
           ("Ella", 8), ("Evie", 7), ("Charlotte", 7), ("Alice", 6), ("Daisy", 6)],
    "DE": [("Mia", 18), ("Emma", 17), ("Hannah", 16), ("Sofia", 15), ("Lina", 14),
           ("Lea", 13), ("Emilia", 13), ("Marie", 12), ("Lena", 12), ("Anna", 11),
           ("Leonie", 10), ("Johanna", 9), ("Clara", 9), ("Laura", 8), ("Julia", 7)],
    "JP": [("Yui", 20), ("Aoi", 18), ("Hina", 17), ("Rin", 16), ("Sakura", 15),
           ("Yuna", 14), ("Mio", 13), ("Akari", 12), ("Hina", 11), ("Kokoro", 10),
           ("Mei", 9), ("Nanami", 9), ("Haruka", 8), ("Kaede", 8), ("Riko", 7)],
    "SG": [("Hui Ling", 12), ("Jia Yi", 12), ("Xin Yi", 10), ("Chloe", 10), ("Rachel", 9),
           ("Sarah", 9), ("Nicole", 8), ("Wen Xin", 8), ("Amanda", 7), ("Michelle", 7)],
}

LAST = {
    "US": [("Smith", 30), ("Johnson", 26), ("Williams", 24), ("Brown", 23), ("Jones", 22),
           ("Garcia", 21), ("Miller", 20), ("Davis", 19), ("Rodriguez", 18), ("Martinez", 17),
           ("Hernandez", 16), ("Lopez", 15), ("Gonzalez", 14), ("Wilson", 14), ("Anderson", 13),
           ("Thomas", 12), ("Taylor", 12), ("Moore", 11), ("Jackson", 11), ("Martin", 10),
           ("Lee", 10), ("Perez", 9), ("Thompson", 9), ("White", 9), ("Harris", 8),
           ("Sanchez", 8), ("Clark", 7), ("Ramirez", 7), ("Lewis", 7), ("Robinson", 6)],
    "GB": [("Smith", 28), ("Jones", 24), ("Taylor", 22), ("Brown", 21), ("Williams", 20),
           ("Wilson", 18), ("Johnson", 17), ("Davies", 16), ("Robinson", 15), ("Wright", 14),
           ("Thompson", 13), ("Evans", 12), ("Walker", 12), ("White", 11), ("Roberts", 11),
           ("Green", 10), ("Hall", 10), ("Wood", 9), ("Jackson", 9), ("Clarke", 8)],
    "DE": [("Müller", 30), ("Schmidt", 26), ("Schneider", 24), ("Fischer", 22), ("Weber", 20),
           ("Meyer", 19), ("Wagner", 18), ("Becker", 17), ("Schulz", 16), ("Hoffmann", 15),
           ("Schafer", 13), ("Koch", 12), ("Bauer", 12), ("Richter", 11), ("Klein", 10)],
    "JP": [("Sato", 30), ("Suzuki", 27), ("Takahashi", 24), ("Tanaka", 22), ("Watanabe", 20),
           ("Ito", 19), ("Yamamoto", 17), ("Nakamura", 16), ("Kobayashi", 15), ("Kato", 14),
           ("Yoshida", 13), ("Yamada", 12), ("Sasaki", 11), ("Yamaguchi", 10), ("Matsumoto", 9)],
    "SG": [("Tan", 26), ("Lim", 24), ("Lee", 22), ("Ng", 20), ("Ong", 18),
           ("Goh", 16), ("Chua", 15), ("Wong", 14), ("Koh", 13), ("Teo", 12)],
}

# 明显不能用/容易被关联的名字
BANNED = {
    "mark zuckerberg", "test user", "john doe", "jane doe", "facebook user",
    "elon musk", "bill gates", "admin", "user", "test test", "asdf asdf",
    "donald trump", "joe biden", "barack obama",
}


def _weighted(pairs: list[tuple[str, int]]) -> str:
    total = sum(w for _, w in pairs)
    r = random.uniform(0, total)
    upto = 0.0
    for name, w in pairs:
        upto += w
        if r <= upto:
            return name
    return pairs[-1][0]


def _sane(name: str) -> bool:
    """长度/字符/重复字母都检查一遍，去掉明显机器生成的痕迹。"""
    if not re.fullmatch(r"[A-Za-z][A-Za-z' -]{1,18}", name):
        return False
    if re.search(r"(.)\1{2,}", name):        # aaargh 这类连续重复
        return False
    if len(name) < 2:
        return False
    return True


def generate(country: str = "US", sex: int = 2, used: set | None = None,
             attempts: int = 40) -> tuple[str, str]:
    """返回 (first, last)。sex: 1=female, 2=male。批内去重。"""
    if used is None:
        used = set()
    c = country.upper()
    pool_f = FEMALE.get(c, FEMALE["US"]) if sex == 1 else MALE.get(c, MALE["US"])
    pool_l = LAST.get(c, LAST["US"])
    for _ in range(attempts):
        f = _weighted(pool_f)
        l = _weighted(pool_l)
        key = f"{f} {l}".lower()
        if key in used or key in BANNED or not _sane(f) or not _sane(l):
            continue
        used.add(key)
        return f, l
    # 兜底：加一个中间首字母，避免死循环
    f = _weighted(pool_f); l = _weighted(pool_l)
    mid = random.choice("ABCDEFGHIJKLMNOPRSTW")
    key = f"{f} {mid}. {l}".lower()
    used.add(key)
    return f, f"{mid}. {l}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--country", default="US")
    ap.add_argument("--sex", type=int, default=2, choices=[1, 2])
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    used: set = set()
    rows = []
    for _ in range(a.count):
        f, l = generate(a.country, a.sex, used)
        rows.append({"first": f, "last": l, "sex": a.sex, "country": a.country.upper()})
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print(f"{a.country.upper()} / {'female' if a.sex == 1 else 'male'}  共 {len(rows)} 组（已去重）")
    for r in rows:
        print(f"   {r['first']:<14}{r['last']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
