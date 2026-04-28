"""アプリの動作テスト"""
import time
from playwright.sync_api import sync_playwright

APP_URL = "https://cf-idea-generator.streamlit.app"
TEST_EMAIL = "test@test.com"
TEST_PASSWORD = "Test1234!"
AMAZON_URL = "https://www.amazon.co.jp/dp/B00BB76JZQ"

def test():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            slow_mo=300,
            executable_path="C:/Program Files/Google/Chrome/Application/chrome.exe"
        )
        page = browser.new_page(viewport={"width": 1280, "height": 900})

        print("1. アプリを開く...")
        page.goto(APP_URL, timeout=60000)
        page.wait_for_load_state("networkidle", timeout=30000)
        time.sleep(3)

        # ログイン
        print("2. ログイン...")
        frame = page.frame_locator("iframe[title='streamlitApp']").first
        frame.get_by_role("tab", name="ログイン").click()
        time.sleep(1)
        frame.get_by_label("メールアドレス").first.fill(TEST_EMAIL)
        frame.get_by_role("textbox", name="パスワード").fill(TEST_PASSWORD)
        frame.get_by_role("button", name="ログイン").click()
        time.sleep(5)
        page.screenshot(path="C:/Users/長沼有香/AppData/Local/Temp/after_login.png")
        print("   → ログイン後スクリーンショット保存")

        # ホーム画面確認
        frame = page.frame_locator("iframe[title='streamlitApp']").first
        print("3. Amazon URL 入力...")
        frame.get_by_placeholder("https://www.amazon.co.jp/dp/XXXXXXXXXX").fill(AMAZON_URL)
        time.sleep(1)

        print("4. アイデア生成ボタンをクリック...")
        frame.get_by_role("button", name="アイデアを生成する").click()
        page.screenshot(path="C:/Users/長沼有香/AppData/Local/Temp/after_click.png")

        print("5. 結果を待つ（最大3分）...")
        try:
            # アイデアカードが表示されるまで待つ
            frame.get_by_role("button", name="CFページを生成する").first.wait_for(timeout=180000)
            page.screenshot(path="C:/Users/長沼有香/AppData/Local/Temp/result.png")
            print("OK: アイデア生成完了")
        except Exception as e:
            page.screenshot(path="C:/Users/長沼有香/AppData/Local/Temp/result.png")
            print(f"NG: タイムアウトまたはエラー: {e}")

        browser.close()

if __name__ == "__main__":
    test()
