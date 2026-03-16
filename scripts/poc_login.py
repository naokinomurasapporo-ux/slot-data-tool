"""
PoC Step 1: サイトセブン スマホサイトへのログイン確認

目的:
  - ログインが正常にできるか確認する
  - ブラウザの動作を目視で確認する（headless=False で実行）

実行方法:
  python scripts/poc_login.py
"""

import os
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# .env からログイン情報を読み込む
load_dotenv()

EMAIL = os.getenv("SITE7_EMAIL")
PASSWORD = os.getenv("SITE7_PASSWORD")

# サイトセブン スマホ版のログインページURL
LOGIN_URL = "https://m.site777.jp/f/A0100.do"


def run():
    if not EMAIL or not PASSWORD:
        print("[ERROR] .env に SITE7_EMAIL と SITE7_PASSWORD を設定してください")
        print("  cp .env.example .env  を実行してから入力してください")
        return

    with sync_playwright() as p:
        # headless=False にすることでブラウザが実際に開く（動作確認しやすい）
        browser = p.chromium.launch(headless=False)

        # スマホ画面として起動（iPhone 12 相当）
        # ignore_https_errors=True: HTTPS証明書エラーが出ても続行する
        #   → サイトセブンのスマホサイトで証明書エラーが出る場合に対応
        context = browser.new_context(
            viewport={"width": 390, "height": 844},
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/15.0 Mobile/15E148 Safari/604.1"
            ),
            ignore_https_errors=True,  # 証明書エラー対策
        )

        page = context.new_page()

        print(f"[INFO] ログインページを開きます: {LOGIN_URL}")
        page.goto(LOGIN_URL)
        page.wait_for_load_state("domcontentloaded")

        # スクリーンショットを保存（ログイン前の状態確認）
        page.screenshot(path="data/raw/01_before_login.png")
        print("[INFO] スクリーンショット保存: data/raw/01_before_login.png")

        # -------------------------------------------------------
        # 【次のステップで対応】ログインフォームのセレクタ（仮置き）
        #
        # ブラウザが開いたら、ページを右クリック→「検証」で
        # メールアドレスとパスワードの入力欄を確認してください。
        #
        # 例（実際のHTMLに合わせて変更が必要）:
        #   page.fill('input[name="loginId"]', EMAIL)    # メールアドレス入力欄
        #   page.fill('input[name="password"]', PASSWORD) # パスワード入力欄
        #   page.click('button[type="submit"]')           # ログインボタン
        #
        # ※ セレクタが違うとエラーになるため、まずブラウザで目視確認してください
        # -------------------------------------------------------
        print("\n[INFO] ページのタイトル:", page.title())
        print("[INFO] 現在のURL:", page.url)
        print("\n[NEXT] ブラウザが開いている間にページのHTMLを確認して")
        print("       ログインフォームの input 要素の name や id を調べてください")
        print("       確認後、ブラウザを閉じると終了します")

        # ブラウザを30秒間開いたままにする（目視確認用）
        page.wait_for_timeout(30000)

        browser.close()
        print("[INFO] 完了")


if __name__ == "__main__":
    run()
