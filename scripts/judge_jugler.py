#!/usr/bin/env python3
"""
高設定判定スクリプト

data/raw/jugler_daiatari.json を読み込み、config/rules.json のルールに基づいて
各台に judge（◎ / ○ / △ / × / blank）を付与する。
結果を data/processed/jugler_daiatari_judged.json に保存し、
各機種の上位 TOP_N 件を標準出力に表示する。
"""

import json
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
RAW_PATH  = BASE_DIR / "data" / "raw"  / "jugler_daiatari.json"
RULES_PATH = BASE_DIR / "config" / "rules.json"
OUT_PATH  = BASE_DIR / "data" / "processed" / "jugler_daiatari_judged.json"

TOP_N = 3  # 各機種の表示上位件数

# 判定の優先順（ソート用）
JUDGE_ORDER = {"◎": 0, "○": 1, "△": 2, "×": 3, "blank": 4}


def safe_int(value, default: int = 999999) -> int:
    """'--' や空文字、None など変換できない値を安全に int へ変換する。失敗時は default を返す。"""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# ルール検索
# ---------------------------------------------------------------------------

def find_rule(machine_name: str, rules: dict) -> dict:
    """
    機種名に部分一致するルールを返す。
    複数マッチした場合はキーが長い（より具体的な）ものを優先。
    見つからなければ default を返す。
    """
    matched = [
        (key, rule)
        for key, rule in rules.items()
        if key not in ("default", "_comment", "_threshold_note")
        and key in machine_name
    ]
    if matched:
        # より長いキー（より具体的）を優先
        matched.sort(key=lambda x: len(x[0]), reverse=True)
        return matched[0][1]
    return rules["default"]


# ---------------------------------------------------------------------------
# 判定ロジック
# ---------------------------------------------------------------------------

def judge_unit(games: int, rb_count: int, combined_period: int, rule: dict) -> str:
    """
    1台分の判定を返す。

    引数:
        games          : ゲーム数
        rb_count       : RB回数（回）
        combined_period: 合算出現率の分母（例: 112 → 1/112）
        rule           : rules.json から取得したルール辞書

    返値:
        "◎" / "○" / "△" / "×" / "blank"
    """
    # ① ゲーム数不足 → blank
    if games < rule["min_games_blank"]:
        return "blank"

    # RB出現率（1/N の N）を計算。RBが0回なら判定不能扱い
    rb_period = games / rb_count if rb_count > 0 else float("inf")

    # ② ◎: min_games_double以上 かつ RB<=best かつ 合算<=best
    if (
        games >= rule["min_games_double"]
        and rb_period <= rule["reg_best"]
        and combined_period <= rule["comb_best"]
    ):
        return "◎"

    # ③ ○: min_games_circle以上 かつ RB<=better かつ 合算<=better
    if (
        games >= rule["min_games_circle"]
        and rb_period <= rule["reg_better"]
        and combined_period <= rule["comb_better"]
    ):
        return "○"

    # ④ △: min_games_blank以上 かつ (RB<=good または 合算<=good)
    if rb_period <= rule["reg_good"] or combined_period <= rule["comb_good"]:
        return "△"

    # ⑤ それ以外 → ×
    return "×"


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------

def main():
    # --- データ読み込み ---
    with open(RAW_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    with open(RULES_PATH, encoding="utf-8") as f:
        rules = json.load(f)

    result = {
        "store_name": raw["store_name"],
        "jugler_machines": [],
    }

    # --- 各機種・各台に判定を付与 ---
    for machine in raw["jugler_machines"]:
        machine_name = machine["name"]
        rule = find_rule(machine_name, rules)

        judged_units = []
        for unit in machine["slot_data"]:
            games           = safe_int(unit.get("games", 0), default=0)
            rb_count        = safe_int(unit.get("rb", 0), default=0)
            combined_period = safe_int(unit.get("combined", 999999))

            judge = judge_unit(games, rb_count, combined_period, rule)

            judged_units.append({**unit, "judge": judge})

        result["jugler_machines"].append({
            **{k: v for k, v in machine.items() if k != "slot_data"},
            "rule_used": next(
                (key for key in rules
                 if key not in ("default", "_comment", "_threshold_note")
                 and key in machine_name),
                "default"
            ),
            "slot_data": judged_units,
        })

    # --- JSON 保存 ---
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"✓ 保存完了: {OUT_PATH}\n")

    # --- 標準出力: 各機種の上位 TOP_N 件 ---
    print(f"{'=' * 60}")
    print(f"  店舗: {result['store_name']}")
    print(f"{'=' * 60}\n")

    for machine in result["jugler_machines"]:
        name  = machine["name"]
        units = machine["slot_data"]
        rule_key = machine["rule_used"]

        # 判定の良い順 → 同判定内は合算の小さい順
        sorted_units = sorted(
            units,
            key=lambda u: (JUDGE_ORDER.get(u["judge"], 9), safe_int(u.get("combined", 999999))),
        )
        top = sorted_units[:TOP_N]

        print(f"【{name}】  (適用ルール: {rule_key})")
        print(f"  {'台番':>6}  {'G数':>6}  {'RB率(1/N)':>9}  {'合算(1/N)':>9}  {'判定':>4}")
        print(f"  {'-' * 46}")
        for u in top:
            rb_count = safe_int(u.get("rb", 0), default=0)
            games    = safe_int(u.get("games", 0), default=0)
            rb_period = games / rb_count if rb_count > 0 else 9999
            print(
                f"  {u['unit']:>6}  {u['games']:>6}  "
                f"{rb_period:>9.0f}  {u['combined']:>9}  {u['judge']:>4}"
            )
        print()


if __name__ == "__main__":
    main()
