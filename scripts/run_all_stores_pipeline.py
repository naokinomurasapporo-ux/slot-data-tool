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
  python scripts/run_all_stores_pipeline.py --date 20260315        # 日付を指定して保存
  python scripts/run_all_stores_pipeline.py --skip-existing        # 既存ファイルがある店舗をスキップ
  python scripts/run_all_stores_pipeline.py --backfill 7           # 直近7日の欠けた日をまとめて埋める
  python scripts/run_all_stores_pipeline.py --backfill 7 --dry-run # 実行せず欠けた日だけ確認する
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from run_one_store_pipeline import scrape, attach_judges, print_summary
from poc_scrape_one_store import SESSION_PATH

BASE_DIR = Path(__file__).parent.parent
STORES_CONFIG_PATH = BASE_DIR / "config" / "stores.json"
RULES_PATH = BASE_DIR / "config" / "rules.json"
PROCESSED_DIR = BASE_DIR / "data" / "processed"


# ---------------------------------------------------------------------------
# 引数パース
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="全店舗データ取得・高設定判定パイプライン",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  python scripts/run_all_stores_pipeline.py
  python scripts/run_all_stores_pipeline.py --date 20260315
  python scripts/run_all_stores_pipeline.py --skip-existing
  python scripts/run_all_stores_pipeline.py --backfill 7
  python scripts/run_all_stores_pipeline.py --backfill 7 --dry-run
        """,
    )
    parser.add_argument(
        "--date",
        metavar="YYYYMMDD",
        default=None,
        help="保存ファイルの日付ラベルを指定（省略時は今日）",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="指定日付の judged.json が既に存在する店舗はスキップする",
    )
    parser.add_argument(
        "--backfill",
        type=int,
        metavar="N",
        default=None,
        help="直近N日間で欠けている日付をまとめて埋める",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="（--backfill と併用）実際には実行せず、欠けている日付だけ表示する",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="確認プロンプトをスキップして即実行する（管理画面・自動化用）",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# ファイル存在チェック
# ---------------------------------------------------------------------------

def judged_path(store_name: str, date_str: str) -> Path:
    safe_store = store_name.replace("/", "_").replace(" ", "_")[:20]
    return PROCESSED_DIR / f"{date_str}_{safe_store}_judged.json"


def judged_exists(store_name: str, date_str: str) -> bool:
    return judged_path(store_name, date_str).exists()


# ---------------------------------------------------------------------------
# 欠けている日付を調べる
# ---------------------------------------------------------------------------

def find_missing_combos(stores: list[dict], days: int) -> list[tuple[str, str]]:
    """
    直近 days 日間（今日を除く）で judged.json が存在しない
    (date_str, store_name) の組み合わせを返す。古い日付から順に並ぶ。
    """
    today = date.today()
    missing = []
    for n in range(days, 0, -1):          # days日前 → 1日前 の順
        d = today - timedelta(days=n)
        date_str = d.strftime("%Y%m%d")
        for store in stores:
            if not judged_exists(store["store_name"], date_str):
                missing.append((date_str, store["store_name"]))
    return missing


# ---------------------------------------------------------------------------
# 店舗設定の読み込み
# ---------------------------------------------------------------------------

def load_stores_config() -> list[dict]:
    if not STORES_CONFIG_PATH.exists():
        print(f"[ERROR] 店舗設定ファイルが見つかりません: {STORES_CONFIG_PATH}")
        return []
    with open(STORES_CONFIG_PATH, encoding="utf-8") as f:
        stores = json.load(f)
    enabled = [s for s in stores if s.get("enabled", False)]
    enabled.sort(key=lambda s: s.get("sort_order", 999))
    return enabled


# ---------------------------------------------------------------------------
# 1店舗のスクレイピング → 判定（保存はしない）
# ---------------------------------------------------------------------------

def scrape_and_judge(store_name: str, rules: dict,
                     target_date: str | None = None) -> list[dict] | None:
    """
    スクレイピングと高設定判定だけ行い、結果を返す。
    target_date を指定すると、大当り一覧の日付タブを押してその日のデータを取得する。
    失敗した場合は None を返す。
    """
    print(f"\n  【フェーズ1】データ取得中 ...")
    jugler_results = scrape(store_name, target_date=target_date)
    if not jugler_results:
        print(f"  [ERROR] データ取得に失敗しました。")
        return None

    machine_count = len(jugler_results)
    unit_count = sum(len(m.get("slot_data", [])) for m in jugler_results)
    print(f"  取得完了: {machine_count} 機種 / {unit_count} 台")

    print(f"  【フェーズ2】高設定判定中 ...")
    judged = attach_judges(jugler_results, rules)
    print(f"  判定完了")

    return judged


# ---------------------------------------------------------------------------
# 1店舗 1日付 の保存
# ---------------------------------------------------------------------------

def save_judged(store_name: str, judged_machines: list[dict], date_str: str) -> Path:
    """judged.json を date_str のラベルで保存する。"""
    out_path = judged_path(store_name, date_str)
    output = {
        "date": date_str,
        "store_name": store_name,
        "jugler_machines": judged_machines,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    return out_path


# ---------------------------------------------------------------------------
# 通常実行: 指定日付で全店舗を処理
# ---------------------------------------------------------------------------

def run_one_store(store_name: str, rules: dict, target_date: str,
                  skip_existing: bool = False) -> dict:
    """
    1店舗分の「取得 → 判定 → 保存」を実行する。
    """
    result = {
        "store_name": store_name,
        "success": False,
        "out_path": None,
        "machine_count": 0,
        "unit_count": 0,
        "skipped": False,
    }

    if skip_existing and judged_exists(store_name, target_date):
        print(f"  スキップ（既存あり）: {judged_path(store_name, target_date).name}")
        result["skipped"] = True
        result["success"] = True
        return result

    judged = scrape_and_judge(store_name, rules, target_date=target_date)
    if judged is None:
        return result

    result["machine_count"] = len(judged)
    result["unit_count"] = sum(len(m.get("slot_data", [])) for m in judged)

    print(f"  【フェーズ3】ファイル保存中 ...")
    out_path = save_judged(store_name, judged, target_date)
    print(f"  保存完了: {out_path.relative_to(BASE_DIR)}")

    print_summary(store_name, judged)

    result["success"] = True
    result["out_path"] = str(out_path.relative_to(BASE_DIR))
    return result


# ---------------------------------------------------------------------------
# バックフィル実行
# ---------------------------------------------------------------------------

def run_backfill(stores: list[dict], rules: dict, missing: list[tuple[str, str]]) -> None:
    """
    missing の (date_str, store_name) について、大当り一覧の日付タブを押して
    実際のその日のデータを取得・保存する。

    取得の順序: 日付ごと × 店舗ごとに1ブラウザセッションを使用する。
    日付タブが存在しない場合はその (日付, 店舗) の取得をスキップする。
    """
    # date_str → [store_name, ...] にグループ化（日付順に処理）
    date_to_stores: dict[str, list[str]] = defaultdict(list)
    store_order = [s["store_name"] for s in stores]
    for date_str, store_name in missing:
        date_to_stores[date_str].append(store_name)

    # 日付を古い順に処理
    sorted_dates = sorted(date_to_stores.keys())
    total_combos = len(missing)
    done = 0

    backfill_results: list[dict] = []

    for target_date in sorted_dates:
        target_stores = sorted(
            date_to_stores[target_date],
            key=lambda n: store_order.index(n) if n in store_order else 999,
        )

        print(f"\n{'═' * 60}")
        print(f"  バックフィル日付: {target_date}  ({len(target_stores)} 店舗)")
        print(f"{'═' * 60}")

        for store_name in target_stores:
            done += 1
            print(f"\n{'─' * 60}")
            print(f"  [{done}/{total_combos}] {store_name}  /  {target_date}")
            print(f"{'─' * 60}")

            judged = scrape_and_judge(store_name, rules, target_date=target_date)

            if judged is None:
                print(f"  [ERROR] 取得失敗 → スキップします")
                backfill_results.append({
                    "date": target_date,
                    "store_name": store_name,
                    "success": False,
                    "reason": "scrape_failed",
                })
                continue

            # データが取れたか確認（全機種でタブが見つからない場合も考慮）
            has_data = any(m.get("slot_data") for m in judged)
            if not has_data:
                all_errors = [m.get("error", "") for m in judged]
                tab_missing = all("日付タブ" in e for e in all_errors if e)
                reason = "date_tab_not_found" if tab_missing else "no_data"
                print(f"  [WARN] データなし（{reason}）→ スキップします")
                backfill_results.append({
                    "date": target_date,
                    "store_name": store_name,
                    "success": False,
                    "reason": reason,
                })
                continue

            print(f"  【フェーズ3】ファイル保存中 ...")
            out_path = save_judged(store_name, judged, target_date)
            print(f"  保存完了: {out_path.relative_to(BASE_DIR)}")
            backfill_results.append({
                "date": target_date,
                "store_name": store_name,
                "success": True,
                "out_path": str(out_path.relative_to(BASE_DIR)),
            })

    # サマリー
    print("\n" + "=" * 60)
    print("  バックフィル サマリー")
    print("=" * 60)
    succeeded = [r for r in backfill_results if r["success"]]
    failed    = [r for r in backfill_results if not r["success"]]
    tab_miss  = [r for r in failed if r.get("reason") == "date_tab_not_found"]
    other_err = [r for r in failed if r.get("reason") != "date_tab_not_found"]

    print(f"\n  成功: {len(succeeded)} 件  /  失敗: {len(failed)} 件（合計 {total_combos} 件）")
    if tab_miss:
        print(f"\n  [サイトで取得不可（日付タブなし）: {len(tab_miss)} 件]")
        for r in tab_miss:
            print(f"    - {r['date']}  {r['store_name']}")
    if other_err:
        print(f"\n  [取得エラー: {len(other_err)} 件]")
        for r in other_err:
            print(f"    - {r['date']}  {r['store_name']}")
    print("\n" + "=" * 60)


# ---------------------------------------------------------------------------
# 全店舗サマリー表示
# ---------------------------------------------------------------------------

def print_all_summary(results: list[dict]):
    print("\n" + "=" * 60)
    print("  全店舗 処理サマリー")
    print("=" * 60)

    succeeded = [r for r in results if r["success"] and not r.get("skipped")]
    skipped   = [r for r in results if r.get("skipped")]
    failed    = [r for r in results if not r["success"]]

    print(f"\n  処理店舗数: {len(results)} 店舗")
    print(f"  成功: {len(succeeded)} 店舗  /  スキップ: {len(skipped)} 店舗  /  失敗: {len(failed)} 店舗\n")

    print(f"  {'店舗名':<25}  {'状態':^8}  {'機種数':>5}  {'台数':>5}  出力ファイル")
    print(f"  {'-' * 78}")

    for r in results:
        if r.get("skipped"):
            status = "スキップ"
        elif r["success"]:
            status = "OK"
        else:
            status = "失敗"
        out = r["out_path"] or "-"
        print(
            f"  {r['store_name']:<25}  {status:^8}  "
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
    args = parse_args()

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    (BASE_DIR / "data" / "raw").mkdir(parents=True, exist_ok=True)

    # セッション確認
    session_path = BASE_DIR / SESSION_PATH
    if not session_path.exists():
        print(f"[ERROR] セッションファイルが見つかりません: {session_path}")
        print("\n先に以下を実行してください:")
        print("  python scripts/save_session.py")
        return

    # 店舗リスト
    stores = load_stores_config()
    if not stores:
        print("[ERROR] 処理対象の店舗がありません。")
        print(f"config/stores.json の enabled を true にしてください。")
        return

    # ルール
    with open(RULES_PATH, encoding="utf-8") as f:
        rules = json.load(f)

    # ===== バックフィルモード =====
    if args.backfill:
        missing = find_missing_combos(stores, args.backfill)

        print("=" * 60)
        print(f"  バックフィルモード（直近 {args.backfill} 日間をチェック）")
        print("=" * 60)

        if not missing:
            print(f"\n  直近 {args.backfill} 日間の欠けはありません。\n")
            return

        # 欠けている日付×店舗を一覧表示
        print(f"\n  欠けている日付 × 店舗: {len(missing)} 件\n")
        current_date = None
        for date_str, store_name in missing:
            if date_str != current_date:
                print(f"  【{date_str}】")
                current_date = date_str
            print(f"    - {store_name}")
        print()

        if args.dry_run:
            print("  --dry-run モード: 実際の取得は行いません。")
            return

        print("  日付タブ方式で過去日のデータも取得します。")
        print()

        if not args.yes:
            try:
                confirm = input("  続行しますか？ [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n  キャンセルしました。")
                return

            if confirm != "y":
                print("  キャンセルしました。")
                return

        run_backfill(stores, rules, missing)
        return

    # ===== 通常モード =====
    target_date = args.date or date.today().strftime("%Y%m%d")

    # --date の形式チェック
    if args.date:
        if len(args.date) != 8 or not args.date.isdigit():
            print(f"[ERROR] --date の形式が正しくありません: {args.date}")
            print("        例: --date 20260315")
            return

    print("=" * 60)
    print(f"  複数店舗パイプライン開始")
    print(f"  日付        : {target_date}")
    print(f"  処理店舗数  : {len(stores)} 店舗")
    if args.skip_existing:
        print(f"  既存スキップ: ON")
    for i, s in enumerate(stores, 1):
        print(f"    {i}. {s['store_name']}")
    print("=" * 60)

    all_results = []
    for i, store in enumerate(stores, 1):
        store_name = store["store_name"]
        print(f"\n{'─' * 60}")
        print(f"  [{i}/{len(stores)}] 店舗: {store_name}")
        print(f"{'─' * 60}")

        result = run_one_store(store_name, rules, target_date,
                               skip_existing=args.skip_existing)
        all_results.append(result)

    print_all_summary(all_results)


if __name__ == "__main__":
    main()
