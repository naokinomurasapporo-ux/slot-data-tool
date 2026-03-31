#!/usr/bin/env python3
"""
パイプラインスクリプト: 1回の実行で「取得 → 判定 → 保存」を完結させる

処理の流れ:
  1. config/storage_state.json のセッションを使ってサイトにアクセス
  2. マイホールから config/test_store.json に設定した店舗を開く
  3. ジャグラー機種一覧を取得
  4. 大当り一覧から各台データを取得
  5. config/rules.json のルールで高設定判定（◎/○/△/×）を付与
  6. data/processed/YYYYMMDD_<店舗名>_judged.json に保存

前提:
  - 事前に python scripts/save_session.py を実行してセッションを保存済みであること
  - config/test_store.json の store_name に対象店舗名を設定していること

実行方法:
  python scripts/run_one_store_pipeline.py
"""

import json
import sys
from datetime import date
from pathlib import Path

# 同じ scripts/ フォルダにある既存モジュールを読み込む
sys.path.insert(0, str(Path(__file__).parent))

from poc_scrape_one_store import (
    TOP_URL,
    BASE_URL,
    SESSION_PATH,
    dismiss_overlays,
    click_daiatari_list,
    click_date_tab,
    click_pachislo_all,
    extract_machine_list,
    extract_slot_data,
    filter_jugler,
    find_store_in_myhole,
    load_store_config,
    verify_machine_name_on_page,
    take_pre_extract_screenshot,
)
from judge_jugler import JUDGE_ORDER, find_rule, judge_unit, judge_unit_with_debug, safe_int
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# スクレイピング
# ---------------------------------------------------------------------------

def scrape(store_name: str, target_date: str | None = None) -> list[dict]:
    """
    マイホール経由でジャグラー各機種の大当り一覧データを取得する。

    Args:
        store_name  : 取得対象の店舗名
        target_date : "YYYYMMDD" 形式の日付。大当り一覧の日付タブを確認してから
                      その日のデータを取得する。None の場合は今日の日付を使用。
                      日付タブが存在しない（サイト未更新）場合は取得を中断する。

    Returns:
        [{"index": ..., "name": "機種名", "href": "...", "slot_data": [...]}, ...]
        失敗した場合は空リスト
    """
    session_path = BASE_DIR / SESSION_PATH
    today_str = date.today().strftime("%Y%m%d")
    if not target_date:
        target_date = today_str
    need_date_tab = True  # 今日分でも必ず日付タブを確認・クリックする
    display_date = target_date
    screenshot_dir = str(BASE_DIR / "data" / "raw")

    if need_date_tab:
        print(f"  [INFO] 日付タブモード: {target_date} のデータを取得します")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 390, "height": 844},
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/15.0 Mobile/15E148 Safari/604.1"
            ),
            ignore_https_errors=True,
            storage_state=str(session_path),
        )
        page = context.new_page()

        # Step 1: トップページ
        print(f"\n[Step 1] トップページを開きます")
        page.goto(TOP_URL)
        page.wait_for_load_state("domcontentloaded")
        dismiss_overlays(page)

        # Step 2: マイリスト
        print("[Step 2] 「マイリスト」タブへ移動します")
        for sel in ["text=マイリスト", "a:has-text('マイリスト')", "[href*='mylist']", "[href*='B01']"]:
            try:
                elem = page.locator(sel).first
                if elem.is_visible(timeout=2000):
                    elem.click()
                    page.wait_for_load_state("domcontentloaded")
                    print(f"  → クリック成功（{sel}）")
                    break
            except Exception:
                continue

        # Step 3: マイホール
        print("[Step 3] 「マイホール」一覧を開きます")
        for sel in ["text=マイホール", "a:has-text('マイホール')", "[href*='myhall']", "[href*='B02']"]:
            try:
                elem = page.locator(sel).first
                if elem.is_visible(timeout=2000):
                    elem.click()
                    page.wait_for_load_state("domcontentloaded")
                    print(f"  → クリック成功（{sel}）")
                    break
            except Exception:
                continue

        # Step 4: 店舗を探してクリック
        print(f"[Step 4] 「{store_name}」を探します")
        found = find_store_in_myhole(page, store_name)
        if not found:
            browser.close()
            return []

        page.wait_for_load_state("domcontentloaded")
        dismiss_overlays(page)

        # Step 5: 「パチスロ すべて」をクリック
        print("[Step 5] 「パチスロ すべて」をクリックします")
        dismiss_overlays(page)
        if not click_pachislo_all(page):
            browser.close()
            return []

        # Step 6: 機種一覧を取得してジャグラーだけ絞り込む
        print("[Step 6] ジャグラー機種を抽出します")
        all_machines = extract_machine_list(page)
        jugler_machines = filter_jugler(all_machines)
        print(f"  → ジャグラー機種: {len(jugler_machines)} 件")
        for m in jugler_machines:
            print(f"     {m['index']:3d}. {m['name']}")

        # Step 7: 各機種の大当り一覧を取得
        print(f"\n[Step 7] 各機種の大当り一覧を取得します（{len(jugler_machines)} 機種）")
        results = []

        for i, machine in enumerate(jugler_machines):
            mname = machine["name"]
            href = machine["href"]
            print(f"\n  [{i + 1}/{len(jugler_machines)}] {mname}")

            if not href:
                print("    リンクなし → スキップ")
                results.append({**machine, "slot_data": []})
                continue

            machine_url = href if href.startswith("http") else BASE_URL + href

            try:
                page.goto(machine_url)
                page.wait_for_load_state("domcontentloaded")
                dismiss_overlays(page)

                if not click_daiatari_list(page):
                    results.append({**machine, "slot_data": [], "error": "大当り一覧ボタンが見つからなかった"})
                    continue

                # 日付タブの確認・クリック（サイト未更新の場合は中断）
                if need_date_tab:
                    if not click_date_tab(page, target_date, screenshot_dir=screenshot_dir):
                        is_today = target_date == today_str
                        reason = (
                            f"日付タブ {target_date} が見つかりません（サイトにまだ今日のデータが掲載されていない可能性があります）"
                            if is_today
                            else f"日付タブ {target_date} が見つかりませんでした"
                        )
                        print(f"    [ERROR] {reason}")
                        results.append({
                            **machine,
                            "slot_data": [],
                            "error": reason,
                        })
                        continue

                # ── 取得直前スクリーンショット（店舗名・機種名・日付を記録） ──
                take_pre_extract_screenshot(
                    page, store_name, mname, display_date, screenshot_dir
                )

                # ── 機種名の確認（日付タブクリック後に別機種へ遷移していないか） ──
                is_match, found_name = verify_machine_name_on_page(page, mname)
                if not is_match:
                    print(
                        f"    [ERROR] 機種名不一致！\n"
                        f"            想定: 「{mname}」\n"
                        f"            実際: 「{found_name}」\n"
                        f"            → データを保存せずエラー扱いにします"
                    )
                    results.append({
                        **machine,
                        "slot_data": [],
                        "error": f"機種名不一致: 想定={mname}, 実際={found_name}",
                    })
                    continue
                print(f"    [OK] 機種名確認: 「{found_name}」")

                slot_data = extract_slot_data(page)
                unit_count = sum(1 for r in slot_data if "unit" in r)
                first_units = [r.get("unit") for r in slot_data[:5] if "unit" in r]

                # ── 取得後の詳細ログ（原因調査用） ──
                print(
                    f"    [INFO] 対象日付={display_date} / 機種={mname} / "
                    f"表の行数={len(slot_data)} / 台番先頭={first_units}"
                )
                print(f"    → 有効台数: {unit_count} 台")

                # ── 台数 0 はリトライ（AJAXの描画遅延への対策） ──
                if unit_count == 0 and need_date_tab:
                    print(
                        f"    [WARNING] 台数が 0 件です。3秒待ってリトライします..."
                    )
                    page.wait_for_timeout(3000)
                    slot_data = extract_slot_data(page)
                    unit_count = sum(1 for r in slot_data if "unit" in r)
                    first_units = [r.get("unit") for r in slot_data[:5] if "unit" in r]
                    print(
                        f"    [INFO] リトライ後: 行数={len(slot_data)} / 台番先頭={first_units}"
                    )
                    if unit_count == 0:
                        print(
                            f"    [ERROR] リトライ後も 0 件です。"
                            f"日付タブ切替後のAJAXデータ読み込みに失敗しています。"
                        )
                    else:
                        print(f"    [INFO] リトライ成功: {unit_count} 台取得")
                elif unit_count == 0:
                    print(
                        f"    [WARNING] 台数が 0 件です。"
                        f"ページ構造が変わった可能性があります。"
                    )
                elif unit_count < 3:
                    print(
                        f"    [WARN] 取得台数が少なすぎます（{unit_count} 台）。"
                        f"データに問題がある可能性があります。"
                    )

                results.append({**machine, "slot_data": slot_data})

            except Exception as e:
                print(f"    [ERROR] {e}")
                results.append({**machine, "slot_data": [], "error": str(e)})

        browser.close()
        return results


# ---------------------------------------------------------------------------
# 判定付与
# ---------------------------------------------------------------------------

def attach_judges(jugler_results: list[dict], rules: dict) -> list[dict]:
    """
    各台に judge（◎/○/△/×/blank）と debug 情報を付与する。

    debug フィールドの内容:
        base_judge  : 基本判定結果（昇格前）
        rb_period   : 実測 RB 出現率（1/N のN）
        target_judge: 昇格先（昇格対象外は None）
        reg_score   : REG 昇格スコア
        comb_score  : 合算 昇格スコア
        promotion   : 昇格したか（bool）
        final_judge : 最終判定結果
        reason      : 判定理由の文字列
    """
    judged = []

    for machine in jugler_results:
        machine_name = machine["name"]
        rule = find_rule(machine_name, rules)
        rule_key = next(
            (k for k in rules if k not in ("default", "_comment", "_threshold_note") and k in machine_name),
            "default",
        )

        judged_units = []
        for unit in machine.get("slot_data", []):
            try:
                games = int(unit.get("games", 0))
                rb_count = int(unit.get("rb", 0))
                combined = int(unit.get("combined", 9999))
                judge, debug = judge_unit_with_debug(games, rb_count, combined, rule)
            except (ValueError, TypeError):
                judge = "blank"
                debug = {
                    "base_judge": "blank", "rb_period": None, "target_judge": None,
                    "reg_score": None, "comb_score": None,
                    "promotion": False, "final_judge": "blank", "reason": "パースエラー",
                }

            judged_units.append({**unit, "judge": judge, "debug": debug})

        judged.append({
            **{k: v for k, v in machine.items() if k != "slot_data"},
            "rule_used": rule_key,
            "slot_data": judged_units,
        })

    return judged


# ---------------------------------------------------------------------------
# 結果表示
# ---------------------------------------------------------------------------

def print_summary(store_name: str, judged_machines: list[dict], top_n: int = 5):
    print(f"\n{'=' * 60}")
    print(f"  高設定判定結果: {store_name}")
    print(f"{'=' * 60}\n")

    for machine in judged_machines:
        name = machine["name"]
        units = machine["slot_data"]
        rule_key = machine["rule_used"]

        # 判定の良い順 → 同判定内は合算の小さい順
        sorted_units = sorted(
            units,
            key=lambda u: (JUDGE_ORDER.get(u["judge"], 9), safe_int(u.get("combined", 999999))),
        )
        top = sorted_units[:top_n]

        print(f"【{name}】  (適用ルール: {rule_key})")
        print(f"  {'台番':>6}  {'G数':>6}  {'RB率(1/N)':>9}  {'合算(1/N)':>9}  {'基本':>4}  {'最終':>4}  判定理由")
        print(f"  {'-' * 75}")
        for u in top:
            rb_count = safe_int(u.get("rb", 0), default=0)
            games = safe_int(u.get("games", 0), default=0)
            rb_period = games / rb_count if rb_count > 0 else 9999
            dbg = u.get("debug", {})
            print(
                f"  {u.get('unit', '?'):>6}  {u.get('games', '?'):>6}  "
                f"{rb_period:>9.0f}  {u.get('combined', '?'):>9}  "
                f"{dbg.get('base_judge', '?'):>4}  {u.get('judge', '?'):>4}  "
                f"{dbg.get('reason', '')}"
            )
        print()


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

    # 店舗設定
    store = load_store_config(BASE_DIR / "config" / "test_store.json")
    store_name = store["store_name"]

    # ルール
    with open(BASE_DIR / "config" / "rules.json", encoding="utf-8") as f:
        rules = json.load(f)

    print("=" * 60)
    print(f"  パイプライン開始")
    print(f"  日付    : {today}")
    print(f"  対象店舗: {store_name}")
    print("=" * 60)

    # ── フェーズ1: スクレイピング ──────────────────────────────
    print("\n【フェーズ1】データ取得")
    jugler_results = scrape(store_name)

    if not jugler_results:
        print("[ERROR] データ取得に失敗しました。処理を終了します。")
        return

    total_units = sum(len(m.get("slot_data", [])) for m in jugler_results)
    print(f"\n取得完了: {len(jugler_results)} 機種 / {total_units} 台")

    # ── フェーズ2: 高設定判定 ─────────────────────────────────
    print("\n【フェーズ2】高設定判定")
    judged_machines = attach_judges(jugler_results, rules)
    print("判定完了")

    # ── フェーズ3: 保存 ───────────────────────────────────────
    print("\n【フェーズ3】ファイル保存")

    # 出力ファイル名: data/processed/YYYYMMDD_店舗名_judged.json
    safe_store = store_name.replace("/", "_").replace(" ", "_")[:20]
    out_path = BASE_DIR / "data" / "processed" / f"{today}_{safe_store}_judged.json"

    output = {
        "date": today,
        "store_name": store_name,
        "jugler_machines": judged_machines,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"✓ 保存完了: {out_path}")

    # ── 結果表示 ──────────────────────────────────────────────
    print_summary(store_name, judged_machines)

    print("=" * 60)
    print(f"  パイプライン完了")
    print(f"  出力ファイル: {out_path.relative_to(BASE_DIR)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
