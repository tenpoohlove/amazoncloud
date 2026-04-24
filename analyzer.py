"""
analyzer.py: レビューを分析し、新商品アイデア10個を生成する（Claude API使用）

参考書籍: The 16-Word Sales Letter™ by Evaldo Albuquerque
  ──────────────────────────────────────────────────
  16ワードの核心:
    「成約するコピーの秘訣は、One Beliefを定義し、10の質問に答えることだ」

  One Belief の公式:
    「[新しい機会] が [顧客の欲求] への鍵であり、[新メカニズム] でしか手に入らない」

  10の質問:
    Q1 なぜこれは今まで見たものと違うのか？       → 新規性・ドーパミン放出
    Q2 自分にとって何のメリットがあるか？         → 大きな約束
    Q3 本物だという証拠は？                       → ABT構造ストーリー（And/But/Therefore）
    Q4 今まで何が自分を妨げてきたのか？           → 真の問題（Q1の逆転・希望を与える）
    Q5 誰/何が悪いのか？                          → 共通の敵（Us vs Them）
    Q6 なぜ今すぐ行動しなければならないか？       → Either-Or緊急性・FOMO
    Q7 なぜあなたを信頼できるのか？               → 信頼構築（3ストーリー）
    Q8 それはどのように機能するのか？             → 新メカニズムの仕組み・論理的証拠
    Q9 どうすれば始められるか？                   → ノーブレイナーオファー・価値スタック
    Q10 失うものは何か？                          → プッシュプル・3択提示・痛みの絵

  ──────────────────────────────────────────────────
  商品開発ツールへの適用:
    - Q1〜Q8: 新商品コンセプトの設計に使う
    - Q9: クラウドファンディングのオファー設計に使う
    - Q10: CFページのクロージング文に使う
    - 「新規性アドバイス」: Q1・Q4・Q8を組み合わせた独自分析
"""

import json
import os
import re
from google import genai

GEMINI_MODEL = "gemini-2.5-flash"


# ─────────────────────────────────────────────
# 難易度定義
# ─────────────────────────────────────────────
DIFFICULTY = {
    1: {
        "label": "★1",
        "name": "超低コスト",
        "desc": "製造コスト1万円以内。既存素材・部品の組み合わせのみ。金型不要。",
    },
    2: {
        "label": "★2",
        "name": "低コスト",
        "desc": "製造コスト5万円以内。小ロット生産可能。既製品部品で対応。",
    },
    3: {
        "label": "★3",
        "name": "中コスト",
        "desc": "製造コスト10万円以内。専門加工・素材が必要だが既存設備で対応可能。",
    },
    4: {
        "label": "★4",
        "name": "高難度",
        "desc": "金型・専用治具などの新規設備作成が必要。量産前提の設計が必要。",
    },
    5: {
        "label": "★5",
        "name": "超高コスト",
        "desc": "製造コスト50万円以上。高度技術・設備・長い開発期間が必要。",
    },
}


# ─────────────────────────────────────────────
# プロンプト生成
# ─────────────────────────────────────────────
def _format_reviews(reviews: list, max_count: int = 200, max_chars: int = 200) -> str:
    """
    レビューリストを整形する。
    reviews は [{"star": int, "text": str}] または [str] の両方に対応。
    ★1・★2 を先頭にソートして表示する。
    """
    if not reviews:
        return "  （レビューなし）"

    # 辞書形式に統一
    normalized = []
    for r in reviews:
        if isinstance(r, dict):
            normalized.append(r)
        else:
            normalized.append({"star": 0, "text": str(r)})

    # ★1・★2 を先頭にソート（低評価優先）
    sorted_reviews = sorted(normalized, key=lambda x: x.get("star", 99))

    lines = []
    for i, r in enumerate(sorted_reviews[:max_count]):
        star = r.get("star", 0)
        text = r.get("text", "")[:max_chars]
        star_mark = f"★{star}" if star > 0 else "★?"
        lines.append(f"  [{i+1}] {star_mark} {text}")

    return "\n".join(lines)


def _build_prompt(product_data: dict, difficulty_filter: int | None) -> str:
    title = product_data.get("title", "不明")
    url = product_data.get("url", "")
    bullets = "\n".join(f"  ・{b}" for b in product_data.get("bullets", [])[:10])
    description = product_data.get("description", "")[:300]
    strategy = product_data.get("strategy", "")
    total_on_amazon = product_data.get("total_reviews", 0)

    include_similar = product_data.get("include_similar", True)
    mode = product_data.get("mode", "main_only")
    main_reviews = product_data.get("reviews", [])

    # ── モード別: レビューテキストと収集説明 ────────
    reviews_text = _format_reviews(main_reviews, max_count=200)
    if mode == "main_only":
        strategy_note = (
            f"モード: 対象商品のみ（チェックなし）\n"
            f"  収集: 対象商品ページ表示分 → 取得 {len(main_reviews)}件"
        )
        similar_text = "（類似品レビューは収集していません）"
    else:
        strategy_note = (
            f"モード: 類似品含む（チェックあり）\n"
            f"  収集: 対象商品 {len(main_reviews)}件 + 類似品4商品 各8件程度"
        )
        similar_items = product_data.get("similar_data", [])
        similar_text = ""
        for i, sim in enumerate(similar_items, 1):
            sim_fmt = _format_reviews(sim["reviews"], max_count=50)
            similar_text += (
                f"\n  ▶ 類似品{i}「{sim['title']}」({sim['url']})\n"
                f"     収集件数: {len(sim['reviews'])}件（★1 優先）\n"
                f"{sim_fmt}\n"
            )
        if not similar_text:
            similar_text = "（類似品レビューを取得できませんでした）"

    # difficulty_filter は int | list[int] | None のいずれか
    if isinstance(difficulty_filter, int):
        difficulty_filter = [difficulty_filter] if difficulty_filter > 0 else []

    if difficulty_filter:
        if len(difficulty_filter) == 1:
            d = DIFFICULTY[difficulty_filter[0]]
            diff_instruction = (
                f"【難易度指定】{d['label']} {d['name']} のみ → {d['desc']}"
            )
            count_note = f"すべて {d['label']} で統一（10個）"
        else:
            labels = " と ".join(DIFFICULTY[d]["label"] for d in difficulty_filter)
            descs = "　".join(
                f"{DIFFICULTY[d]['label']}: {DIFFICULTY[d]['desc']}"
                for d in difficulty_filter
            )
            per = max(1, 10 // len(difficulty_filter))
            counts = " + ".join(
                f"{DIFFICULTY[d]['label']}×{per}" for d in difficulty_filter
            )
            diff_instruction = f"【難易度指定】{labels} のみ\n  {descs}"
            count_note = counts + "（合計10個になるよう均等に割り当てること）"
    else:
        diff_instruction = "【難易度】★1〜★5 をバランスよく（各2件ずつ）"
        count_note = "★1×2・★2×2・★3×2・★4×2・★5×2"

    return f"""あなたはクラウドファンディング向け新商品開発の専門家です。
日本人ユーザーが使うツールなので、すべての出力は必ず日本語で書いてください。

以下のAmazon商品データを分析し、「16-Word Sales Letter™」フレームワーク（Evaldo Albuquerque著）
に基づいた新商品アイデアを10個提案してください。

【カテゴリ制約・最重要】提案するアイデア10個は必ず「{title}」と完全に同じカテゴリ・用途に限定すること。
カテゴリが異なる商品（アクセサリー・関連品・別ジャンル）は絶対に提案しないこと。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【分析対象商品】
商品名: {title}
URL: {url}
特徴:
{bullets or "  （特徴情報なし）"}
説明文: {description or "（なし）"}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【レビュー収集状況】
{strategy_note}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【対象商品のレビュー（★1 優先・低評価 = 不満・痛点の宝庫）】
{reviews_text}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【類似品のレビュー】
{similar_text}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{diff_instruction}
{count_note}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

═══════════════════════════════════════════
【16-Word Sales Letter™ フレームワーク 完全版】
═══════════════════════════════════════════

■ One Belief（商品コンセプトの核）の公式
  「[新しい機会] が [顧客の欲求] への鍵であり、[新メカニズム] でしか手に入らない」

■ 10の質問（全問必須）
  Q1 なぜこれは今まで見たものと違うのか？
     → 脳内ドーパミン放出のための独自性。「新しいものを見た！」という感覚を作る。
     → USP（独自の売り）を1行で。既存商品にない要素を明確に。

  Q2 自分にとって何のメリットがあるか？
     → 最初の1ページで大きな約束をする。顧客の感情的ベネフィットを具体的に。
     → 「最も重要な言葉は『あなた』」（ダール・カーネギー）

  Q3 本物だという証拠は？（ABT構造）
     → And（背景）＋ But（問題・転換）＋ Therefore（解決・結論）の1文ストーリー。
     → 事実の羅列より物語で包む（カーネギーメロン大研究: 物語は寄付額2倍）

  Q4 今まで何が自分を妨げてきたのか？（真の問題）
     → Q1の逆転: 「新機会が○○だから成功する」→「今まで○○でなかったから失敗してきた」
     → 顧客の過去の失敗を正当化し、希望を与える。「あなたのせいじゃない」

  Q5 誰/何が悪いのか？（共通の敵）
     → 顧客の既存の不満・信念に沿った「共通の敵」を設定（大企業・業界慣行・旧技術）
     → Us vs Them: 仲間意識を作り、オキシトシン（信頼ホルモン）を放出させる

  Q6 なぜ今すぐ行動しなければならないか？（緊急性）
     → Either-Orの仮想ジレンマ: 「今すぐ行動するか、チャンスを逃すか」
     → FOMO（Fear of Missing Out）を活用。CF特有の「先着・期間限定・社会トレンド」

  Q7 なぜあなたを信頼できるのか？（信頼構築）
     → 3つのストーリーライン（どれか1つまたは組み合わせ）:
       ① 「同じ靴を履いてきた」: 顧客と同じ苦労を経験し、解決した実体験
       ② 「ロビンフッド」: 富裕層・内部者だけの秘密を暴露して公開する
       ③ 「専門家」: 著書・メディア露出・実績・資格で権威を示す

  Q8 それはどのように機能するのか？（メカニズム説明）
     → 新メカニズムの仕組みをロジカルに説明。「なるほど、そういうことか」と納得させる。
     → 既存の信念・常識を活用して説明するとより強力（ゴート博士の睾丸移植の話）
     → ABTストーリー構造で退屈にならないように包む

  Q9 どうすれば始められるか？（オファー設計）
     → 価値とコストの「差」を最大化するノーブレイナーオファー
     → 価値スタック: 各特典の価値を積み上げ、最終価格を「お得すぎる」と感じさせる
     → ボーナス特典は「メイン商品を完璧に補完するもの」に限定（Todd Brown S.I.N.オファー）
     → リスク逆転（返金保証）で購入障壁をゼロに近づける

  Q10 失うものは何か？（プッシュプル・クロージング）
     → 押し売りせず、3択を提示して「自分で決めた感」を演出（プッシュ）
     → 行動しなかった場合の痛みを絵で見せる（プル）
     → 「あなたはどちらのタイプか？夢を語るだけの人か、行動する人か」

═══════════════════════════════════════════
【分析手順（Chain-of-Thought）】
═══════════════════════════════════════════

STEP 1 — レビューから痛点を抽出
  ・「対象商品（{title}）のレビュー」から不満・不便・要望を最低15個抽出（最優先）
  ・次に「類似品のレビュー」から共通する痛点を追加（補足として使用）
  ・抽出した痛点はすべて「{title}」カテゴリの商品改善に直結するものに限定

STEP 2 — ニーズのクラスタリング
  ・「機能・デザイン・価格・耐久性・使いやすさ・付属品・梱包」等でグループ化

STEP 3 — One Belief の設計
  ・各クラスターに対し [新機会] × [欲求] × [新メカニズム] を定義
  ・※ Q4は必ずQ1の逆転になっているか確認すること

STEP 4 — 10の質問に回答
  ・Q1〜Q10すべてに答える（すべて日本語で）

STEP 5 — 新規性アドバイス
  ・「どのような新規性を足せばよいか」を具体的に3点アドバイス
  ・素材・製法・デザイン・機能・梱包・ターゲット変更など多角的に

STEP 6 — Constitutional Review（自己審査）
  □ 提案した10個すべてが「{title}」と同じカテゴリ・用途か確認
  □ 無関係なカテゴリ・アクセサリー・別ジャンルのアイデアがあれば除外し同カテゴリで置き換える
  □ 実際のレビューから根拠を取れているか
  □ One Beliefの3要素が揃っているか
  □ Q4がQ1の逆転になっているか
  □ Q8にロジカルな説明があるか
  □ Q9にオファー要素（価値・ボーナス・保証）があるか
  □ Q10がプッシュプル構造になっているか
  □ 製造コスト目安は現実的か
  □ すべて日本語で書かれているか

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【出力形式】
以下のJSON配列のみを返してください（マークダウン・コードブロック不要）。
すべての値を日本語で書いてください。

[
  {{
    "id": 1,
    "difficulty": 1,
    "difficulty_label": "★1",
    "difficulty_name": "超低コスト",
    "title": "商品タイトル（25文字以内・日本語）",
    "estimated_cost": "製造コスト目安（例: 3,000〜8,000円/個）",

    "one_belief": {{
      "new_opportunity": "新しい機会（30文字以内・日本語）",
      "desire": "顧客の欲求（30文字以内・日本語）",
      "new_mechanism": "新メカニズム（30文字以内・日本語）",
      "full_statement": "「〇〇を使えば、△△できる。それを実現するのが□□だ」のように、普通の日本語で読んで自然な一文（80文字以内）"
    }},

    "q1_novelty": "なぜ他と違うのか・USP（60文字以内）",
    "q2_benefit": "顧客への大きな約束・ベネフィット（60文字以内）",
    "q3_proof_abt": "And＋But＋Thereforeの証拠ストーリー（100文字以内）",
    "q4_real_problem": "今まで解決できなかった真の問題（60文字以内）",
    "q5_enemy": "共通の敵（40文字以内）",
    "q6_urgency": "今すぐ支援すべきCF緊急性メッセージ（60文字以内）",
    "q7_trust": "信頼構築ストーリーのヒント（60文字以内）",
    "q8_mechanism": "新メカニズムの仕組み説明（80文字以内）",
    "q9_offer": "ノーブレイナーオファーの設計案（80文字以内）",
    "q10_pushpull": "プッシュプル・クロージング文（80文字以内）",

    "novelty_advice": [
      "新規性アドバイス1（具体的に）",
      "新規性アドバイス2（具体的に）",
      "新規性アドバイス3（具体的に）"
    ],

    "evidence": "根拠となったレビュー抜粋（40文字以内）"
  }}
]

必ず10個・すべて日本語・JSON配列のみで返してください。"""


# ─────────────────────────────────────────────
# メイン分析関数
# ─────────────────────────────────────────────
def analyze_and_generate_ideas(
    product_data: dict,
    difficulty_filter=None,
    api_key: str | None = None,
) -> list[dict]:
    """
    レビューデータを分析し、16-Word Sales Letter™ フレームワーク完全版
    （Q1〜Q10 + One Belief + 新規性アドバイス）の新商品アイデア10個を返す。
    すべての出力は日本語。

    Args:
        product_data: scraper.scrape_all() の返り値
        difficulty_filter: 1〜5 で指定（None = すべて）
        api_key: Anthropic API キー（None なら環境変数から取得）

    Returns:
        list of idea dicts (10件)
    """
    _api_key = api_key or os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=_api_key)

    prompt = _build_prompt(product_data, difficulty_filter)

    message = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )

    raw = message.text.strip()

    try:
        ideas = json.loads(raw)
    except json.JSONDecodeError:
        clean = re.sub(r"```(?:json)?", "", raw).strip()
        m = re.search(r"\[.*\]", clean, re.DOTALL)
        if not m:
            raise ValueError(f"AIの返答をJSONとして解析できませんでした:\n{raw[:500]}")
        ideas = json.loads(m.group())

    if not isinstance(ideas, list):
        raise ValueError("AIがリスト形式で返答しませんでした")

    if len(ideas) < 10:
        print(f"[analyzer] 警告: {len(ideas)} 件のみ生成（期待値: 10）")

    # 難易度順（★1→★5）でソートしてIDを振り直し、10件に統一
    ideas.sort(key=lambda x: x.get("difficulty", 99))
    ideas = ideas[:10]

    # 難易度分布チェック: フィルター未指定時に欠けている難易度を補完リクエスト
    if not difficulty_filter:
        present = {idea.get("difficulty") for idea in ideas}
        missing = [d for d in range(1, 6) if d not in present]
        if missing:
            fill_prompt = _build_prompt(product_data, missing)
            fill_msg = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=fill_prompt,
            )
            try:
                fill_raw = fill_msg.text.strip()
                fill_ideas = json.loads(fill_raw)
                if not isinstance(fill_ideas, list):
                    raise ValueError
            except Exception:
                clean2 = re.sub(r"```(?:json)?", "", fill_msg.text.strip()).strip()
                m2 = re.search(r"\[.*\]", clean2, re.DOTALL)
                fill_ideas = json.loads(m2.group()) if m2 else []

            # 不足難易度の分だけ置き換え（元の10件から重複難易度の末尾を削除して追加）
            for fi in fill_ideas:
                fd = fi.get("difficulty")
                if fd in missing:
                    # 同じ難易度が今の ideas にあれば最後の1件を削除
                    for j in range(len(ideas) - 1, -1, -1):
                        if ideas[j].get("difficulty") == fd:
                            ideas.pop(j)
                            break
                    ideas.append(fi)
                    missing = [d for d in missing if d != fd]
                    if not missing:
                        break

            ideas.sort(key=lambda x: x.get("difficulty", 99))
            ideas = ideas[:10]
            if missing:
                print(f"[analyzer] 難易度補完後も不足: {missing}")

    for i, idea in enumerate(ideas, 1):
        idea["id"] = i

    return ideas


def get_difficulty_options() -> dict:
    return {
        0: "すべての難易度（★1〜★5）",
        **{k: f"{v['label']} {v['name']} — {v['desc']}" for k, v in DIFFICULTY.items()},
    }


# ─────────────────────────────────────────────
# ディープダイブ（Makuakeパターン クラファンページ生成）
# ─────────────────────────────────────────────
def generate_deep_dive_content(
    idea: dict,
    product_data: dict,
    api_key: str | None = None,
) -> dict:
    """
    Makuakeトップ30成功パターンに基づいてクラファンページ構成を生成する。

    Returns:
        {
          "catchcopy": ["案1", "案2", "案3"],
          "page_sections": [
            {"section": int, "name": str, "purpose": str, "content": str, "media": str},
            ... 10 items
          ],
          "returns": {
            "early_bird": {"label": str, "discount": str, "limit": str, "price": str, "description": str},
            "standard":   {"label": str, "discount": str, "price": str, "description": str},
            "premium":    {"label": str, "price": str, "description": str}
          },
          "checklist": [{"item": str, "status": "OK"|"要強化", "how": str}, ... 10 items],
          "improvements": ["提案1", "提案2", "提案3"]
        }
    """
    _api_key = api_key or os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=_api_key)

    ob = idea.get("one_belief", {})
    diff = idea.get("difficulty", 1)
    diff_info = DIFFICULTY.get(diff, DIFFICULTY[1])
    title_main = product_data.get("title", "不明")

    # Makuake参考データ取得（エラーは無視）
    try:
        from scraper import fetch_makuake_references
        category_words = [
            t for t in re.split(r'[\s\[\]【】（）()「」、。・/\-_]+', title_main)
            if len(t) >= 3 and re.search(r'[ぁ-んァ-ン一-龥]', t)
        ][:2]
        makuake_keyword = " ".join(category_words) if category_words else ""
        makuake_refs = fetch_makuake_references(keyword=makuake_keyword, n=2)
        makuake_context = "\n".join(
            f"【参考{i+1}】{r['title']}\n{r['catch']}\n{r['body']}"
            for i, r in enumerate(makuake_refs)
        ) if makuake_refs else ""
    except Exception:
        makuake_context = ""

    makuake_ref_section = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【Makuake売れ筋商品ページの参考（文章トーン・構成を参考にすること）】
{makuake_context}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""" if makuake_context else ""

    prompt = f"""あなたはMakuakeで歴代トップ30の実績を持つクラウドファンディング専門コピーライターです。
すべての出力は必ず日本語で書いてください。
フレームワーク名・ツール名（「16-Word」「One Belief」等）は文章内に一切記載しないこと。

以下のアイデアデータをもとに、Makuakeトップ30案件の成功パターンに従った
クラウドファンディングページ構成を生成してください。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【選択されたアイデア】
No.{idea.get('id', 1)}: {idea.get('title', '')}
難易度: {diff_info['label']} {diff_info['name']}  製造コスト: {idea.get('estimated_cost', '')}

核心コンセプト: 「{ob.get('full_statement', '')}」
  新しい機会: {ob.get('new_opportunity', '')}
  顧客の欲求: {ob.get('desire', '')}
  新メカニズム: {ob.get('new_mechanism', '')}

Q1 新規性: {idea.get('q1_novelty', '')}
Q2 ベネフィット: {idea.get('q2_benefit', '')}
Q3 証拠(ABT): {idea.get('q3_proof_abt', '')}
Q4 真の問題: {idea.get('q4_real_problem', '')}
Q5 共通の敵: {idea.get('q5_enemy', '')}
Q6 緊急性: {idea.get('q6_urgency', '')}
Q7 信頼: {idea.get('q7_trust', '')}
Q8 メカニズム: {idea.get('q8_mechanism', '')}
Q9 オファー: {idea.get('q9_offer', '')}
Q10 クロージング: {idea.get('q10_pushpull', '')}

新規性アドバイス:
{chr(10).join(f'  ・{a}' for a in idea.get('novelty_advice', []))}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【元商品情報】
商品名: {title_main}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{makuake_ref_section}
【Makuakeトップ30 成功パターン（必ず従うこと）】

■ 売れるページの本質構造
  ・コピー = 価値の要約（冒頭1文で価値を言い切る）
  ・本文 = 証拠の連鎖（数値・受賞・実績・比較・動画説明）
  ・オファー = 行動の後押し（限定価格・先行販売・数量制限）

■ 最頻出の成功パターン（上位7つ）
  1. 社会的証明（応援購入額・サポーター数・メディア掲載） ← 最重要
  2. 強いベネフィット（一文で価値が伝わるキャッチ）
  3. デザイン性の訴求
  4. 体験価値の提示（利用シーン動画/GIF）
  5. 価格アンカー + 限定性
  6. 動画/GIFによる視覚証拠
  7. 権威性（受賞・認証・著名パートナー）

■ キャッチコピーの鉄則
  ✗ 機能羅列・「高品質」「便利」などの抽象ワード単独使用
  ✓ 「〇〇しながら△△できる」→ ベネフィット型
  ✓ 「もう〇〇で悩まない」→ 問題解決型
  ✓ 「〇〇が変わる、生活が変わる」→ 変化訴求型

以下のJSONを返してください。マークダウン・コードブロック不要。

{{
  "catchcopy": [
    "キャッチコピー案1（20〜40文字。ベネフィット訴求型）",
    "キャッチコピー案2（問題解決・共感型）",
    "キャッチコピー案3（変化訴求型）"
  ],

  "page_sections": [
    {{
      "section": 1,
      "name": "ファーストビュー",
      "purpose": "強いキャッチで商品の全体像を伝える",
      "content": "実際に使えるコピー・文章（150〜400字。具体的に書くこと。読んで『これだ！』と思わせる）",
      "media": "推奨メディア形式（例: 商品全体の動画30秒、使用シーンGIF）"
    }},
    {{
      "section": 2,
      "name": "課題提起（共感）",
      "purpose": "ターゲットの痛みを言語化し自分事にさせる",
      "content": "読者の日常の不満・もどかしさを具体的に言語化した文章（150〜300字）",
      "media": "共感を呼ぶライフスタイル画像、ビフォー画像"
    }},
    {{
      "section": 3,
      "name": "解決策の提示",
      "purpose": "なぜこのアイデアが唯一の答えなのかを示す",
      "content": "既存品の限界を暴き、このアイデアの革新性を伝える文章（150〜300字）",
      "media": "比較表、仕組み図解、アニメーションGIF"
    }},
    {{
      "section": 4,
      "name": "機能・ベネフィット",
      "purpose": "具体的な価値をPoint形式で整理する",
      "content": "Point1〜Point5形式で各機能とそのベネフィットを具体的に（各50〜100字）",
      "media": "各Pointに対応した製品写真またはアイコン"
    }},
    {{
      "section": 5,
      "name": "技術的証拠・差別化",
      "purpose": "数値・比較・認証で本物感を担保する",
      "content": "数値データ・特許・認証・他社比較などの客観的証拠（150〜300字）",
      "media": "比較グラフ、認証マーク、スペック比較表"
    }},
    {{
      "section": 6,
      "name": "利用シーン別ベネフィット",
      "purpose": "シーン×ベネフィットで購入後の生活を想像させる",
      "content": "3〜4つの具体的な利用シーンとそれぞれのベネフィット（各100字）",
      "media": "各シーンの利用シーン写真またはGIF"
    }},
    {{
      "section": 7,
      "name": "信頼形成",
      "purpose": "受賞・認証・開発背景で信頼性を構築する",
      "content": "開発背景・開発者の想い・実績・受賞歴を語る文章（150〜300字）",
      "media": "開発者写真、受賞トロフィー、メディア掲載ロゴ"
    }},
    {{
      "section": 8,
      "name": "社会的証明",
      "purpose": "サポーター・メディア・口コミで安心感を生む",
      "content": "支援者コメント・メディア掲載・テスター声を想定した文章（150〜300字）",
      "media": "応援コメント画像、メディアロゴ、SNS投稿スクリーンショット"
    }},
    {{
      "section": 9,
      "name": "オファー設計",
      "purpose": "今すぐ支援する理由を価格と限定性で作る",
      "content": "アーリーバード特典・先着数量制限・CF限定価格の訴求文（150〜250字）",
      "media": "価格比較バナー、残り数量カウントダウン"
    }},
    {{
      "section": 10,
      "name": "FAQ・保証・CTA",
      "purpose": "不安を除去し購入導線を完成させる",
      "content": "よくある質問3〜5件と回答、保証内容、最後の応援購入CTAボタン文（150〜250字）",
      "media": "FAQ アコーディオン、CTAボタン（目立つ配色）"
    }}
  ],

  "returns": {{
    "early_bird": {{
      "label": "アーリーバード",
      "discount": "35%OFF",
      "limit": "先着XX名様限定",
      "price": "XX,XXX円（通常XX,XXX円）",
      "description": "このリターンの内容・付帯特典・配送時期（100字以内）"
    }},
    "standard": {{
      "label": "通常先行",
      "discount": "20%OFF",
      "price": "XX,XXX円（通常XX,XXX円）",
      "description": "内容・特典（100字以内）"
    }},
    "premium": {{
      "label": "プレミアム",
      "price": "XX,XXX円",
      "description": "上位リターンの内容・付加価値・限定特典（100字以内）"
    }}
  }},

  "checklist": [
    {{"item": "ファーストビューで一文の強い価値提案", "status": "OK", "how": "どう満たしているか（50字以内）"}},
    {{"item": "ターゲットの痛み・課題の明確化", "status": "OK または 要強化", "how": "..."}},
    {{"item": "差別化を動画/GIF/比較表で視覚化", "status": "...", "how": "..."}},
    {{"item": "スペック・認証・受賞・実績など客観的証拠", "status": "...", "how": "..."}},
    {{"item": "利用シーン別ベネフィットの整理", "status": "...", "how": "..."}},
    {{"item": "価格アンカー + 限定リターンの設計", "status": "...", "how": "..."}},
    {{"item": "FAQ・保証・サポートの明記", "status": "...", "how": "..."}},
    {{"item": "開発ストーリー + 実行者の信頼情報", "status": "...", "how": "..."}},
    {{"item": "社会的証明（応援コメント・達成実績・メディア）", "status": "...", "how": "..."}},
    {{"item": "一貫したCTA（応援購入）導線", "status": "...", "how": "..."}}
  ],

  "improvements": [
    "改善提案1（具体的に。トップ3に入るために最優先すべきこと）",
    "改善提案2",
    "改善提案3"
  ]
}}

すべて日本語で、実際のMakuakeページで使える具体的な文章・数値を書いてください。"""

    message = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )

    raw = message.text.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        clean = re.sub(r"```(?:json)?", "", raw).strip()
        m = re.search(r"\{.*\}", clean, re.DOTALL)
        if not m:
            raise ValueError(f"ディープダイブのJSON解析に失敗しました:\n{raw[:500]}")
        result = json.loads(m.group())

    return result


# ─────────────────────────────────────────────
# PDF生成
# ─────────────────────────────────────────────
def _get_jp_font_path() -> str | None:
    """日本語フォントファイルを探す（Windows / Linux両対応）"""
    candidates = [
        # Windows
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/msgothic.ttc",
        "C:/Windows/Fonts/YuGothL.ttc",
        "C:/Windows/Fonts/yugothic.ttf",
        "C:/Windows/Fonts/yumin.ttf",
        # Linux (Noto CJK - packages.txt で apt install)
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def generate_pdf_bytes(
    product_data: dict,
    idea: dict,
    deep_dive: dict,
    generated_at: str,
    model_name: str = "claude-sonnet-4-6",
) -> bytes:
    """
    クラウドファンディング新商品企画レポートをPDF bytesで生成する。
    reportlab + 日本語フォント（Meiryo / MS Gothic等）を使用。
    """
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, KeepTogether,
    )
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # ── フォント登録 ──────────────────────────────
    font_path = _get_jp_font_path()
    fn, fnb = "Helvetica", "Helvetica-Bold"
    if font_path:
        try:
            pdfmetrics.registerFont(TTFont("JP", font_path))
            pdfmetrics.registerFont(TTFont("JPB", font_path))
            fn, fnb = "JP", "JPB"
        except Exception:
            try:
                pdfmetrics.registerFont(TTFont("JP", font_path, subfontIndex=0))
                pdfmetrics.registerFont(TTFont("JPB", font_path, subfontIndex=0))
                fn, fnb = "JP", "JPB"
            except Exception:
                pass

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=25 * mm,
        rightMargin=25 * mm,
        topMargin=25 * mm,
        bottomMargin=25 * mm,
    )

    W = A4[0] - 50 * mm  # usable width

    # ── スタイル定義 ──────────────────────────────
    def _s(name, font=None, **kw) -> ParagraphStyle:
        return ParagraphStyle(name, fontName=font or fn, **kw)

    s_title  = _s("title",  font=fnb, fontSize=17, leading=26, spaceAfter=6,
                  textColor=colors.HexColor("#1a1a2e"))
    s_sub    = _s("sub",    fontSize=9, leading=14, spaceAfter=18,
                  textColor=colors.HexColor("#888888"))
    s_h1     = _s("h1",     font=fnb, fontSize=13, leading=20,
                  spaceBefore=20, spaceAfter=10,
                  textColor=colors.HexColor("#2c3e50"))
    s_h2     = _s("h2",     font=fnb, fontSize=11, leading=18,
                  spaceBefore=12, spaceAfter=6,
                  textColor=colors.HexColor("#34495e"))
    s_body   = _s("body",   fontSize=10, leading=18, spaceAfter=6)
    s_bullet = _s("bullet", fontSize=10, leading=18, leftIndent=14,
                  spaceAfter=4, firstLineIndent=0)
    s_box    = _s("box",    fontSize=10, leading=18, spaceAfter=6,
                  leftIndent=10, rightIndent=10,
                  backColor=colors.HexColor("#f0f4f8"), borderPadding=8)

    def hr():
        return HRFlowable(width="100%", thickness=0.5,
                          color=colors.HexColor("#cccccc"),
                          spaceAfter=10, spaceBefore=14)

    def section(title_text: str) -> list:
        return [Spacer(1, 6), hr(), Paragraph(title_text, s_h1)]

    # ── コンテンツ構築 ─────────────────────────────
    story = []

    # ヘッダー
    story.append(Paragraph("クラウドファンディング新商品企画レポート", s_title))
    story.append(Paragraph(
        f"生成日時: {generated_at}　｜　使用LLM: {model_name}　｜　"
        f"分析商品: {product_data.get('title', '')[:40]}",
        s_sub,
    ))

    # 1. 選択されたアイデア
    story += section("■ 選択されたアイデア")
    ob = idea.get("one_belief", {})
    diff = idea.get("difficulty", 1)
    diff_info = DIFFICULTY.get(diff, DIFFICULTY[1])
    s_cell_h = _s("cell_h", font=fnb, fontSize=10, leading=17)
    s_cell   = _s("cell",            fontSize=10, leading=17, wordWrap="CJK")
    idea_rows = [
        [Paragraph("No.", s_cell_h),        Paragraph(str(idea.get("id", "")), s_cell)],
        [Paragraph("商品名", s_cell_h),      Paragraph(idea.get("title", ""), s_cell)],
        [Paragraph("難易度", s_cell_h),      Paragraph(f"{diff_info['label']} {diff_info['name']}", s_cell)],
        [Paragraph("製造コスト", s_cell_h),  Paragraph(idea.get("estimated_cost", ""), s_cell)],
        [Paragraph("コアメッセージ", s_cell_h), Paragraph(ob.get("full_statement", ""), s_cell)],
        [Paragraph("根拠レビュー", s_cell_h),   Paragraph(idea.get("evidence", ""), s_cell)],
    ]
    t = Table(idea_rows, colWidths=[34 * mm, W - 34 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (0, -1), colors.HexColor("#e8f4f8")),
        ("FONTSIZE",      (0, 0), (-1, -1), 10),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
        ("ROWBACKGROUNDS",(0, 0), (-1, -1), [colors.HexColor("#ffffff"), colors.HexColor("#f9f9f9")]),
    ]))
    story.append(t)

    # 2. キャッチコピー
    story += section("■ キャッチコピー 3案")
    for i, cc in enumerate(deep_dive.get("catchcopy", []), 1):
        story.append(Paragraph(f"案{i}：{cc}", s_box))
        story.append(Spacer(1, 3))

    # 3. ページ構成（10セクション）
    sections_data = deep_dive.get("page_sections", [])
    if sections_data:
        story += section("■ Makuakeページ構成（10セクション）")
        for sec in sections_data:
            sec_title = f"セクション{sec.get('section','')}：{sec.get('name','')}　— {sec.get('purpose','')}"
            story.append(Paragraph(sec_title, s_h2))
            content = sec.get("content", "")
            if content:
                story.append(Paragraph(content.replace("\n", "<br/>"), s_body))
            if sec.get("media"):
                story.append(Paragraph(f"推奨メディア: {sec['media']}", _s("cap", fontSize=9, leading=13, textColor=colors.HexColor("#888888"))))
            story.append(Spacer(1, 4))

    # 4. リターン設計
    ret = deep_dive.get("returns", {})
    if ret:
        story += section("■ リターン設計（3段階）")
        ret_labels = [
            ("early_bird", "アーリーバード（35%OFF）"),
            ("standard",   "通常先行（20%OFF）"),
            ("premium",    "プレミアム"),
        ]
        for key, default_label in ret_labels:
            tier = ret.get(key, {})
            if not tier:
                continue
            discount = f"　{tier.get('discount','')}" if tier.get("discount") else ""
            limit    = f"　{tier.get('limit','')}" if tier.get("limit") else ""
            story.append(Paragraph(f"{tier.get('label', default_label)}{discount}{limit}", s_h2))
            if tier.get("price"):
                story.append(Paragraph(tier["price"], _s("price", font=fnb, fontSize=12, leading=18, textColor=colors.HexColor("#2c3e50"))))
            if tier.get("description"):
                story.append(Paragraph(tier["description"].replace("\n", "<br/>"), s_body))
            story.append(Spacer(1, 4))

    # 5. チェックリスト + 改善提案
    checklist = deep_dive.get("checklist", [])
    if checklist:
        story += section("■ トップ3チェックリスト（10項目）")
        cl_rows = []
        for item in checklist:
            status = item.get("status", "")
            status_text = "✅ OK" if status == "OK" else "⚠️ 要強化"
            cl_rows.append([
                Paragraph(item.get("item", ""), s_cell),
                Paragraph(status_text, _s("st", font=fnb, fontSize=10, leading=16)),
                Paragraph(item.get("how", ""), s_cell),
            ])
        cl_table = Table(cl_rows, colWidths=[60 * mm, 22 * mm, W - 82 * mm])
        cl_table.setStyle(TableStyle([
            ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.HexColor("#ffffff"), colors.HexColor("#f9f9f9")]),
        ]))
        story.append(cl_table)

    improvements = deep_dive.get("improvements", [])
    if improvements:
        story.append(Paragraph("改善提案（優先度順）", s_h2))
        for i, imp in enumerate(improvements, 1):
            story.append(Paragraph(f"{i}. {imp}", s_bullet))

    # 6. Q1〜Q10（付録）
    story += section("■ アイデア分析データ Q1〜Q10（付録）")
    q_rows = [
        ["Q1 新規性",       idea.get("q1_novelty", "")],
        ["Q2 ベネフィット",  idea.get("q2_benefit", "")],
        ["Q3 証拠(ABT)",    idea.get("q3_proof_abt", "")],
        ["Q4 真の問題",     idea.get("q4_real_problem", "")],
        ["Q5 共通の敵",     idea.get("q5_enemy", "")],
        ["Q6 緊急性",       idea.get("q6_urgency", "")],
        ["Q7 信頼",         idea.get("q7_trust", "")],
        ["Q8 メカニズム",   idea.get("q8_mechanism", "")],
        ["Q9 オファー",     idea.get("q9_offer", "")],
        ["Q10 クロージング",idea.get("q10_pushpull", "")],
    ]
    qt = Table(
        [[Paragraph(r[0], _s("qk", font=fnb, fontSize=10, leading=16)),
          Paragraph(r[1], _s("qv", fontSize=10, leading=16, wordWrap="CJK"))]
         for r in q_rows],
        colWidths=[34 * mm, W - 34 * mm],
    )
    qt.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (0, -1), colors.HexColor("#eef2f7")),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.HexColor("#ffffff"), colors.HexColor("#f9f9f9")]),
    ]))
    story.append(qt)

    doc.build(story)
    return buf.getvalue()
