#!/usr/bin/env python3
"""
高設定判定スクリプト

data/raw/jugler_daiatari.json を読み込み、config/rules.json のルールに基づいて
各台に judge（◎ / ○ / △ / × / blank）を付与する。
結果を data/processed/jugler_daiatari_judged.json に保存し、
各機種の上位 TOP_N 件を標準出力に表示する。

# 判定仕様（2026年改訂版）
## 基本判定
  ◎ = 設定6相当: games >= min_games_double かつ REG <= reg_best  かつ 合算 <= comb_best
  ○ = 設定5相当: games >= min_games_circle かつ REG <= reg_better かつ 合算 <= comb_better
  △ = 設定4相当: games >= min_games_blank  かつ REG <= reg_good   かつ 合算 <= comb_good  (AND条件)
  × = それ以外

## 昇格判定（最大1段階）
  × → △、△ → ○、○ → ◎ の1段階昇格のみ許可。
  昇格スコア = (lower - actual) / (lower - upper)
    lower: 昇格元基準（×の場合は reg_good * 2 を仮想lower として使用）
    upper: 昇格先基準
  昇格条件: 少なくとも片方が1.0以上 かつ 両方が0.3以上 かつ 合計が1.2以上 かつ 昇格先の回転数条件を満たす
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

# 各判定ランクのしきい値キー名（reg, comb）
THRESHOLD_KEYS: dict[str, tuple[str, str]] = {
    "△": ("reg_good",   "comb_good"),
    "○": ("reg_better", "comb_better"),
    "◎": ("reg_best",   "comb_best"),
}

# 昇格先マップ: 基本判定 → 昇格先
PROMOTION_TARGET: dict[str, str] = {
    "×": "△",
    "△": "○",
    "○": "◎",
}

# 昇格先の回転数条件キー名
PROMOTION_MIN_GAMES: dict[str, str] = {
    "△": "min_games_blank",
    "○": "min_games_circle",
    "◎": "min_games_double",
}

# × の仮想lower を算出するための倍率
# score = (lower - actual) / (lower - upper) で lower = upper * _X_LOWER_MULTIPLIER
# actual = upper (△基準ちょうど) のとき score = 1.0 になる
_X_LOWER_MULTIPLIER = 2.0


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
# 昇格スコア計算
# ---------------------------------------------------------------------------

def _calc_score(actual: float, lower: float, upper: float) -> float:
    """
    昇格到達度スコアを計算する。

    score = (lower - actual) / (lower - upper)

    前提: lower > upper（ジャグラーは 1/N の N が小さいほど良い = upper < lower）
      score = 0.0 : 昇格元基準ちょうど（lower）
      score = 1.0 : 昇格先基準ちょうど（upper）
      score > 1.0 : 昇格先基準をさらに上回る
      score < 0   : 昇格元基準より悪い
    """
    if lower == upper:
        # 閾値が同一（設定5=設定6など）の場合: 以下なら確実に達成、超えていれば確実に未達
        return 999.0 if actual <= upper else -999.0
    return (lower - actual) / (lower - upper)


# ---------------------------------------------------------------------------
# 判定ロジック（詳細版）
# ---------------------------------------------------------------------------

def judge_unit_with_debug(
    games: int, rb_count: int, combined_period: int, rule: dict
) -> tuple[str, dict]:
    """
    1台分の判定を行い、(最終判定, デバッグ情報) を返す。

    デバッグ情報の構造:
        base_judge  : 基本判定結果（昇格前）
        rb_period   : 実測 RB 出現率（1/N のN）、RB0回の場合は None
        target_judge: 昇格先（昇格対象外は None）
        reg_score   : REG 昇格スコア（None = スコア計算なし）
        comb_score  : 合算 昇格スコア（None = スコア計算なし）
        promotion   : 昇格したか（bool）
        final_judge : 最終判定結果
        reason      : 判定理由の文字列
    """
    # ① ゲーム数不足 → blank
    if games < rule["min_games_blank"]:
        debug = {
            "base_judge":   "blank",
            "rb_period":    None,
            "target_judge": None,
            "reg_score":    None,
            "comb_score":   None,
            "promotion":    False,
            "final_judge":  "blank",
            "reason":       f"G数不足({games}<{rule['min_games_blank']})",
        }
        return "blank", debug

    # RB出現率（1/N の N）。RBが0回なら inf（判定不能扱い）
    rb_period = games / rb_count if rb_count > 0 else float("inf")

    # ── 基本判定 ────────────────────────────────────────────────────────────

    # ② ◎: min_games_double以上 かつ REG<=設定6基準 かつ 合算<=設定6基準
    if (
        games >= rule["min_games_double"]
        and rb_period <= rule["reg_best"]
        and combined_period <= rule["comb_best"]
    ):
        base_judge = "◎"

    # ③ ○: min_games_circle以上 かつ REG<=設定5基準 かつ 合算<=設定5基準
    elif (
        games >= rule["min_games_circle"]
        and rb_period <= rule["reg_better"]
        and combined_period <= rule["comb_better"]
    ):
        base_judge = "○"

    # ④ △: REG<=設定4基準 かつ 合算<=設定4基準（AND条件）
    elif (
        rb_period <= rule["reg_good"]
        and combined_period <= rule["comb_good"]
    ):
        base_judge = "△"

    # ⑤ それ以外 → ×
    else:
        base_judge = "×"

    # ── 昇格判定（最大1段階）───────────────────────────────────────────────

    target_judge = PROMOTION_TARGET.get(base_judge)  # ◎ は None（昇格なし）
    reg_score = None
    comb_score = None
    promotion = False
    reason = ""

    if target_judge is not None:
        # 昇格先の回転数条件を確認
        min_games_key = PROMOTION_MIN_GAMES[target_judge]
        min_games_required = rule[min_games_key]

        if games < min_games_required:
            reason = f"昇格NG: G数不足({games}<{min_games_required}) {base_judge}→{target_judge}"
        else:
            # 昇格先・昇格元のしきい値を取得
            upper_reg_key, upper_comb_key = THRESHOLD_KEYS[target_judge]
            upper_reg  = rule[upper_reg_key]
            upper_comb = rule[upper_comb_key]

            if base_judge == "×":
                # × には明示的な下限がないため、仮想lower = upper * 倍率 を使用
                lower_reg  = upper_reg  * _X_LOWER_MULTIPLIER
                lower_comb = upper_comb * _X_LOWER_MULTIPLIER
            else:
                lower_reg_key, lower_comb_key = THRESHOLD_KEYS[base_judge]
                lower_reg  = rule[lower_reg_key]
                lower_comb = rule[lower_comb_key]

            # スコア計算（RB0回 = rb_period=inf → スコアは -inf となり昇格不可）
            raw_reg_score  = _calc_score(rb_period,          lower_reg,  upper_reg)
            raw_comb_score = _calc_score(float(combined_period), lower_comb, upper_comb)

            reg_score  = round(raw_reg_score,  3)
            comb_score = round(raw_comb_score, 3)

            # 昇格条件チェック
            at_least_one_ge_1 = (raw_reg_score >= 1.0 or  raw_comb_score >= 1.0)
            both_ge_0_3       = (raw_reg_score >= 0.3 and raw_comb_score >= 0.3)
            score_sum         = raw_reg_score + raw_comb_score
            sum_ge_1_2        = (score_sum >= 1.2)

            if at_least_one_ge_1 and both_ge_0_3 and sum_ge_1_2:
                promotion = True
                reason = (
                    f"昇格OK: {base_judge}→{target_judge} "
                    f"REG={reg_score:.3f} 合算={comb_score:.3f} 合計={score_sum:.3f}"
                )
            else:
                fails = []
                if not at_least_one_ge_1:
                    fails.append("1.0以上なし")
                if not both_ge_0_3:
                    fails.append(f"0.3未満あり(REG={reg_score:.3f},合算={comb_score:.3f})")
                if not sum_ge_1_2:
                    fails.append(f"合計{score_sum:.3f}<1.2")
                reason = (
                    f"昇格NG: {base_judge}→{target_judge} "
                    f"REG={reg_score:.3f} 合算={comb_score:.3f} ({', '.join(fails)})"
                )
    else:
        reason = f"昇格対象外({base_judge}は最上位または blank)"

    final_judge = target_judge if promotion else base_judge

    debug = {
        "base_judge":   base_judge,
        "rb_period":    round(rb_period, 1) if rb_count > 0 else None,
        "target_judge": target_judge,
        "reg_score":    reg_score,
        "comb_score":   comb_score,
        "promotion":    promotion,
        "final_judge":  final_judge,
        "reason":       reason,
    }
    return final_judge, debug


# ---------------------------------------------------------------------------
# 判定ロジック（後方互換版 — judge のみを返す）
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

    詳細なデバッグ情報が必要な場合は judge_unit_with_debug() を使用すること。
    """
    judge, _ = judge_unit_with_debug(games, rb_count, combined_period, rule)
    return judge


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

            judge, debug = judge_unit_with_debug(games, rb_count, combined_period, rule)

            judged_units.append({**unit, "judge": judge, "debug": debug})

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
    print(f"{'=' * 70}")
    print(f"  店舗: {result['store_name']}")
    print(f"{'=' * 70}\n")

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
        print(f"  {'台番':>6}  {'G数':>6}  {'RB率(1/N)':>9}  {'合算(1/N)':>9}  {'基本':>4}  {'最終':>4}  昇格理由")
        print(f"  {'-' * 70}")
        for u in top:
            rb_count = safe_int(u.get("rb", 0), default=0)
            games    = safe_int(u.get("games", 0), default=0)
            rb_period = games / rb_count if rb_count > 0 else 9999
            dbg = u.get("debug", {})
            print(
                f"  {u['unit']:>6}  {u['games']:>6}  "
                f"{rb_period:>9.0f}  {u['combined']:>9}  "
                f"{dbg.get('base_judge', '?'):>4}  {u['judge']:>4}  "
                f"{dbg.get('reason', '')}"
            )
        print()


if __name__ == "__main__":
    main()
