"""
create_admin.py: 管理者アカウントを初回作成するスクリプト

使い方:
    python create_admin.py
"""

import sqlite3
import auth

auth.init_db()

EMAIL    = "tenpoohlove@gmail.com"
NAME     = "管理者"
PHONE    = ""
PASSWORD = "admin1234"

ok, token_or_err = auth.create_user(EMAIL, PHONE, NAME, PASSWORD, newsletter_consent=False)

if not ok:
    if "既に登録" in token_or_err:
        print("[SKIP] " + EMAIL + " は既に登録済みです。")
    else:
        print("[ERROR] 作成失敗: " + token_or_err)
else:
    conn = sqlite3.connect(auth.DB_PATH)
    conn.execute(
        "UPDATE users SET is_verified=1, is_admin=1, verification_token='' WHERE email=?",
        (EMAIL,),
    )
    conn.commit()
    conn.close()
    print("[OK] 管理者アカウントを作成しました")
    print("     メール    : " + EMAIL)
    print("     パスワード: " + PASSWORD)
    print("     ※ ログイン後すぐにパスワードを変更してください")
