"""
main.py: クラウドファンディング新商品アイデアジェネレーター（Streamlit UI）

【2ステージ構成】
  Stage 1: URL入力 → スクレイピング → 10アイデアカード表示（1分以内）
  Stage 2: アイデア選択 → 詳細ページ（4タブ: 経歴/セールス文章/アプローチ/商品）
           → PDF出力

フレームワーク: The 16-Word Sales Letter™ (Evaldo Albuquerque)

起動方法:
    streamlit run main.py
"""

import os
import json
from datetime import datetime
import streamlit as st
from dotenv import load_dotenv

from scraper import scrape_all, extract_asin
from analyzer import (
    analyze_and_generate_ideas,
    generate_deep_dive_content,
    generate_pdf_bytes,
    get_difficulty_options,
    DIFFICULTY,
)

load_dotenv()

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
    ("stage", "input"),          # "input" | "ideas" | "deepdive"
    ("product_data", None),
    ("ideas", None),
    ("url", ""),
    ("selected_idea_id", None),
    ("deep_dive_cache", {}),     # {idea_id: deep_dive_dict}
    ("api_key", ""),
]:
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ─────────────────────────────────────────────
# サイドバー
# ─────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 設定")

    api_key_input = st.text_input(
        "Gemini APIキー",
        type="password",
        value=st.session_state.get("api_key") or os.getenv("GEMINI_API_KEY", ""),
        help="https://aistudio.google.com/apikey で取得できます（無料）",
    )
    if api_key_input:
        st.session_state["api_key"] = api_key_input

    if api_key_input:
        if st.button("🔌 接続確認", use_container_width=True):
            try:
                from google import genai
                c = genai.Client(api_key=api_key_input)
                c.models.generate_content(model="gemini-2.5-flash", contents="hi")
                st.success("✅ APIキーが有効です")
            except Exception as e:
                st.error(f"❌ 接続エラー: {e}")

    st.divider()
    st.markdown("### 📊 難易度の定義")
    for k, v in DIFFICULTY.items():
        st.markdown(f"**{v['label']} {v['name']}**  \n{v['desc']}")
        st.markdown("")

    st.divider()
    st.markdown("### 📖 16-Word Sales Letter™")
    st.info(
        "**One Belief の公式:**\n\n"
        "「[新しい機会] が [顧客の欲求] への鍵であり、\n"
        "[新メカニズム] でしか手に入らない」\n\n"
        "**10の質問:**\n"
        "Q1 新規性  Q2 ベネフィット  Q3 証拠\n"
        "Q4 真の問題  Q5 共通の敵  Q6 緊急性\n"
        "Q7 信頼  Q8 仕組み  Q9 オファー  Q10 クロージング"
    )

    st.divider()
    st.markdown("### ⚠️ Amazonブロックについて")
    st.warning(
        "Amazonはスクレイピングをブロックすることがあります。\n\n"
        "対処法:\n"
        "1. しばらく待って再試行\n"
        "2. VPNを使用\n"
        "3. ScraperAPI などを利用"
    )


# ─────────────────────────────────────────────
# ヘルパー関数
# ─────────────────────────────────────────────
_DIFF_ICON = {1: "🟢", 2: "🔵", 3: "🟡", 4: "🟠", 5: "🔴"}
_DIFF_COLOR = {
    1: "#d4edda", 2: "#cce5ff", 3: "#fff3cd",
    4: "#ffe0b2", 5: "#ffcccc",
}


def _idea_card(idea: dict, col):
    """アイデアカードをカラムに描画する。詳細ボタンが押されたら True を返す。"""
    diff = idea.get("difficulty", 1)
    icon = _DIFF_ICON.get(diff, "⚪")
    diff_info = DIFFICULTY.get(diff, DIFFICULTY[1])
    bg = _DIFF_COLOR.get(diff, "#ffffff")
    ob = idea.get("one_belief", {})

    with col:
        with st.container(border=True):
            st.markdown(
                f"<div style='font-size:11px;color:#666;margin-bottom:4px'>"
                f"{icon} {diff_info['label']} {diff_info['name']}　"
                f"｜　製造コスト: {idea.get('estimated_cost', '—')}"
                f"</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div style='font-weight:bold;font-size:15px;margin-bottom:6px'>"
                f"No.{idea['id']:02d}　{idea['title']}"
                f"</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div style='font-size:12px;color:#444;background:{bg};"
                f"padding:6px 10px;border-radius:6px;margin-bottom:6px'>"
                f"💬 {ob.get('full_statement', '—')}"
                f"</div>",
                unsafe_allow_html=True,
            )
            st.caption(f"🔑 {idea.get('q1_novelty', '—')[:60]}")
            if st.button("詳細を見る →", key=f"detail_{idea['id']}", use_container_width=True):
                return True
    return False


# ─────────────────────────────────────────────
# Stage: input（入力フォーム）
# ─────────────────────────────────────────────
def show_input():
    st.title("💡 クラファン新商品アイデアジェネレーター")
    st.caption(
        "Amazon商品URLを貼るだけで、**16-Word Sales Letter™** フレームワーク準拠の"
        "新商品アイデア10個を1分以内で生成。気になるアイデアはさらに深掘りできます。"
    )

    with st.form("main_form"):
        url = st.text_input(
            "🔗 Amazon 商品URL",
            value=st.session_state.get("url", ""),
            placeholder="https://www.amazon.co.jp/dp/XXXXXXXXXX",
        )

        col_diff, col_sim, col_hint = st.columns([2, 1, 2])
        with col_diff:
            diff_options = get_difficulty_options()
            selected_diff = st.selectbox(
                "📊 難易度フィルター",
                options=list(diff_options.keys()),
                format_func=lambda x: diff_options[x],
                index=0,
            )
        with col_sim:
            st.markdown("　")
            include_similar = st.checkbox(
                "類似品も含める",
                value=True,
                help=(
                    "チェックあり: 同ページに表示されている類似品（最大4件）の"
                    "★1〜★2レビューも収集します。\n"
                    "チェックなし: 対象商品★1×200件（高速）。"
                ),
            )
        with col_hint:
            st.markdown("")
            st.caption(
                "★1=製造1万円以内　★2=5万円以内　★3=10万円以内\n"
                "★4=金型など新規設備必要　★5=50万円以上"
            )
            if include_similar:
                st.caption("🟡 類似品あり: 深い調査（時間がかかります）")
            else:
                st.caption("⚡ 類似品なし: 高速モード")

        submitted = st.form_submit_button(
            "🔍 アイデアを生成する",
            use_container_width=True,
            type="primary",
        )

    if not submitted:
        return

    # バリデーション
    if not url:
        st.error("Amazon商品URLを入力してください。")
        return
    if "amazon" not in url.lower():
        st.error("AmazonのURLを入力してください。")
        return
    if not extract_asin(url):
        st.error("URLからASIN（商品ID）を抽出できませんでした。商品ページのURLを確認してください。")
        return

    api_key = st.session_state.get("api_key") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        st.error(
            "Gemini APIキーが未設定です。\n"
            "サイドバーで入力するか、.env ファイルに GEMINI_API_KEY を設定してください。"
        )
        return

    # 実行
    progress_bar = st.progress(0)
    status_text = st.empty()

    def update_progress(msg: str, pct: int):
        progress_bar.progress(pct)
        status_text.text(f"⏳ {msg}")

    try:
        product_data = scrape_all(url, include_similar=include_similar, progress_callback=update_progress)
        update_progress("AIがアイデアを生成中...", 85)
        diff_filter = selected_diff if selected_diff > 0 else None
        ideas = analyze_and_generate_ideas(product_data, diff_filter, api_key)
        progress_bar.progress(100)
        status_text.empty()
    except RuntimeError as e:
        progress_bar.empty()
        status_text.empty()
        st.error(f"スクレイピングエラー: {e}")
        return
    except Exception as e:
        progress_bar.empty()
        status_text.empty()
        st.error(f"エラーが発生しました: {e}")
        return

    # ステージ遷移
    st.session_state["product_data"] = product_data
    st.session_state["ideas"] = ideas
    st.session_state["url"] = url
    st.session_state["stage"] = "ideas"
    st.session_state["deep_dive_cache"] = {}
    st.rerun()


# ─────────────────────────────────────────────
# Stage: ideas（10アイデアカード）
# ─────────────────────────────────────────────
def show_ideas():
    product_data = st.session_state["product_data"]
    ideas = st.session_state["ideas"]

    # ── ヘッダー ──────────────────────────────────
    st.title("💡 新商品アイデア 10選")

    col_back, col_title = st.columns([1, 5])
    with col_back:
        if st.button("← 条件を変更する"):
            st.session_state["stage"] = "input"
            st.rerun()

    st.subheader(f"📦 分析商品: {product_data['title']}")

    # ── 収集サマリー ─────────────────────────────
    mode = product_data.get("mode", "main_only")
    main_rev_count = len(product_data.get("reviews", []))
    similar_data = product_data.get("similar_data", [])
    sim_rev_total = sum(len(s["reviews"]) for s in similar_data)

    if mode == "main_only":
        st.info(
            f"**収集モード:** ⚡ 対象商品のみ　｜　"
            f"取得レビュー: **{main_rev_count}件**　｜　"
            f"Amazon総数: {product_data.get('total_reviews', 0)}件"
        )
    else:
        st.info(
            f"**収集モード:** 🔍 類似品含む　｜　"
            f"類似品 **{len(similar_data)}商品** × ★1 各50件　｜　"
            f"合計取得: **{sim_rev_total}件**"
        )

    # ── 参照URL一覧 ─────────────────────────────
    sources = product_data.get("sources", [])
    if sources:
        with st.expander("🔗 参照したURL一覧", expanded=False):
            _icon_map = {"main": "🟢", "similar": "🟡"}
            for s in sources:
                icon = _icon_map.get(s.get("type", ""), "⚪")
                label = s.get("type_label", "")
                title_s = s.get("title", "不明")[:45]
                count = s.get("review_count", 0)
                url_s = s.get("url", "")
                asin_s = s.get("asin", "")
                total_amz = s.get("total_on_amazon", 0)

                ca, cb, cc = st.columns([3, 2, 2])
                with ca:
                    st.markdown(f"{icon} **[{label}]** {title_s}")
                    st.caption(f"ASIN: `{asin_s}`　｜　[Amazonで確認]({url_s})")
                with cb:
                    st.markdown(f"収集レビュー数: **{count}件**")
                with cc:
                    if total_amz > 0 and count > 0:
                        coverage = min(100, round(count / total_amz * 100))
                        st.progress(coverage / 100, text=f"収集率 {coverage}%")
                    elif count == 0:
                        st.caption("ページ情報のみ")
                st.divider()

    # ── カードグリッド ───────────────────────────
    st.divider()
    st.markdown("#### 気になるアイデアの **詳細を見る →** をクリックしてください")
    st.caption("16-Word Sales Letter™ フレームワーク準拠　｜　One Belief + Q1〜Q10")

    selected_id = None
    rows = [ideas[i:i+2] for i in range(0, len(ideas), 2)]
    for row in rows:
        cols = st.columns(2)
        for idea, col in zip(row, cols):
            if _idea_card(idea, col):
                selected_id = idea["id"]

    if selected_id is not None:
        st.session_state["selected_idea_id"] = selected_id
        st.session_state["stage"] = "deepdive"
        st.rerun()

    # ── JSONダウンロード ─────────────────────────
    st.divider()
    st.download_button(
        label="📥 アイデア全件をJSONでダウンロード",
        data=json.dumps(ideas, ensure_ascii=False, indent=2),
        file_name=f"ideas_{extract_asin(st.session_state.get('url', 'unknown'))}.json",
        mime="application/json",
    )


# ─────────────────────────────────────────────
# Stage: deepdive（詳細ページ）
# ─────────────────────────────────────────────
def show_deepdive():
    product_data = st.session_state["product_data"]
    ideas = st.session_state["ideas"]
    selected_id = st.session_state["selected_idea_id"]
    api_key = st.session_state.get("api_key") or os.getenv("GEMINI_API_KEY")

    # 選択されたアイデアを取得
    idea = next((i for i in ideas if i["id"] == selected_id), None)
    if idea is None:
        st.error("アイデアが見つかりません。")
        st.session_state["stage"] = "ideas"
        st.rerun()
        return

    # ── ナビゲーション ────────────────────────────
    col_back, col_list = st.columns([1, 4])
    with col_back:
        if st.button("← アイデア一覧に戻る"):
            st.session_state["stage"] = "ideas"
            st.rerun()

    # ── ヘッダー ──────────────────────────────────
    diff = idea.get("difficulty", 1)
    diff_info = DIFFICULTY.get(diff, DIFFICULTY[1])
    icon = _DIFF_ICON.get(diff, "⚪")
    ob = idea.get("one_belief", {})

    st.markdown(
        f"## {icon} No.{idea['id']:02d}　{idea['title']}",
    )
    st.caption(
        f"分析商品: {product_data['title'][:50]}　｜　"
        f"難易度: {diff_info['label']} {diff_info['name']}　｜　"
        f"製造コスト: {idea.get('estimated_cost', '—')}　｜　"
        f"使用LLM: claude-sonnet-4-6"
    )
    st.info(f"💬 One Belief: 「{ob.get('full_statement', '—')}」")

    # ── ディープダイブコンテンツの生成（キャッシュ） ─
    cache = st.session_state.get("deep_dive_cache", {})
    if selected_id not in cache:
        with st.spinner("詳細コンテンツを生成中...（30〜60秒かかる場合があります）"):
            try:
                deep_dive = generate_deep_dive_content(idea, product_data, api_key)
                cache[selected_id] = deep_dive
                st.session_state["deep_dive_cache"] = cache
            except Exception as e:
                st.error(f"詳細コンテンツの生成に失敗しました: {e}")
                return
    else:
        deep_dive = cache[selected_id]

    # ── タブ ────────────────────────────────────
    tab_keiji, tab_sales, tab_approach, tab_product = st.tabs([
        "📝 経歴（キャッチコピー）",
        "📄 セールス文章",
        "🚀 アプローチ方法",
        "📦 商品プロダクト",
    ])

    # ── Tab 1: 経歴（キャッチコピー）──────────────
    with tab_keiji:
        st.subheader("🎯 キャッチコピー 3案")
        st.caption("クラウドファンディングページのメインキャッチとして使えます")

        catchcopy_list = deep_dive.get("catchcopy", [])
        for i, cc in enumerate(catchcopy_list, 1):
            st.markdown(
                f"<div style='background:#f0f4f8;padding:14px 18px;"
                f"border-radius:8px;border-left:4px solid #2c7be5;"
                f"font-size:16px;font-weight:bold;margin-bottom:10px'>"
                f"案{i}　{cc}"
                f"</div>",
                unsafe_allow_html=True,
            )

        st.divider()
        st.subheader("🎯 One Belief 詳細")
        c1, c2, c3 = st.columns(3)
        c1.metric("新しい機会", ob.get("new_opportunity", "—"))
        c2.metric("顧客の欲求", ob.get("desire", "—"))
        c3.metric("新メカニズム", ob.get("new_mechanism", "—"))

        st.divider()
        st.subheader("🛠 新規性アドバイス")
        for i, adv in enumerate(idea.get("novelty_advice", []), 1):
            st.markdown(f"**{i}.** {adv}")

    # ── Tab 2: セールス文章 ──────────────────────
    with tab_sales:
        st.subheader("📄 クラウドファンディング用セールスレター")
        st.caption("16-Word Sales Letter™ フレームワークに基づいた全文セールス文章")

        sales_letter = deep_dive.get("sales_letter", "")
        if sales_letter:
            # セクション別に表示
            sections = sales_letter.split("\n\n")
            for section in sections:
                section = section.strip()
                if not section:
                    continue
                if section.startswith("【") and "】" in section:
                    # セクションヘッダー
                    header_end = section.index("】") + 1
                    header = section[:header_end]
                    body = section[header_end:].strip()
                    st.markdown(f"**{header}**")
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
                ("q1_novelty",      "Q1｜新規性"),
                ("q2_benefit",      "Q2｜ベネフィット"),
                ("q3_proof_abt",    "Q3｜証拠(ABT)"),
                ("q4_real_problem", "Q4｜真の問題"),
                ("q5_enemy",        "Q5｜共通の敵"),
            ]:
                st.markdown(f"**{label}**")
                st.write(idea.get(q, "—"))
        with right:
            for q, label in [
                ("q6_urgency",   "Q6｜緊急性"),
                ("q7_trust",     "Q7｜信頼"),
                ("q8_mechanism", "Q8｜メカニズム"),
                ("q9_offer",     "Q9｜オファー"),
                ("q10_pushpull", "Q10｜クロージング"),
            ]:
                st.markdown(f"**{label}**")
                if q in ("q6_urgency", "q10_pushpull"):
                    highlight_color = "#fff3cd" if q == "q6_urgency" else "#fdecea"
                    border_color = "#f39c12" if q == "q6_urgency" else "#e74c3c"
                    prefix = "🚀" if q == "q6_urgency" else "💥"
                    st.markdown(
                        f"<div style='background:{highlight_color};padding:8px 12px;"
                        f"border-radius:6px;border-left:4px solid {border_color};'>"
                        f"{prefix} {idea.get(q, '—')}</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.write(idea.get(q, "—"))

    # ── Tab 3: アプローチ方法 ─────────────────────
    with tab_approach:
        st.subheader("🚀 マーケティング・アプローチ方法")
        approach = deep_dive.get("approach", {})

        if approach.get("overview"):
            st.info(f"**戦略概要:** {approach['overview']}")

        approach_sections = [
            ("sns",        "📱 SNS戦略"),
            ("influencer", "🤝 インフルエンサー活用"),
            ("pr",         "📰 PR・メディア戦略"),
            ("cf_launch",  "🎯 クラファン立ち上げ戦略"),
            ("timeline",   "📅 ローンチタイムライン"),
        ]
        for key, label in approach_sections:
            val = approach.get(key, "")
            if val:
                with st.expander(label, expanded=True):
                    st.write(val)

    # ── Tab 4: 商品プロダクト ─────────────────────
    with tab_product:
        st.subheader("📦 商品プロダクト概要")
        product = deep_dive.get("product", {})

        if product.get("summary"):
            st.markdown("**商品概要**")
            st.markdown(
                f"<div style='background:#f8f9fa;padding:14px 18px;"
                f"border-radius:8px;border-left:4px solid #28a745;"
                f"font-size:14px;line-height:1.7;margin-bottom:12px'>"
                f"{product['summary']}"
                f"</div>",
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
                    f"<div style='background:#fff3cd;padding:10px 14px;"
                    f"border-radius:6px;border-left:4px solid #f39c12;'>"
                    f"🎯 {product['cf_goal']}"
                    f"</div>",
                    unsafe_allow_html=True,
                )

        if product.get("production_notes"):
            st.divider()
            st.markdown("**製造・調達上の注意点**")
            st.write(product["production_notes"])

        # 再生成ボタン（商品概要のみ再生成）
        st.divider()
        if st.button("🔄 商品概要を再生成する", use_container_width=False):
            with st.spinner("再生成中..."):
                try:
                    new_dd = generate_deep_dive_content(idea, product_data, api_key)
                    cache[selected_id] = new_dd
                    st.session_state["deep_dive_cache"] = cache
                    st.rerun()
                except Exception as e:
                    st.error(f"再生成に失敗しました: {e}")

    # ── PDFダウンロード ──────────────────────────
    st.divider()
    col_pdf, col_json = st.columns([2, 1])
    with col_pdf:
        try:
            generated_at = datetime.now().strftime("%Y年%m月%d日 %H:%M")
            pdf_bytes = generate_pdf_bytes(
                product_data=product_data,
                idea=idea,
                deep_dive=deep_dive,
                generated_at=generated_at,
                model_name="claude-sonnet-4-6",
            )
            st.download_button(
                label="📄 PDFレポートをダウンロード（クラファン企画書）",
                data=pdf_bytes,
                file_name=f"cf_report_{extract_asin(st.session_state.get('url', 'unknown'))}_idea{idea['id']:02d}.pdf",
                mime="application/pdf",
                use_container_width=True,
                type="primary",
            )
        except Exception as e:
            st.warning(f"PDF生成に失敗しました: {e}")
            st.info("reportlab をインストールしてください: `pip install reportlab`")

    with col_json:
        all_content = {
            "idea": idea,
            "deep_dive": deep_dive,
            "product_title": product_data.get("title", ""),
            "generated_at": datetime.now().isoformat(),
        }
        st.download_button(
            label="📥 JSONダウンロード",
            data=json.dumps(all_content, ensure_ascii=False, indent=2),
            file_name=f"deepdive_{extract_asin(st.session_state.get('url', 'unknown'))}_idea{idea['id']:02d}.json",
            mime="application/json",
            use_container_width=True,
        )


# ─────────────────────────────────────────────
# ルーティング
# ─────────────────────────────────────────────
stage = st.session_state.get("stage", "input")

if stage == "input":
    show_input()
elif stage == "ideas":
    show_ideas()
elif stage == "deepdive":
    show_deepdive()
else:
    st.session_state["stage"] = "input"
    st.rerun()
