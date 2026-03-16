"""
セッション保存スクリプト

目的:
  - ブラウザを開いて、手動でサイトセブンにログインする
  - ログイン後に Enter を押すと、ログイン済み状態を config/storage_state.json に保存する
  - 次回以降は poc_open_with_session.py でログイン不要で開ける

実行方法:
  python scripts/save_session.py

手順:
  1. このスクリプトを実行するとブラウザが開く
  2. 「有料会員ログインはこちら」→「クレジットカード決済」→ パスワード入力でログインする
  3. ログインが完了したら、ターミナルに戻って Enter を押す
  4. config/storage_state.json に保存されて完了
"""

import os
from playwright.sync_api import sync_playwright

# ログイン先URL（サイトセブン スマホ版トップ）
LOGIN_URL = "https://m.site777.jp/f/A0100.do"

# セッション保存先
SESSION_PATH = "config/storage_state.json"


def run():
    print("=" * 50)
    print("  サイトセブン セッション保存スクリプト")
    print("=" * 50)
    print()
    print(f"[INFO] ブラウザを開きます: {LOGIN_URL}")
    print()
    print("【手順】")
    print("  1. ブラウザが開いたら、手動でログインしてください")
    print("     「有料会員ログインはこちら」→「クレジットカード決済」→ パスワード入力")
    print("  2. ログインが完了したら、このターミナルに戻ってきてください")
    print("  3. Enter を押すと、ログイン状態を保存して終了します")
    print()

    with sync_playwright() as p:
        # headless=False: ブラウザを画面に表示する（手動操作が必要なため）
        browser = p.chromium.launch(headless=False)

        # スマホ画面として起動（iPhone 12 相当）
        # ignore_https_errors=True: 証明書エラーが出ても止まらないようにする
        context = browser.new_context(
            viewport={"width": 390, "height": 844},
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/15.0 Mobile/15E148 Safari/604.1"
            ),
            ignore_https_errors=True,
        )

        page = context.new_page()
        page.goto(LOGIN_URL)
        page.wait_for_load_state("domcontentloaded")

        print(f"[INFO] ページを開きました: {page.url}")
        print()
        print(">>> ブラウザでログインが完了したら、ここで Enter を押してください <<<")
        input()  # Enter を待つ

        # ログイン済み状態（Cookie・LocalStorageなど）を保存
        os.makedirs("config", exist_ok=True)
        context.storage_state(path=SESSION_PATH)

        print(f"[OK] セッションを保存しました: {SESSION_PATH}")
        print()
        print("次回からは以下のコマンドでログイン済み状態で開けます:")
        print("  python scripts/poc_open_with_session.py")
        print()

        browser.close()
        print("[INFO] 完了")


if __name__ == "__main__":
    run()
