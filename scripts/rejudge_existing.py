#!/usr/bin/env python3
"""
既存 judged.json の判定を新ロジックで再計算するスクリプト

スクレイピングなしで、data/processed/ に保存済みの YYYYMMDD_*_judged.json を
読み込み、games / rb / combined から新しい判定ロジックで再判定して上書きする。

使用方法:
  python scripts/rejudge_existing.py                        # 全ファイルを再判定
  python scripts/rejudge_existing.py --date 20260325        # 指定日だけ再判定
  python scripts/rejudge_existing.py --dry-run              # 変化のある台数だけ確認（上書きなし）
  python scripts/rejudge_existing.py --date 20260325 --dry-run  # 指定日の dry-run
  python scripts/rejudge_existing.py --store アミューズ        # 特定店舗のみ

再判定後は build_30day_store_json.py を実行して30日JSONを再生成すること:
  python scripts/build_30day_store_json.py
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from judge_jugler import find_rule, judge_unit_with_debug, safe_int

BASE_DIR = Path(__file__).parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"
RULES_PATH = BASE_DIR / "config" / "rules.json"

JUDGED_FILE_PATTERN = re.compile(r"^(\d{8})_.+_judged\.json$")
EXCLUDE_FILES = {"jugler_daiatari_judged.json"}


def rejudge_file(path: Path, rules: dict, dry_run: bool = False) -> dict:
    """
    1ファイルを再判定し、変更統計を返す。
    dry_run=False の場合はファイルを上書きする。
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    changed = 0
    total = 0
    change_examples = []

    for machine in data.get("jugler_machines", []):
        machine_name = machine.get("name", "")
        rule = find_rule(machine_name, rules)

        for unit in machine.get("slot_data", []):
            try:
                games    = int(unit.get("games", 0))
                rb_count = int(unit.get("rb", 0))
                combined = int(unit.get("combined", 9999))
            except (ValueError, TypeError):
                continue

            old_judge = unit.get("judge", "blank")
            new_judge, new_debug = judge_unit_with_debug(games, rb_count, combined, rule)

            total += 1
            if old_judge != new_judge:
                changed += 1
                if len(change_examples) < 5:
                    change_examples.append({
                        "unit":      unit.get("unit", "?"),
                        "machine":   machine_name,
                        "old_judge": old_judge,
                        "new_judge": new_judge,
                        "reason":    new_debug.get("reason", ""),
                    })

            if not dry_run:
                unit["judge"] = new_judge
                unit["debug"] = new_debug

    if not dry_run and changed > 0:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    return {"total": total, "changed": changed, "examples": change_examples}


def main():
    parser = argparse.ArgumentParser(
        description="既存 judged.json を新ロジックで再判定する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  python scripts/rejudge_existing.py                         # 全件再判定
  python scripts/rejudge_existing.py --date 20260325         # 20260325 のみ再判定
  python scripts/rejudge_existing.py --date 20260325 --dry-run
  python scripts/rejudge_existing.py --store アミューズ       # 特定店舗のみ
        """,
    )
    parser.add_argument("--dry-run", action="store_true", help="確認のみ（ファイルを上書きしない）")
    parser.add_argument(
        "--date",
        metavar="YYYYMMDD",
        default=None,
        help="指定した日付のファイルだけ処理する（例: 20260325）。省略時は全ファイル",
    )
    parser.add_argument("--store", metavar="STORE_NAME", default=None, help="特定店舗のみ処理")
    args = parser.parse_args()

    # --date の形式チェック
    if args.date and not re.match(r"^\d{8}$", args.date):
        print(f"[ERROR] --date は YYYYMMDD 形式で指定してください（例: 20260325）")
        sys.exit(1)

    if not PROCESSED_DIR.exists():
        print(f"[ERROR] data/processed/ が見つかりません: {PROCESSED_DIR}")
        sys.exit(1)

    with open(RULES_PATH, encoding="utf-8") as f:
        rules = json.load(f)

    # 対象ファイルを収集
    target_files = []
    for p in sorted(PROCESSED_DIR.glob("*_judged.json")):
        if p.name in EXCLUDE_FILES:
            continue
        if not JUDGED_FILE_PATTERN.match(p.name):
            continue
        if args.date and not p.name.startswith(args.date):
            continue
        if args.store and args.store not in p.name:
            continue
        target_files.append(p)

    if not target_files:
        print("[WARN] 対象ファイルが見つかりませんでした。")
        sys.exit(0)

    mode = "DRY-RUN" if args.dry_run else "上書き"
    date_label = f" / 日付フィルタ: {args.date}" if args.date else ""
    store_label = f" / 店舗フィルタ: {args.store}" if args.store else ""
    print(f"[INFO] 対象ファイル: {len(target_files)} 件 ({mode}モード{date_label}{store_label})")
    print()

    total_files = 0
    total_changed = 0
    total_units = 0

    for path in target_files:
        stats = rejudge_file(path, rules, dry_run=args.dry_run)
        total_files += 1
        total_changed += stats["changed"]
        total_units += stats["total"]

        status = f"変化: {stats['changed']:3d}/{stats['total']:3d} 台"
        print(f"  {path.name:<45}  {status}")

        for ex in stats["examples"]:
            print(
                f"      台{ex['unit']:>5} [{ex['machine']}] "
                f"{ex['old_judge']} → {ex['new_judge']}  {ex['reason']}"
            )

    print()
    print(f"{'=' * 60}")
    print(f"  処理ファイル数 : {total_files}")
    print(f"  総台数         : {total_units}")
    print(f"  判定変化台数   : {total_changed}")
    print(f"{'=' * 60}")

    if args.dry_run:
        print("\n※ dry-run モードのためファイルは変更されていません。")
        print("  実際に上書きするには --dry-run を外して再実行してください。")
    else:
        print("\n再判定完了。次のコマンドで 30 日 JSON を再生成してください:")
        print("  python scripts/build_30day_store_json.py")


if __name__ == "__main__":
    main()
