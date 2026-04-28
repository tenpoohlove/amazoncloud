# 引き継ぎファイル — 2026-04-28 セッション終了時点

このファイルを読めば、前回の会話を参照しなくても次のセッションで即座に続きから始められる。

---

## このプロジェクトの概要

StreamlitベースのクラファンAIアイデア生成ツール（p1）。
Amazonレビューを収集・分析し、クラファン向けの商品アイデアとCFページ草稿を生成する。
**本番URL**: https://cf-idea-generator.streamlit.app

主要ファイル:
- `main.py` — Streamlit UI 本体
- `analyzer.py` — Gemini API 呼び出し・アイデア生成・CF生成
- `scraper.py` — Amazon スクレイパー
- `auth.py` — PostgreSQL ユーザー管理・セッション・暗号化設定

---

## 今日（2026-04-28）やったこと

### 確認メール送信: Resend → Gmail SMTP に切り替え（完了）

**経緯:**
- Resend（onboarding@resend.dev）は「自分のメールアドレスにしか送れない」制限あり
- `tenpoohlove+test@gmail.com` への送信が `550 You can only send testing emails to your own email address` エラーで失敗
- Gmail SMTP（smtp.gmail.com:587）+ アプリパスワードに切り替えた

**アプリパスワード:** `qcffyhlfsecwdzgk`（16桁、Fernet暗号化してNeon DBに保存済み）

### auth.py 修正（コミット: 424abb5）
- `_get_smtp_config()` の優先順位を「env var優先」から「DB優先→env varフォールバック」に変更
- 管理パネルで設定変更した内容が確実に反映される

### main.py 修正（今日最後のコミット）
- BASE_URL の取得を2箇所修正（line 371: パスワードリセット、line 423: 新規登録）
- `os.getenv("BASE_URL", "http://localhost:8501")` → `os.getenv("BASE_URL") or auth.get_setting("base_url") or "http://localhost:8501"`
- **理由**: アプリ再起動直後にenv varが読めず localhost のURLがメールに入ってしまう問題を防ぐ

### Neon DB に設定を追加（手動実行済み）
| キー | 値 |
|---|---|
| smtp_host | smtp.gmail.com |
| smtp_port | 587 |
| smtp_user | tenpoohlove@gmail.com |
| smtp_pass | Fernet暗号化済み（Gmail App Password） |
| smtp_from | tenpoohlove@gmail.com |
| base_url | https://cf-idea-generator.streamlit.app |

### Streamlit Cloud Secrets 更新（完了）
```toml
DATABASE_URL = "postgresql://neondb_owner:npg_7HjwqpfDNPM5@ep-misty-star-amhjte2o-pooler.c-5.us-east-1.aws.neon.tech/neondb?sslmode=require"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = "587"
SMTP_USER = "tenpoohlove@gmail.com"
SMTP_PASS = "qcffyhlfsecwdzgk"
SMTP_FROM = "tenpoohlove@gmail.com"
BASE_URL = "https://cf-idea-generator.streamlit.app"
```

### 確認メール動作確認（ほぼ完了）
- `tenpoohlove+test@gmail.com` で新規登録 → Gmail に確認メール着信を確認
- メール内リンクをクリック → 「サーバーに接続できなかった」エラー（localhost URLが入っていた）
- 原因: DB への base_url 追加前に登録したため、古いリンクだった
- 暫定対応: テストユーザー（id=13）を手動で `is_verified=1` に更新済み
- main.py の修正（今日のコミット）により今後は正しいURLが入るはず

---

## 次セッションでやること（優先度順）

### 【最重要】確認メール E2E テスト
```bash
# まず DB からテストユーザーを削除
# Neon コンソール (https://console.neon.tech) SQL Editor で実行:
# DELETE FROM users WHERE email='tenpoohlove+test@gmail.com';

# テスト実行
python -X utf8 test_register.py
```
- `tenpoohlove@gmail.com` の受信箱に確認メールが届くか確認
- メール内リンクをクリック → 正しいURL（https://cf-idea-generator.streamlit.app?token=...）でアプリが開くか確認
- ログインできれば完全に動作確認完了

### 【次】Streamlit Cloud Secrets のインデント確認
https://share.streamlit.io → アプリ選択 → Settings → Secrets
SMTP_PORT の前に余分なスペースが入っている可能性がある。
すべてのキーが同じインデントレベル（先頭スペースなし）になっているか確認・修正。

### 【低優先】test@test.com テストユーザーの処理
DB に `test@test.com`（パスワード: Test1234!）が残っている。
本番前に削除するか開発用として残すか判断：
```sql
DELETE FROM users WHERE email='test@test.com';
```

### 【低優先】クライアントへの引き渡し準備
- アプリURLを渡して新規登録してもらう
- 確認メールが届いたら認証してもらう
- admin 権限を付与する（admin_settingsテーブルで `is_admin=1`）

---

## 現在の重要な設定・認証情報

| 何 | 値/場所 |
|---|---|
| 本番アプリURL | https://cf-idea-generator.streamlit.app |
| Neon DB接続 | `postgresql://neondb_owner:npg_7HjwqpfDNPM5@ep-misty-star-amhjte2o-pooler.c-5.us-east-1.aws.neon.tech/neondb?sslmode=require` |
| fernet_key | `uHea17CIUdN4bxh0WvfIyptoUxmOXQUCozoUforQ` |
| Gmail App Password | `qcffyhlfsecwdzgk`（2段階認証は維持すること） |
| テスト管理者アカウント | tenpoohlove@gmail.com / パスワード: Test1234!（DBで直接bcrypt更新済み） |
| Streamlit Cloud | https://share.streamlit.io |

---

## 既知の問題・注意事項

1. **Resend は使わない**: Gmailに切り替え済み。env var に SMTP_HOST=smtp.sendgrid.net等が入るとそちらが使われる（DB優先だが env var も読む）
2. **Googleアプリパスワード**: `qcffyhlfsecwdzgk`。Googleアカウントの2段階認証を無効にすると無効になる
3. **テストアドレス**: `tenpoohlove+test@gmail.com` は `tenpoohlove@gmail.com` の受信箱に届く（Gmailの＋エイリアス仕様）
4. **test@test.com**: パスワード Test1234! でログイン可能な開発用アカウント（DB削除まで残存）

---

## 現在の git 状態（セッション終了時点）

ブランチ: main
- `main.py`: BASE_URL 修正（line 371, 423）
- `auth.py`: SMTP設定DB優先化（コミット 424abb5）
- `test_register.py`: E2E登録テスト（新規追跡ファイル）
- `HANDOFF.md`: このファイル

---

## 認証メール送信の全パイプライン

```
新規登録フォーム送信
↓
auth.py: create_user() → tokenを生成
↓
main.py (line 423):
  base_url = os.getenv("BASE_URL") or auth.get_setting("base_url") or "http://localhost:8501"
  → "https://cf-idea-generator.streamlit.app"
↓
auth.py: send_verification_email()
  → smtp.gmail.com:587 (STARTTLS)
  → SMTP_USER: tenpoohlove@gmail.com
  → SMTP_PASS: qcffyhlfsecwdzgk
↓
Gmail受信（tenpoohlove+test@gmail.com → tenpoohloveの受信箱）
↓
リンク: https://cf-idea-generator.streamlit.app?token=XXX&action=verify
↓
アプリが token 検証 → is_verified=1 → ログイン可能
```
