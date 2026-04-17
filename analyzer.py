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

    if difficulty_filter:
        d = DIFFICULTY[difficulty_filter]
        diff_instruction = (
            f"【難易度指定】{d['label']} {d['name']} のみ → {d['desc']}"
        )
        count_note = f"すべて {d['label']} で統一"
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
    difficulty_filter: int | None = None,
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

    # 難易度順（★1→★5）でソートしてIDを振り直す
    ideas.sort(key=lambda x: x.get("difficulty", 99))
    for i, idea in enumerate(ideas, 1):
        idea["id"] = i

    return ideas


def get_difficulty_options() -> dict:
    return {
        0: "すべての難易度（★1〜★5）",
        **{k: f"{v['label']} {v['name']} — {v['desc']}" for k, v in DIFFICULTY.items()},
    }


# ─────────────────────────────────────────────
# ディープダイブ（詳細コンテンツ生成）
# ─────────────────────────────────────────────
def generate_deep_dive_content(
    idea: dict,
    product_data: dict,
    api_key: str | None = None,
) -> dict:
    """
    選択されたアイデアに対して4タブ分のコンテンツを一括生成する。

    Returns:
        {
          "catchcopy": ["コピー案1", "コピー案2", "コピー案3"],
          "sales_letter": "全文セールスレター（散文・長文）",
          "approach": {
            "overview": "全体戦略の概要",
            "sns": "SNS戦略",
            "influencer": "インフルエンサー活用戦略",
            "pr": "PR・メディア戦略",
            "cf_launch": "クラファン立ち上げ戦略",
            "timeline": "ローンチまでのタイムライン（例: 1〜12週）"
          },
          "product": {
            "summary": "商品概要（3〜5文）",
            "features": ["特徴1", "特徴2", "特徴3", "特徴4", "特徴5"],
            "target_customer": "ターゲット顧客像",
            "price_strategy": "価格・CF割引戦略",
            "production_notes": "製造・調達上の注意点",
            "cf_goal": "CF目標金額の目安と根拠"
          }
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
        makuake_refs = fetch_makuake_references(n=2)
        makuake_context = "\n".join(
            f"【参考{i+1}】{r['title']}\n{r['catch']}\n{r['body']}"
            for i, r in enumerate(makuake_refs)
        ) if makuake_refs else ""
    except Exception:
        makuake_context = ""

    makuake_section = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【Makuake売れ筋商品ページの参考（文章トーン・構成を参考にすること）】
{makuake_context}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""" if makuake_context else ""

    prompt = f"""あなたはクラウドファンディング専門のコピーライター兼マーケティング戦略家です。
すべての出力は必ず日本語で書いてください。
Makuakeで実際に売れた商品ページの文章トーン・構成を参考に、読んで「欲しい！」と感じさせる文章を書いてください。

以下の新商品アイデアについて、クラウドファンディング用の詳細コンテンツを生成してください。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【選択されたアイデア】
No.{idea.get('id', 1)}: {idea.get('title', '')}
難易度: {diff_info['label']} {diff_info['name']}  製造コスト: {idea.get('estimated_cost', '')}

One Belief（核心コンセプト）:
  「{ob.get('full_statement', '')}」
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
{makuake_section}
以下の4セクションを含むJSONを返してください。マークダウン・コードブロック不要。

{{
  "catchcopy": [
    "キャッチコピー案1（20〜40文字。読んだ瞬間に『欲しい』と思わせる。ベネフィット訴求型）",
    "キャッチコピー案2（同文字数。問題解決・痛みを言語化するタイプ。共感型）",
    "キャッチコピー案3（同文字数。未来の自分を想像させる変化訴求型）"
  ],

  "sales_letter": "クラウドファンディングページの本文として使える完全なセールスレター。読んだ人が『これ欲しい、今すぐ支援したい』と感じるように書く。論理ではなく感情で動かすこと。以下の構成で読み物として自然な日本語の散文で書く（見出しは【】で囲む）:\\n\\n【思わず手が止まる一文】読者が共感して『そう！まさにこれ！』となる強烈な問いかけか宣言（1〜2文）\\n\\n【あなたの話ですよね？】読者の日常のリアルな不満・もどかしさを言語化して共感を作る（3〜5文）\\n\\n【実は、解決策はもうある】このアイデアがなぜ今まで誰も気づかなかった答えなのか（3〜5文）\\n\\n【手に入れた未来を想像してください】使った後の生活・感情の変化を具体的に描写する（3〜5文）\\n\\n【信じられないかもしれませんが】実際の根拠・ストーリーで「本物感」を作る（4〜6文）\\n\\n【なぜ今まで解決できなかったのか】既存品の限界・業界の問題を暴く（3〜5文）\\n\\n【今だけのチャンス】CFだからこそ実現できる限定特典・今動く理由（2〜3文）\\n\\n【なぜ私たちを信頼できるか】開発背景・想い・実績（2〜4文）\\n\\n【どうやって実現するのか】仕組みをわかりやすく・納得感を作る（3〜5文）\\n\\n【あなたへの特別なオファー】CF支援者限定の価格・特典・リターンの価値を具体的金額で（3〜5文）\\n\\n【最後にひとつだけ】支援しない場合に失うものを示しつつ、温かく背中を押す（3〜4文）",

  "approach": {{
    "overview": "マーケティング全体戦略の概要（100文字以内）",
    "sns": "SNS戦略（Instagram/X/TikTok等の活用法、投稿内容・頻度・ハッシュタグ戦略）（200文字以内）",
    "influencer": "インフルエンサー活用戦略（ターゲットとなるインフルエンサー像・協力依頼内容・プレゼント戦略）（200文字以内）",
    "pr": "PR・メディア戦略（プレスリリース先・メディアの種類・ニュースバリューの作り方）（200文字以内）",
    "cf_launch": "クラファン立ち上げ戦略（事前予約・ウォームアップ施策・CF公開タイミング・ゴール設定）（200文字以内）",
    "timeline": "ローンチまでの12週間タイムライン（例: 1〜2週目: XX、3〜4週目: YY ... 11〜12週目: CF開始）"
  }},

  "product": {{
    "summary": "商品概要（ターゲット・用途・核心価値を含む3〜5文の説明文）",
    "features": [
      "特徴・機能1（具体的に）",
      "特徴・機能2",
      "特徴・機能3",
      "特徴・機能4",
      "特徴・機能5"
    ],
    "target_customer": "ターゲット顧客像（年齢・性別・ライフスタイル・購入動機を含む具体的な人物像）",
    "price_strategy": "価格戦略（想定小売価格・CF早期割引率・リターン段階設計の提案）",
    "production_notes": "製造・調達・品質管理上の主な注意点（2〜3点）",
    "cf_goal": "CF目標金額の目安と根拠（製造コスト・ロット数・利益率から逆算）"
  }}
}}

すべて日本語で、実用的で具体的な内容を書いてください。sales_letterは特に長く詳細に（800文字以上）。"""

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
    """Windowsシステムから日本語フォントファイルを探す"""
    candidates = [
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/msgothic.ttc",
        "C:/Windows/Fonts/YuGothL.ttc",
        "C:/Windows/Fonts/yugothic.ttf",
        "C:/Windows/Fonts/yumin.ttf",
    ]
    import os
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

    s_title  = _s("title",  font=fnb, fontSize=16, leading=24, spaceAfter=6,
                  textColor=colors.HexColor("#1a1a2e"))
    s_sub    = _s("sub",    fontSize=8, leading=13, spaceAfter=16,
                  textColor=colors.HexColor("#888888"))
    s_h1     = _s("h1",     font=fnb, fontSize=12, leading=18,
                  spaceBefore=16, spaceAfter=8,
                  textColor=colors.HexColor("#2c3e50"))
    s_h2     = _s("h2",     font=fnb, fontSize=10, leading=16,
                  spaceBefore=10, spaceAfter=5,
                  textColor=colors.HexColor("#34495e"))
    s_body   = _s("body",   fontSize=9, leading=16, spaceAfter=5)
    s_bullet = _s("bullet", fontSize=9, leading=15, leftIndent=14,
                  spaceAfter=3, firstLineIndent=0)
    s_box    = _s("box",    fontSize=9, leading=15, spaceAfter=5,
                  leftIndent=10, rightIndent=10,
                  backColor=colors.HexColor("#f0f4f8"), borderPadding=6)

    def hr():
        return HRFlowable(width="100%", thickness=0.5,
                          color=colors.HexColor("#cccccc"),
                          spaceAfter=8, spaceBefore=12)

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
    s_cell_h = _s("cell_h", font=fnb, fontSize=9, leading=15)
    s_cell   = _s("cell",            fontSize=9, leading=15, wordWrap="CJK")
    idea_rows = [
        [Paragraph("No.", s_cell_h),        Paragraph(str(idea.get("id", "")), s_cell)],
        [Paragraph("商品名", s_cell_h),      Paragraph(idea.get("title", ""), s_cell)],
        [Paragraph("難易度", s_cell_h),      Paragraph(f"{diff_info['label']} {diff_info['name']}", s_cell)],
        [Paragraph("製造コスト", s_cell_h),  Paragraph(idea.get("estimated_cost", ""), s_cell)],
        [Paragraph("コアメッセージ", s_cell_h), Paragraph(ob.get("full_statement", ""), s_cell)],
    ]
    t = Table(idea_rows, colWidths=[34 * mm, W - 34 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (0, -1), colors.HexColor("#e8f4f8")),
        ("FONTSIZE",      (0, 0), (-1, -1), 9),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS",(0, 0), (-1, -1), [colors.HexColor("#ffffff"), colors.HexColor("#f9f9f9")]),
    ]))
    story.append(t)

    # 2. キャッチコピー
    story += section("■ キャッチコピー 3案")
    for i, cc in enumerate(deep_dive.get("catchcopy", []), 1):
        story.append(Paragraph(f"案{i}：{cc}", s_box))
        story.append(Spacer(1, 3))

    # 3. セールス文章
    story += section("■ セールス文章")
    for para in deep_dive.get("sales_letter", "").split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if para.startswith("【") or para.startswith("■"):
            story.append(Paragraph(para, s_h2))
        else:
            story.append(Paragraph(para.replace("\n", "<br/>"), s_body))

    # 4. アプローチ方法
    story += section("■ アプローチ方法")
    approach = deep_dive.get("approach", {})
    approach_labels = [
        ("overview",    "全体戦略概要"),
        ("sns",         "SNS戦略"),
        ("influencer",  "インフルエンサー戦略"),
        ("pr",          "PR・メディア戦略"),
        ("cf_launch",   "CF立ち上げ戦略"),
        ("timeline",    "タイムライン"),
    ]
    for key, label in approach_labels:
        val = approach.get(key, "")
        if val:
            story.append(Paragraph(label, s_h2))
            val = "\n".join(val) if isinstance(val, list) else str(val)
            story.append(Paragraph(val.replace("\n", "<br/>"), s_body))

    # 5. 商品プロダクト
    story += section("■ 商品プロダクト")
    product = deep_dive.get("product", {})
    if product.get("summary"):
        story.append(Paragraph("商品概要", s_h2))
        story.append(Paragraph(product["summary"].replace("\n", "<br/>"), s_body))
    if product.get("features"):
        story.append(Paragraph("主な特徴・機能", s_h2))
        for f in product["features"]:
            story.append(Paragraph(f"・{f}", s_bullet))
    prod_fields = [
        ("target_customer", "ターゲット顧客"),
        ("price_strategy",  "価格戦略"),
        ("production_notes","製造上の注意点"),
        ("cf_goal",         "CF目標金額"),
    ]
    for key, label in prod_fields:
        val = product.get(key, "")
        if val:
            story.append(Paragraph(label, s_h2))
            val = "\n".join(val) if isinstance(val, list) else str(val)
            story.append(Paragraph(val.replace("\n", "<br/>"), s_body))

    # 6. Q1〜Q10（付録）
    story += section("■ 16-Word Sales Letter™ Q1〜Q10（付録）")
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
        [[Paragraph(r[0], _s("qk", font=fnb, fontSize=8, leading=13)),
          Paragraph(r[1], _s("qv", fontSize=8, leading=13, wordWrap="CJK"))]
         for r in q_rows],
        colWidths=[34 * mm, W - 34 * mm],
    )
    qt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2f7")),
        ("GRID",       (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("VALIGN",     (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.HexColor("#ffffff"), colors.HexColor("#f9f9f9")]),
    ]))
    story.append(qt)

    doc.build(story)
    return buf.getvalue()
