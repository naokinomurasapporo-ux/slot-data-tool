"""
PoC Step 3: ジャグラー各機種の「大当り一覧」ページから台データを取得する

目的:
  - 保存済みセッション（config/storage_state.json）を使ってログイン状態を再利用する
  - マイリスト → マイホール から対象店舗を選んでページを開く
  - 店舗ページで「パチスロ すべて」を選択する
  - 機種一覧から「ジャグラー」を含む機種だけを抽出する
  - 各ジャグラー機種ページで「大当り一覧」ボタンを押す
  - 大当り一覧ページから台番号・BB・RB・合成確率・ゲーム数を取得する
  - 機種名ごとに結果を標準出力に表示し、JSONファイルにも保存する

前提:
  - 事前に python scripts/save_session.py を実行してセッションを保存済みであること
  - config/test_store.json の store_name に対象店舗名を設定していること

実行方法:
  python scripts/poc_scrape_one_store.py
"""

import json
import os
from playwright.sync_api import Page, sync_playwright

# サイトセブン スマホ版トップ
TOP_URL = "https://m.site777.jp/f/A0100.do"

# 機種ページなどの相対URLに付けるベースURL
BASE_URL = "https://m.site777.jp/f/"

# セッション保存先
SESSION_PATH = "config/storage_state.json"


def load_store_config(path="config/test_store.json"):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def accept_cookie_policy(page: Page) -> bool:
    """
    Cookieポリシーのポップアップを検出して「承諾する」ボタンを押す。

    Returns:
        True  : ポップアップが見つかり、承諾した
        False : ポップアップが存在しなかった（スキップ）
    """
    candidates = [
        "text=承諾する",
        "text=同意する",
        "text=すべてのCookieを承諾",
        "text=Accept",
        "[id*='cookie'] button",
        "[class*='cookie'] button",
        "[id*='consent'] button",
        "[class*='consent'] button",
    ]

    for selector in candidates:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=1500):
                btn.click()
                print(f"[INFO] Cookieポリシーを承諾しました（セレクタ: {selector}）")
                page.wait_for_load_state("domcontentloaded")
                return True
        except Exception:
            continue

    return False


def dismiss_modal(page: Page, screenshot_dir: str = "data/raw") -> bool:
    """
    広告モーダル・お知らせポップアップを検出して閉じる。

    × ボタン、閉じるボタン、close アイコンなど複数の候補で探す。
    検出時はスクリーンショットを保存する。
    見つからなければ何もせずに続行する。

    Returns:
        True  : モーダルを検出して閉じた
        False : モーダルが存在しなかった（スキップ）
    """
    import time

    close_selectors = [
        # テキスト系（× や 閉じる）
        "button:has-text('×')",
        "button:has-text('✕')",
        "button:has-text('✖')",
        "button:has-text('閉じる')",
        "button:has-text('close')",
        "button:has-text('Close')",
        "a:has-text('×')",
        "a:has-text('閉じる')",
        # class 名系
        "[class*='modal-close']",
        "[class*='modal__close']",
        "[class*='popup-close']",
        "[class*='popup__close']",
        "[class*='close-btn']",
        "[class*='closeBtn']",
        "[class*='close_btn']",
        "[class*='js-close']",
        # モーダル内の close ボタン
        "[class*='modal'] button",
        "[class*='popup'] button",
        "[class*='overlay'] button",
        # aria-label 系
        "[aria-label='close']",
        "[aria-label='Close']",
        "[aria-label='閉じる']",
        # id 系
        "[id*='close']",
        "[id*='modal'] button",
        # 確認系ボタン
        "button:has-text('OK')",
        "button:has-text('確認しました')",
        "button:has-text('後で')",
    ]

    for selector in close_selectors:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=800):
                # 検出時にスクリーンショットを保存
                try:
                    ts = int(time.time())
                    ss_path = f"{screenshot_dir}/modal_detected_{ts}.png"
                    page.screenshot(path=ss_path)
                    print(f"[INFO] モーダル検出 → スクリーンショット保存: {ss_path}")
                except Exception:
                    pass

                btn.click()
                print(f"[INFO] 広告/モーダルを閉じました（セレクタ: {selector}）")
                page.wait_for_timeout(500)
                return True
        except Exception:
            continue

    return False


def dismiss_overlays(page: Page, screenshot_dir: str = "data/raw") -> None:
    """
    Cookie同意バナーと広告モーダルの両方を閉じる共通関数。

    ページ遷移後やクリック前に呼び出すことで、
    ポップアップによる操作ブロックを防ぐ。
    """
    accept_cookie_policy(page)
    dismiss_modal(page, screenshot_dir)


def find_store_in_myhole(page: Page, store_name: str):
    """
    マイホール一覧ページから店舗を探してクリックする。

    一致ルール:
      1. 完全一致
      2. 部分一致
      3. どちらも見つからなければ候補一覧を表示

    Returns:
        True  : 店舗が見つかりクリックした
        False : 見つからなかった
    """
    links = page.locator("a").all()

    candidates = []
    for link in links:
        try:
            text = link.inner_text(timeout=500).strip()
            if text:
                candidates.append((text, link))
        except Exception:
            continue

    # 1. 完全一致
    for text, link in candidates:
        if text == store_name:
            print(f"[OK] 完全一致で店舗を発見: 「{text}」")
            link.click()
            return True

    # 2. 部分一致
    for text, link in candidates:
        if store_name in text or text in store_name:
            print(f"[OK] 部分一致で店舗を発見: 「{text}」（検索: 「{store_name}」）")
            link.click()
            return True

    # 3. 見つからなかった場合: 候補一覧を出力
    print(f"[NG] 店舗名「{store_name}」がマイホール一覧に見つかりませんでした")
    print()
    print("【マイホール一覧の候補店舗名】")
    candidate_texts = [text for text, _ in candidates if len(text) > 1]
    if candidate_texts:
        for t in candidate_texts:
            print(f"  - {t}")
    else:
        print("  （テキスト付きリンクが見つかりませんでした）")
    print()
    print("config/test_store.json の store_name を上記いずれかに合わせてください")
    return False


def click_pachislo_all(page: Page) -> bool:
    """
    店舗ページで「パチスロ すべて」ボタンをクリックする。

    サイトセブンのスマホ版では、種別選択画面に
    「パチスロ すべて」「パチンコ すべて」などのボタンが並んでいる。
    テキスト・href・クラスなど複数の方法で探す。

    セレクタ候補の考え方:
      - text=パチスロ すべて  → ボタンのテキストが完全一致するケース
      - text=パチスロ         → 「すべて」が別タグに入っているケース
      - a:has-text('パチスロ') → aタグの子孫に「パチスロ」の文字があるケース
      - [href*='slot']        → URLに "slot" が含まれるリンク
      - [href*='pachislo']    → URLに "pachislo" が含まれるリンク
      - [href*='C01']         → サイトセブン内部のパチスロ系URL

    Returns:
        True  : クリックできた
        False : 見つからなかった
    """
    selectors = [
        "text=パチスロ すべて",
        "a:has-text('パチスロ すべて')",
        "text=パチスロ　すべて",          # 全角スペースのケース
        "a:has-text('パチスロ')",         # 「すべて」が別タグの可能性
        "[href*='slot']",
        "[href*='pachislo']",
        "[href*='C01']",
        "[href*='kishu']",
    ]

    for sel in selectors:
        try:
            elem = page.locator(sel).first
            if elem.is_visible(timeout=2000):
                text_preview = elem.inner_text(timeout=500).strip()[:30]
                print(f"[INFO] 「パチスロ すべて」をクリックします（セレクタ: {sel}、テキスト: 「{text_preview}」）")
                elem.click()
                page.wait_for_load_state("domcontentloaded")
                return True
        except Exception:
            continue

    # 見つからなかった場合: ページ上の全リンクを表示してデバッグを助ける
    print("[WARN] 「パチスロ すべて」ボタンが見つかりませんでした")
    print()
    print("【ページ上のリンク一覧（デバッグ用）】")
    links = page.locator("a").all()
    for link in links:
        try:
            text = link.inner_text(timeout=300).strip()
            href = link.get_attribute("href") or ""
            if text or href:
                print(f"  テキスト: 「{text[:40]}」  href: {href[:60]}")
        except Exception:
            continue
    print()
    return False


def extract_machine_list(page: Page) -> list[dict]:
    """
    機種一覧ページから全機種名とリンクを取得する。

    サイトセブンの機種一覧は以下のような構造が多い:
      - <ul> や <li> に機種名テキストが入っている
      - <a> タグで各機種の詳細ページへリンクしている
      - クラス名に "kishu", "model", "machine" などが付いていることがある

    ここでは「リンクのうち、ページ内に複数並んでいるもの」を機種一覧と判断する。

    Returns:
        [{"index": 1, "name": "機種名", "href": "URL"}, ...]
    """
    machines = []

    # まずリンクを全部取得し、機種名らしいものを集める
    links = page.locator("a").all()
    seen = set()

    for link in links:
        try:
            text = link.inner_text(timeout=500).strip()
            href = link.get_attribute("href") or ""
            if not text or len(text) < 2:
                continue
            # ナビゲーション系リンクを除外（短すぎるものや典型的なナビ文言）
            nav_words = ["トップ", "マイリスト", "マイホール", "戻る", "メニュー", "ログアウト",
                         "ホーム", "設定", "お知らせ", "ランキング", "検索", "パチンコ", "パチスロ"]
            if text in nav_words:
                continue
            key = text
            if key not in seen:
                seen.add(key)
                machines.append({
                    "name": text,
                    "href": href,
                })
        except Exception:
            continue

    # indexを付ける（ページ上の並び順を保持）
    result = [{"index": i + 1, **m} for i, m in enumerate(machines)]
    return result


def filter_jugler(machines: list[dict]) -> list[dict]:
    """「ジャグラー」を含む機種だけを返す（並び順はそのまま）"""
    return [m for m in machines if "ジャグラー" in m["name"]]


def click_daiatari_list(page: Page) -> bool:
    """
    機種ページで「大当り一覧」ボタン／リンクを探してクリックする。

    サイトセブンのスマホ版では、機種ページに
    「大当り一覧」「ボーナス一覧」などのタブ・ボタンが並んでいる。
    クリック後、大当り一覧ページへ遷移する。

    セレクタ候補の考え方:
      - text=大当り一覧         → ボタン／リンクのテキストが完全一致
      - a:has-text('大当り一覧') → <a> タグの子孫に「大当り一覧」がある
      - text=大当り             → 「一覧」が別タグに入っているケース
      - [href*='daiatari']      → URLに "daiatari" を含むリンク
      - [href*='bonus']         → URLに "bonus" を含むリンク
      - [href*='jackpot']       → 英語表記のリンク

    Returns:
        True  : クリックできた
        False : 見つからなかった
    """
    selectors = [
        "text=大当り一覧",
        "a:has-text('大当り一覧')",
        "text=大当たり一覧",
        "a:has-text('大当たり一覧')",
        "text=ボーナス一覧",
        "a:has-text('ボーナス一覧')",
        "[href*='daiatari']",
        "[href*='bonus_list']",
        "[href*='bonuslist']",
        "[href*='jackpot']",
        # タブ構造でボタンになっているケース
        "button:has-text('大当り')",
        "button:has-text('大当たり')",
        "button:has-text('ボーナス')",
    ]

    for sel in selectors:
        try:
            elem = page.locator(sel).first
            if elem.is_visible(timeout=2000):
                text_preview = elem.inner_text(timeout=500).strip()[:30]
                print(f"    [INFO] 「大当り一覧」をクリックします（セレクタ: {sel}、テキスト: 「{text_preview}」）")
                elem.click()
                page.wait_for_load_state("domcontentloaded")
                return True
        except Exception:
            continue

    # 見つからなかった場合: ページ上の全リンク・ボタンをデバッグ表示
    print("    [WARN] 「大当り一覧」ボタン／リンクが見つかりませんでした")
    print("    【ページ上のリンク一覧（デバッグ用）】")
    links = page.locator("a").all()
    for link in links:
        try:
            text = link.inner_text(timeout=300).strip()
            href = link.get_attribute("href") or ""
            if text or href:
                print(f"      テキスト: 「{text[:40]}」  href: {href[:60]}")
        except Exception:
            continue
    return False


def extract_slot_data(page: Page) -> list[dict]:
    """
    大当り一覧ページから各台のデータを取得する。

    取得項目（見えている場合のみ）:
      - 台番号  : unit
      - BB回数  : bb
      - RB回数  : rb
      - 合成確率: combined
      - ゲーム数: games

    サイトセブンでは通常テーブル（<table>）形式か、
    リスト形式（<ul>/<li>）で並んでいる。

    アプローチ:
      1. <table> があれば、ヘッダー行でカラム名を特定して各行を読む
      2. テーブルがなければ数字のみの要素を台番号として拾う（フォールバック）

    Returns:
        [
          {"unit": "1", "bb": "5", "rb": "8", "combined": "1/120.5", "games": "400"},
          ...
        ]
        取れなかった場合は空リスト
    """
    rows = []

    # --- アプローチ1: テーブル形式 ---
    try:
        tables = page.locator("table").all()
        for table in tables:
            # ヘッダー行を取得してカラムマッピングを作る
            header_cells = table.locator("th").all()
            headers = []
            for cell in header_cells:
                try:
                    headers.append(cell.inner_text(timeout=300).strip())
                except Exception:
                    headers.append("")

            if not headers:
                # th がなければ最初の tr を見出しとして扱う
                first_row = table.locator("tr").first
                tds = first_row.locator("td").all()
                for td in tds:
                    try:
                        headers.append(td.inner_text(timeout=300).strip())
                    except Exception:
                        headers.append("")

            if not headers:
                continue

            # カラム名 → インデックス のマッピング
            col_map = {}
            UNIT_WORDS  = ["台番", "台番号", "番号", "No", "no"]
            BB_WORDS    = ["BB", "ビッグ", "BIG", "big"]
            RB_WORDS    = ["RB", "レギュラー", "REG", "reg"]
            COMB_WORDS  = ["合成", "ボーナス確率", "総合", "合算"]
            GAMES_WORDS = ["ゲーム", "G数", "回転数", "累計"]

            for i, h in enumerate(headers):
                for w in UNIT_WORDS:
                    if w in h:
                        col_map.setdefault("unit", i)
                for w in BB_WORDS:
                    if w in h:
                        col_map.setdefault("bb", i)
                for w in RB_WORDS:
                    if w in h:
                        col_map.setdefault("rb", i)
                for w in COMB_WORDS:
                    if w in h:
                        col_map.setdefault("combined", i)
                for w in GAMES_WORDS:
                    if w in h:
                        col_map.setdefault("games", i)

            # データ行（th 行を除いた tr）を読む
            data_rows = table.locator("tr").all()
            for tr in data_rows:
                tds = tr.locator("td").all()
                if not tds:
                    continue
                cells = []
                for td in tds:
                    try:
                        cells.append(td.inner_text(timeout=300).strip())
                    except Exception:
                        cells.append("")

                row = {}
                # カラムマッピングがあれば利用
                for key, idx in col_map.items():
                    if idx < len(cells):
                        row[key] = cells[idx]

                # マッピングがなくても全セルを raw_cells として保存
                if not row and cells:
                    row = {"raw_cells": cells}
                elif cells:
                    row["raw_cells"] = cells

                # 台番号が数字かどうかで有効行を判定
                unit_val = row.get("unit", "")
                if unit_val.isdigit() and 1 <= int(unit_val) <= 9999:
                    rows.append(row)
                elif not col_map and cells and cells[0].isdigit():
                    # マッピングなし・先頭セルが数字 → 台番号とみなす
                    rows.append({"unit": cells[0], "raw_cells": cells})

            if rows:
                return rows

    except Exception as e:
        print(f"    [WARN] テーブル解析エラー: {e}")

    # --- アプローチ2: フォールバック（数字のみ要素を台番号として拾う） ---
    unit_selectors = [
        "[class*='unit']",
        "[class*='dai']",
        "[class*='num']",
        "[class*='ban']",
        "li",
        "td",
        "a",
    ]
    for sel in unit_selectors:
        try:
            elems = page.locator(sel).all()
            candidates = []
            for elem in elems:
                try:
                    text = elem.inner_text(timeout=300).strip()
                    if text.isdigit() and 1 <= int(text) <= 9999:
                        candidates.append({"unit": text})
                except Exception:
                    continue
            if len(candidates) >= 2:
                return candidates
        except Exception:
            continue

    return []


def run():
    os.makedirs("data/raw", exist_ok=True)

    # --- セッションファイルの確認 ---
    if not os.path.exists(SESSION_PATH):
        print(f"[ERROR] セッションファイルが見つかりません: {SESSION_PATH}")
        print()
        print("先に以下のコマンドでセッションを保存してください:")
        print("  python scripts/save_session.py")
        return

    # --- 店舗設定の読み込み ---
    store = load_store_config()
    store_name = store["store_name"]

    if store_name == "テスト店舗名":
        print("[ERROR] config/test_store.json の store_name を実際の店舗名に変更してください")
        return

    print("=" * 50)
    print("  マイホール経由 店舗ナビゲーション + ジャグラー機種抽出")
    print("=" * 50)
    print(f"[INFO] 検索する店舗名: {store_name}")
    print(f"[INFO] セッション: {SESSION_PATH}")
    print()

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
            storage_state=SESSION_PATH,
        )

        page = context.new_page()

        # --- Step 1: トップページを開く ---
        print(f"[Step 1] トップページを開きます: {TOP_URL}")
        page.goto(TOP_URL)
        page.wait_for_load_state("domcontentloaded")
        accept_cookie_policy(page)
        page.screenshot(path="data/raw/step1_top.png")
        print(f"[INFO] スクリーンショット保存: data/raw/step1_top.png")
        print(f"[INFO] 現在のURL: {page.url}")
        print()

        # --- Step 2: 「マイリスト」タブへ移動 ---
        print("[Step 2] 「マイリスト」タブへ移動します")
        mylist_selectors = [
            "text=マイリスト",
            "a:has-text('マイリスト')",
            "[href*='mylist']",
            "[href*='my_list']",
            "[href*='B01']",
        ]
        clicked = False
        for sel in mylist_selectors:
            try:
                elem = page.locator(sel).first
                if elem.is_visible(timeout=2000):
                    elem.click()
                    page.wait_for_load_state("domcontentloaded")
                    clicked = True
                    print(f"[INFO] 「マイリスト」タブをクリックしました（セレクタ: {sel}）")
                    break
            except Exception:
                continue

        if not clicked:
            print("[WARN] 「マイリスト」タブが見つかりませんでした")
            print("       スクリーンショットを確認して、セレクタを修正してください")

        page.screenshot(path="data/raw/step2_mylist.png")
        print(f"[INFO] スクリーンショット保存: data/raw/step2_mylist.png")
        print(f"[INFO] 現在のURL: {page.url}")
        print()

        # --- Step 3: 「マイホール」一覧を開く ---
        print("[Step 3] 「マイホール」一覧を開きます")
        myhole_selectors = [
            "text=マイホール",
            "a:has-text('マイホール')",
            "[href*='myhall']",
            "[href*='my_hall']",
            "[href*='myhole']",
            "[href*='B02']",
        ]
        clicked = False
        for sel in myhole_selectors:
            try:
                elem = page.locator(sel).first
                if elem.is_visible(timeout=2000):
                    elem.click()
                    page.wait_for_load_state("domcontentloaded")
                    clicked = True
                    print(f"[INFO] 「マイホール」をクリックしました（セレクタ: {sel}）")
                    break
            except Exception:
                continue

        if not clicked:
            print("[WARN] 「マイホール」が見つかりませんでした")
            print("       スクリーンショットを確認して、セレクタを修正してください")

        page.screenshot(path="data/raw/step3_myhole_list.png")
        print(f"[INFO] スクリーンショット保存: data/raw/step3_myhole_list.png")
        print(f"[INFO] 現在のURL: {page.url}")
        print()

        # --- Step 4: 店舗名で一致する店舗を探してクリック ---
        print(f"[Step 4] 「{store_name}」を一覧から探します")
        found = find_store_in_myhole(page, store_name)

        if not found:
            page.screenshot(path="data/raw/step4_not_found.png")
            print(f"[INFO] スクリーンショット保存: data/raw/step4_not_found.png")
            print()
            print(">>> ブラウザを閉じるには Enter を押してください <<<")
            try:
                input()
            except EOFError:
                pass
            browser.close()
            return

        # --- Step 5: 遷移後の店舗ページを確認 ---
        page.wait_for_load_state("domcontentloaded")
        accept_cookie_policy(page)
        page.screenshot(path="data/raw/step5_store_page.png")
        print(f"[INFO] スクリーンショット保存: data/raw/step5_store_page.png")
        print(f"[INFO] 現在のURL: {page.url}")
        print()

        # 店舗名をページから取得
        fetched_name = None
        name_selectors = ["h1", "h2", ".hall-name", ".store-name", ".hall_name", ".storeName"]
        for sel in name_selectors:
            try:
                elem = page.locator(sel).first
                if elem.is_visible(timeout=1500):
                    fetched_name = elem.inner_text(timeout=1500).strip()
                    if fetched_name:
                        break
            except Exception:
                continue

        if fetched_name:
            print(f"[RESULT] 店舗名（ページから取得）: {fetched_name}")
        else:
            title = page.title()
            print(f"[RESULT] 店舗名（ページタイトル）: {title}")
        print()

        # --- Step 6: 「パチスロ すべて」をクリック ---
        print("[Step 6] 「パチスロ すべて」をクリックします")
        pachislo_found = click_pachislo_all(page)

        if not pachislo_found:
            page.screenshot(path="data/raw/step6_pachislo_not_found.png")
            print(f"[INFO] スクリーンショット保存: data/raw/step6_pachislo_not_found.png")
            print()
            print("「パチスロ すべて」が見つかりませんでした。")
            print("上記のリンク一覧を見て、正しいセレクタを調べてください。")
            print()
            print(">>> ブラウザを閉じるには Enter を押してください <<<")
            try:
                input()
            except EOFError:
                pass
            browser.close()
            return

        page.screenshot(path="data/raw/step6_pachislo_list.png")
        print(f"[INFO] スクリーンショット保存: data/raw/step6_pachislo_list.png")
        print(f"[INFO] 現在のURL: {page.url}")
        print()

        # --- Step 7: 機種一覧を取得 ---
        print("[Step 7] 機種一覧を取得します")
        all_machines = extract_machine_list(page)
        print(f"[INFO] 取得した機種数（ナビ除外後）: {len(all_machines)} 件")
        print()

        # 全機種をデバッグ表示（最初の20件）
        print("【取得した機種一覧（最初の20件）】")
        for m in all_machines[:20]:
            print(f"  {m['index']:3d}. {m['name']}")
        if len(all_machines) > 20:
            print(f"  ... 以降 {len(all_machines) - 20} 件省略")
        print()

        # --- Step 8: ジャグラー系を抽出 ---
        print("[Step 8] 「ジャグラー」を含む機種を抽出します")
        jugler_machines = filter_jugler(all_machines)

        print()
        print("=" * 50)
        print(f"  ジャグラー系機種一覧（{len(jugler_machines)} 件）")
        print("=" * 50)
        if jugler_machines:
            for m in jugler_machines:
                print(f"  {m['index']:3d}. {m['name']}")
                if m["href"]:
                    print(f"       └─ {m['href']}")
        else:
            print("  （ジャグラー系機種が見つかりませんでした）")
            print()
            print("  ※ 機種名の表記が異なる可能性があります。")
            print("  　 上記「機種一覧」の表示を確認してください。")
        print()

        # --- Step 9: 各機種ページで「大当り一覧」を押してデータ取得 ---
        print("[Step 9] 各ジャグラー機種ページから大当り一覧を取得します")
        print()

        jugler_results = []

        for i, machine in enumerate(jugler_machines):
            mname = machine["name"]
            href = machine["href"]
            safe_name = mname.replace("/", "_").replace(" ", "_")[:30]

            print(f"  [{i + 1}/{len(jugler_machines)}] {mname}")

            if not href:
                print("    リンクなし（スキップ）")
                jugler_results.append({**machine, "slot_data": []})
                print()
                continue

            # 相対URLの場合はベースURLを付ける
            machine_url = href if href.startswith("http") else BASE_URL + href
            print(f"    URL: {machine_url}")

            try:
                # Step 9a: 機種ページを開く
                page.goto(machine_url)
                page.wait_for_load_state("domcontentloaded")

                # 機種ページのスクリーンショットを保存（デバッグ用）
                ss_machine = f"data/raw/step9_machine_{i + 1}_{safe_name}.png"
                page.screenshot(path=ss_machine)
                print(f"    機種ページ SS: {ss_machine}")

                # Step 9b: 「大当り一覧」ボタンをクリック
                daiatari_found = click_daiatari_list(page)

                if not daiatari_found:
                    # 見つからなかった場合: 機種ページのスクリーンショットを保存
                    ss_path = f"data/raw/step9_no_daiatari_{safe_name}.png"
                    page.screenshot(path=ss_path)
                    print(f"    [WARN] 「大当り一覧」が見つかりません")
                    print(f"    スクリーンショット保存: {ss_path}")
                    print(f"    現在のURL: {page.url}")
                    jugler_results.append({**machine, "slot_data": [], "error": "大当り一覧ボタンが見つからなかった"})
                    print()
                    continue

                # 大当り一覧ページのスクリーンショットを保存（デバッグ用）
                ss_daiatari = f"data/raw/step9_daiatari_{i + 1}_{safe_name}.png"
                page.screenshot(path=ss_daiatari)
                print(f"    大当り一覧ページ SS: {ss_daiatari}")
                print(f"    大当り一覧URL: {page.url}")

                # Step 9c: データ抽出
                slot_data = extract_slot_data(page)

                if slot_data:
                    unit_count = sum(1 for r in slot_data if "unit" in r)
                    print(f"    取得件数: {unit_count} 台")
                    # 最初の3件をプレビュー表示
                    for row in slot_data[:3]:
                        preview_parts = []
                        if "unit" in row:
                            preview_parts.append(f"台{row['unit']}")
                        if "bb" in row:
                            preview_parts.append(f"BB:{row['bb']}")
                        if "rb" in row:
                            preview_parts.append(f"RB:{row['rb']}")
                        if "combined" in row:
                            preview_parts.append(f"合成:{row['combined']}")
                        if "games" in row:
                            preview_parts.append(f"G:{row['games']}")
                        if not preview_parts and "raw_cells" in row:
                            preview_parts.append(f"セル: {row['raw_cells'][:5]}")
                        print(f"      例） {' / '.join(preview_parts)}")
                    if len(slot_data) > 3:
                        print(f"      ... 他 {len(slot_data) - 3} 件")
                else:
                    # データが取れなかった場合: スクリーンショット + ページ内容を表示
                    ss_path = f"data/raw/step9_nodata_{safe_name}.png"
                    page.screenshot(path=ss_path)
                    print(f"    [WARN] データが取れませんでした")
                    print(f"    スクリーンショット保存: {ss_path}")
                    try:
                        body_text = page.locator("body").inner_text(timeout=2000)
                        preview_text = body_text[:300].replace("\n", " ")
                        print(f"    ページ内容（先頭300文字）: {preview_text}")
                    except Exception:
                        pass

                jugler_results.append({**machine, "slot_data": slot_data})

            except Exception as e:
                print(f"    [ERROR] ページ取得エラー: {e}")
                try:
                    ss_path = f"data/raw/step9_error_{i + 1}.png"
                    page.screenshot(path=ss_path)
                    print(f"    スクリーンショット保存: {ss_path}")
                except Exception:
                    pass
                jugler_results.append({**machine, "slot_data": [], "error": str(e)})

            print()

        # --- Step 9 結果まとめ ---
        print("=" * 50)
        print("  大当り一覧 取得結果")
        print("=" * 50)
        for m in jugler_results:
            data = m.get("slot_data", [])
            err = m.get("error", "")
            unit_count = sum(1 for r in data if "unit" in r)
            status = f"{unit_count}台" if data else f"取得失敗 ({err})" if err else "0件"
            print(f"  【{m['name']}】 {status}")
            if data:
                # 最初の5台を表示
                for row in data[:5]:
                    parts = []
                    for k in ("unit", "bb", "rb", "combined", "games"):
                        if k in row:
                            parts.append(f"{k}={row[k]}")
                    if not parts and "raw_cells" in row:
                        parts.append(str(row["raw_cells"][:5]))
                    print(f"    {' | '.join(parts)}")
                if len(data) > 5:
                    print(f"    ... 他 {len(data) - 5} 件")
        print()

        # --- Step 10: JSONに保存 ---
        output = {
            "store_name": store_name,
            "jugler_machines": jugler_results,
        }
        json_path = "data/raw/jugler_daiatari.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"[INFO] JSONを保存しました: {json_path}")
        print()

        page.screenshot(path="data/raw/step10_final.png")
        print(f"[INFO] スクリーンショット保存: data/raw/step10_final.png")
        print()
        print(">>> ブラウザを閉じるには Enter を押してください <<<")
        try:
            input()
        except EOFError:
            pass

        browser.close()
        print("[INFO] 完了")


if __name__ == "__main__":
    run()
