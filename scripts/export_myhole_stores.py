#!/usr/bin/env python3
"""
マイホール一覧から店舗名を自動取得して config/stores.json に書き出すスクリプト

処理の流れ:
  1. config/storage_state.json のセッションを使ってサイトにアクセス
  2. トップ → マイリスト → マイホール一覧 へ移動
  3. マイホールに登録されている店舗名をすべて取得
  4. config/stores.json に保存（enabled=false、sort_order は取得順）

前提:
  - 事前に python scripts/save_session.py を実行してセッションを保存済みであること

実行方法:
  python scripts/export_myhole_stores.py
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from poc_scrape_one_store import TOP_URL, SESSION_PATH, accept_cookie_policy
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).parent.parent

# ──────────────────────────────────────────────
# 都道府県名（完全一致で除外）
# ──────────────────────────────────────────────
PREFS = {
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
}

# ──────────────────────────────────────────────
# ナビゲーション系テキスト（完全一致で除外）
# ──────────────────────────────────────────────
NAV_WORDS = {
    "トップ", "マイリスト", "マイホール", "戻る", "メニュー", "ログアウト",
    "ホーム", "設定", "お知らせ", "ランキング", "検索", "パチンコ", "パチスロ",
    "ログイン", "新規登録", "TOP", "MENU", "マイページ", "会員登録",
    "プライバシーポリシー", "利用規約", "お問い合わせ", "ヘルプ",
    "シェア", "ツイートする", "フォロー", "閲覧履歴", "マイプロフィール",
    "ホール検索", "パチンコ機種情報", "パチスロ機種情報", "みんなの遊技広場",
    "投稿した台データ一覧", "入会について", "退会について", "FAQ",
    "新台導入カレンダー", "総合案内",
}

# ──────────────────────────────────────────────
# テキストパターン除外（部分一致・正規表現）
# ──────────────────────────────────────────────
EXCLUDE_TEXT_PATTERNS = [
    r"LV:\d+",                     # ユーザー投稿 例: けろぴん(LV:11)...
    r"投稿：\d+件",                 # 機種情報 例: ゴーゴージャグラー３投稿：116件
    r"ホール導入開始日：",           # 機種情報
    r"G[\d,]+出[\d,]+",            # 台データ 例: G9,323出7,743
    r"◆",                          # お知らせ記号
    r"▼",                          # ボタン系
    r"お知らせ$",
    r"^次の\d+件",
    r"一覧はこちら$",
    r"^無料公開ホール",
    r"コースを退会する",
    r"メールアドレス変更",
    r"メールアドレス解除",
    r"アンケートに答える",
    r"よくある質問",
    r"推奨QRアプリ",
    r"Tweets by",
    r"ピックアップ",
    r"取材スケジュール",
    r"機種情報",
    r"遊技広場",
]

# ──────────────────────────────────────────────
# URLパターン除外（hrefに含まれる文字列）
# ──────────────────────────────────────────────
EXCLUDE_URL_PATTERNS = [
    r"pref",       # 都道府県検索
    r"ken=",       # 都道府県コード
    r"kishu",      # 機種
    r"machine",    # 機種
    r"twitter",    # Twitter
    r"facebook",
    r"line\.me",
    r"javascript:", # JSリンク
    r"^#",         # アンカーのみ
]

# ──────────────────────────────────────────────
# マイホールセクションを探すCSSセレクタ（上から順に試す）
# ──────────────────────────────────────────────
MYHOLE_SECTION_SELECTORS = [
    # セクションタグで囲まれている場合
    "section:has(:text('マイホール')) a",
    # h2/h3見出し付近のリスト
    "h2:has-text('マイホール') + ul a",
    "h2:has-text('マイホール') ~ ul a",
    "h3:has-text('マイホール') + ul a",
    "h3:has-text('マイホール') ~ ul a",
    # divで囲まれている場合
    "div:has(> :text-is('マイホール')) a",
    "div.myhole a",
    "div#myhole a",
    "[data-type='myhole'] a",
]


def navigate_to_myhole_list(page) -> bool:
    """
    トップページからマイホール一覧ページへ移動する。

    Returns:
        True  : マイホール一覧へ移動できた
        False : 移動できなかった
    """
    # Step 1: トップページ
    print("[Step 1] トップページを開きます")
    page.goto(TOP_URL)
    page.wait_for_load_state("domcontentloaded")
    accept_cookie_policy(page)

    # Step 2: マイリスト
    print("[Step 2] 「マイリスト」へ移動します")
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
    else:
        print("  [WARN] 「マイリスト」が見つかりませんでした")

    # Step 3: マイホール
    print("[Step 3] 「マイホール」一覧を開きます")
    for sel in ["text=マイホール", "a:has-text('マイホール')", "[href*='myhall']", "[href*='myhole']", "[href*='B02']"]:
        try:
            elem = page.locator(sel).first
            if elem.is_visible(timeout=2000):
                elem.click()
                page.wait_for_load_state("domcontentloaded")
                print(f"  → クリック成功（{sel}）")
                return True
        except Exception:
            continue

    print("  [WARN] 「マイホール」が見つかりませんでした")
    return False


def save_debug_html(page, output_dir: Path) -> Path:
    """
    デバッグ用にページのHTMLを保存する。
    返り値: 保存先パス
    """
    html_path = output_dir / "export_myhole_list.html"
    html_content = page.content()
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    return html_path


def is_excluded_by_text(text: str) -> tuple[bool, str]:
    """
    テキストが除外対象かどうか判定する。

    Returns:
        (True, 理由) / (False, "")
    """
    if text in PREFS:
        return True, "都道府県名"
    if text in NAV_WORDS:
        return True, "ナビゲーション"
    for pat in EXCLUDE_TEXT_PATTERNS:
        if re.search(pat, text):
            return True, f"除外パターン一致: {pat}"
    return False, ""


def is_excluded_by_url(href: str) -> tuple[bool, str]:
    """
    URLが除外対象かどうか判定する。

    Returns:
        (True, 理由) / (False, "")
    """
    if not href:
        return True, "hrefなし"
    for pat in EXCLUDE_URL_PATTERNS:
        if re.search(pat, href):
            return True, f"URLパターン: {pat}"
    return False, ""


def extract_stores_from_section(page) -> list[dict] | None:
    """
    マイホールセクションのCSSセレクタを試して、そのエリア内のリンクだけを取得する。

    Returns:
        リンク情報のリスト [{"text": ..., "href": ...}, ...] または None（セクション特定できず）
    """
    for sel in MYHOLE_SECTION_SELECTORS:
        try:
            links = page.locator(sel).all()
            if len(links) >= 2:
                results = []
                for link in links:
                    try:
                        text = link.inner_text(timeout=500).strip()
                        href = link.get_attribute("href") or ""
                        if text:
                            results.append({"text": text, "href": href})
                    except Exception:
                        continue
                if results:
                    print(f"  → セクションセレクタ成功: {sel}（{len(results)} 件）")
                    return results
        except Exception:
            continue
    return None


def extract_all_links(page) -> list[dict]:
    """
    ページ全体の <a> タグをすべて取得する（フォールバック用）。

    Returns:
        リンク情報のリスト [{"text": ..., "href": ...}, ...]
    """
    links = page.locator("a").all()
    results = []
    for link in links:
        try:
            text = link.inner_text(timeout=500).strip()
            href = link.get_attribute("href") or ""
            if text:
                results.append({"text": text, "href": href})
        except Exception:
            continue
    return results


def filter_store_links(raw_links: list[dict]) -> tuple[list[str], list[dict]]:
    """
    リンクリストから実際のホール名だけを抽出し、除外候補も返す。

    Returns:
        (ホール名リスト, 除外されたリンク情報リスト)
    """
    store_names = []
    excluded = []
    seen = set()

    for item in raw_links:
        text = item["text"]
        href = item.get("href", "")

        # 空・短すぎるものを除外
        if not text or len(text) <= 2:
            continue

        # 複数行で長すぎる（投稿テキストなど）
        if "\n" in text and len(text.split("\n")) > 3:
            excluded.append({**item, "reason": "複数行テキスト（投稿等）"})
            continue

        # テキストで除外
        is_bad_text, reason_text = is_excluded_by_text(text)
        if is_bad_text:
            excluded.append({**item, "reason": reason_text})
            continue

        # URLで除外
        is_bad_url, reason_url = is_excluded_by_url(href)
        if is_bad_url:
            excluded.append({**item, "reason": reason_url})
            continue

        # 重複除外
        if text in seen:
            continue

        seen.add(text)
        store_names.append(text)

    return store_names, excluded


def save_stores_json(store_names: list[str], output_path: Path) -> None:
    """
    店舗名リストを stores.json に保存する。

    形式:
      [
        {"store_name": "店舗名", "enabled": false, "sort_order": 1},
        ...
      ]
    """
    stores = [
        {
            "store_name": name,
            "enabled": False,
            "sort_order": i + 1,
        }
        for i, name in enumerate(store_names)
    ]

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(stores, f, ensure_ascii=False, indent=2)


def main():
    session_path = BASE_DIR / SESSION_PATH

    # セッション確認
    if not session_path.exists():
        print(f"[ERROR] セッションファイルが見つかりません: {session_path}")
        print("\n先に以下を実行してください:")
        print("  python scripts/save_session.py")
        sys.exit(1)

    output_path = BASE_DIR / "config" / "stores.json"
    debug_dir = BASE_DIR / "data" / "raw"
    debug_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("  マイホール一覧 → stores.json 書き出しツール")
    print("=" * 60)
    print(f"  セッション : {session_path}")
    print(f"  出力先     : {output_path}")
    print("=" * 60)

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

        # マイホール一覧ページへ移動
        ok = navigate_to_myhole_list(page)

        # デバッグ用スクリーンショット
        ss_path = debug_dir / "export_myhole_list.png"
        page.screenshot(path=str(ss_path))
        print(f"\n[INFO] スクリーンショット保存: {ss_path}")
        print(f"[INFO] 現在のURL: {page.url}")

        if not ok:
            print("\n[ERROR] マイホール一覧へ移動できませんでした。")
            print("スクリーンショット（data/raw/export_myhole_list.png）を確認してください。")
            browser.close()
            sys.exit(1)

        # デバッグ用HTML保存
        html_path = save_debug_html(page, debug_dir)
        print(f"[INFO] HTML保存: {html_path}")

        # ── Step 4: リンク取得 ──────────────────────────────────
        print("\n[Step 4] マイホール一覧のリンクを取得します")

        # まずセクションセレクタで絞り込みを試みる
        print("  [試行1] マイホールセクションのCSSセレクタで絞り込みを試みます...")
        section_links = extract_stores_from_section(page)

        if section_links is not None:
            print(f"  → セクション内リンク {len(section_links)} 件を取得しました")
            raw_links = section_links
            extraction_mode = "セクション限定"
        else:
            print("  → セクションが特定できませんでした。ページ全体のリンクを取得します")
            raw_links = extract_all_links(page)
            print(f"  → ページ全体 {len(raw_links)} 件のリンクを取得しました")
            extraction_mode = "ページ全体（フィルタ済み）"

        browser.close()

    # ── Step 5: フィルタリング ──────────────────────────────────
    print(f"\n[Step 5] フィルタリング中... （取得モード: {extraction_mode}）")
    store_names, excluded = filter_store_links(raw_links)

    # ── 抽出候補 出力 ──────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"  ✅ 抽出候補（ホール名と思われるもの）: {len(store_names)} 件")
    print(f"{'=' * 60}")
    for i, name in enumerate(store_names, 1):
        print(f"  {i:3d}. {name}")

    # ── 除外候補 出力 ──────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"  ❌ 除外候補（ホール名でないと判断したもの）: {len(excluded)} 件")
    print(f"{'=' * 60}")
    for item in excluded:
        text_preview = item["text"][:40].replace("\n", " ")
        print(f"  [{item['reason']}] {text_preview}")

    # ── 確認 ──────────────────────────────────────────────────
    if not store_names:
        print("\n[ERROR] ホール名が1件も取得できませんでした。")
        print("以下を確認してください:")
        print(f"  スクリーンショット : {ss_path}")
        print(f"  HTML断片          : {html_path}")
        print("\nヒント: マイホールに店舗が登録されていない場合は、")
        print("先にサイトセブンのアプリ／サイトでマイホール登録を行ってください。")
        sys.exit(1)

    # ── stores.json に保存 ─────────────────────────────────────
    save_stores_json(store_names, output_path)

    print(f"\n✓ 保存完了: {output_path}")
    print(f"  {len(store_names)} 件のホールを保存しました（すべて enabled=false）")
    print()
    print("━" * 60)
    print("⚠️  もし抽出候補に余計なものが混じっていた場合:")
    print("  data/raw/export_myhole_list.html をブラウザで開くと")
    print("  ページ構造を確認できます。")
    print("  確認後、このスクリプトの EXCLUDE_TEXT_PATTERNS または")
    print("  EXCLUDE_URL_PATTERNS にパターンを追加してください。")
    print("━" * 60)
    print()
    print("次のステップ:")
    print("  config/stores.json を開いて、データ取得したい店舗の")
    print("  enabled を false → true に変更してください。")
    print()
    print("  変更後に以下を実行するとデータ取得が始まります:")
    print("  python scripts/run_all_stores_pipeline.py")


if __name__ == "__main__":
    main()
