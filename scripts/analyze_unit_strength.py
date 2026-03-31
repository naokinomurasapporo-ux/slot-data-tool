#!/usr/bin/env python3
"""
台番号別強さ分析スクリプト

30d_<店舗名>.json を読み込み、台番号ごとの「強さ」を3つの観点で分析する。

  案1: スコア集計ランキング  (◎=3, ○=2, △=1, ×=0, blank=除外)
  案2: 高設定出現率          (◎+○日数 / 判定あり日数)
  案3: 末尾番号傾向分析      (台番号末尾1桁でグループ化)

実行方法:
  python scripts/analyze_unit_strength.py
  python scripts/analyze_unit_strength.py --store 本八幡ＵＮＯ
  python scripts/analyze_unit_strength.py --store 本八幡ＵＮＯ --top 20
  python scripts/analyze_unit_strength.py --store 本八幡ＵＮＯ --min-days 5
  python scripts/analyze_unit_strength.py --store 本八幡ＵＮＯ --suffix-len 2
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"
STORES_CONFIG = BASE_DIR / "config" / "stores.json"

JUDGE_SCORE = {"◎": 3, "○": 2, "△": 1, "×": 0}
HIGH_JUDGES = {"◎", "○"}


# ---------------------------------------------------------------------------
# データ読み込み
# ---------------------------------------------------------------------------

def load_store_names() -> list[str]:
    if not STORES_CONFIG.exists():
        return []
    with open(STORES_CONFIG, encoding="utf-8") as f:
        stores = json.load(f)
    return [s["store_name"] for s in stores if s.get("enabled", True)]


def load_30d_json(store_name: str) -> dict | None:
    safe_name = store_name.replace("/", "_").replace(" ", "_").replace("　", "_")
    path = PROCESSED_DIR / f"30d_{safe_name}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------

def aggregate_units(store_json: dict) -> list[dict]:
    """
    全機種をまたいで台番号ごとに集計する。
    同じ台番号が複数機種にある場合は合算する（機種変更等）。
    """
    unit_stats: dict[str, dict] = defaultdict(lambda: {
        "score": 0,
        "double": 0,   # ◎
        "circle": 0,   # ○
        "triangle": 0, # △
        "cross": 0,    # ×
        "judged_days": 0,
        "machines": set(),
    })

    for machine in store_json.get("machines", []):
        mname = machine["name"]
        for unit in machine.get("units", []):
            uid = unit["unit"]
            stats = unit_stats[uid]
            stats["machines"].add(mname)
            for day_data in unit.get("days", {}).values():
                judge = day_data.get("judge", "blank")
                if judge == "blank" or judge == "" or judge not in JUDGE_SCORE:
                    continue
                stats["judged_days"] += 1
                stats["score"] += JUDGE_SCORE[judge]
                if judge == "◎":
                    stats["double"] += 1
                elif judge == "○":
                    stats["circle"] += 1
                elif judge == "△":
                    stats["triangle"] += 1
                elif judge == "×":
                    stats["cross"] += 1

    results = []
    for uid, s in unit_stats.items():
        judged = s["judged_days"]
        high = s["double"] + s["circle"]
        results.append({
            "unit": uid,
            "judged_days": judged,
            "score": s["score"],
            "avg_score": s["score"] / judged if judged > 0 else 0.0,
            "double": s["double"],
            "circle": s["circle"],
            "triangle": s["triangle"],
            "cross": s["cross"],
            "high_rate": high / judged if judged > 0 else 0.0,
            "machines": sorted(s["machines"]),
        })

    return results


def aggregate_suffix(unit_rows: list[dict], suffix_len: int) -> list[dict]:
    """末尾N桁でグループ化して集計する。"""
    suffix_stats: dict[str, dict] = defaultdict(lambda: {
        "score": 0,
        "judged_days": 0,
        "high_days": 0,
        "unit_count": 0,
    })

    for row in unit_rows:
        uid = row["unit"]
        if not uid.isdigit():
            suffix = uid[-suffix_len:]
        else:
            suffix = uid[-suffix_len:].zfill(suffix_len)
        s = suffix_stats[suffix]
        s["judged_days"] += row["judged_days"]
        s["score"] += row["score"]
        s["high_days"] += row["double"] + row["circle"]
        s["unit_count"] += 1

    results = []
    for suffix, s in suffix_stats.items():
        judged = s["judged_days"]
        results.append({
            "suffix": suffix,
            "unit_count": s["unit_count"],
            "judged_days": judged,
            "score": s["score"],
            "avg_score": s["score"] / judged if judged > 0 else 0.0,
            "high_days": s["high_days"],
            "high_rate": s["high_days"] / judged if judged > 0 else 0.0,
        })

    return results


# ---------------------------------------------------------------------------
# 表示
# ---------------------------------------------------------------------------

def print_separator(char="─", width=80):
    print(char * width)


def print_unit_ranking(unit_rows: list[dict], top: int, min_days: int, store_name: str):
    filtered = [r for r in unit_rows if r["judged_days"] >= min_days]

    print()
    print_separator("═")
    print(f"  店舗: {store_name}")
    print_separator("═")
    print(f"  集計台数: {len(unit_rows)} 台  /  最小判定日数フィルタ: {min_days}日以上 → {len(filtered)} 台")
    print()

    # 案1: スコアランキング
    print("【案1】スコアランキング (◎=3点 ○=2点 △=1点 ×=0点 / 判定あり日のみ)")
    print_separator()
    print(f"{'順位':>4}  {'台番号':>6}  {'判定日':>5}  {'合計':>5}  {'平均':>5}  {'◎':>4}  {'○':>4}  {'△':>4}  {'×':>4}  機種")
    print_separator()
    ranked = sorted(filtered, key=lambda r: (-r["avg_score"], -r["score"], r["unit"]))
    for i, r in enumerate(ranked[:top], 1):
        machines_str = ", ".join(r["machines"])
        if len(machines_str) > 30:
            machines_str = machines_str[:27] + "..."
        print(
            f"{i:>4}  {r['unit']:>6}  {r['judged_days']:>5}日"
            f"  {r['score']:>5}pt  {r['avg_score']:>5.2f}"
            f"  {r['double']:>4}  {r['circle']:>4}  {r['triangle']:>4}  {r['cross']:>4}"
            f"  {machines_str}"
        )
    print_separator()
    print()

    # 案2: 高設定出現率ランキング
    print("【案2】高設定出現率ランキング (◎+○日数 / 判定あり日数)")
    print_separator()
    print(f"{'順位':>4}  {'台番号':>6}  {'判定日':>5}  {'高設定':>6}  {'出現率':>7}  {'◎':>4}  {'○':>4}  機種")
    print_separator()
    ranked2 = sorted(filtered, key=lambda r: (-r["high_rate"], -(r["double"] + r["circle"]), r["unit"]))
    for i, r in enumerate(ranked2[:top], 1):
        high = r["double"] + r["circle"]
        machines_str = ", ".join(r["machines"])
        if len(machines_str) > 30:
            machines_str = machines_str[:27] + "..."
        print(
            f"{i:>4}  {r['unit']:>6}  {r['judged_days']:>5}日"
            f"  {high:>5}日  {r['high_rate']:>6.1%}"
            f"  {r['double']:>4}  {r['circle']:>4}"
            f"  {machines_str}"
        )
    print_separator()
    print()


def print_suffix_analysis(suffix_rows: list[dict], suffix_len: int, min_units: int = 2):
    print(f"【案3】末尾{suffix_len}桁グループ別傾向分析")
    print_separator()

    filtered = [r for r in suffix_rows if r["unit_count"] >= min_units]
    if not filtered:
        filtered = suffix_rows

    ranked = sorted(filtered, key=lambda r: (-r["high_rate"], -r["avg_score"]))

    print(f"{'末尾':>6}  {'台数':>4}  {'判定日':>6}  {'高設定日':>8}  {'高設定率':>8}  {'平均スコア':>9}  {'評価'}")
    print_separator()
    for r in ranked:
        bar_len = int(r["high_rate"] * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(
            f"末尾{r['suffix']:>2}  {r['unit_count']:>4}台  {r['judged_days']:>6}日"
            f"  {r['high_days']:>8}日  {r['high_rate']:>7.1%}"
            f"  {r['avg_score']:>9.2f}  {bar}"
        )
    print_separator()
    print()


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="台番号別の強さを分析する (案1: スコア, 案2: 高設定率, 案3: 末尾分析)"
    )
    parser.add_argument("--store", metavar="STORE_NAME", default=None,
                        help="特定の店舗名（部分一致）。省略時は全店舗")
    parser.add_argument("--top", type=int, default=15, metavar="N",
                        help="案1・案2で表示する上位N台 (デフォルト: 15)")
    parser.add_argument("--min-days", type=int, default=3, metavar="N",
                        help="集計に含める最小判定日数 (デフォルト: 3)")
    parser.add_argument("--suffix-len", type=int, default=1, choices=[1, 2, 3],
                        help="末尾分析の桁数 (デフォルト: 1)")
    args = parser.parse_args()

    store_names = load_store_names()
    if not store_names:
        # stores.json がなければ 30d_*.json を直接探す
        store_names = [
            p.stem[len("30d_"):].replace("_", "　")
            for p in PROCESSED_DIR.glob("30d_*.json")
        ]

    if args.store:
        store_names = [s for s in store_names if args.store in s]
        if not store_names:
            print(f"[ERROR] 店舗名「{args.store}」にマッチする店舗が見つかりません。")
            sys.exit(1)

    if not store_names:
        print("[ERROR] 対象店舗が見つかりません。")
        sys.exit(1)

    for store_name in store_names:
        store_json = load_30d_json(store_name)
        if store_json is None:
            print(f"[WARN] {store_name} の 30d JSON が見つかりません。スキップします。")
            continue

        unit_rows = aggregate_units(store_json)
        if not unit_rows:
            print(f"[WARN] {store_name} にデータがありません。スキップします。")
            continue

        print_unit_ranking(unit_rows, top=args.top, min_days=args.min_days, store_name=store_name)

        suffix_rows = aggregate_suffix(unit_rows, suffix_len=args.suffix_len)
        print_suffix_analysis(suffix_rows, suffix_len=args.suffix_len)

        if len(store_names) > 1:
            print()


if __name__ == "__main__":
    main()
