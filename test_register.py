"""新規登録テスト + 確認メール送信確認"""
import time
from playwright.sync_api import sync_playwright

APP_URL = "https://cf-idea-generator.streamlit.app"

TEST_EMAIL = "tenpoohlove+test@gmail.com"
TEST_NAME  = "テスト太郎"
TEST_PHONE = "090-0000-0000"
TEST_PASS  = "Test1234!"

def test_register():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            slow_mo=400,
            executable_path="C:/Program Files/Google/Chrome/Application/chrome.exe"
        )
        page = browser.new_page(viewport={"width": 1280, "height": 900})

        print("1. アプリを開く...")
        page.goto(APP_URL, timeout=60000)
        page.wait_for_load_state("networkidle", timeout=30000)
        time.sleep(5)

        frame = page.frame_locator("iframe[title='streamlitApp']").first

        print("2. 「新規登録」タブをクリック...")
        frame.get_by_role("tab", name="新規登録").click()
        time.sleep(2)

        # 新規登録フォーム内に絞り込む（register_form という key がある）
        reg_form = frame.locator("[data-testid='stForm']").last

        print("3. フォームを入力...")
        reg_form.get_by_placeholder("山田 太郎").fill(TEST_NAME)
        reg_form.get_by_placeholder("example@email.com").fill(TEST_EMAIL)
        reg_form.get_by_placeholder("090-0000-0000").fill(TEST_PHONE)
        # パスワード欄
        reg_form.locator("input[type='password']").nth(0).fill(TEST_PASS)
        reg_form.locator("input[type='password']").nth(1).fill(TEST_PASS)
        time.sleep(1)

        print("4. チェックボックスをオン（利用規約必須）...")
        checkboxes = reg_form.get_by_role("checkbox")
        count = checkboxes.count()
        print(f"   チェックボックス数: {count}")
        for i in range(count):
            try:
                cb = checkboxes.nth(i)
                cb.scroll_into_view_if_needed(timeout=5000)
                cb.dispatch_event("click")
                print(f"   checkbox[{i}] clicked via dispatch")
            except Exception as e:
                print(f"   checkbox[{i}] skip: {e}")
        time.sleep(1)

        page.screenshot(path="C:/Users/長沼有香/AppData/Local/Temp/register_before.png")
        print("   → 入力済みスクリーンショット保存")

        print("5. 「登録する」ボタンをクリック...")
        reg_form.get_by_role("button", name="登録する").click()
        time.sleep(8)

        page.screenshot(path="C:/Users/長沼有香/AppData/Local/Temp/register_after.png")
        print("   → 登録後スクリーンショット保存")

        # 結果確認
        print("\n--- 結果 ---")
        try:
            # 成功メッセージ
            success = frame.locator("[data-testid='stSuccess']")
            if success.count() > 0 and success.first.is_visible():
                msg = success.first.inner_text(timeout=3000)
                print("[OK] " + msg)
            else:
                # エラーメッセージ
                alerts = frame.locator("[data-testid='stAlert']")
                if alerts.count() > 0:
                    for i in range(min(alerts.count(), 3)):
                        a = alerts.nth(i)
                        if a.is_visible(timeout=500):
                            msg = a.inner_text(timeout=2000)
                            print(f"[NG] アラート[{i}]: {msg[:120]}")
                else:
                    print("? 結果不明 → register_after.png を確認してください")
        except Exception as e:
            print(f"確認エラー: {e}")

        print("\n→ Gmail(tenpoohlove@gmail.com)に確認メールが届いているか確認してください")
        time.sleep(5)
        browser.close()

if __name__ == "__main__":
    test_register()
