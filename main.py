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
    generate_deep_dive_content,
    generate_pdf_bytes,
    DIFFICULTY,
)

load_dotenv()
auth.init_db()

# ─────────────────────────────────────────────
# Cookie manager（エラー時は graceful degradation）
# ─────────────────────────────────────────────
try:
    import extra_streamlit_components as stx

    @st.cache_resource
    def _get_cookie_manager():
        return stx.CookieManager(key="session_cookie")

    _COOKIE_AVAILABLE = True
except Exception:
    _COOKIE_AVAILABLE = False

    class _DummyCookieManager:
        def get(self, key):
            return None
        def set(self, *a, **kw):
            pass
        def delete(self, *a, **kw):
            pass

    def _get_cookie_manager():
        return _DummyCookieManager()


def _cookie_get(key: str):
    try:
        return _get_cookie_manager().get(key)
    except Exception:
        return None


def _cookie_set(key: str, value: str, cookie_key: str):
    try:
        _get_cookie_manager().set(key, value, key=cookie_key)
    except Exception:
        pass


def _cookie_delete(key: str, cookie_key: str):
    try:
        _get_cookie_manager().delete(key, key=cookie_key)
    except Exception:
        pass


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


def _idea_card(idea: dict, col):
    diff = idea.get("difficulty", 1)
    icon = _DIFF_ICON.get(diff, "⚪")
    diff_info = DIFFICULTY.get(diff, DIFFICULTY[1])
    bg = _DIFF_COLOR.get(diff, "#ffffff")
    ob = idea.get("one_belief", {})

    with col:
        with st.container(border=True):
            st.markdown(
                f"<div style='font-size:12px;margin-bottom:6px'>"
                f"{icon} {diff_info['label']} {diff_info['name']}　"
                f"｜　製造コスト: {idea.get('estimated_cost', '—')}"
                f"</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div style='font-weight:bold;font-size:20px;margin-bottom:8px'>"
                f"No.{idea.get('id', 0):02d}　{idea.get('title', '（タイトルなし）')}"
                f"</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div style='font-size:13px;color:#1a1a1a;background:{bg};"
                f"padding:8px 12px;border-radius:6px;margin-bottom:8px'>"
                f"💡 {ob.get('full_statement', '—')}"
                f"</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div style='font-size:13px;margin-bottom:4px'>"
                f"🔑 {idea.get('q1_novelty', '—')[:60]}"
                f"</div>",
                unsafe_allow_html=True,
            )
            if idea.get("evidence"):
                st.markdown(
                    f"<div style='font-size:12px;margin-bottom:4px'>"
                    f"📝 根拠レビュー: {idea['evidence'][:60]}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            if st.button("🔍 深堀する", key=f"detail_{idea['id']}",
                         use_container_width=True, type="primary"):
                return True
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
                        st.session_state["user"] = user
                        saved_key = auth.get_user_api_key(user["id"])
                        if saved_key:
                            st.session_state["api_key"] = saved_key
                        token = auth.create_session(user["id"], days=30)
                        _cookie_set("st_session", token, "set_login")
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
    elif stage == "deepdive":
        _show_deepdive()
    else:
        st.session_state["stage"] = "input"
        st.rerun()


def _show_input():
    st.title("💡 クラファン新商品アイデアジェネレーター")
    st.caption("Amazon商品URLを貼るだけで新商品アイデア10個を生成。気になるアイデアはさらに深掘りできます。")

    api_key_check = st.session_state.get("api_key") or os.getenv("GEMINI_API_KEY")
    if not api_key_check:
        st.warning("⚠️ Gemini APIキーが未設定です。左メニューの「設定」から設定してください。")

    if st.session_state.get("last_error"):
        st.error(f"前回のエラー: {st.session_state['last_error']}")
        if st.button("🔄 もう一度試す", type="secondary"):
            st.session_state["last_error"] = ""
            st.rerun()

    with st.form("main_form"):
        url = st.text_input(
            "🔗 Amazon 商品URL",
            value=st.session_state.get("url", ""),
            placeholder="https://www.amazon.co.jp/dp/XXXXXXXXXX",
        )

        col_diff, col_sim, col_mode = st.columns([2, 2, 2])

        with col_diff:
            st.markdown(
                "**📊 難易度フィルター**　"
                "<span style='font-size:11px;opacity:0.6'>（未選択 = すべて）</span>",
                unsafe_allow_html=True,
            )
            selected_diffs = []
            for k, v in DIFFICULTY.items():
                if st.checkbox(f"★{k} {v['name']}", help=v["desc"], key=f"diff_cb_{k}"):
                    selected_diffs.append(k)

        with col_sim:
            _sim_options = {
                0:  "0件（対象商品のみ）⚡ 約30秒",
                5:  "5件（+40件）約1分",
                10: "10件（+80件）約1.5分",
                20: "20件（+160件）約2〜3分",
            }
            sim_count = st.selectbox(
                "🔍 類似品レビュー数",
                options=list(_sim_options.keys()),
                format_func=lambda x: _sim_options[x],
                index=1,
            )
            st.caption("※ 20件超はAmazonのボット検知リスクが高まるため上限を20件に設定しています。")

        with col_mode:
            st.markdown("")
            if sim_count == 0:
                st.caption("⚡ 対象商品のみ: 高速モード")
            else:
                st.caption("🟡 類似品あり: 深い調査（時間がかかります）")

        st.markdown("##### 📝 レビュー収集モード")
        review_mode = st.radio(
            "レビュー収集モード",
            options=["amazon", "gemini"],
            format_func=lambda x: (
                "🛒 Amazonレビューのみ（実レビュー・高速）"
                if x == "amazon" else
                "🔍 Gemini Web検索レビュー込み（大量収集・低速）"
            ),
            horizontal=True,
            label_visibility="collapsed",
        )
        if review_mode == "gemini":
            st.caption(
                "※ GeminiがWeb全体（Amazon・楽天・価格.com・ブログ等）を検索してレビュー・口コミを収集します。"
                "AIによる要約を含みます。商品あたり約100件追加。収集に時間がかかります。"
            )

        submitted = st.form_submit_button(
            "🔍 アイデアを生成する", use_container_width=True, type="primary"
        )

    if not submitted:
        return

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
        st.error("Gemini APIキーが未設定です。左メニューの「設定」から入力してください。")
        return

    progress_bar = st.progress(0)
    status_text  = st.empty()

    def update_progress(msg, pct):
        progress_bar.progress(pct)
        status_text.text(f"⏳ {msg}")

    try:
        product_data = scrape_all(
            url,
            max_similar_products=sim_count,
            progress_callback=update_progress,
            api_key=api_key,
            use_gemini_reviews=(review_mode == "gemini"),
        )
        update_progress("AIがアイデアを生成中...", 85)
        diff_filter = selected_diffs if selected_diffs else None
        ideas = analyze_and_generate_ideas(product_data, diff_filter, api_key)
        progress_bar.progress(100)
        status_text.empty()
    except RuntimeError as e:
        progress_bar.empty(); status_text.empty()
        st.error(f"スクレイピングエラー: {e}")
        st.session_state["last_error"] = str(e)
        return
    except Exception as e:
        progress_bar.empty(); status_text.empty()
        st.error(f"エラーが発生しました: {e}")
        st.session_state["last_error"] = str(e)
        return

    st.session_state["product_data"] = product_data
    st.session_state["ideas"]        = ideas
    st.session_state["url"]          = url
    st.session_state["stage"]        = "ideas"
    st.session_state["deep_dive_cache"] = {}

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
    product_data = st.session_state["product_data"]
    ideas        = st.session_state["ideas"]

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
        review_breakdown = f"Amazon **{amz_cnt}件** + Web検索 **{gem_cnt}件** = 合計 **{main_rev_count}件**"
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

    selected_id = None
    for idea in ideas:
        col = st.columns(1)[0]
        if _idea_card(idea, col):
            selected_id = idea["id"]

    if selected_id is not None:
        st.session_state["selected_idea_id"] = selected_id
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

    if st.button("← アイデア一覧に戻る"):
        st.session_state["stage"] = "ideas"
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
        st.markdown(
            "<div style='border:2px solid #2c7be5;padding:24px;"
            "border-radius:12px;text-align:center;margin-bottom:16px'>"
            "<div style='font-size:28px;margin-bottom:8px'>🔍</div>"
            "<div style='font-size:18px;font-weight:bold;margin-bottom:6px'>"
            "深堀り中です...</div>"
            "<div style='font-size:14px'>セールスレター・戦略を生成しています（30〜60秒）</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        progress = st.progress(0, text="アイデアを分析中...")
        try:
            progress.progress(30, text="コアメッセージを構築中...")
            deep_dive = generate_deep_dive_content(idea, product_data, api_key)
            progress.progress(90, text="仕上げ中...")
            cache[selected_id] = deep_dive
            st.session_state["deep_dive_cache"] = cache
            progress.progress(100, text="完了！")
            st.rerun()
        except Exception as e:
            st.error(f"生成に失敗しました: {e}")
            return
    else:
        deep_dive = cache[selected_id]

    tab_keiji, tab_sales, tab_approach, tab_product = st.tabs([
        "📝 キャッチコピー", "📄 セールス文章", "🚀 アプローチ方法", "📦 商品プロダクト",
    ])

    with tab_keiji:
        st.subheader("🎯 キャッチコピー 3案")
        for i, cc in enumerate(deep_dive.get("catchcopy", []), 1):
            st.markdown(
                f"<div style='padding:14px 18px;border-radius:8px;"
                f"border-left:4px solid #2c7be5;"
                f"font-size:17px;font-weight:bold;margin-bottom:12px'>"
                f"案{i}　{cc}</div>",
                unsafe_allow_html=True,
            )
        st.divider()
        st.subheader("🎯 One Belief 詳細")
        for label, key in [("新しい機会", "new_opportunity"), ("顧客の欲求", "desire"), ("新メカニズム", "new_mechanism")]:
            st.markdown(
                f"<div style='margin-bottom:10px'>"
                f"<span style='font-size:11px;opacity:0.7'>{label}</span><br>"
                f"<span style='font-size:16px;font-weight:bold'>{ob.get(key, '—')}</span></div>",
                unsafe_allow_html=True,
            )
        st.divider()
        st.subheader("🛠 新規性アドバイス")
        for i, adv in enumerate(idea.get("novelty_advice", []), 1):
            st.markdown(f"**{i}.** {adv}")

    with tab_sales:
        st.subheader("📄 クラウドファンディング用セールスレター")
        sales_letter = deep_dive.get("sales_letter", "")
        if sales_letter:
            for section in sales_letter.split("\n\n"):
                section = section.strip()
                if not section:
                    continue
                if section.startswith("【") and "】" in section:
                    header_end = section.index("】") + 1
                    st.markdown(f"**{section[:header_end]}**")
                    body = section[header_end:].strip()
                    if body:
                        st.write(body)
                else:
                    st.write(section)
                st.markdown("")
        else:
            st.warning("セールス文章を生成できませんでした。")

        st.divider()
        st.subheader("📋 Q1〜Q10（設計根拠）")
        left, right = st.columns(2)
        with left:
            for q, label in [
                ("q1_novelty", "Q1｜新規性"), ("q2_benefit", "Q2｜ベネフィット"),
                ("q3_proof_abt", "Q3｜証拠(ABT)"), ("q4_real_problem", "Q4｜真の問題"),
                ("q5_enemy", "Q5｜共通の敵"),
            ]:
                st.markdown(f"**{label}**")
                st.write(idea.get(q, "—"))
        with right:
            for q, label in [
                ("q6_urgency", "Q6｜緊急性"), ("q7_trust", "Q7｜信頼"),
                ("q8_mechanism", "Q8｜メカニズム"), ("q9_offer", "Q9｜オファー"),
                ("q10_pushpull", "Q10｜クロージング"),
            ]:
                st.markdown(f"**{label}**")
                if q in ("q6_urgency", "q10_pushpull"):
                    border_color = "#f39c12" if q == "q6_urgency" else "#e74c3c"
                    prefix = "🚀" if q == "q6_urgency" else "💥"
                    st.markdown(
                        f"<div style='padding:8px 12px;border-radius:6px;"
                        f"border-left:4px solid {border_color};'>"
                        f"{prefix} {idea.get(q, '—')}</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.write(idea.get(q, "—"))

        if idea.get("evidence"):
            st.divider()
            st.markdown(
                f"<div style='padding:10px 14px;border-radius:6px;"
                f"border-left:4px solid #6c757d;font-size:13px;opacity:0.85'>"
                f"📝 <b>根拠レビュー:</b> {idea['evidence']}</div>",
                unsafe_allow_html=True,
            )

    with tab_approach:
        st.subheader("🚀 マーケティング・アプローチ方法")
        approach = deep_dive.get("approach", {})
        if approach.get("overview"):
            st.info(f"**戦略概要:** {approach['overview']}")
        for key, label in [
            ("sns", "📱 SNS戦略"), ("influencer", "🤝 インフルエンサー活用"),
            ("pr", "📰 PR・メディア戦略"), ("cf_launch", "🎯 クラファン立ち上げ戦略"),
            ("timeline", "📅 ローンチタイムライン"),
        ]:
            if approach.get(key):
                with st.expander(label, expanded=True):
                    st.write(approach[key])

    with tab_product:
        st.subheader("📦 商品プロダクト概要")
        product = deep_dive.get("product", {})
        if product.get("summary"):
            st.markdown("**商品概要**")
            st.markdown(
                f"<div style='padding:14px 18px;border-radius:8px;"
                f"border-left:4px solid #28a745;font-size:14px;line-height:1.7;margin-bottom:12px'>"
                f"{product['summary']}</div>",
                unsafe_allow_html=True,
            )
        c1, c2 = st.columns(2)
        with c1:
            if product.get("features"):
                st.markdown("**主な特徴・機能**")
                for f in product["features"]:
                    st.markdown(f"✅ {f}")
            if product.get("target_customer"):
                st.divider()
                st.markdown("**ターゲット顧客**")
                st.write(product["target_customer"])
        with c2:
            if product.get("price_strategy"):
                st.markdown("**価格・CF割引戦略**")
                st.write(product["price_strategy"])
            if product.get("cf_goal"):
                st.divider()
                st.markdown("**CF目標金額の目安**")
                st.markdown(
                    f"<div style='padding:10px 14px;border-radius:6px;"
                    f"border-left:4px solid #f39c12;'>"
                    f"🎯 {product['cf_goal']}</div>",
                    unsafe_allow_html=True,
                )
        if product.get("production_notes"):
            st.divider()
            st.markdown("**製造・調達上の注意点**")
            st.write(product["production_notes"])
        st.divider()
        if st.button("🔄 商品概要を再生成する"):
            with st.spinner("再生成中..."):
                try:
                    cache[selected_id] = generate_deep_dive_content(idea, product_data, api_key)
                    st.session_state["deep_dive_cache"] = cache
                    st.rerun()
                except Exception as e:
                    st.error(f"再生成に失敗しました: {e}")

    st.divider()
    try:
        pdf_bytes = generate_pdf_bytes(
            product_data=product_data, idea=idea, deep_dive=deep_dive,
            generated_at=datetime.now().strftime("%Y年%m月%d日 %H:%M"),
            model_name="Gemini 2.5 Flash",
        )
        st.download_button(
            label="📄 PDFレポートをダウンロード（クラファン企画書）",
            data=pdf_bytes,
            file_name=f"cf_report_{extract_asin(st.session_state.get('url','unknown'))}_idea{idea['id']:02d}.pdf",
            mime="application/pdf",
            use_container_width=True,
            type="primary",
        )
    except Exception as e:
        st.warning(f"PDF生成に失敗しました: {e}")


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
                st.markdown(
                    f"<div style='padding:10px 14px;border-radius:8px;"
                    f"border-left:4px solid #2c7be5;margin-bottom:8px;"
                    f"background:{bg}'>"
                    f"<b>{icon} No.{idea.get('id',0):02d}　{idea.get('title','')}</b>　"
                    f"<span style='font-size:12px;opacity:0.7'>{diff_info['label']} {diff_info['name']}</span><br>"
                    f"<span style='font-size:13px'>{ob.get('full_statement','')}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
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
本サービスはGemini API（Google）を使用しています。外部APIの利用にあたっては各サービスの利用規約が適用されます。

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
                auth.set_setting("smtp_pass", smtp_pass)
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
            user = saved_user

if user is None:
    show_auth()
    st.stop()

# サイドバーを完全に非表示
st.markdown("""
<style>
[data-testid="stSidebar"] { display: none; }
[data-testid="stSidebarCollapsedControl"] { display: none; }
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
        st.page_link(_home_page,     label="ホーム",   icon="🏠")
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
            f"<div style='text-align:right;padding-top:6px;font-size:13px;opacity:0.7'>"
            f"👤 {user.get('name', '')}</div>",
            unsafe_allow_html=True,
        )
    with rc2:
        if st.button("ログアウト", use_container_width=True):
            token = _cookie_get("st_session")
            if token:
                auth.delete_session(token)
            _cookie_delete("st_session", "del_logout")
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()

st.divider()

pg.run()
