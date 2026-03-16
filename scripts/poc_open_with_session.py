"""
セッション再利用スクリプト

目的:
  - save_session.py で保存したログイン済み状態を使ってブラウザを開く
  - Cookieポリシーのポップアップを自動承諾する
  - ログイン済みかどうかを確認しやすい情報を表示する

実行方法:
  python scripts/poc_open_with_session.py

前提:
  - 事前に python scripts/save_session.py を実行してセッションを保存済みであること
"""

import os
from playwright.sync_api import Page, sync_playwright

# 開くページ（ログイン後に見られるマイページ想定）
TARGET_URL = "https://m.site777.jp/f/A0100.do"

# セッション保存先（save_session.py と同じパスを指定）
SESSION_PATH = "config/storage_state.json"

# マイページURL（ログイン済み確認用）
MYPAGE_URL = "https://m.site777.jp/f/A0200.do"


def accept_cookie_policy(page: Page) -> bool:
    """
    Cookieポリシーのポップアップを検出して「承諾する」ボタンを押す。

    Returns:
        True  : ポップアップが見つかり、承諾した
        False : ポップアップが存在しなかった（スキップ）
    """
    # よくある候補セレクタを順に試す
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


def check_login_status(page: Page) -> bool:
    """
    現在のページのテキストからログイン状態を判定する。

    サイトセブンの挙動:
      - 未ログイン : 「ようこそ、名無しさん」と表示される
      - ログイン済み: 「ようこそ、<ユーザー名>さん」と表示される（名無し以外）
      - ログインページ: 「ログイン」の文字のみ、「ログアウト」なし

    Returns:
        True  : ログイン済みと判定
        False : 未ログインまたは不明
    """
    body_text = page.inner_text("body")

    # 「ようこそ、名無しさん」→ ゲスト状態（未ログイン）
    if "ようこそ、名無し" in body_text or "ようこそ,名無し" in body_text:
        print("[NG] 未ログイン（ゲスト）状態と判定されました")
        print("     「ようこそ、名無しさん」の表示を確認")
        print("     → save_session.py を再実行してセッションを更新してください")
        return False

    # 「ようこそ、〇〇さん」かつ名無しでない → ログイン済み
    if "ようこそ" in body_text and "名無し" not in body_text:
        # ようこそ に続くユーザー名を抽出して表示
        import re
        m = re.search(r"ようこそ[、,](.+?)さん", body_text)
        username = m.group(1) if m else "（取得できず）"
        print(f"[OK] ログイン済みと判定されました（ユーザー名: {username}さん）")
        return True

    # 「ログアウト」リンクがある → ログイン済み
    if "ログアウト" in body_text:
        print("[OK] ログイン済みと判定されました（「ログアウト」の文字を確認）")
        return True

    # ログインページにリダイレクトされている
    if "ログイン" in body_text and "ログアウト" not in body_text:
        print("[NG] ログインページに遷移しています（セッション切れの可能性）")
        print("     → save_session.py を再実行してセッションを更新してください")
        return False

    print("[?] ログイン状態を自動判定できませんでした")
    print("    スクリーンショットで手動確認してください")
    return False


def run():
    print("=" * 50)
    print("  サイトセブン セッション確認スクリプト")
    print("=" * 50)
    print()

    if not os.path.exists(SESSION_PATH):
        print(f"[ERROR] セッションファイルが見つかりません: {SESSION_PATH}")
        print()
        print("先に以下のコマンドでセッションを保存してください:")
        print("  python scripts/save_session.py")
        return

    print(f"[INFO] セッションファイルを読み込みます: {SESSION_PATH}")
    print(f"[INFO] ページを開きます: {TARGET_URL}")
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
        page.goto(TARGET_URL)
        page.wait_for_load_state("domcontentloaded")

        # --- Cookie ポリシー対応 ---
        cookie_accepted = accept_cookie_policy(page)
        if not cookie_accepted:
            print("[INFO] Cookieポリシーのポップアップは検出されませんでした")

        # --- Cookie承諾後の状態でスクリーンショット ---
        os.makedirs("data/raw", exist_ok=True)
        screenshot_path = "data/raw/02_session_check.png"
        page.screenshot(path=screenshot_path)

        print(f"[INFO] ページタイトル : {page.title()}")
        print(f"[INFO] 現在のURL     : {page.url}")
        print(f"[INFO] スクリーンショット保存: {screenshot_path}")
        print()

        # --- ログイン状態の再判定 ---
        logged_in = check_login_status(page)

        # 判定が不確かな場合はマイページへ遷移して再確認
        if not logged_in:
            print()
            print(f"[INFO] マイページへ遷移してログイン状態を再確認します: {MYPAGE_URL}")
            page.goto(MYPAGE_URL)
            page.wait_for_load_state("domcontentloaded")

            # マイページでもCookieポップアップが出る場合に備える
            accept_cookie_policy(page)

            mypage_screenshot = "data/raw/02b_mypage_check.png"
            page.screenshot(path=mypage_screenshot)
            print(f"[INFO] マイページスクリーンショット保存: {mypage_screenshot}")
            print(f"[INFO] 現在のURL: {page.url}")
            print()
            check_login_status(page)

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
