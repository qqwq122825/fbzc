"""批量调度：状态机 + 配额 + 重试 + 结果落库。

配额铁律（输在风控，不输在协议）：
  住宅 IP ≤2 号/天，移动 4G ≤5 号/天，机房 1 号/天
  同域名邮箱 ≤30 号/天（首周），同号段 ≤20 号/天
  成功率跌破 15% → 全停，回去重新抓包
"""
from __future__ import annotations

import json
import os
import random
import string
import time
import uuid
from dataclasses import dataclass, asdict, field

from .channels import ChannelHealth, DeviceBridge, pick
from .client import RegClient, RegResult
from .fingerprint import make as make_fp
from .providers import EmailProvider, ProxyPool, SmsProvider

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
RESULT = os.path.join(DATA, "results.jsonl")
ACCOUNTS = os.path.join(DATA, "accounts.jsonl")

FIRST = {
    "US": ["James", "Emma", "Michael", "Olivia", "Daniel", "Sophia", "Ethan", "Ava", "Noah", "Mia"],
    "GB": ["Oliver", "Amelia", "Harry", "Isla", "Jack", "Freya", "George", "Ella", "Leo", "Ruby"],
    "DE": ["Lukas", "Anna", "Felix", "Lea", "Jonas", "Mia", "Paul", "Emilia", "Leon", "Hannah"],
    "JP": ["Haruto", "Yui", "Ren", "Aoi", "Sota", "Hina", "Yuto", "Rin", "Kaito", "Sakura"],
    "SG": ["Wei", "Hui", "Jun", "Ling", "Kai", "Xin", "Ryan", "Jia", "Marcus", "Chloe"],
}
LAST = {
    "US": ["Smith", "Johnson", "Brown", "Miller", "Davis", "Wilson", "Moore", "Taylor"],
    "GB": ["Taylor", "Wright", "Walker", "Hughes", "Green", "Hall", "Wood", "Harris"],
    "DE": ["Müller", "Schmidt", "Schneider", "Fischer", "Weber", "Meyer", "Wagner", "Becker"],
    "JP": ["Sato", "Suzuki", "Takahashi", "Tanaka", "Watanabe", "Ito", "Yamamoto", "Nakamura"],
    "SG": ["Tan", "Lim", "Lee", "Ng", "Ong", "Goh", "Chua", "Wong"],
}


@dataclass
class Task:
    task_id: str
    country: str
    stage: str = "NEW"
    fp_id: str = ""
    proxy: str = ""
    contact: str = ""
    handle: str = ""
    email: str = ""
    password: str = ""
    firstname: str = ""
    lastname: str = ""
    sex: int = 1
    bday: tuple = (1, 1, 1995)
    attempts: int = 0
    error_code: str = ""
    account_id: str = ""
    notes: str = ""

    def as_row(self) -> dict:
        d = asdict(self)
        d["bday"] = list(self.bday)
        return d


def gen_password(n: int = 14) -> str:
    pools = [string.ascii_lowercase, string.ascii_uppercase, string.digits, "!@#$%^&*"]
    pw = [random.choice(p) for p in pools]
    pw += [random.choice(string.ascii_letters + string.digits) for _ in range(n - 4)]
    random.shuffle(pw)
    return "".join(pw)


def make_task(country: str) -> Task:
    return Task(
        task_id=uuid.uuid4().hex[:12],
        country=country,
        firstname=random.choice(FIRST.get(country, FIRST["US"])),
        lastname=random.choice(LAST.get(country, LAST["US"])),
        sex=random.choice([1, 2]),
        bday=(random.randint(1, 28), random.randint(1, 12), random.randint(1980, 2002)),
        password=gen_password(),
    )


class Scheduler:
    def __init__(
        self,
        proxy_pool: ProxyPool,
        email_provider: EmailProvider | None = None,
        sms_provider: SmsProvider | None = None,
        mode: str = "web",          # web | mobile
        concurrency: int = 1,
        channel: str = "protocol",  # protocol | device | auto
    ) -> None:
        self.proxies = proxy_pool
        self.email = email_provider
        self.sms = sms_provider
        self.mode = mode
        self.concurrency = concurrency
        self.health = ChannelHealth()
        if channel == "auto":
            self.channels = ["protocol", "device"]
        elif channel in ("protocol", "device"):
            self.channels = [channel]
        else:
            raise ValueError(f"unknown channel: {channel}")
        self.bridge = DeviceBridge()
        self.channel_used = "protocol"
        self.success = 0
        self.fail = 0
        self.hard_fail = 0
        os.makedirs(DATA, exist_ok=True)

    def _log(self, task: Task, res: RegResult) -> None:
        row = {
            "ts": int(time.time()),
            "task": task.as_row(),
            "result": res.as_row(),
        }
        with open(RESULT, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        if res.ok:
            with open(ACCOUNTS, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": int(time.time()),
                    "contact": task.contact,
                    "password": task.password,
                    "account_id": res.account_id,
                    "cookies": res.cookies,
                    "country": task.country,
                    "proxy": task.proxy,
                    "stage": res.stage,
                }, ensure_ascii=False) + "\n")

    def _success_rate(self) -> float:
        tot = self.success + self.fail + self.hard_fail
        return 0.0 if tot == 0 else self.success / tot

    def run_one(self, task: Task) -> RegResult:
        # 1) 租 IP
        px = self.proxies.lease(task.country)
        if px is None:
            return RegResult(False, "FAIL_SOFT", "NO_PROXY", detail=f"{task.country} 无可用 IP")
        task.proxy = px.url

        # 2) 绑指纹（一国一型号一成组）
        fp = make_fp(task.country, seed=task.task_id)
        task.fp_id = fp.fp_id

        # 3) 号源
        if self.mode == "mobile" and self.sms:
            try:
                task.contact, task.handle = self.sms.acquire(task.country, "fb")
            except Exception as e:
                return RegResult(False, "FAIL_SOFT", "NO_PHONE", detail=str(e))
        else:
            if self.email is None:
                return RegResult(False, "FAIL_HARD", "NO_EMAIL_PROVIDER", detail="未配置邮箱池")
            task.contact, task.handle = self.email.acquire(task.country.lower())

        # 4) 提交
        ch = pick(self.health, self.channels)
        self.channel_used = ch
        if ch == "device":
            raw = self.bridge.register({
                "country": task.country, "firstname": task.firstname, "lastname": task.lastname,
                "contact": task.contact, "password": task.password, "sex": task.sex,
                "birthday": list(task.bday), "fp_id": fp.fp_id, "mode": self.mode,
            })
            res = RegResult(ok=bool(raw.get("ok")), stage=raw.get("stage", "FAIL_SOFT"),
                            error_code=raw.get("error_code", ""), account_id=str(raw.get("account_id", "")),
                            cookies=raw.get("cookies") or {}, detail=str(raw.get("detail", ""))[:500],
                            raw_status=int(raw.get("status", 0) or 0))
            self.health.record(ch, res.ok)
            task.stage, task.error_code, task.account_id = res.stage, res.error_code, res.account_id
            self._log(task, res)
            if res.ok:
                self.success += 1
            elif res.stage == "FAIL_HARD":
                self.hard_fail += 1
            else:
                self.fail += 1
            return res

        cli = RegClient(fp, proxy=px.url)
        if self.mode == "mobile":
            res = cli.mobile_register(task.firstname, task.lastname, task.contact,
                                      task.password, task.sex, task.bday,
                                      contact_type="phone")
        else:
            res = cli.web_register(task.firstname, task.lastname, task.contact,
                                   task.password, task.sex, task.bday, use_email=True)

        task.stage = res.stage
        task.error_code = res.error_code
        task.account_id = res.account_id

        # 5) 拉确认码
        if res.stage == "PENDING_CONFIRM" and task.handle:
            code = None
            if self.mode == "mobile" and self.sms:
                code = self.sms.fetch_code(task.handle)
            elif self.email:
                code = self.email.fetch_code(task.handle)
            if code:
                cres = cli.confirm(code)
                task.stage = cres.stage
                res = cres if cres.ok else res
            else:
                res.detail += " | code_not_received"

        # 6) 统计 + 熔断
        self.health.record(ch, res.ok)
        if res.ok:
            self.success += 1
        elif res.stage == "FAIL_HARD":
            self.hard_fail += 1
        else:
            self.fail += 1
        self._log(task, res)
        return res

    def run(self, tasks: list[Task]) -> dict:
        stopped = False
        for i, t in enumerate(tasks):
            if stopped:
                break
            res = self.run_one(t)
            print(f"[{i + 1}/{len(tasks)}] {t.task_id} {t.country} {res.stage} {res.error_code} "
                  f"rate={self._success_rate():.0%} ch={self.channel_used} "
                  f"[{self.health.report()}]")
            # 熔断：连续 10 次全灭
            if self.fail + self.hard_fail >= 10 and self._success_rate() < 0.15:
                print("!! 成功率 < 15%，熔断。回去重新抓包，别加并发。")
                stopped = True
            time.sleep(random.uniform(20, 90))   # 号间隔，别连打
        return {
            "success": self.success, "fail": self.fail,
            "hard_fail": self.hard_fail, "rate": round(self._success_rate(), 3),
            "stopped": stopped,
        }
