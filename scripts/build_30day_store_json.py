#!/usr/bin/env python3
"""
30日表示用データ整形スクリプト

data/processed/ に蓄積された YYYYMMDD_店舗名_judged.json を読み込み、
店舗ごとに過去30日分を横持ち（日付×台番号）にまとめた JSON を生成する。

出力先:
  data/processed/30d_<店舗名>.json   （ローカル保存）
  docs/data/30d_<店舗名>.json        （GitHub Pages 用）
  docs/data/stores.json              （店舗一覧マニフェスト）

出力構造:
  {
    "store_name": "アミューズ千葉店",
    "generated_at": "20260316",
    "dates": ["20260301", "20260302", ...],   // 古い順
    "machines": [
      {
        "name": "マイジャグラーV",
        "rule_used": "マイジャグラー",
        "units": [
          {
            "unit": "842",
            "days": {
              "20260301": {"judge": "×", "combined": "147", "rb": "14", "bb": "19", "games": "4864"},
              "20260316": {"judge": "◎", "combined": "110", "rb": "20", "bb": "22", "games": "5200"}
            }
          },
          ...
        ]
      },
      ...
    ]
  }

実行方法:
  python scripts/build_30day_store_json.py
  python scripts/build_30day_store_json.py --store アミューズ千葉店
  python scripts/build_30day_store_json.py --days 7
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"
DOCS_DATA_DIR = BASE_DIR / "docs" / "data"

# ファイル名パターン: YYYYMMDD_店舗名_judged.json
JUDGED_FILE_PATTERN = re.compile(r"^(\d{8})_.+_judged\.json$")

# jugler_daiatari_judged.json など旧形式のファイルは除外
EXCLUDE_FILES = {"jugler_daiatari_judged.json"}


# ---------------------------------------------------------------------------
# ファイル収集
# ---------------------------------------------------------------------------

def collect_judged_files(processed_dir: Path) -> list[tuple[str, Path]]:
    """
    YYYYMMDD_*_judged.json を全件収集して (date_str, path) のリストで返す。
    古い順にソート済み。
    """
    results = []
    for p in processed_dir.glob("*_judged.json"):
        if p.name in EXCLUDE_FILES:
            continue
        m = JUDGED_FILE_PATTERN.match(p.name)
        if m:
            results.append((m.group(1), p))
    results.sort(key=lambda x: x[0])
    return results


# ---------------------------------------------------------------------------
# データ読み込み・グループ化
# ---------------------------------------------------------------------------

def load_and_group(
    judged_files: list[tuple[str, Path]], days: int = 30
) -> dict[str, dict[str, dict]]:
    """
    各ファイルを読み込み、store_name → {date_str → judged_data} の辞書を返す。
    各店舗について新しい順に days 件だけ保持する。
    """
    # store_name → {date_str: raw_data}
    store_map: dict[str, dict[str, dict]] = defaultdict(dict)

    for date_str, path in judged_files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[WARN] 読み込みスキップ: {path.name} ({e})")
            continue

        store_name = data.get("store_name", "")
        if not store_name:
            print(f"[WARN] store_name が空のためスキップ: {path.name}")
            continue

        # 同じ日付で複数ファイルがある場合は後勝ち（新しいファイルで上書き）
        store_map[store_name][date_str] = data

    # 各店舗を新しい順に days 件だけに絞る
    trimmed: dict[str, dict[str, dict]] = {}
    for store_name, date_map in store_map.items():
        sorted_dates = sorted(date_map.keys(), reverse=True)[:days]
        trimmed[store_name] = {d: date_map[d] for d in sorted(sorted_dates)}

    return trimmed


# ---------------------------------------------------------------------------
# 横持ちデータの構築
# ---------------------------------------------------------------------------

def build_store_json(store_name: str, date_map: dict[str, dict]) -> dict:
    """
    1店舗分のデータを横持ち構造に変換する。

    引数:
        store_name : 店舗名
        date_map   : {date_str: judged_data} の辞書（古い順ソート済み）

    返値:
        Web表示用の横持ち辞書
    """
    dates = sorted(date_map.keys())  # 古い順

    # 機種名 → {unit_id → {date_str → day_data}} を積み上げる
    # 機種の並び順を保持するために OrderedDict 的に処理
    machine_order: list[str] = []
    machine_seen: set[str] = set()

    # machine_name → rule_used（最新日付のものを採用）
    machine_rule: dict[str, str] = {}

    # machine_name → unit_id → date_str → day_data
    machine_unit_days: dict[str, dict[str, dict[str, dict]]] = defaultdict(
        lambda: defaultdict(dict)
    )

    for date_str in dates:
        data = date_map[date_str]
        for machine in data.get("jugler_machines", []):
            mname = machine.get("name", "")
            if not mname:
                continue

            # 機種の登場順を記録
            if mname not in machine_seen:
                machine_seen.add(mname)
                machine_order.append(mname)

            # 最新日付のルールで上書き（dates は古い順なので後勝ち）
            machine_rule[mname] = machine.get("rule_used", "default")

            for unit in machine.get("slot_data", []):
                unit_id = unit.get("unit", "")
                if not unit_id:
                    continue

                machine_unit_days[mname][unit_id][date_str] = {
                    "judge":    unit.get("judge", "blank"),
                    "combined": unit.get("combined", "--"),
                    "rb":       unit.get("rb", "--"),
                    "bb":       unit.get("bb", "--"),
                    "games":    unit.get("games", "--"),
                }

    # 機種ごとに units リストを組み立てる
    machines_output = []
    for mname in machine_order:
        unit_days = machine_unit_days[mname]

        # 台番号を数値順にソート（数字でない場合は文字列順）
        def unit_sort_key(uid: str) -> tuple:
            return (0, int(uid)) if uid.isdigit() else (1, uid)

        units_output = [
            {
                "unit": uid,
                "days": unit_days[uid],
            }
            for uid in sorted(unit_days.keys(), key=unit_sort_key)
        ]

        machines_output.append({
            "name":     mname,
            "rule_used": machine_rule.get(mname, "default"),
            "units":    units_output,
        })

    return {
        "store_name":   store_name,
        "generated_at": date.today().strftime("%Y%m%d"),
        "dates":        dates,
        "machines":     machines_output,
    }


# ---------------------------------------------------------------------------
# 保存
# ---------------------------------------------------------------------------

def save_store_json(store_name: str, store_json: dict, processed_dir: Path) -> Path:
    """
    30d_<店舗名>.json として保存し、パスを返す。
    """
    safe_name = store_name.replace("/", "_").replace(" ", "_").replace("　", "_")
    out_path = processed_dir / f"30d_{safe_name}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(store_json, f, ensure_ascii=False, indent=2)
    return out_path


# ---------------------------------------------------------------------------
# docs/data/ への出力（GitHub Pages 用）
# ---------------------------------------------------------------------------

def save_docs_outputs(all_store_jsons: dict[str, dict], docs_data_dir: Path) -> None:
    """
    docs/data/ に 30d_*.json と stores.json を保存する（GitHub Pages 用）。

    stores.json は index.html が店舗一覧を取得するためのマニフェスト。
    """
    docs_data_dir.mkdir(parents=True, exist_ok=True)

    stores_list = []
    for store_name, store_json in all_store_jsons.items():
        safe_name = store_name.replace("/", "_").replace(" ", "_").replace("\u3000", "_")
        file_name = f"30d_{safe_name}.json"
        out_path = docs_data_dir / file_name
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(store_json, f, ensure_ascii=False, indent=2)
        stores_list.append({
            "store_name": store_name,
            "file": file_name,
            "date_count": len(store_json["dates"]),
            "latest_date": store_json["dates"][-1] if store_json["dates"] else "",
        })

    manifest = {
        "generated_at": date.today().strftime("%Y%m%d"),
        "stores": stores_list,
    }
    with open(docs_data_dir / "stores.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"[INFO] docs/data/ → {len(stores_list)} 店舗 + stores.json を保存しました")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="judged.json を読み込んで30日横持ちデータを生成する"
    )
    parser.add_argument(
        "--store",
        metavar="STORE_NAME",
        default=None,
        help="特定の店舗だけ処理する（省略時は全店舗）",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        metavar="N",
        help="集計する日数（デフォルト: 30）",
    )
    args = parser.parse_args()

    if not PROCESSED_DIR.exists():
        print(f"[ERROR] data/processed/ が見つかりません: {PROCESSED_DIR}")
        sys.exit(1)

    # ファイル収集
    judged_files = collect_judged_files(PROCESSED_DIR)
    if not judged_files:
        print("[ERROR] 対象ファイルが見つかりませんでした。")
        print(f"  {PROCESSED_DIR} に YYYYMMDD_店舗名_judged.json が必要です。")
        sys.exit(1)

    print(f"[INFO] 対象ファイル数: {len(judged_files)} 件")

    # 読み込み・グループ化
    store_data = load_and_group(judged_files, days=args.days)

    # --store 指定時は絞り込み
    if args.store:
        matched = {k: v for k, v in store_data.items() if args.store in k}
        if not matched:
            print(f"[ERROR] 店舗名「{args.store}」にマッチする店舗が見つかりません。")
            print(f"  利用可能な店舗: {', '.join(store_data.keys())}")
            sys.exit(1)
        store_data = matched

    print(f"[INFO] 処理対象店舗: {len(store_data)} 店舗")
    print()

    # 各店舗を変換して保存
    all_store_jsons: dict[str, dict] = {}
    for store_name, date_map in store_data.items():
        dates = sorted(date_map.keys())
        print(f"  【{store_name}】")
        print(f"    日付範囲: {dates[0]} 〜 {dates[-1]}  ({len(dates)} 日分)")

        store_json = build_store_json(store_name, date_map)
        all_store_jsons[store_name] = store_json

        machine_count = len(store_json["machines"])
        unit_count = sum(len(m["units"]) for m in store_json["machines"])
        print(f"    機種数: {machine_count}  /  台数: {unit_count}")

        out_path = save_store_json(store_name, store_json, PROCESSED_DIR)
        print(f"    保存先(local): {out_path.relative_to(BASE_DIR)}")
        print()

    # docs/data/ にも出力（GitHub Pages 用）
    save_docs_outputs(all_store_jsons, DOCS_DATA_DIR)
    print("\n完了")


if __name__ == "__main__":
    main()
