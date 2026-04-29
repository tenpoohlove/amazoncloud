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
        time.sleep(5)

        # ログイン
        print("2. ログイン...")
        frame = page.frame_locator("iframe[title='streamlitApp']").first
        frame.get_by_role("tab", name="ログイン").click()
        time.sleep(1)
        frame.get_by_label("メールアドレス").first.fill(TEST_EMAIL)
        frame.get_by_role("textbox", name="パスワード").fill(TEST_PASSWORD)
        frame.get_by_role("button", name="ログイン").click()
        time.sleep(8)
        page.screenshot(path="C:/Users/長沼有香/AppData/Local/Temp/after_login.png")
        print("   → ログイン後スクリーンショット保存")

        # ホーム画面確認
        frame = page.frame_locator("iframe[title='streamlitApp']").first

        print("3. 類似品0件を選択（高速モード）...")
        try:
            frame.get_by_role("button", name="0件").click()
            time.sleep(2)
        except Exception as e:
            print(f"   0件ボタン見つからず（スキップ）: {e}")

        print("4. Amazon URL 入力...")
        frame.get_by_placeholder("https://www.amazon.co.jp/dp/XXXXXXXXXX").fill(AMAZON_URL)
        time.sleep(3)

        print("5. アイデア生成ボタンをクリック...")
        frame.get_by_role("button", name="アイデアを生成する").click()
        page.screenshot(path="C:/Users/長沼有香/AppData/Local/Temp/after_click.png")

        print("6. 結果を待つ（最大5分）...")
        start = time.time()
        success = False
        last_print = 0
        while time.time() - start < 300:
            elapsed = int(time.time() - start)

            # アイデアカードが表示されたか確認（「深堀する」ボタンが出ればOK）
            try:
                frame.get_by_role("button", name="深堀する").first.wait_for(timeout=10000)
                success = True
                break
            except Exception:
                pass

            # エラーメッセージが表示されたか確認
            try:
                alerts = frame.locator("[data-testid='stAlert']")
                if alerts.count() > 0:
                    for i in range(alerts.count()):
                        a = alerts.nth(i)
                        if a.is_visible(timeout=500):
                            try:
                                msg = a.inner_text(timeout=2000)
                                print(f"   エラー/警告: {msg[:120]}")
                            except Exception:
                                pass
            except Exception:
                pass

            # 30秒ごとに進捗報告
            if elapsed // 30 > last_print:
                last_print = elapsed // 30
                page.screenshot(path=f"C:/Users/長沼有香/AppData/Local/Temp/progress_{elapsed}s.png")
                print(f"   {elapsed}秒経過... 待機中")

        page.screenshot(path="C:/Users/長沼有香/AppData/Local/Temp/result.png")
        if success:
            print("OK: アイデア生成完了")
        else:
            print("NG: タイムアウトまたはエラー発生")

        time.sleep(3)
        browser.close()

if __name__ == "__main__":
    test()
