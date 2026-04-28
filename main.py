"""
main.py: クラウドファンディング新商品アイデアジェネレーター（Streamlit UI）

起動方法:
    streamlit run main.py
"""

import os
from datetime import datetime
import streamlit as st
from dotenv import load_dotenv

import auth
from scraper import scrape_all, extract_asin
from analyzer import (
    analyze_and_generate_ideas,
    generate_ideas_fast,
    generate_idea_analysis,
    generate_deep_dive_content,
    regenerate_with_checklist,
    generate_pdf_bytes,
    DIFFICULTY,
)

load_dotenv()
auth.init_db()

# ─────────────────────────────────────────────
# Cookie manager
# st.context.cookies は WebSocket 接続確立時のHTTPヘッダーを読む。
# st.rerun() は既存 WebSocket を使うため新Cookie は届かない。
# → JS で cookie を書いてフルページリロードするのが唯一の確実な方法。
# ─────────────────────────────────────────────
import streamlit.components.v1 as _stc


def _cookie_get(key: str) -> str | None:
    try:
        return st.context.cookies.get(key)
    except Exception:
        return None


def _cookie_set(key: str, value: str, cookie_key: str = ""):
    """JS でブラウザに cookie をセット（リロードなし）。"""
    max_age = 30 * 24 * 3600
    safe_val = value.replace("\\", "\\\\").replace("'", "\\'")
    _stc.html(
        f"<script>"
        f"document.cookie='{key}={safe_val};max-age={max_age};path=/;SameSite=Lax';"
        f"</script>",
        height=0,
    )


def _cookie_delete(key: str, cookie_key: str = ""):
    """JS でブラウザの cookie を削除してページをフルリロード。"""
    _stc.html(
        f"<script>"
        f"document.cookie='{key}=;max-age=0;path=/;SameSite=Strict';"
        f"window.top.location.reload();"
        f"</script>",
        height=1,
    )


# ─────────────────────────────────────────────
# ページ設定
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="クラファン新商品アイデアジェネレーター",
    page_icon="💡",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ─────────────────────────────────────────────
# セッション初期化
# ─────────────────────────────────────────────
for _k, _v in [
    ("stage", "input"),
    ("product_data", None),
    ("ideas", None),
    ("url", ""),
    ("selected_idea_id", None),
    ("deep_dive_cache", {}),
    ("api_key", ""),
    ("last_error", ""),
    ("user", None),
    ("api_test_result", None),
    ("deepdiving_id", None),
    ("cf_btn_loading", False),
    ("gen_btn_loading", False),
    ("regen_btn_loading", False),
    ("diff_cb_all", True),
    ("diff_cb_1", False),
    ("diff_cb_2", False),
    ("diff_cb_3", False),
    ("diff_cb_4", False),
    ("diff_cb_5", False),
    ("sim_count", 5),
    ("review_mode", "amazon"),
]:
    if _k not in st.session_state:
        st.session_state[_k] = _v



# ─────────────────────────────────────────────
# クエリパラメータ処理（メール認証・パスワードリセット）
# ─────────────────────────────────────────────
verify_token = st.query_params.get("verify_token", "")
reset_token  = st.query_params.get("reset_token", "")

if verify_token:
    if auth.verify_email_token(verify_token):
        st.query_params.clear()
        st.success("✅ メール認証が完了しました！ログインしてください。")
    else:
        st.query_params.clear()
        st.error("認証リンクが無効または期限切れです。再度ご登録ください。")


# ─────────────────────────────────────────────
# ヘルパー
# ─────────────────────────────────────────────
_DIFF_ICON = {1: "🟢", 2: "🔵", 3: "🟡", 4: "🟠", 5: "🔴"}
_DIFF_COLOR = {
    1: "#d4edda", 2: "#cce5ff", 3: "#fff3cd",
    4: "#ffe0b2", 5: "#ffcccc",
}


# ─────────────────────────────────────────────
# ドラフト状態の保存・復元
# ─────────────────────────────────────────────
def _save_draft():
    """現在の作業状態をDBに保存する（リフレッシュ時の復元用）"""
    user = st.session_state.get("user")
    if not user:
        return
    product_data = st.session_state.get("product_data")
    if not product_data:
        return
    # レビュー生データは除いてコンパクトに保存
    product_data_light = {
        k: v for k, v in product_data.items()
        if k not in ("reviews", "similar_data")
    }
    product_data_light["_review_count"] = len(product_data.get("reviews", []))
    product_data_light["_similar_count"] = len(product_data.get("similar_data", []))
    state = {
        "stage": st.session_state.get("stage", "ideas"),
        "url": st.session_state.get("url", ""),
        "ideas": st.session_state.get("ideas", []),
        "selected_idea_id": st.session_state.get("selected_idea_id"),
        "deep_dive_cache": st.session_state.get("deep_dive_cache", {}),
        "product_data": product_data_light,
    }
    try:
        auth.save_draft_state(user["id"], state)
    except Exception:
        pass


def _restore_draft(user: dict):
    """DBから保存済み作業状態を復元する（初回セッション時のみ適用）"""
    if st.session_state.get("product_data") is not None:
        return  # 既にデータがある場合は上書きしない
    try:
        draft = auth.get_draft_state(user["id"])
    except Exception:
        return
    if not draft or not draft.get("ideas"):
        return

    st.session_state["product_data"] = draft.get("product_data")
    st.session_state["ideas"] = draft.get("ideas", [])
    st.session_state["url"] = draft.get("url", "")
    st.session_state["selected_idea_id"] = draft.get("selected_idea_id")
    # JSON経由でキーが文字列化されるのを整数に戻す
    raw_cache = draft.get("deep_dive_cache", {})
    restored_cache = {}
    for k, v in raw_cache.items():
        try:
            restored_cache[int(k)] = v
        except (ValueError, TypeError):
            pass
    st.session_state["deep_dive_cache"] = restored_cache

    stage = draft.get("stage", "ideas")
    # 分析中ステージは完了済みかチェックしてから復元
    if stage == "analyzing_idea":
        selected = draft.get("selected_idea_id")
        ideas = draft.get("ideas", [])
        idea = next((i for i in ideas if i["id"] == selected), None)
        stage = "analysis" if (idea and idea.get("_analyzed")) else "ideas"
    # データが存在しないステージへの復元は ideas に落とす
    if stage in ("analysis", "deepdive") and not draft.get("selected_idea_id"):
        stage = "ideas"
    st.session_state["stage"] = stage


def _render_pdf_button(product_data: dict, idea: dict, deep_dive: dict, key_suffix: str = ""):
    """現在の deep_dive 内容で PDF を生成してダウンロードボタンを表示する（セッションキャッシュ済み）"""
    idea_id = idea.get("id", 0)
    pdf_cache = st.session_state.setdefault("pdf_cache", {})
    if idea_id not in pdf_cache:
        try:
            pdf_cache[idea_id] = generate_pdf_bytes(
                product_data=product_data,
                idea=idea,
                deep_dive=deep_dive,
                generated_at=datetime.now().strftime("%Y年%m月%d日 %H:%M"),
                model_name="生成AI",
            )
        except Exception as e:
            st.warning(f"PDF生成に失敗しました: {e}")
            return
    asin = extract_asin(st.session_state.get("url", "unknown")) or "unknown"
    st.download_button(
        label="📄 PDFレポートをダウンロード（クラファン企画書）",
        data=pdf_cache[idea_id],
        file_name=f"cf_report_{asin}_idea{idea_id:02d}.pdf",
        mime="application/pdf",
        use_container_width=True,
        type="primary",
        key=f"pdf_dl_{key_suffix}",
    )


def _idea_card(idea: dict, col):
    diff = idea.get("difficulty", 1)
    icon = _DIFF_ICON.get(diff, "⚪")
    diff_info = DIFFICULTY.get(diff, DIFFICULTY[1])
    bg = _DIFF_COLOR.get(diff, "#ffffff")
    ob = idea.get("one_belief", {})

    with col:
        with st.container(border=True):
            st.markdown(
                f"<div style='font-size:14px;margin-bottom:6px'>"
                f"{icon} {diff_info['label']} {diff_info['name']}　"
                f"｜　製造コスト: {idea.get('estimated_cost', '—')}"
                f"</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div style='font-weight:bold;font-size:22px;margin-bottom:8px'>"
                f"No.{idea.get('id', 0):02d}　{idea.get('title', '（タイトルなし）')}"
                f"</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div style='font-size:15px;color:#1a1a1a;background:{bg};"
                f"padding:8px 12px;border-radius:6px;margin-bottom:8px'>"
                f"💡 {ob.get('full_statement', '—')}"
                f"</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div style='font-size:15px;margin-bottom:4px;color:inherit'>"
                f"🔑 {idea.get('q1_novelty', '—')[:60]}"
                f"</div>",
                unsafe_allow_html=True,
            )
            if idea.get("evidence"):
                st.markdown(
                    f"<div style='font-size:14px;margin-bottom:4px;color:inherit'>"
                    f"📝 根拠レビュー: {idea['evidence'][:60]}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            is_loading = st.session_state.get("deepdiving_id") == idea["id"]
            if is_loading:
                st.button("⏳ 深堀中...", key=f"detail_{idea['id']}",
                          use_container_width=True, type="primary", disabled=True)
            else:
                if st.button("🔍 深堀する", key=f"detail_{idea['id']}",
                             use_container_width=True, type="primary"):
                    st.session_state["deepdiving_id"] = idea["id"]
                    st.rerun()
    return False


# ─────────────────────────────────────────────
# パスワードリセット画面（トークンがURLにある場合）
# ─────────────────────────────────────────────
def show_reset_password_form(token: str):
    st.title("🔑 パスワードリセット")
    user = auth.validate_reset_token(token)
    if not user:
        st.error("リセットリンクが無効または期限切れです（有効期限: 1時間）。再度お試しください。")
        if st.button("ログイン画面に戻る"):
            st.query_params.clear()
            st.rerun()
        return

    st.info(f"**{user['email']}** のパスワードを変更します。")
    col_l, col_c, col_r = st.columns([1, 2, 1])
    with col_c:
        with st.form("reset_form"):
            new_pass  = st.text_input("新しいパスワード（8文字以上）", type="password")
            new_pass2 = st.text_input("新しいパスワード（確認）", type="password")
            submitted = st.form_submit_button("パスワードを変更する", use_container_width=True, type="primary")
        if submitted:
            if len(new_pass) < 8:
                st.error("パスワードは8文字以上にしてください。")
            elif new_pass != new_pass2:
                st.error("パスワードが一致しません。")
            else:
                if auth.apply_reset_password(token, new_pass):
                    st.success("✅ パスワードを変更しました。新しいパスワードでログインしてください。")
                    st.query_params.clear()
                    st.rerun()
                else:
                    st.error("変更に失敗しました。リンクが期限切れの可能性があります。")


# ─────────────────────────────────────────────
# 認証画面（未ログイン時）
# ─────────────────────────────────────────────
def show_auth():
    st.title("💡 クラファン新商品アイデアジェネレーター")
    st.markdown("---")
    col_l, col_c, col_r = st.columns([1, 2, 1])
    with col_c:
        tab_login, tab_register = st.tabs(["ログイン", "新規登録"])

        with tab_login:
            st.markdown("#### ログイン")
            with st.form("login_form"):
                email    = st.text_input("メールアドレス", placeholder="example@email.com")
                password = st.text_input("パスワード", type="password")
                submitted = st.form_submit_button(
                    "ログイン", use_container_width=True, type="primary"
                )
            if submitted:
                if not email or not password:
                    st.error("メールアドレスとパスワードを入力してください。")
                else:
                    user, err = auth.authenticate(email, password)
                    if err:
                        st.error(err)
                    else:
                        token = auth.create_session(user["id"], days=30)
                        st.session_state["user"] = dict(user)
                        st.session_state["_session_token"] = token
                        saved_key = auth.get_user_api_key(user["id"])
                        if saved_key:
                            st.session_state["api_key"] = saved_key
                        st.rerun()

            st.markdown("---")
            st.markdown("##### パスワードをお忘れですか？")
            with st.form("forgot_form"):
                forgot_email = st.text_input("登録済みメールアドレス", placeholder="example@email.com",
                                             label_visibility="collapsed")
                forgot_submitted = st.form_submit_button(
                    "リセットメールを送る", use_container_width=True
                )
            if forgot_submitted:
                if not forgot_email:
                    st.error("メールアドレスを入力してください。")
                else:
                    ok, token_or_err = auth.create_reset_token(forgot_email)
                    if not ok:
                        st.error(token_or_err)
                    else:
                        base_url = os.getenv("BASE_URL", "http://localhost:8501")
                        conn = auth._get_conn()
                        row = conn.execute(
                            "SELECT name FROM users WHERE email=?",
                            (forgot_email.lower().strip(),)
                        ).fetchone()
                        conn.close()
                        name = row["name"] if row else ""
                        sent, _ = auth.send_password_reset_email(
                            forgot_email, name, token_or_err, base_url
                        )
                        if sent:
                            st.success(f"✅ パスワードリセットメールを {forgot_email} に送りました。")
                        else:
                            reset_url = f"{base_url}?reset_token={token_or_err}"
                            st.success("✅ リセットリンクを発行しました。")
                            st.info(
                                f"以下のリンクからパスワードを変更してください（1時間有効）:\n\n"
                                f"[🔑 パスワードをリセットする]({reset_url})"
                            )

        with tab_register:
            st.markdown("#### 新規アカウント登録")
            with st.form("register_form"):
                r_name       = st.text_input("お名前", placeholder="山田 太郎")
                r_email      = st.text_input("メールアドレス", placeholder="example@email.com")
                r_phone      = st.text_input("電話番号", placeholder="090-0000-0000")
                r_pass       = st.text_input("パスワード（8文字以上）", type="password")
                r_pass2      = st.text_input("パスワード（確認）", type="password")
                r_newsletter = st.checkbox("オニオンリンクからのお知らせメールを受け取る")
                r_terms      = st.checkbox(
                    "**[利用規約](/terms)** に同意する（必須）"
                )
                submitted_r = st.form_submit_button(
                    "登録する", use_container_width=True, type="primary"
                )
            if submitted_r:
                if not all([r_name, r_email, r_phone, r_pass, r_pass2]):
                    st.error("すべての項目を入力してください。")
                elif not r_terms:
                    st.error("利用規約への同意が必要です。")
                elif len(r_pass) < 8:
                    st.error("パスワードは8文字以上にしてください。")
                elif r_pass != r_pass2:
                    st.error("パスワードが一致しません。")
                else:
                    ok, token_or_err = auth.create_user(
                        r_email, r_phone, r_name, r_pass, r_newsletter
                    )
                    if not ok:
                        st.error(token_or_err)
                    else:
                        base_url = os.getenv("BASE_URL", "http://localhost:8501")
                        sent, _ = auth.send_verification_email(
                            r_email, r_name, token_or_err, base_url
                        )
                        if sent:
                            st.success(f"✅ 登録完了！{r_email} に確認メールを送りました。")
                        else:
                            verify_url = f"{base_url}?verify_token={token_or_err}"
                            st.success("✅ 登録完了！")
                            st.info(
                                f"以下のリンクをクリックしてメール認証を完了してください:\n\n"
                                f"[✅ メールアドレスを確認する]({verify_url})"
                            )


# ─────────────────────────────────────────────
# ページ: ホーム（input / ideas / deepdive）
# ─────────────────────────────────────────────
def page_home():
    stage = st.session_state.get("stage", "input")
    if stage == "input":
        _show_input()
    elif stage == "ideas":
        _show_ideas()
    elif stage == "analyzing_idea":
        _show_analyzing_idea()
    elif stage == "analysis":
        _show_analysis()
    elif stage == "deepdive":
        _show_deepdive()
    else:
        st.session_state["stage"] = "input"
        st.rerun()


def _gen_overlay(title: str, subtitle: str = "") -> st.delta_generator.DeltaGenerator:
    """全画面オーバーレイを表示しステータス更新用のplaceholderを返す。"""
    st.markdown(f"""
<style>@keyframes _ov_spin {{ to {{ transform: rotate(360deg); }} }}</style>
<div style='position:fixed;top:0;left:0;width:100vw;height:100vh;
            background:rgba(0,0,0,0.55);z-index:99999;
            display:flex;align-items:center;justify-content:center;
            pointer-events:all;cursor:not-allowed'>
  <div style='background:#1a1a2e;border-radius:16px;padding:40px 56px;text-align:center;
              box-shadow:0 8px 32px rgba(0,0,0,0.5)'>
    <div style='width:48px;height:48px;border:4px solid rgba(255,255,255,0.2);
                border-top-color:#4fa3ff;border-radius:50%;
                animation:_ov_spin 0.8s linear infinite;margin:0 auto 20px'></div>
    <div style='font-size:20px;font-weight:700;color:#ffffff;margin-bottom:8px'>{title}</div>
    <div style='font-size:14px;color:#aaaacc'>{subtitle}</div>
  </div>
</div>
""", unsafe_allow_html=True)
    return st.empty()


def _set_status(ph, text: str):
    """画面下部に固定表示されるステータストーストを更新する。"""
    ph.markdown(
        f"<div style='position:fixed;bottom:24px;left:50%;transform:translateX(-50%);"
        f"background:rgba(15,35,70,0.95);color:#ffffff;padding:10px 28px;"
        f"border-radius:8px;font-size:14px;font-weight:500;z-index:100000;"
        f"box-shadow:0 4px 16px rgba(0,0,0,0.5);white-space:nowrap'>"
        f"⏳ {text}</div>",
        unsafe_allow_html=True,
    )


def _show_input():
    if st.session_state.get("gen_btn_loading"):
        _gen_overlay("アイデアを生成中...", "しばらくお待ちください（30〜60秒）")

    st.title("💡 クラファン新商品アイデアジェネレーター")
    st.caption("Amazon商品URLを貼るだけで新商品アイデア10個を生成。気になるアイデアはさらに深掘りできます。")

    api_key_check = st.session_state.get("api_key") or os.getenv("GEMINI_API_KEY")
    if not api_key_check:
        st.warning("⚠️ APIキーが未設定です。左メニューの「設定」から設定してください。")

    if st.session_state.get("last_error"):
        st.error(f"前回のエラー: {st.session_state['last_error']}")
        if st.button("🔄 もう一度試す", type="secondary"):
            st.session_state["last_error"] = ""
            st.rerun()

    # ① URL入力カード
    with st.container(border=True):
        st.markdown("<p style='font-size:16px;font-weight:600;color:#888;letter-spacing:1px;margin:0 0 6px'>STEP 1　分析したい商品</p>", unsafe_allow_html=True)
        _u_col, _clr_col = st.columns([9, 1])
        with _u_col:
            url = st.text_input(
                "🔗 Amazon 商品URL",
                value=st.session_state.get("url", ""),
                placeholder="https://www.amazon.co.jp/dp/XXXXXXXXXX",
                key="url_input_field",
            )
        with _clr_col:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            if st.button("✕", key="url_clear_btn", help="URLをクリア", use_container_width=True):
                st.session_state["url"] = ""
                st.session_state["url_input_field"] = ""
                st.rerun()

    # ② 難易度フィルターカード
    with st.container(border=True):
        st.markdown("<p style='font-size:16px;font-weight:600;color:#888;letter-spacing:1px;margin:0 0 6px'>STEP 2　難易度フィルター</p>", unsafe_allow_html=True)
        _diff_opts = [
            ("all", "すべて", "", "すべての難易度を対象にします"),
            (1, "★1", "超低コスト ⓘ", DIFFICULTY[1]["desc"]),
            (2, "★2", "低コスト ⓘ",   DIFFICULTY[2]["desc"]),
            (3, "★3", "中コスト ⓘ",   DIFFICULTY[3]["desc"]),
            (4, "★4", "高難度 ⓘ",     DIFFICULTY[4]["desc"]),
            (5, "★5", "超高難度 ⓘ",   DIFFICULTY[5]["desc"]),
        ]
        _dc = st.columns(6)
        for _col, (_key, _main, _sub, _help) in zip(_dc, _diff_opts):
            if _key == "all":
                _sel = st.session_state.get("diff_cb_all", True)
            else:
                _sel = st.session_state.get(f"diff_cb_{_key}", False)
            _lbl = f"**{_main}** {_sub}" if _sub else f"**{_main}**"
            if _col.button(_lbl, key=f"diff_card_{_key}", help=_help,
                           type="primary" if _sel else "secondary", use_container_width=True):
                if _key == "all":
                    st.session_state["diff_cb_all"] = True
                    for _i in range(1, 6):
                        st.session_state[f"diff_cb_{_i}"] = True
                else:
                    st.session_state[f"diff_cb_{_key}"] = not _sel
                    st.session_state["diff_cb_all"] = all(
                        st.session_state.get(f"diff_cb_{_i}", False) for _i in range(1, 6)
                    )
                st.rerun()
    _checked = {k: st.session_state.get(f"diff_cb_{k}", False) for k in range(1, 6)}
    if st.session_state.get("diff_cb_all") or not any(_checked.values()):
        selected_diffs = []
    else:
        selected_diffs = [k for k, v in _checked.items() if v]

    # ③ 詳細設定カード（ラジオはフォーム外でcolumns均等配置）
    with st.container(border=True):
        st.markdown("<p style='font-size:16px;font-weight:600;color:#888;letter-spacing:1px;margin:0 0 6px'>STEP 3　詳細設定</p>", unsafe_allow_html=True)

        st.markdown("<p style='font-size:13px;color:#888;margin:4px 0 6px'>🔍 類似品レビュー数</p>", unsafe_allow_html=True)
        _sim_opts = [(0, "0件", "対象商品のみ ⚡約30秒"), (5, "5件", "+40件 約1分"), (10, "10件", "+80件 約1.5分"), (20, "20件", "+160件 約2〜3分")]
        _sc = st.columns(4)
        for _col, (_val, _main, _sub) in zip(_sc, _sim_opts):
            _sel = st.session_state.get("sim_count", 5) == _val
            if _col.button(f"**{_main}**", key=f"sim_card_{_val}", help=_sub,
                           type="primary" if _sel else "secondary", use_container_width=True):
                st.session_state["sim_count"] = _val
                st.rerun()
        sim_count = st.session_state.get("sim_count", 5)

        st.markdown("<p style='font-size:13px;color:#888;margin:12px 0 6px'>📝 レビュー収集モード</p>", unsafe_allow_html=True)
        _mode_opts = [
            ("amazon", "🛒 Amazonのみ", "実レビューのみ収集・高速"),
            ("gemini", "🔍 AI Web検索込み", "Amazon＋Web全体・大量収集・低速"),
        ]
        _mc = st.columns(2)
        for _col, (_val, _main, _sub) in zip(_mc, _mode_opts):
            _sel = st.session_state.get("review_mode", "amazon") == _val
            if _col.button(f"**{_main}**", key=f"mode_card_{_val}", help=_sub,
                           type="primary" if _sel else "secondary", use_container_width=True):
                st.session_state["review_mode"] = _val
                st.rerun()
        review_mode = st.session_state.get("review_mode", "amazon")
        if review_mode == "gemini":
            st.caption("※ AIがWeb全体（Amazon・ショッピングサイト・ブログ等）を検索してレビュー・口コミを収集します。AIによる要約を含みます。商品あたり約100件追加。収集に時間がかかります。")

        if st.session_state.get("gen_btn_loading"):
            st.button("⏳ 生成中...", disabled=True, use_container_width=True, type="primary", key="gen_btn")
        else:
            if st.button("🔍 アイデアを生成する", use_container_width=True, type="primary", key="gen_btn"):
                st.session_state["gen_btn_loading"] = True
                st.rerun()

    if not st.session_state.get("gen_btn_loading"):
        return
    st.session_state["gen_btn_loading"] = False

    url = st.session_state.get("url", "")
    if not url:
        st.error("Amazon商品URLを入力してください。")
        return
    if "amazon" not in url.lower():
        st.error("AmazonのURLを入力してください。")
        return
    if not extract_asin(url):
        st.error("URLからASIN（商品ID）を抽出できませんでした。")
        return

    api_key = st.session_state.get("api_key") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        st.error("APIキーが未設定です。左メニューの「設定」から入力してください。")
        return

    _ph = st.empty()

    def update_progress(msg, pct):
        _set_status(_ph, msg)

    try:
        product_data = scrape_all(
            url,
            max_similar_products=sim_count,
            progress_callback=update_progress,
            api_key=api_key,
            use_gemini_reviews=(review_mode == "gemini"),
        )
        _set_status(_ph, "AIがアイデア10個を生成中...")
        diff_filter = selected_diffs if selected_diffs else None
        ideas = generate_ideas_fast(product_data, diff_filter, api_key)
        _ph.empty()
    except RuntimeError as e:
        _ph.empty()
        st.error(f"スクレイピングエラー: {e}")
        st.session_state["last_error"] = str(e)
        return
    except Exception as e:
        _ph.empty()
        st.error(f"エラーが発生しました: {e}")
        st.session_state["last_error"] = str(e)
        return

    st.session_state["product_data"] = product_data
    st.session_state["ideas"]        = ideas
    st.session_state["url"]          = url
    st.session_state["stage"]        = "ideas"
    st.session_state["deep_dive_cache"] = {}
    _save_draft()

    # 履歴に保存
    user = st.session_state.get("user")
    if user:
        try:
            auth.save_idea_history(
                user["id"], url,
                product_data.get("title", ""),
                ideas,
            )
        except Exception:
            pass

    st.rerun()


def _show_ideas():
    product_data = st.session_state.get("product_data")
    ideas        = st.session_state.get("ideas", [])
    if not product_data:
        st.session_state["stage"] = "input"
        st.rerun()
        return

    st.title("💡 新商品アイデア 10選")
    if st.button("← 条件を変更する"):
        st.session_state["stage"] = "input"
        st.rerun()

    st.subheader(f"📦 分析商品: {product_data['title']}")

    mode         = product_data.get("mode", "main_only")
    main_rev_count = len(product_data.get("reviews", []))
    similar_data = product_data.get("similar_data", [])
    amz_cnt      = product_data.get("amazon_review_count", main_rev_count)
    gem_cnt      = product_data.get("gemini_review_count", 0)

    if gem_cnt:
        review_breakdown = f"Amazon **{amz_cnt}件** + AI Web検索 **{gem_cnt}件** = 合計 **{main_rev_count}件**"
    else:
        review_breakdown = f"Amazon **{amz_cnt}件**"

    if mode == "main_only":
        st.info(
            f"**収集モード:** ⚡ 対象商品のみ　｜　"
            f"{review_breakdown}　｜　Amazon総数: {product_data.get('total_reviews', 0)}件"
        )
    else:
        st.info(
            f"**収集モード:** 🔍 類似品含む　｜　"
            f"{review_breakdown}　｜　類似品 **{len(similar_data)}商品**"
        )

    sources = product_data.get("sources", [])
    if sources:
        with st.expander("🔗 参照したURL一覧", expanded=False):
            _icon_map = {"main": "🟢", "similar": "🟡"}
            for s in sources:
                icon = _icon_map.get(s.get("type", ""), "⚪")
                ca, cb, cc = st.columns([3, 2, 2])
                with ca:
                    st.markdown(f"{icon} **[{s.get('type_label','')}]** {s.get('title','')[:45]}")
                    st.caption(f"ASIN: `{s.get('asin','')}`　｜　[Amazonで確認]({s.get('url','')})")
                with cb:
                    st.markdown(f"取得レビュー: **{s.get('review_count',0)}件**")
                with cc:
                    if s.get("total_on_amazon", 0) > 0:
                        st.caption(f"Amazon総レビュー数: {s['total_on_amazon']}件")
                st.divider()

    st.divider()
    st.markdown("#### 気になるアイデアの **🔍 深堀する** をクリックしてください")

    for idea in ideas:
        col = st.columns(1)[0]
        _idea_card(idea, col)

    deepdiving_id = st.session_state.get("deepdiving_id")
    if deepdiving_id is not None:
        _idea_title = next((i.get("title", "") for i in ideas if i["id"] == deepdiving_id), "")
        _gen_overlay(f"「{_idea_title}」を分析中...", "詳細分析を生成しています（10〜30秒）")
        st.session_state["selected_idea_id"] = deepdiving_id
        st.session_state["deepdiving_id"] = None
        st.session_state["stage"] = "analyzing_idea"
        st.rerun()


def _show_analyzing_idea():
    """選択したアイデアのQ1-Q10詳細分析を生成する中間ページ"""
    ideas = st.session_state["ideas"]
    selected_id = st.session_state["selected_idea_id"]
    product_data = st.session_state["product_data"]
    api_key = st.session_state.get("api_key") or os.getenv("GEMINI_API_KEY")

    idea = next((i for i in ideas if i["id"] == selected_id), None)
    if idea is None:
        st.session_state["stage"] = "ideas"
        st.rerun()
        return

    if idea.get("_analyzed"):
        st.session_state["stage"] = "analysis"
        st.rerun()
        return

    _ph = _gen_overlay(f"「{idea['title']}」を分析中...", "詳細分析を生成しています（10〜30秒）")
    _set_status(_ph, "詳細分析を生成中...")
    try:
        _set_status(_ph, "AIで詳細分析を生成中...")
        full_idea = generate_idea_analysis(idea, product_data, api_key)
        for j, x in enumerate(ideas):
            if x["id"] == selected_id:
                ideas[j] = full_idea
                break
        st.session_state["ideas"] = ideas
        st.session_state["stage"] = "analysis"
        _save_draft()
        _ph.empty()
        st.rerun()
    except Exception as e:
        _ph.empty()
        st.error(f"分析生成エラー: {e}")
        if st.button("← アイデア一覧に戻る"):
            st.session_state["stage"] = "ideas"
            st.rerun()


def _show_analysis():
    """中間ページ: Q1-Q10 分析データを詳細表示（APIコールなし）"""
    ideas      = st.session_state["ideas"]
    selected_id = st.session_state["selected_idea_id"]

    idea = next((i for i in ideas if i["id"] == selected_id), None)
    if idea is None:
        st.session_state["stage"] = "ideas"
        st.rerun()
        return

    diff      = idea.get("difficulty", 1)
    diff_info = DIFFICULTY.get(diff, DIFFICULTY[1])
    icon      = _DIFF_ICON.get(diff, "⚪")
    ob        = idea.get("one_belief", {})

    col_back, col_fwd = st.columns([1, 4])
    with col_back:
        if st.button("← アイデア一覧に戻る"):
            st.session_state["stage"] = "ideas"
            st.rerun()

    st.markdown(f"## {icon} No.{idea['id']:02d}　{idea['title']}")
    st.caption(
        f"難易度: {diff_info['label']} {diff_info['name']}　｜　"
        f"製造コスト: {idea.get('estimated_cost', '—')}"
    )

    # One Belief カード
    st.markdown(
        f"<div style='padding:16px 20px;border-radius:10px;"
        f"border:2px solid #2c7be5;background:#e8f4ff;margin:12px 0 20px 0;"
        f"font-size:18px;font-weight:bold;line-height:1.7;color:#1a1a1a'>"
        f"💡 {ob.get('full_statement', '—')}"
        f"</div>",
        unsafe_allow_html=True,
    )

    ob_cols = st.columns(3)
    for col, (label, key, color) in zip(ob_cols, [
        ("新しい機会", "new_opportunity", "#e8f4f8"),
        ("顧客の欲求",  "desire",          "#fff3cd"),
        ("新メカニズム","new_mechanism",   "#e8f8e8"),
    ]):
        with col:
            st.markdown(
                f"<div style='padding:12px 14px;border-radius:8px;background:{color};"
                f"min-height:80px;color:#1a1a1a'>"
                f"<div style='font-size:13px;opacity:0.65;margin-bottom:4px'>{label}</div>"
                f"<div style='font-size:16px;font-weight:bold'>{ob.get(key, '—')}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    st.markdown("---")
    st.markdown("### 📋 アイデア分析データ")
    st.caption("クラファンページ生成に使われる分析データです。")

    _Q_LABELS = [
        ("q1_novelty",     "Q1｜新規性",     "なぜ今、誰も実現できていないのか"),
        ("q2_benefit",     "Q2｜ベネフィット","顧客が得られる最大の価値"),
        ("q3_proof_abt",   "Q3｜証拠(ABT)",  "ビフォー→ブリッジ→アフターの構造"),
        ("q4_real_problem","Q4｜真の問題",    "表面的な不満の奥にある本当の問題"),
        ("q5_enemy",       "Q5｜共通の敵",   "顧客が怒りを向けるべき対象"),
        ("q6_urgency",     "Q6｜緊急性",     "今すぐ行動すべき理由"),
        ("q7_trust",       "Q7｜信頼",       "信用させるための証拠・実績"),
        ("q8_mechanism",   "Q8｜メカニズム", "なぜこのアイデアが機能するのか"),
        ("q9_offer",       "Q9｜オファー",   "クラファンの価格・特典・条件"),
        ("q10_pushpull",   "Q10｜クロージング","最後の一押し・プッシュ＆プル"),
    ]
    _Q_COLORS = {
        "q6_urgency":     ("#fff3cd", "#f39c12"),
        "q10_pushpull":   ("#ffebee", "#e74c3c"),
        "q5_enemy":       ("#fce4ec", "#c62828"),
        "q3_proof_abt":   ("#e8f5e9", "#2e7d32"),
        "q7_trust":       ("#e3f2fd", "#1565c0"),
        "q9_offer":       ("#f3e5f5", "#6a1b9a"),
    }

    left_qs, right_qs = _Q_LABELS[:5], _Q_LABELS[5:]
    col_l, col_r = st.columns(2)
    for qs, col in [(left_qs, col_l), (right_qs, col_r)]:
        with col:
            for key, label, hint in qs:
                bg, border = _Q_COLORS.get(key, ("#f8f9fa", "#6c757d"))
                st.markdown(
                    f"<div style='padding:12px 14px;border-radius:8px;"
                    f"border-left:4px solid {border};background:{bg};"
                    f"margin-bottom:10px;color:#1a1a1a'>"
                    f"<div style='font-size:13px;opacity:0.65;margin-bottom:3px'>"
                    f"{label}　<span style='font-weight:normal'>{hint}</span></div>"
                    f"<div style='font-size:15px;line-height:1.6'>{idea.get(key, '—')}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

    if idea.get("novelty_advice"):
        st.markdown("---")
        st.markdown("### 🛠 新規性アドバイス")
        for i, adv in enumerate(idea["novelty_advice"], 1):
            st.markdown(f"**{i}.** {adv}")

    if idea.get("evidence"):
        st.markdown(
            f"<div style='padding:10px 14px;border-radius:6px;"
            f"border-left:4px solid #6c757d;background:#f8f9fa;font-size:15px;"
            f"margin-top:12px;color:#1a1a1a'>"
            f"📝 <b>根拠レビュー:</b> {idea['evidence']}</div>",
            unsafe_allow_html=True,
        )

    st.markdown("---")
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        if st.button("🚀 クラファンページを生成する", use_container_width=True, type="primary", key="cf_gen_btn"):
            st.session_state["stage"] = "deepdive"
            st.rerun()


def _show_deepdive():
    product_data = st.session_state["product_data"]
    ideas        = st.session_state["ideas"]
    selected_id  = st.session_state["selected_idea_id"]
    api_key      = st.session_state.get("api_key") or os.getenv("GEMINI_API_KEY")

    idea = next((i for i in ideas if i["id"] == selected_id), None)
    if idea is None:
        st.session_state["stage"] = "ideas"
        st.rerun()
        return

    col_back2, _ = st.columns([1, 4])
    with col_back2:
        if st.button("← 分析ページに戻る"):
            st.session_state["stage"] = "analysis"
            st.rerun()

    diff      = idea.get("difficulty", 1)
    diff_info = DIFFICULTY.get(diff, DIFFICULTY[1])
    icon      = _DIFF_ICON.get(diff, "⚪")
    ob        = idea.get("one_belief", {})

    st.markdown(f"## {icon} No.{idea['id']:02d}　{idea['title']}")
    st.caption(
        f"分析商品: {product_data['title'][:50]}　｜　"
        f"難易度: {diff_info['label']} {diff_info['name']}　｜　"
        f"製造コスト: {idea.get('estimated_cost', '—')}"
    )
    st.info(f"💡 コアメッセージ：「{ob.get('full_statement', '—')}」")

    cache = st.session_state.get("deep_dive_cache", {})
    if selected_id not in cache:
        _ph = _gen_overlay("クラファンページを生成中...", "10セクション構成・リターン設計・チェックリストを生成しています（30〜60秒）")
        _set_status(_ph, "分析データを読み込み中...")
        try:
            _set_status(_ph, "ページ構成を生成中...")
            deep_dive = generate_deep_dive_content(idea, product_data, api_key)
            _set_status(_ph, "仕上げ中...")
            cache[selected_id] = deep_dive
            st.session_state["deep_dive_cache"] = cache
            _save_draft()
            _ph.empty()
            st.rerun()
        except Exception as e:
            _ph.empty()
            st.error(f"生成に失敗しました: {e}")
            return
    else:
        deep_dive = cache[selected_id]

    tab_copy, tab_pages, tab_returns, tab_check = st.tabs([
        "🎯 キャッチコピー", "📄 ページ構成（10セクション）", "💰 リターン設計", "✅ コンセプト強度チェック",
    ])

    with tab_copy:
        st.subheader("🎯 キャッチコピー 3案")
        for i, cc in enumerate(deep_dive.get("catchcopy", []), 1):
            st.markdown(
                f"<div style='padding:14px 18px;border-radius:8px;"
                f"border-left:4px solid #2c7be5;"
                f"font-size:19px;font-weight:bold;margin-bottom:12px'>"
                f"案{i}　{cc}</div>",
                unsafe_allow_html=True,
            )
        st.divider()
        if st.button("🔄 再生成する", key="regen_cf"):
            with st.spinner("再生成中..."):
                try:
                    cache[selected_id] = generate_deep_dive_content(idea, product_data, api_key)
                    st.session_state["deep_dive_cache"] = cache
                    st.rerun()
                except Exception as e:
                    st.error(f"再生成に失敗しました: {e}")

    with tab_pages:
        st.subheader("📄 クラファンページ構成（10セクション）")
        _section_icons = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
        sections = deep_dive.get("page_sections", [])
        if sections:
            for sec in sections:
                idx = sec.get("section", 1) - 1
                icon_s = _section_icons[idx] if 0 <= idx < len(_section_icons) else "▶"
                with st.expander(
                    f"{icon_s} **セクション{sec.get('section','')}：{sec.get('name','')}**　"
                    f"— {sec.get('purpose','')}",
                    expanded=(idx == 0),
                ):
                    st.markdown(
                        f"<div style='padding:14px 18px;border-radius:8px;"
                        f"background:#f8f9fa;font-size:15px;line-height:1.8;"
                        f"white-space:pre-wrap;margin-bottom:8px;color:#1a1a1a'>"
                        f"{sec.get('content','')}"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    if sec.get("media"):
                        st.caption(f"📸 推奨メディア: {sec['media']}")
        else:
            st.warning("ページ構成を生成できませんでした。再生成してください。")

    with tab_returns:
        st.subheader("💰 リターン設計（3段階）")
        ret = deep_dive.get("returns", {})
        _tier_colors = {
            "early_bird": ("#fff8e1", "#f39c12", "🥇"),
            "standard":   ("#e8f4f8", "#2c7be5", "🥈"),
            "premium":    ("#f3e5f5", "#8e44ad", "🥉"),
        }
        for key in ["early_bird", "standard", "premium"]:
            tier = ret.get(key, {})
            if not tier:
                continue
            bg, border, medal = _tier_colors[key]
            discount = f"　**{tier.get('discount','')}**" if tier.get("discount") else ""
            limit    = f"　{tier.get('limit','')}" if tier.get("limit") else ""
            st.markdown(
                f"<div style='padding:16px 20px;border-radius:10px;"
                f"border-left:5px solid {border};background:{bg};margin-bottom:14px;color:#1a1a1a'>"
                f"<div style='font-size:18px;font-weight:bold;margin-bottom:6px'>"
                f"{medal} {tier.get('label','')}{discount}{limit}</div>"
                f"<div style='font-size:22px;font-weight:bold;color:{border};"
                f"margin-bottom:8px'>{tier.get('price','')}</div>"
                f"<div style='font-size:15px;line-height:1.7'>{tier.get('description','')}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    needs_work = []
    with tab_check:
        checklist = deep_dive.get("checklist", [])
        if checklist:
            ok_count = sum(1 for item in checklist if item.get("status") == "OK")
            total = len(checklist)
            score_pct = ok_count / total
            score_color = "#28a745" if score_pct >= 0.8 else "#f39c12" if score_pct >= 0.5 else "#dc3545"
            score_label = "🟢 このコンセプトは強い" if score_pct >= 0.8 else "🟡 もう少し磨けば戦える" if score_pct >= 0.5 else "🔴 コンセプトを練り直そう"
            st.markdown(
                f"<div style='padding:16px 20px;border-radius:12px;background:#f8f9fa;"
                f"margin-bottom:24px;text-align:center;color:#1a1a1a'>"
                f"<div style='font-size:13px;opacity:0.65;margin-bottom:4px'>コンセプト強度スコア</div>"
                f"<div style='font-size:48px;font-weight:bold;color:{score_color};line-height:1.1'>"
                f"{ok_count}<span style='font-size:24px'> / {total}</span></div>"
                f"<div style='font-size:16px;font-weight:bold;color:{score_color};margin-top:4px'>"
                f"{score_label}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

            needs_work = [item for item in checklist if item.get("status") != "OK"]
            ok_items   = [item for item in checklist if item.get("status") == "OK"]

            if needs_work:
                st.markdown(
                    f"<div style='font-size:17px;font-weight:bold;margin-bottom:10px;color:#c0392b'>"
                    f"⚠️ このまま出すなら要注意（{len(needs_work)}件）</div>",
                    unsafe_allow_html=True,
                )
                for item in needs_work:
                    st.markdown(
                        f"<div style='padding:12px 16px;border-radius:8px;"
                        f"border-left:4px solid #f39c12;background:#fff8e1;"
                        f"margin-bottom:10px;color:#1a1a1a'>"
                        f"<div style='font-weight:bold;font-size:15px;margin-bottom:4px'>"
                        f"⚠️ {item.get('item','')}</div>"
                        f"<div style='font-size:14px;line-height:1.6'>→ {item.get('how','')}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

            if ok_items:
                with st.expander(f"✅ コンセプトの強み（{len(ok_items)}件）", expanded=False):
                    for item in ok_items:
                        st.markdown(
                            f"<div style='padding:10px 14px;border-radius:8px;"
                            f"border-left:4px solid #28a745;background:#f0fff4;"
                            f"margin-bottom:8px;color:#1a1a1a'>"
                            f"<div style='font-weight:bold;font-size:14px;margin-bottom:2px'>"
                            f"✅ {item.get('item','')}</div>"
                            f"<div style='font-size:13px;opacity:0.8'>{item.get('how','')}</div>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
        else:
            st.warning("チェックリストを生成できませんでした。")

        improvements = deep_dive.get("improvements", [])
        if improvements:
            st.divider()
            st.subheader("🔧 改善提案（優先度順）")
            for i, imp in enumerate(improvements, 1):
                st.markdown(
                    f"<div style='padding:12px 14px;border-radius:8px;"
                    f"border-left:4px solid #2c7be5;background:#e8f4f8;"
                    f"margin-bottom:8px;font-size:15px;line-height:1.6;color:#1a1a1a'>"
                    f"<b>{i}.</b> {imp}"
                    f"</div>",
                    unsafe_allow_html=True,
                )

        if checklist:
            st.divider()
            if needs_work:
                st.markdown(
                    f"<div style='font-size:15px;color:#1a1a1a;margin-bottom:8px'>"
                    f"要強化が <b>{len(needs_work)} 件</b> あります。"
                    f"フィードバックをもとに改善版を自動生成できます。</div>",
                    unsafe_allow_html=True,
                )
                regen_label = "🔄 弱点を改善して再生成する"
            else:
                st.markdown(
                    "<div style='font-size:15px;color:#1a1a1a;margin-bottom:8px'>"
                    "全項目クリアしています。内容が気に入らない場合は別パターンを生成できます。</div>",
                    unsafe_allow_html=True,
                )
                regen_label = "🔄 別パターンを生成する"
            if st.session_state.get("regen_btn_loading"):
                st.button("⏳ 生成中...", disabled=True, use_container_width=True, type="primary", key="regen_with_checklist")
                _ph_r = _gen_overlay("改善版を生成中...", "要強化項目のフィードバックを反映しています（30〜60秒）")
                _set_status(_ph_r, "改善版を生成中...")
                try:
                    _set_status(_ph_r, "フィードバックを反映中...")
                    improved = regenerate_with_checklist(
                        idea, product_data, checklist, api_key
                    )
                    _set_status(_ph_r, "仕上げ中...")
                    cache[selected_id] = improved
                    st.session_state["deep_dive_cache"] = cache
                    _save_draft()
                    _ph_r.empty()
                    st.session_state["regen_btn_loading"] = False
                    st.rerun()
                except Exception as e:
                    _ph_r.empty()
                    st.session_state["regen_btn_loading"] = False
                    st.error(f"再生成に失敗しました: {e}")
            else:
                if st.button(
                    regen_label,
                    key="regen_with_checklist",
                    use_container_width=True,
                    type="primary",
                ):
                    st.session_state["regen_btn_loading"] = True
                    st.rerun()

        st.divider()
        _render_pdf_button(product_data, idea, deep_dive, "tab_check")

    st.divider()
    _render_pdf_button(product_data, idea, deep_dive, "bottom")


# ─────────────────────────────────────────────
# ページ: 設定
# ─────────────────────────────────────────────
def page_settings():
    user = st.session_state.get("user", {})
    st.title("⚙️ APIキー設定")
    st.markdown("---")

    col_l, col_c, col_r = st.columns([1, 3, 1])
    with col_c:
        st.markdown("#### Gemini APIキー")
        api_key_input = st.text_input(
            "APIキー",
            type="password",
            value=st.session_state.get("api_key", ""),
            label_visibility="collapsed",
            placeholder="AIzaSy...",
        )

        col_get, col_test = st.columns(2)
        with col_get:
            st.link_button(
                "🔗 APIキーを取得する",
                url="https://aistudio.google.com/apikey",
                use_container_width=True,
            )
        with col_test:
            if st.button("🔌 接続テスト", use_container_width=True):
                if not api_key_input:
                    st.error("APIキーを入力してください。")
                else:
                    with st.spinner("接続確認中..."):
                        try:
                            from google import genai
                            c = genai.Client(api_key=api_key_input)
                            c.models.generate_content(model="gemini-2.5-flash", contents="hi")
                            st.session_state["api_test_result"] = "ok"
                        except Exception as e:
                            st.session_state["api_test_result"] = f"error:{e}"
                    st.rerun()

        result = st.session_state.get("api_test_result")
        if result == "ok":
            st.success("✅ APIキーが有効です")
        elif result and result.startswith("error:"):
            st.error(f"❌ 接続エラー: {result[6:]}")

        st.markdown("---")
        if st.button("💾 保存する", type="primary", use_container_width=True):
            if not api_key_input:
                st.error("APIキーを入力してください。")
            else:
                st.session_state["api_key"] = api_key_input
                auth.update_api_key(user["id"], api_key_input)
                st.session_state["api_test_result"] = None
                st.success("✅ APIキーを保存しました。")


# ─────────────────────────────────────────────
# ページ: 履歴
# ─────────────────────────────────────────────
def page_history():
    user = st.session_state.get("user", {})
    st.title("📚 生成履歴")
    st.markdown("---")

    history = auth.get_idea_history(user["id"])
    if not history:
        st.info("まだ生成履歴がありません。ホームからアイデアを生成すると自動保存されます。")
        return

    st.markdown(f"**{len(history)} 件の履歴があります。**")
    st.caption("アイデアカードをクリックすると詳細が展開されます。")
    st.markdown("")

    for item in history:
        with st.expander(
            f"📦 {item['product_title'][:50]}　｜　{item['created_at'][:16]}　｜　{len(item['ideas'])}件",
            expanded=False,
        ):
            st.caption(f"URL: {item['product_url']}")
            st.markdown("")
            ideas = item["ideas"]
            for idea in ideas:
                diff = idea.get("difficulty", 1)
                icon = _DIFF_ICON.get(diff, "⚪")
                diff_info = DIFFICULTY.get(diff, DIFFICULTY[1])
                bg = _DIFF_COLOR.get(diff, "#ffffff")
                ob = idea.get("one_belief", {})
                col_card, col_btn = st.columns([9, 1])
                with col_card:
                    st.markdown(
                        f"<div style='padding:10px 14px;border-radius:8px;"
                        f"border-left:4px solid #2c7be5;margin-bottom:8px;"
                        f"background:{bg};color:#1a1a1a'>"
                        f"<b>{icon} No.{idea.get('id',0):02d}　{idea.get('title','')}</b>　"
                        f"<span style='font-size:14px;opacity:0.7'>{diff_info['label']} {diff_info['name']}</span><br>"
                        f"<span style='font-size:15px'>{ob.get('full_statement','')}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                with col_btn:
                    if st.button("🔍 深堀", key=f"dd_hist_{item['id']}_{idea.get('id',0)}"):
                        ideas_copy = []
                        for i in item["ideas"]:
                            ic = dict(i)
                            ic["_analyzed"] = bool(ic.get("q1_novelty"))
                            ideas_copy.append(ic)
                        selected = next(
                            (i for i in ideas_copy if i.get("id") == idea.get("id")),
                            ideas_copy[0],
                        )
                        st.session_state["product_data"] = {
                            "title": item["product_title"],
                            "url": item["product_url"],
                            "mode": "main_only",
                            "reviews": [],
                            "similar_data": [],
                            "total_reviews": 0,
                        }
                        st.session_state["ideas"] = ideas_copy
                        st.session_state["url"] = item["product_url"]
                        st.session_state["selected_idea_id"] = idea.get("id")
                        st.session_state["stage"] = "analysis" if selected.get("_analyzed") else "analyzing_idea"
                        st.session_state["deep_dive_cache"] = {}
                        st.switch_page(_home_page)
            st.markdown("")
            if st.button("🗑 この履歴を削除", key=f"del_hist_{item['id']}"):
                auth.delete_history_item(item["id"], user["id"])
                st.rerun()


# ─────────────────────────────────────────────
# ページ: 利用規約
# ─────────────────────────────────────────────
def page_terms():
    st.title("📄 利用規約")
    st.markdown("---")
    st.markdown("""
## 利用規約

**最終更新日: 2026年4月24日**

### 第1条（サービスの目的）
本サービス「クラファン新商品アイデアジェネレーター」は、クラウドファンディングに向けた新商品アイデアの検討を支援することを目的としています。

### 第2条（利用登録）
利用者は、所定の方法により利用登録を行うことで本サービスを利用できます。登録情報は正確な内容を入力してください。

### 第3条（禁止事項）
以下の行為を禁止します。
- 法令または公序良俗に違反する行為
- 本サービスへの不正アクセス・サーバーへの過度な負荷をかける行為
- 他のユーザーへの嫌がらせ・迷惑行為
- 本サービスで生成されたコンテンツの無断転載・二次配布

### 第4条（AIによる生成物の取り扱い）
- 本サービスが生成するアイデアおよびコンテンツはAIによる自動生成物です。
- 商用利用する場合は、知的財産権・法令への適合性を利用者自身でご確認ください。
- 生成内容の正確性・完全性は保証されません。

### 第5条（外部サービスの利用）
本サービスは外部AI APIを使用しています。外部APIの利用にあたっては各サービスの利用規約が適用されます。

### 第6条（プライバシー）
登録されたメールアドレス・氏名・電話番号は、サービス運営および重要なご連絡にのみ使用します。第三者への提供は行いません。

### 第7条（免責事項）
本サービスの利用により生じた損害について、運営者は一切の責任を負いません。

### 第8条（変更・停止）
運営者は事前通知なく本サービスの内容変更・停止を行う場合があります。

---
お問い合わせ: 本サービス管理者まで
    """)


# ─────────────────────────────────────────────
# ページ: 管理者
# ─────────────────────────────────────────────
def page_admin():
    user = st.session_state.get("user", {})
    if not user.get("is_admin"):
        st.error("管理者権限がありません。")
        return

    st.title("👑 管理者ページ")
    st.markdown("---")

    tab_users, tab_smtp = st.tabs(["👥 ユーザー管理", "📧 メール設定（SMTP）"])

    # ── ユーザー管理タブ ──────────────────────────
    with tab_users:
        users     = auth.get_all_users()
        unverified = sum(1 for u in users if not u["is_verified"])
        st.markdown(f"**登録ユーザー数: {len(users)} 名**　｜　未認証: {unverified} 名")
        st.markdown("")

        if not users:
            st.info("登録ユーザーがいません。")
        else:
            header_cols = st.columns([3, 2, 2, 2, 1, 1, 1, 2])
            for col, label in zip(
                header_cols,
                ["メールアドレス", "名前", "電話番号", "登録日時", "認証", "管理者", "メルマガ", "操作"],
            ):
                col.markdown(f"**{label}**")
            st.divider()

            for u in users:
                cols = st.columns([3, 2, 2, 2, 1, 1, 1, 2])
                cols[0].write(u["email"])
                cols[1].write(u["name"])
                cols[2].write(u["phone"] or "—")
                cols[3].write(u["created_at"][:16])
                cols[4].write("✅" if u["is_verified"] else "❌ 未認証")
                cols[5].write("👑" if u["is_admin"] else "—")
                cols[6].write("✅" if u["newsletter_consent"] else "—")

                with cols[7]:
                    if not u["is_verified"]:
                        if st.button("認証する", key=f"verify_{u['id']}", type="primary",
                                     use_container_width=True):
                            auth.set_user_verified(u["id"], True)
                            st.success(f"{u['name']} を認証済みにしました")
                            st.rerun()
                    else:
                        if st.button("認証取消", key=f"unverify_{u['id']}", type="secondary",
                                     use_container_width=True):
                            auth.set_user_verified(u["id"], False)
                            st.rerun()

                if not u["is_admin"] and u["id"] != user.get("id"):
                    with st.expander(f"⚠️ {u['name']} を削除", expanded=False):
                        st.warning(f"**{u['email']}** のアカウントとセッションを完全削除します。この操作は取り消せません。")
                        if st.button("削除する", key=f"delete_{u['id']}", type="primary"):
                            auth.delete_user(u["id"])
                            st.success(f"{u['name']} を削除しました")
                            st.rerun()

    # ── SMTP設定タブ ──────────────────────────────
    with tab_smtp:
        st.markdown("#### メール送信設定（SMTP）")
        st.caption(
            "設定するとユーザー登録時の認証メール・パスワードリセットメールが自動送信されます。"
            "Gmailの場合は「アプリパスワード」（16桁）を使用してください。"
        )
        st.markdown("")

        cfg = auth._get_smtp_config()

        col_l, col_r = st.columns(2)
        with col_l:
            smtp_host = st.text_input("SMTPホスト", value=cfg["host"],
                                      placeholder="smtp.gmail.com")
            smtp_user = st.text_input("SMTPユーザー（送信元メールアドレス）",
                                      value=cfg["user"],
                                      placeholder="example@gmail.com")
            smtp_from = st.text_input("送信元表示名メール（省略可）",
                                      value=cfg["from"],
                                      placeholder="example@gmail.com")
        with col_r:
            smtp_port = st.number_input("SMTPポート", value=cfg["port"],
                                        min_value=1, max_value=65535, step=1)
            smtp_pass = st.text_input("SMTPパスワード（アプリパスワード）",
                                      type="password",
                                      value=cfg["pass"],
                                      placeholder="xxxx xxxx xxxx xxxx")

        col_save, col_test = st.columns(2)
        with col_save:
            if st.button("💾 保存する", type="primary", use_container_width=True):
                auth.set_setting("smtp_host", smtp_host)
                auth.set_setting("smtp_port", str(smtp_port))
                auth.set_setting("smtp_user", smtp_user)
                auth.set_encrypted_setting("smtp_pass", smtp_pass)
                auth.set_setting("smtp_from", smtp_from or smtp_user)
                st.success("✅ SMTP設定を保存しました。")

        with col_test:
            test_to = st.text_input("テスト送信先メール", placeholder="test@example.com",
                                     label_visibility="collapsed")
            if st.button("📨 テスト送信", use_container_width=True):
                if not test_to:
                    st.error("テスト送信先を入力してください。")
                else:
                    ok, err = auth._send_email(
                        test_to,
                        "【クラファンツール】SMTPテスト送信",
                        "SMTPの設定が正常に完了しています。",
                        "<p>SMTPの設定が正常に完了しています。</p>",
                    )
                    if ok:
                        st.success(f"✅ {test_to} にテストメールを送信しました。")
                    else:
                        st.error(f"❌ 送信失敗: {err}")

        st.markdown("---")
        st.markdown("##### Gmail アプリパスワードの取得手順")
        st.markdown("""
1. [Googleアカウント](https://myaccount.google.com/) にログイン
2. 「セキュリティ」→「2段階認証プロセス」を有効化
3. 「アプリパスワード」→「その他」→ 名前を入力して生成
4. 表示された16桁のパスワードを上の「SMTPパスワード」に入力
        """)


# ─────────────────────────────────────────────
# ルーティング
# ─────────────────────────────────────────────

# パスワードリセット画面（ログイン不要）
if reset_token:
    show_reset_password_form(reset_token)
    st.stop()

user = st.session_state.get("user")

# ログイン直後のクッキー書き込み（st.rerun()後の最初のレンダリングで実行）
if "_session_token" in st.session_state:
    _cookie_set("st_session", st.session_state.pop("_session_token"))

# Cookieからの自動ログイン
if user is None:
    token = _cookie_get("st_session")
    if token:
        saved_user = auth.validate_session(token)
        if saved_user:
            st.session_state["user"] = saved_user
            saved_key = auth.get_user_api_key(saved_user["id"])
            if saved_key:
                st.session_state["api_key"] = saved_key
            _restore_draft(saved_user)
            user = saved_user

if user is None:
    show_auth()
    st.stop()

# サイドバーを完全に非表示
st.markdown("""
<style>
[data-testid="stSidebar"] { display: none; }
[data-testid="stSidebarCollapsedControl"] { display: none; }
/* ベースフォントサイズ */
.stMarkdown p, .stMarkdown li, .stMarkdown span { font-size: 17px; }
/* 見出し */
.stMarkdown h1 { font-size: 2.2rem !important; }
.stMarkdown h2 { font-size: 1.8rem !important; }
.stMarkdown h3 { font-size: 1.5rem !important; }
.stMarkdown h4 { font-size: 1.25rem !important; font-weight: 700 !important; }
.stMarkdown h5 { font-size: 1.1rem !important; font-weight: 600 !important; }
/* ウィジェットラベル */
.stTextInput label { font-size: 17px !important; font-weight: 600 !important; }
.stSelectbox label { font-size: 17px !important; font-weight: 600 !important; }
.stCheckbox label { font-size: 15px !important; }
.stRadio label, .stRadio span { font-size: 17px !important; }
.stRadio > label:first-child { font-size: 17px !important; font-weight: 600 !important; }
.stSelectbox [data-baseweb="select"] { font-size: 16px; }
.stTextInput input { font-size: 16px !important; }
.stAlert p { font-size: 17px; }
.stCaption p { font-size: 14px !important; }
.streamlit-expanderHeader p { font-size: 17px !important; }
[data-baseweb="tab"] button { font-size: 16px !important; }
.stInfo p, .stWarning p, .stSuccess p, .stError p { font-size: 17px; }
/* ページリンク */
[data-testid="stPageLink"] p { font-size: 15px !important; }
/* 入力フォームの枠を消して難易度フィルターと一体化 */
[data-testid="stForm"] {
  border: none !important;
  padding: 0 !important;
  box-shadow: none !important;
}
/* カード選択ボタン（高さ・行間）*/
[data-testid="stHorizontalBlock"] [data-testid="stBaseButton-secondary"],
[data-testid="stHorizontalBlock"] [data-testid="stBaseButton-primary"] {
  min-height: 36px !important;
  white-space: pre-wrap !important;
  line-height: 1.4 !important;
  cursor: help !important;
}
/* 選択中カードを柔らかい透け赤に（列レイアウト内のみ） */
[data-testid="stHorizontalBlock"] [data-testid="stBaseButton-primary"] {
  background-color: rgba(220, 80, 80, 0.12) !important;
  border: 1.5px solid rgba(220, 80, 80, 0.45) !important;
  color: #e89090 !important;
}
[data-testid="stHorizontalBlock"] [data-testid="stBaseButton-primary"]:hover {
  background-color: rgba(220, 80, 80, 0.2) !important;
  color: #f0a8a8 !important;
}
/* チェックボックスの横間隔を広げる */
[data-testid="stCheckbox"] {
  padding-right: 8px !important;
}
/* ホームナビボタンをページリンク風に */
[data-testid="stButton"][key="nav_home_btn"] button,
div[data-testid="column"] button[kind="secondary"]:has(div p) {
  background: none !important;
  border: none !important;
  padding: 4px 8px !important;
  color: inherit !important;
  font-size: 15px !important;
  font-weight: 400 !important;
  box-shadow: none !important;
  cursor: pointer;
}
</style>
""", unsafe_allow_html=True)

# ページ定義
_home_page     = st.Page(page_home,     title="ホーム",   icon="🏠", default=True)
_settings_page = st.Page(page_settings, title="設定",     icon="⚙️")
_history_page  = st.Page(page_history,  title="履歴",     icon="📚")
_terms_page    = st.Page(page_terms,    title="利用規約", icon="📄")
_pages         = [_home_page, _settings_page, _history_page, _terms_page]

_admin_page = None
if user.get("is_admin"):
    _admin_page = st.Page(page_admin, title="管理者", icon="👑")
    _pages.append(_admin_page)

pg = st.navigation(_pages, position="hidden")

# ─── トップナビゲーションバー ───────────────────
col_left, col_right = st.columns([7, 3])

with col_left:
    lc1, lc2, lc3, lc4, lc5, _ = st.columns([1.2, 1.2, 1.2, 1.5, 1.2, 3.7])
    with lc1:
        if st.button("🏠 ホーム", key="nav_home_btn"):
            st.session_state["stage"] = "input"
            _save_draft()  # DBのstageも"input"に更新してリロード対策
            st.switch_page(_home_page)
    with lc2:
        st.page_link(_settings_page, label="設定",     icon="⚙️")
    with lc3:
        st.page_link(_history_page,  label="履歴",     icon="📚")
    with lc4:
        st.page_link(_terms_page,    label="利用規約", icon="📄")
    with lc5:
        if _admin_page:
            st.page_link(_admin_page, label="管理者",  icon="👑")

with col_right:
    rc1, rc2 = st.columns([3, 2])
    with rc1:
        st.markdown(
            f"<div style='text-align:right;padding-top:6px;font-size:15px;opacity:0.7'>"
            f"👤 {user.get('name', '')}</div>",
            unsafe_allow_html=True,
        )
    with rc2:
        if st.button("ログアウト", use_container_width=True):
            token = _cookie_get("st_session")
            if token:
                auth.delete_session(token)
            _cookie_delete("st_session")
            st.stop()

st.divider()

pg.run()
