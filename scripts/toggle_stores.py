#!/usr/bin/env python3
"""
toggle_stores.py
stores.json の enabled を対話式で切り替えるツール

使い方:
  python scripts/toggle_stores.py          # 対話モード
  python scripts/toggle_stores.py --all-on   # 全店舗 ON
  python scripts/toggle_stores.py --all-off  # 全店舗 OFF
  python scripts/toggle_stores.py --on  1 3  # 1番・3番だけ ON
  python scripts/toggle_stores.py --off 2    # 2番だけ OFF
"""

import json
import sys
from pathlib import Path

STORES_JSON = Path(__file__).parent.parent / "config" / "stores.json"


def load_stores():
    with open(STORES_JSON, encoding="utf-8") as f:
        return json.load(f)


def save_stores(stores):
    with open(STORES_JSON, "w", encoding="utf-8") as f:
        json.dump(stores, f, ensure_ascii=False, indent=2)
    print("✓ stores.json を保存しました")


def print_list(stores):
    print()
    print("  番号  状態   店舗名")
    print("  " + "─" * 50)
    for i, s in enumerate(stores, 1):
        mark = "● ON " if s["enabled"] else "○ OFF"
        print(f"  [{i:2}]  {mark}  {s['store_name']}")
    print()


def interactive_mode(stores):
    """対話モード: 番号を入力して ON/OFF を切り替える"""
    print_list(stores)
    print("操作方法:")
    print("  番号を入力   → ON/OFF を切り替える（例: 3）")
    print("  番号を複数指定 → スペース区切り（例: 1 3 5）")
    print("  all-on       → 全店舗 ON")
    print("  all-off      → 全店舗 OFF")
    print("  q            → 保存せずに終了")
    print("  s            → 保存して終了")
    print()

    changed = False
    while True:
        try:
            cmd = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n終了します（保存なし）")
            return

        if cmd == "q":
            print("変更を破棄して終了します")
            return
        elif cmd == "s":
            if changed:
                save_stores(stores)
            else:
                print("変更はありませんでした")
            return
        elif cmd == "all-on":
            for s in stores:
                s["enabled"] = True
            changed = True
            print("全店舗を ON にしました")
            print_list(stores)
        elif cmd == "all-off":
            for s in stores:
                s["enabled"] = False
            changed = True
            print("全店舗を OFF にしました")
            print_list(stores)
        elif cmd == "":
            continue
        else:
            # 番号指定
            tokens = cmd.split()
            ok = True
            for token in tokens:
                if not token.isdigit():
                    print(f"  ✗ 「{token}」は番号ではありません")
                    ok = False
                    continue
                n = int(token)
                if n < 1 or n > len(stores):
                    print(f"  ✗ 番号 {n} は範囲外です（1〜{len(stores)}）")
                    ok = False
                    continue
            if not ok:
                continue

            for token in tokens:
                n = int(token)
                store = stores[n - 1]
                store["enabled"] = not store["enabled"]
                state = "ON " if store["enabled"] else "OFF"
                print(f"  [{n:2}] {store['store_name']} → {state}")
            changed = True
            print_list(stores)


def main():
    args = sys.argv[1:]

    if not STORES_JSON.exists():
        print(f"✗ ファイルが見つかりません: {STORES_JSON}")
        sys.exit(1)

    stores = load_stores()

    # --- コマンドラインオプション ---
    if "--all-on" in args:
        for s in stores:
            s["enabled"] = True
        print_list(stores)
        save_stores(stores)
        return

    if "--all-off" in args:
        for s in stores:
            s["enabled"] = False
        print_list(stores)
        save_stores(stores)
        return

    if "--on" in args:
        idx = args.index("--on")
        nums = args[idx + 1:]
        for token in nums:
            if token.isdigit():
                n = int(token)
                if 1 <= n <= len(stores):
                    stores[n - 1]["enabled"] = True
                    print(f"  [{n}] {stores[n-1]['store_name']} → ON")
        print_list(stores)
        save_stores(stores)
        return

    if "--off" in args:
        idx = args.index("--off")
        nums = args[idx + 1:]
        for token in nums:
            if token.isdigit():
                n = int(token)
                if 1 <= n <= len(stores):
                    stores[n - 1]["enabled"] = False
                    print(f"  [{n}] {stores[n-1]['store_name']} → OFF")
        print_list(stores)
        save_stores(stores)
        return

    # --- 対話モード ---
    print("=" * 54)
    print("  店舗 ON/OFF 切り替えツール")
    print("=" * 54)
    interactive_mode(stores)


if __name__ == "__main__":
    main()
