#!/usr/bin/env python3
"""
公約カレンダーユーティリティ

指定した日付・店舗名に対して、適用されるタグセットを返す。
  - config/events.json          : 単発公約（特定日）
  - config/recurring_rules.json : 繰り返しルール
  - ゾロ目日（4/4, 7/7 など）   : 自動判定
"""

import json
from datetime import date
from pathlib import Path

BASE_DIR             = Path(__file__).parent.parent
EVENTS_PATH          = BASE_DIR / "config" / "events.json"
RECURRING_RULES_PATH = BASE_DIR / "config" / "recurring_rules.json"


def _load_json(path: Path) -> list:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _is_zorome(d: date) -> bool:
    """月と日が同じ（例: 4/4, 7/7, 11/11）"""
    return d.month == d.day


def _matches_recurrence(d: date, recurrence: dict) -> bool:
    """日付が繰り返しルールにマッチするか判定する。"""
    rtype = recurrence.get("type", "")

    if rtype == "monthly_day":
        return d.day in recurrence.get("days", [])

    elif rtype == "weekly_day":
        # Python weekday(): 0=月〜6=日
        return d.weekday() == recurrence.get("weekday", -1)

    elif rtype == "monthly_nth_weekday":
        # 第n週の指定曜日（例: 第2日曜 → n=2, weekday=6）
        n = recurrence.get("n", 1)
        weekday = recurrence.get("weekday", 0)
        if d.weekday() != weekday:
            return False
        # その月で何番目の該当曜日かを計算
        first_of_month = date(d.year, d.month, 1)
        diff = (weekday - first_of_month.weekday()) % 7
        nth_day = first_of_month.day + diff + (n - 1) * 7
        return d.day == nth_day

    elif rtype == "yearly":
        return d.month == recurrence.get("month") and d.day == recurrence.get("day")

    return False


def get_tags_for_date(target_date_str: str, store_name: str) -> set:
    """
    指定日付・店舗のタグセットを返す。

    引数:
        target_date_str : YYYYMMDD 形式の日付文字列
        store_name      : 店舗名

    返値:
        タグキーの set（例: {"is_tokuteibi", "is_zorome_day"}）
    """
    d = date(
        int(target_date_str[:4]),
        int(target_date_str[4:6]),
        int(target_date_str[6:8]),
    )

    tags: set = set()

    # 1. ゾロ目日の自動判定
    if _is_zorome(d):
        tags.add("is_zorome_day")

    # 2. 単発公約
    for ev in _load_json(EVENTS_PATH):
        if ev.get("store_name") == store_name and ev.get("date") == target_date_str:
            tags.update(ev.get("tags", []))

    # 3. 繰り返しルール（active のみ）
    for rule in _load_json(RECURRING_RULES_PATH):
        if not rule.get("active", True):
            continue
        if rule.get("store_name") != store_name:
            continue
        if _matches_recurrence(d, rule.get("recurrence", {})):
            tags.update(rule.get("tags", []))

    return tags


if __name__ == "__main__":
    # 動作確認: 今日の全店舗のタグを表示
    stores_path = BASE_DIR / "config" / "stores.json"
    today_str = date.today().strftime("%Y%m%d")

    with open(stores_path, encoding="utf-8") as f:
        stores = json.load(f)

    print(f"=== {today_str} のタグ一覧 ===")
    for s in stores:
        if not s.get("enabled", False):
            continue
        name = s["store_name"]
        tags = get_tags_for_date(today_str, name)
        print(f"  {name}: {', '.join(sorted(tags)) if tags else '（なし）'}")
