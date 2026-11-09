#!/usr/bin/env python3
"""Генерирует синтетический журнал событий для кейса A (вымышленное приложение «Волна»).

Только стандартная библиотека, seed фиксирован — файл воспроизводим байт в байт.
Какие закономерности заложены — см. evals/cases/a/expected.md (ключ для судьи; агенту при прогоне
не показывать ни этот скрипт, ни expected.md — см. evals/benchmark.md, «Изоляция»).

Запуск:  python3 evals/make_data.py [--out evals/cases/a/data] [--users 900] [--seed 20261109]
"""
from __future__ import annotations

import argparse
import csv
import math
import random
from datetime import date, timedelta
from pathlib import Path

RELEASE = date(2026, 9, 1)
SIGNUP_START = date(2026, 8, 1)
SIGNUP_END = date(2026, 10, 10)
OBS_END = date(2026, 10, 31)
HORIZON = 30  # дней наблюдения после установки


def user_profile(rng: random.Random, signup: date) -> dict:
    platform = "android" if rng.random() < 0.6 else "ios"
    after = signup >= RELEASE
    if after:
        channel = rng.choices(["organic", "paid_promo", "referral"], [0.45, 0.4, 0.15])[0]
    else:
        channel = rng.choices(["organic", "paid_promo", "referral"], [0.75, 0.05, 0.2])[0]
    version = "3.2" if (platform == "android" and after) else ("3.1" if platform == "android" else "3.1-ios")
    p_onb = 0.82
    if version == "3.2":
        p_onb -= 0.30
    if channel == "paid_promo":
        p_onb -= 0.08
    tau = {"organic": 12.0, "referral": 15.0, "paid_promo": 4.5}[channel]
    base = {"organic": 0.55, "referral": 0.6, "paid_promo": 0.35}[channel]
    return {"platform": platform, "channel": channel, "app_version": version,
            "p_onb": p_onb, "tau": tau, "base": base}


def generate(n_users: int, seed: int) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    days = (SIGNUP_END - SIGNUP_START).days + 1
    users, events = [], []
    for i in range(1, n_users + 1):
        signup = SIGNUP_START + timedelta(days=rng.randrange(days))
        prof = user_profile(rng, signup)
        uid = f"u{i:05d}"
        users.append({"user_id": uid, "signup_date": signup.isoformat(), "platform": prof["platform"],
                      "channel": prof["channel"], "app_version": prof["app_version"]})

        def ev(d: date, name: str, value: str = "") -> None:
            events.append({"user_id": uid, "event_date": d.isoformat(), "event": name,
                           "platform": prof["platform"], "app_version": prof["app_version"],
                           "channel": prof["channel"], "value": value})

        ev(signup, "install")
        ev(signup, "onboarding_start")
        if rng.random() >= prof["p_onb"]:
            if prof["app_version"] == "3.2" and rng.random() < 0.5:
                ev(signup, "registration_error", rng.choice(["sms_timeout", "captcha_fail", "back_pressed"]))
            continue
        ev(signup, "onboarding_complete")
        ev(signup, "session_start")
        ev(signup, "listen", str(rng.randint(3, 40)))
        subscribed = False
        for d in range(1, HORIZON + 1):
            day = signup + timedelta(days=d)
            if day > OBS_END:
                break
            p = prof["base"] * math.exp(-d / prof["tau"]) + 0.03
            if day.weekday() >= 5:
                p *= 0.85
            if rng.random() < p:
                ev(day, "session_start")
                if rng.random() < 0.8:
                    ev(day, "listen", str(rng.randint(2, 60)))
                if not subscribed and d >= 3 and rng.random() < 0.04:
                    ev(day, "subscribe", "299")
                    subscribed = True
                if rng.random() < 0.01:
                    ev(day, "session_start")
    events.sort(key=lambda e: (e["event_date"], e["user_id"]))
    return users, events


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    here = Path(__file__).resolve().parent
    ap.add_argument("--out", type=Path, default=here / "cases" / "a" / "data")
    ap.add_argument("--users", type=int, default=900)
    ap.add_argument("--seed", type=int, default=20261109)
    a = ap.parse_args()
    users, events = generate(a.users, a.seed)
    write_csv(a.out / "users.csv", users)
    write_csv(a.out / "events.csv", events)
    print(f"users.csv: {len(users)} строк, events.csv: {len(events)} строк → {a.out}")


if __name__ == "__main__":
    main()
