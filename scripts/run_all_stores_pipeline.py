#!/usr/bin/env python3
"""
複数店舗パイプラインスクリプト: 「取得 → 判定 → 保存」を複数店舗に対して順番に実行する

処理の流れ:
  1. config/stores.json から enabled=true の店舗を sort_order 順に読み込む
  2. 各店舗ごとに以下を繰り返す:
     a. マイホールから対象店舗を開く
     b. ジャグラー機種一覧を取得
     c. 大当り一覧から各台データを取得
     d. config/rules.json のルールで高設定判定（◎/○/△/×）を付与
     e. data/processed/YYYYMMDD_<店舗名>_judged.json に保存
  3. 全店舗の処理が終わったら合計サマリーを表示

前提:
  - 事前に python scripts/save_session.py を実行してセッションを保存済みであること
  - config/stores.json の enabled を true にした店舗だけ処理される

実行方法:
  python scripts/run_all_stores_pipeline.py
"""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from run_one_store_pipeline import scrape, attach_judges, print_summary
from poc_scrape_one_store import SESSION_PATH

BASE_DIR = Path(__file__).parent.parent
STORES_CONFIG_PATH = BASE_DIR / "config" / "stores.json"
RULES_PATH = BASE_DIR / "config" / "rules.json"


# ---------------------------------------------------------------------------
# 店舗設定の読み込み
# ---------------------------------------------------------------------------

def load_stores_config() -> list[dict]:
    """
    config/stores.json を読み込み、enabled=true の店舗を sort_order 順に返す。
    """
    if not STORES_CONFIG_PATH.exists():
        print(f"[ERROR] 店舗設定ファイルが見つかりません: {STORES_CONFIG_PATH}")
        return []

    with open(STORES_CONFIG_PATH, encoding="utf-8") as f:
        stores = json.load(f)

    enabled = [s for s in stores if s.get("enabled", False)]
    enabled.sort(key=lambda s: s.get("sort_order", 999))
    return enabled


# ---------------------------------------------------------------------------
# 1店舗分のパイプライン
# ---------------------------------------------------------------------------

def run_one_store(store_name: str, rules: dict, today: str) -> dict:
    """
    1店舗分の「取得 → 判定 → 保存」を実行する。

    Returns:
        {
            "store_name": str,
            "success": bool,
            "out_path": str or None,
            "machine_count": int,
            "unit_count": int,
        }
    """
    result = {
        "store_name": store_name,
        "success": False,
        "out_path": None,
        "machine_count": 0,
        "unit_count": 0,
    }

    # フェーズ1: スクレイピング
    print(f"\n  【フェーズ1】データ取得中 ...")
    jugler_results = scrape(store_name)

    if not jugler_results:
        print(f"  [ERROR] データ取得に失敗しました。この店舗をスキップします。")
        return result

    result["machine_count"] = len(jugler_results)
    result["unit_count"] = sum(len(m.get("slot_data", [])) for m in jugler_results)
    print(f"  取得完了: {result['machine_count']} 機種 / {result['unit_count']} 台")

    # フェーズ2: 高設定判定
    print(f"  【フェーズ2】高設定判定中 ...")
    judged_machines = attach_judges(jugler_results, rules)
    print(f"  判定完了")

    # フェーズ3: 保存
    print(f"  【フェーズ3】ファイル保存中 ...")
    safe_store = store_name.replace("/", "_").replace(" ", "_")[:20]
    out_path = BASE_DIR / "data" / "processed" / f"{today}_{safe_store}_judged.json"

    output = {
        "date": today,
        "store_name": store_name,
        "jugler_machines": judged_machines,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"  保存完了: {out_path.relative_to(BASE_DIR)}")

    # 結果表示
    print_summary(store_name, judged_machines)

    result["success"] = True
    result["out_path"] = str(out_path.relative_to(BASE_DIR))
    return result


# ---------------------------------------------------------------------------
# 全店舗サマリー表示
# ---------------------------------------------------------------------------

def print_all_summary(results: list[dict]):
    print("\n" + "=" * 60)
    print("  全店舗 処理サマリー")
    print("=" * 60)

    succeeded = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]

    print(f"\n  処理店舗数: {len(results)} 店舗")
    print(f"  成功: {len(succeeded)} 店舗  /  失敗: {len(failed)} 店舗\n")

    print(f"  {'店舗名':<25}  {'状態':^6}  {'機種数':>5}  {'台数':>5}  出力ファイル")
    print(f"  {'-' * 75}")

    for r in results:
        status = "OK" if r["success"] else "失敗"
        out = r["out_path"] or "-"
        print(
            f"  {r['store_name']:<25}  {status:^6}  "
            f"{r['machine_count']:>5}  {r['unit_count']:>5}  {out}"
        )

    if failed:
        print(f"\n  [注意] 失敗した店舗:")
        for r in failed:
            print(f"    - {r['store_name']}")

    print("\n" + "=" * 60)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main():
    today = date.today().strftime("%Y%m%d")

    (BASE_DIR / "data" / "raw").mkdir(parents=True, exist_ok=True)
    (BASE_DIR / "data" / "processed").mkdir(parents=True, exist_ok=True)

    # セッション確認
    session_path = BASE_DIR / SESSION_PATH
    if not session_path.exists():
        print(f"[ERROR] セッションファイルが見つかりません: {session_path}")
        print("\n先に以下を実行してください:")
        print("  python scripts/save_session.py")
        return

    # 店舗リスト読み込み
    stores = load_stores_config()
    if not stores:
        print("[ERROR] 処理対象の店舗がありません。")
        print(f"config/stores.json の enabled を true にしてください。")
        return

    # ルール読み込み
    with open(RULES_PATH, encoding="utf-8") as f:
        rules = json.load(f)

    print("=" * 60)
    print(f"  複数店舗パイプライン開始")
    print(f"  日付        : {today}")
    print(f"  処理店舗数  : {len(stores)} 店舗")
    for i, s in enumerate(stores, 1):
        print(f"    {i}. {s['store_name']}")
    print("=" * 60)

    # 各店舗を順番に処理
    all_results = []
    for i, store in enumerate(stores, 1):
        store_name = store["store_name"]
        print(f"\n{'─' * 60}")
        print(f"  [{i}/{len(stores)}] 店舗: {store_name}")
        print(f"{'─' * 60}")

        result = run_one_store(store_name, rules, today)
        all_results.append(result)

    # 全店舗サマリー
    print_all_summary(all_results)


if __name__ == "__main__":
    main()
