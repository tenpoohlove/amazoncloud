# 引き継ぎファイル — 2026-04-29 セッション終了時点

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

## 今日（2026-04-29）やったこと

### 1. ページリロード後のセッション維持（Cookie）— 完了・本番動作確認済み

**問題**: F5 でリロードするとログアウトされる。`st.context.cookies` がStreamlit Cloudのリバースプロキシで読めない。

**解決策**: `extra-streamlit-components` の `CookieManager` を採用。
- JS コンポーネントが `document.cookie` を読んで Python に渡す
- Cookie 書き込みは `st.components.v1.html()` でインライン JS を使用
- ログアウト後・Cookie書き込み後は `window.top.location.reload()` でページ全体リロード

**実装箇所** (`main.py` 先頭付近):
```python
_cm_value: str | None = None
if not st.session_state.get("_sc_initialized"):
    _cookie_mgr = stx.CookieManager(key="_cm")
    _cm_value = _cookie_mgr.get("st_session") or None
```
- `_sc_initialized` フラグで CookieManager の描画を初回のみに限定（毎回描画すると余分な rerun が発生しボタン操作に干渉）

**自動ログイン処理** (`main.py` 約1560行付近):
```python
if saved_user:
    st.session_state["user"] = saved_user
    st.session_state["_session_cookie"] = token
    st.session_state["_sc_initialized"] = True
    saved_key = auth.get_user_api_key(saved_user["id"])
    if saved_key:
        st.session_state["api_key"] = saved_key
    _restore_draft(saved_user)
    st.rerun()  # ← DB処理フェーズとUI操作フェーズを分離するため必須
```

---

### 2. タイトルサイズ修正 — 完了

- `_show_analysis()` と `_show_deepdive()` の `## No.XX タイトル` を `# No.XX タイトル`（h2→h1）に変更

---

### 3. PDFダウンロードボタン二重表示の修正 — 完了

- `tab_check`（チェックリストタブ）内に `_render_pdf_button()` が重複して呼ばれていたのを削除
- タブ群の下部に1箇所だけ残す

---

### 4. コンセプト強度スコアが毎回同じ内容になる問題 — 完了

- `analyzer.py` のプロンプトに制約を追記:
  ```
  【チェックリスト評価の重要ルール】
  - checklist の10項目は必ず3〜5項目を「要強化」と評価すること（全部OKは禁止）
  - how はこのアイデアの具体的な内容・商品名・特徴に基づいた文章にすること（汎用的な文章は禁止）
  - 「要強化」の how は課題と具体的な対策をセットで明記すること
  ```

---

### 5. 再生成中のアニメーション修正 — 完了

- キャッチコピー再生成ボタンが `st.spinner()` で実装されており、古いコンテンツが見えたまま小さなスピナーが出ていた
- `_gen_overlay()` による全画面オーバーレイに変更

---

### 6. 難易度フィルターボタン（★1〜★5）の動作改善 — 完了

**問題の経緯**:
1. 最初: クリックすると一瞬暗くなるだけで選択できなかった（`extra-streamlit-components` の CookieManager が毎回描画されて rerun に干渉）
2. `_sc_initialized` フラグで解決したが、今度は「2回暗くなる・数秒かかる」問題
3. 自動ログイン後に `st.rerun()` を追加してDB処理フェーズを分離 → 1回の暗転に減少
4. `st.button()` + `st.rerun()` が2重 rerun を起こしていたので `on_click` コールバックに変更
5. 最終的に `@st.fragment` でフィルター全体をフラグメント化 → **暗転なし・即座に選択できる状態** ✅

**最終実装** (`main.py` 約514行付近):
```python
@st.fragment
def _input_settings():
    # STEP 2: 難易度フィルター（★1〜★5）
    # STEP 3: 類似品レビュー数・収集モード・生成ボタン
    ...
    # 生成ボタンの st.rerun() はフラグメント内でも全体 rerun を起こす（Streamlit仕様）
```

`_show_input()` からは `_input_settings()` を呼び出すだけ。
生成パイプラインが使う `sim_count`・`review_mode`・`selected_diffs` はセッションステートから読み直す。

---

## 現在の git 状態（セッション終了時点）

ブランチ: `main`（origin/main と同期済み）

直近コミット（新→旧）:
```
b1e6f04 Update test_app.py: adjust wait times and add filter button test steps
beeee79 Use @st.fragment for filter buttons to eliminate page-level rerun flash
60637a2 Use on_click callbacks to eliminate extra st.rerun() on filter buttons
07c25ce Fix difficulty button: rerun after auto-login to avoid DB calls on click
e86a60a Fix difficulty button by limiting CookieManager to first render only
689ea32 Fix regen loading: replace st.spinner with full-screen overlay
9904101 Fix title size, PDF duplicate, and checklist evaluation
d113799 Fix session persistence: replace st.context.cookies with CookieManager
```

---

## 次セッションでやること（優先度順）

### 【最重要】確認メール E2E テスト（前回セッションから持ち越し）

まず Neon コンソール（https://console.neon.tech）の SQL Editor でテストユーザーを削除:
```sql
DELETE FROM users WHERE email='tenpoohlove+test@gmail.com';
```

その後テスト実行:
```bash
python -X utf8 test_register.py
```

確認項目:
- `tenpoohlove@gmail.com` 受信箱に確認メールが届くか
- メール内リンクが `https://cf-idea-generator.streamlit.app?token=XXX&action=verify` になっているか
- リンクをクリックしてログインできるか

### 【次】クライアント引き渡し準備

1. アプリURL（https://cf-idea-generator.streamlit.app）をクライアントに送る
2. 新規登録してもらう → 確認メールが届くことを確認
3. クライアントのアカウントに admin 権限を付与:
   ```sql
   -- users テーブルで id を確認してから
   INSERT INTO admin_settings (user_id, is_admin) VALUES (<id>, 1);
   -- または既存行があれば UPDATE
   ```

### 【低優先】開発用テストアカウントの整理

- `test@test.com`（パスワード: Test1234!）が DB に残っている
- 本番前に削除するか判断:
  ```sql
  DELETE FROM users WHERE email='test@test.com';
  ```

---

## 現在の重要な設定・認証情報

| 何 | 値/場所 |
|---|---|
| 本番アプリURL | https://cf-idea-generator.streamlit.app |
| GitHub リポジトリ | https://github.com/tenpoohlove/amazoncloud |
| Neon DB接続 | `postgresql://neondb_owner:npg_7HjwqpfDNPM5@ep-misty-star-amhjte2o-pooler.c-5.us-east-1.aws.neon.tech/neondb?sslmode=require` |
| fernet_key | `uHea17CIUdN4bxh0WvfIyptoUxmOXQUCozoUforQ` |
| Gmail App Password | `qcffyhlfsecwdzgk`（2段階認証は維持すること） |
| テスト管理者アカウント | tenpoohlove@gmail.com / Test1234! |
| Streamlit Cloud 管理 | https://share.streamlit.io |

---

## 既知の問題・注意事項

1. **Resend は使わない**: Gmail SMTP（smtp.gmail.com:587）に切り替え済み
2. **Googleアプリパスワード**: Googleアカウントの2段階認証を無効にすると無効になる
3. **テストアドレス**: `tenpoohlove+test@gmail.com` は `tenpoohlove@gmail.com` の受信箱に届く（Gmailの＋エイリアス仕様）
4. **`@st.fragment` の注意点**: フラグメント内で `st.rerun()` を呼ぶと全体 rerun になる（Streamlit の仕様）。生成ボタンはこれを利用している
5. **CookieManager の描画タイミング**: `_sc_initialized` が False の間だけ描画する設計。毎 rerun で描画すると `Streamlit.setComponentValue()` が毎回呼ばれてボタン操作に干渉する

---

## アーキテクチャ：認証メール送信の全パイプライン

```
新規登録フォーム送信
↓
auth.py: create_user() → tokenを生成
↓
main.py:
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

---

## アーキテクチャ：セッション Cookie の全パイプライン

```
ログイン成功
↓
main.py: st.session_state["_session_token"] = token
↓
次の rerun で _session_set(token) が呼ばれる
↓
stc.html() でインライン JS を実行:
  document.cookie = 'st_session=<token>;max-age=...'
  window.top.location.reload()
↓
ページリロード後の最初の rerun:
  CookieManager が document.cookie を読んで _cm_value に返す
↓
自動ログイン処理:
  auth.validate_session(token) → saved_user
  session_state にユーザー情報を保存
  st.rerun() で UI をクリーンに再描画
```
