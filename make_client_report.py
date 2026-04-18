"""
クライアント向け説明資料PDF生成スクリプト
Amazonレビュー収集仕様変更と代替案の説明
"""
import io
import os
import sys
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

# ── フォント ──────────────────────────────────────────
def _get_font():
    candidates = [
        r"C:\Windows\Fonts\meiryo.ttc",
        r"C:\Windows\Fonts\msgothic.ttc",
        r"C:\Windows\Fonts\msmincho.ttc",
        r"C:\Windows\Fonts\YuGothM.ttc",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None

font_path = _get_font()
fn, fnb = "Helvetica", "Helvetica-Bold"
if font_path:
    try:
        pdfmetrics.registerFont(TTFont("JP",  font_path, subfontIndex=0))
        pdfmetrics.registerFont(TTFont("JPB", font_path, subfontIndex=0))
        fn, fnb = "JP", "JPB"
    except Exception:
        pass

W, H = A4
MARGIN = 18 * mm

# ── カラー ────────────────────────────────────────────
C_NAVY   = colors.HexColor("#1a2c50")
C_BLUE   = colors.HexColor("#2563eb")
C_RED    = colors.HexColor("#dc2626")
C_GREEN  = colors.HexColor("#16a34a")
C_ORANGE = colors.HexColor("#ea580c")
C_GRAY   = colors.HexColor("#6b7280")
C_LGRAY  = colors.HexColor("#f3f4f6")
C_LLGRAY = colors.HexColor("#f9fafb")
C_WHITE  = colors.white
C_YELLOW = colors.HexColor("#fef9c3")
C_LYELLOW= colors.HexColor("#fef08a")

# ── スタイル ──────────────────────────────────────────
def s(name, font=None, **kw):
    return ParagraphStyle(name, fontName=font or fn, **kw)

s_cover_title = s("ct", font=fnb, fontSize=20, leading=30, textColor=C_WHITE,
                  spaceAfter=6, wordWrap="CJK")
s_cover_sub   = s("cs", fontSize=11, leading=18, textColor=colors.HexColor("#cbd5e1"),
                  wordWrap="CJK")
s_cover_date  = s("cd", fontSize=9,  leading=14, textColor=colors.HexColor("#94a3b8"),
                  wordWrap="CJK")

s_section     = s("sec", font=fnb, fontSize=13, leading=20, textColor=C_WHITE,
                  spaceAfter=4, wordWrap="CJK")
s_h2          = s("h2",  font=fnb, fontSize=11, leading=16, textColor=C_NAVY,
                  spaceBefore=7, spaceAfter=4, wordWrap="CJK")
s_h3          = s("h3",  font=fnb, fontSize=10, leading=15, textColor=C_BLUE,
                  spaceBefore=6, spaceAfter=3, wordWrap="CJK")
s_body        = s("body", fontSize=9,  leading=16, spaceAfter=4, wordWrap="CJK")
s_body_bold   = s("bb",   font=fnb, fontSize=9, leading=16, spaceAfter=4, wordWrap="CJK")
s_small       = s("sm",   fontSize=8,  leading=13, textColor=C_GRAY, wordWrap="CJK")
s_bullet      = s("bul",  fontSize=9,  leading=16, leftIndent=12, spaceAfter=3,
                  wordWrap="CJK")
s_check       = s("chk",  fontSize=9,  leading=16, leftIndent=12, spaceAfter=3,
                  wordWrap="CJK")
s_table_h     = s("th",   font=fnb, fontSize=9,  leading=14, textColor=C_WHITE,
                  wordWrap="CJK", alignment=1)
s_table_cell  = s("tc",   fontSize=9,  leading=14, wordWrap="CJK", alignment=1)
s_table_cell_l= s("tcl",  fontSize=9,  leading=14, wordWrap="CJK")
s_caption     = s("cap",  fontSize=8,  leading=13, textColor=C_GRAY,
                  spaceAfter=8, wordWrap="CJK")
s_important   = s("imp",  font=fnb, fontSize=10, leading=16,
                  textColor=C_RED, wordWrap="CJK")
s_q           = s("q",    font=fnb, fontSize=9,  leading=16, textColor=C_NAVY,
                  spaceAfter=3, wordWrap="CJK")
s_qa          = s("qa",   fontSize=9, leading=16, leftIndent=10,
                  spaceAfter=6, wordWrap="CJK", textColor=colors.HexColor("#374151"))

def section_header(title, color=C_NAVY):
    """セクションヘッダーブロック"""
    t = Table([[Paragraph(title, s_section)]],
              colWidths=[W - 2 * MARGIN])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), color),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LEFTPADDING",   (0,0), (-1,-1), 10),
        ("RIGHTPADDING",  (0,0), (-1,-1), 10),
        ("ROUNDEDCORNERS", [4]),
    ]))
    return t

def info_box(text, bg=C_LGRAY, border=C_BLUE, bold=False):
    st = s_body_bold if bold else s_body
    t = Table([[Paragraph(text, st)]], colWidths=[W - 2 * MARGIN])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), bg),
        ("LEFTPADDING",   (0,0), (-1,-1), 12),
        ("RIGHTPADDING",  (0,0), (-1,-1), 12),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LINEAFTER",     (0,0), (0,-1), 3, border),
        ("LINEBEFORE",    (0,0), (0,-1), 3, border),
    ]))
    return t

def build_pdf(output_path: str):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
    )
    story = []
    CW = W - 2 * MARGIN  # コンテンツ幅

    # ═══════════════════════════════════════════════════
    # 表紙
    # ═══════════════════════════════════════════════════
    cover = Table(
        [[Paragraph("Amazonレビュー収集について", s_cover_title)],
         [Paragraph("仕様変更のご報告と代替案のご提案", s_cover_sub)],
         [Spacer(1, 6*mm)],
         [Paragraph("2026年4月18日　作成", s_cover_date)],
         ],
        colWidths=[CW],
    )
    cover.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), C_NAVY),
        ("TOPPADDING",    (0,0), (-1,-1), 14),
        ("BOTTOMPADDING", (0,0), (-1,-1), 14),
        ("LEFTPADDING",   (0,0), (-1,-1), 14),
        ("RIGHTPADDING",  (0,0), (-1,-1), 14),
    ]))
    story.append(cover)
    story.append(Spacer(1, 8*mm))

    # ─── 要約ボックス ─────────────────────────────────
    summary = Table(
        [[Paragraph("このドキュメントについて", s_h3)],
         [Paragraph(
             "Amazonの仕様変更により、ログインなしで取得できるレビュー件数が"
             "大幅に制限されました。本資料では現状の説明と、"
             "目的を達成するための代替案を整理しています。"
             "内容をご確認いただき、今後の仕様についてご判断をお願いします。",
             s_body)]],
        colWidths=[CW],
    )
    summary.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), C_LLGRAY),
        ("LINEABOVE",     (0,0), (-1, 0), 2, C_BLUE),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LEFTPADDING",   (0,0), (-1,-1), 12),
        ("RIGHTPADDING",  (0,0), (-1,-1), 12),
    ]))
    story.append(summary)
    story.append(Spacer(1, 8*mm))

    # ═══════════════════════════════════════════════════
    # 1. 現状報告
    # ═══════════════════════════════════════════════════
    story.append(section_header("1.  現状のご報告"))
    story.append(Spacer(1, 4*mm))

    story.append(Paragraph("もともとの想定 vs 実際にできること", s_h2))

    compare = Table(
        [
            [Paragraph("項目", s_table_h),
             Paragraph("もともとの想定", s_table_h),
             Paragraph("実際（現在）", s_table_h)],
            [Paragraph("Amazonから直接\n取得できる件数", s_table_cell_l),
             Paragraph("200件程度", s_table_cell),
             Paragraph("8件（上限）", s_table_cell)],
            [Paragraph("レビュー本文の内容", s_table_cell_l),
             Paragraph("本物のAmazonレビュー文", s_table_cell),
             Paragraph("本物のレビュー本文", s_table_cell)],
            [Paragraph("追加でAIが収集\nしている件数", s_table_cell_l),
             Paragraph("（想定外）", s_table_cell),
             Paragraph("130〜170件\n（AI要約・後述）", s_table_cell)],
            [Paragraph("コスト", s_table_cell_l),
             Paragraph("無料", s_table_cell),
             Paragraph("無料", s_table_cell)],
        ],
        colWidths=[50*mm, 55*mm, 55*mm],
    )
    compare.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0),  C_NAVY),
        ("BACKGROUND",    (0,1), (-1,1),  colors.HexColor("#fee2e2")),
        ("BACKGROUND",    (0,2), (-1,2),  C_LLGRAY),
        ("BACKGROUND",    (0,3), (-1,3),  C_LLGRAY),
        ("BACKGROUND",    (0,4), (-1,4),  C_LLGRAY),
        ("GRID",          (0,0), (-1,-1), 0.5, colors.HexColor("#e5e7eb")),
        ("TOPPADDING",    (0,0), (-1,-1), 7),
        ("BOTTOMPADDING", (0,0), (-1,-1), 7),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("RIGHTPADDING",  (0,0), (-1,-1), 8),
        ("FONTNAME",      (1,1), (1,1),   fnb),
        ("TEXTCOLOR",     (1,1), (1,1),   C_RED),
        ("FONTNAME",      (2,1), (2,1),   fnb),
        ("TEXTCOLOR",     (2,1), (2,1),   C_GREEN),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.append(compare)
    story.append(Spacer(1, 5*mm))

    story.append(Paragraph("なぜ200件取れなくなったのか", s_h2))
    story.append(info_box(
        "Amazonが2024年11月5日に仕様を変更し、"
        "レビュー一覧ページへのアクセスをログイン必須にしました。"
        "これは日本だけでなく全世界共通の変更です。",
        bg=colors.HexColor("#fef2f2"), border=C_RED
    ))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph("現在アクセスできるページと取得件数：", s_body_bold))
    access_rows = [
        [Paragraph("ページ", s_table_h),
         Paragraph("アクセス結果", s_table_h),
         Paragraph("取得件数", s_table_h)],
        [Paragraph("/dp/商品ページ", s_table_cell_l),
         Paragraph("アクセス可能", s_table_cell),
         Paragraph("8件（固定）", s_table_cell)],
        [Paragraph("/product-reviews/レビュー一覧", s_table_cell_l),
         Paragraph("ログインページに\nリダイレクト", s_table_cell),
         Paragraph("0件", s_table_cell)],
        [Paragraph("内部AJAXエンドポイント", s_table_cell_l),
         Paragraph("403 / 404エラー", s_table_cell),
         Paragraph("0件", s_table_cell)],
        [Paragraph("個別レビューページ", s_table_cell_l),
         Paragraph("ログインページに\nリダイレクト", s_table_cell),
         Paragraph("0件", s_table_cell)],
    ]
    access_t = Table(access_rows, colWidths=[60*mm, 55*mm, 45*mm])
    access_t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0),  C_NAVY),
        ("BACKGROUND",    (0,1), (-1,1),  colors.HexColor("#dcfce7")),
        ("BACKGROUND",    (0,2), (-1,4),  colors.HexColor("#fee2e2")),
        ("GRID",          (0,0), (-1,-1), 0.5, colors.HexColor("#e5e7eb")),
        ("TOPPADDING",    (0,0), (-1,-1), 7),
        ("BOTTOMPADDING", (0,0), (-1,-1), 7),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("RIGHTPADDING",  (0,0), (-1,-1), 8),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.append(access_t)
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        "※ Cookie・Refererヘッダーを付加するなど技術的に試せる方法はすべて実施・確認済みです。",
        s_small))
    story.append(Spacer(1, 6*mm))

    # ─── 現在の補完手段 ──────────────────────────────
    story.append(Paragraph("現在の補完手段（AI検索レビュー）について", s_h2))
    story.append(info_box(
        "現在のツールでは、Amazonの8件に加えて「Gemini AI」がGoogleを検索し、"
        "ブログ・比較サイト・SNSなどから口コミ情報を集めて130〜170件分を補完しています。"
        "ただしこれはAmazonのレビュー本文を直接取得したものではありません。",
        bg=colors.HexColor("#eff6ff"), border=C_BLUE
    ))
    story.append(Spacer(1, 3*mm))
    ai_rows = [
        [Paragraph("項目", s_table_h), Paragraph("内容", s_table_h)],
        [Paragraph("件数", s_table_cell_l),      Paragraph("130〜170件（商品の知名度による）", s_table_cell_l)],
        [Paragraph("内容", s_table_cell_l),       Paragraph("ブログ・SNS・比較記事などをAIが要約したもの", s_table_cell_l)],
        [Paragraph("Amazonのレビュー本文か", s_table_cell_l), Paragraph("違う（AI要約）", s_table_cell_l)],
        [Paragraph("コスト", s_table_cell_l),     Paragraph("無料", s_table_cell_l)],
        [Paragraph("アイデア生成への有効性", s_table_cell_l), Paragraph("実証済み（スーツケース商品でテスト完了）", s_table_cell_l)],
    ]
    ai_t = Table(ai_rows, colWidths=[55*mm, CW - 55*mm])
    ai_t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0),  colors.HexColor("#1d4ed8")),
        ("BACKGROUND",    (0,1), (-1,-1), C_LLGRAY),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [C_LLGRAY, C_WHITE]),
        ("GRID",          (0,0), (-1,-1), 0.5, colors.HexColor("#e5e7eb")),
        ("TOPPADDING",    (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("RIGHTPADDING",  (0,0), (-1,-1), 8),
    ]))
    story.append(ai_t)
    story.append(Spacer(1, 8*mm))

    # ═══════════════════════════════════════════════════
    # 2. 選択肢
    # ═══════════════════════════════════════════════════
    story.append(section_header("2.  選択肢の比較"))
    story.append(Spacer(1, 4*mm))

    story.append(Paragraph(
        "現状を踏まえ、以下の3つの選択肢があります。", s_body))
    story.append(Spacer(1, 3*mm))

    # ── 選択肢A ──────────────────────────────────────
    a_head = Table([[Paragraph("選択肢 A　　現状のまま（変更なし）", s_section)]],
                   colWidths=[CW])
    a_head.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), C_GREEN),
        ("TOPPADDING",    (0,0), (-1,-1), 7),
        ("BOTTOMPADDING", (0,0), (-1,-1), 7),
        ("LEFTPADDING",   (0,0), (-1,-1), 10),
    ]))
    story.append(a_head)
    story.append(Spacer(1, 3*mm))

    a_rows = [
        [Paragraph("月額コスト", s_table_h),    Paragraph("レビュー件数/商品", s_table_h),
         Paragraph("レビューの種類", s_table_h), Paragraph("リスク", s_table_h)],
        [Paragraph("0円", s_table_cell),
         Paragraph("Amazon 8件\n＋AI要約 130〜170件", s_table_cell),
         Paragraph("Amazon本物8件\n＋AI要約", s_table_cell),
         Paragraph("なし", s_table_cell)],
    ]
    a_t = Table(a_rows, colWidths=[CW/4]*4)
    a_t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0),  colors.HexColor("#166534")),
        ("BACKGROUND",    (0,1), (-1,1),  colors.HexColor("#dcfce7")),
        ("GRID",          (0,0), (-1,-1), 0.5, colors.HexColor("#e5e7eb")),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.append(a_t)
    story.append(Spacer(1, 3*mm))
    for txt in [
        "◎ 追加コストゼロで現在のツールがそのまま動く",
        "◎ アイデア生成の精度は実際のテストで確認済み",
        "△ 130〜170件はAmazonのレビュー本文ではなくAI要約",
        "△ マイナーな商品は件数が30〜80件程度に減る場合がある",
    ]:
        story.append(Paragraph(txt, s_bullet))
    story.append(Spacer(1, 6*mm))

    # ── 選択肢B ──────────────────────────────────────
    b_head = Table([[Paragraph("選択肢 B　　無料で本物のレビューを増やす（Outscraper）", s_section)]],
                   colWidths=[CW])
    b_head.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), C_ORANGE),
        ("TOPPADDING",    (0,0), (-1,-1), 7),
        ("BOTTOMPADDING", (0,0), (-1,-1), 7),
        ("LEFTPADDING",   (0,0), (-1,-1), 10),
    ]))
    story.append(b_head)
    story.append(Spacer(1, 3*mm))

    b_rows = [
        [Paragraph("月額コスト", s_table_h),    Paragraph("レビュー件数/商品", s_table_h),
         Paragraph("レビューの種類", s_table_h), Paragraph("リスク", s_table_h)],
        [Paragraph("0円\n（最初の500件まで）\n501件〜は約200円/千件", s_table_cell),
         Paragraph("最大500件\n（無料枠内）", s_table_cell),
         Paragraph("本物のAmazon\nレビュー本文", s_table_cell),
         Paragraph("Amazon利用規約\n違反（後述）", s_table_cell)],
    ]
    b_t = Table(b_rows, colWidths=[CW/4]*4)
    b_t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0),  colors.HexColor("#9a3412")),
        ("BACKGROUND",    (0,1), (-1,1),  colors.HexColor("#fff7ed")),
        ("GRID",          (0,0), (-1,-1), 0.5, colors.HexColor("#e5e7eb")),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.append(b_t)
    story.append(Spacer(1, 3*mm))
    for txt in [
        "◎ 本物のAmazonレビュー本文・星評価がそのまま取得できる",
        "◎ 最初の500件はクレジットカード不要で無料",
        "◎ amazon.co.jp（日本語）に対応",
        "△ Amazon利用規約違反のグレーゾーン（詳細は次ページ）",
        "△ Amazon.co.jpでの動作確認が必要（事前テスト推奨）",
        "△ Amazonの対策強化で突然使えなくなるリスクがある",
    ]:
        story.append(Paragraph(txt, s_bullet))
    story.append(Spacer(1, 6*mm))

    # ── 選択肢C ──────────────────────────────────────
    c_head = Table([[Paragraph("選択肢 C　　有料で本物のレビューを安定取得（Apify）", s_section)]],
                   colWidths=[CW])
    c_head.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), colors.HexColor("#7c3aed")),
        ("TOPPADDING",    (0,0), (-1,-1), 7),
        ("BOTTOMPADDING", (0,0), (-1,-1), 7),
        ("LEFTPADDING",   (0,0), (-1,-1), 10),
    ]))
    story.append(c_head)
    story.append(Spacer(1, 3*mm))

    c_rows = [
        [Paragraph("月額コスト", s_table_h),    Paragraph("レビュー件数/商品", s_table_h),
         Paragraph("レビューの種類", s_table_h), Paragraph("リスク", s_table_h)],
        [Paragraph("約750円/月〜\n（$5〜）", s_table_cell),
         Paragraph("数百〜数千件\n（$0.002/件）", s_table_cell),
         Paragraph("本物のAmazon\nレビュー本文", s_table_cell),
         Paragraph("Amazon利用規約\n違反（後述）", s_table_cell)],
    ]
    c_t = Table(c_rows, colWidths=[CW/4]*4)
    c_t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0),  colors.HexColor("#4c1d95")),
        ("BACKGROUND",    (0,1), (-1,1),  colors.HexColor("#f5f3ff")),
        ("GRID",          (0,0), (-1,-1), 0.5, colors.HexColor("#e5e7eb")),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.append(c_t)
    story.append(Spacer(1, 3*mm))
    for txt in [
        "◎ 本物のAmazonレビュー本文・星評価がそのまま取得できる",
        "◎ amazon.co.jp（日本語）への対応を明示",
        "◎ 月750円から始められる低コスト",
        "△ Amazon利用規約違反のグレーゾーン（詳細は次ページ）",
        "△ Amazonの対策強化で突然使えなくなるリスクがある",
    ]:
        story.append(Paragraph(txt, s_bullet))
    story.append(Spacer(1, 8*mm))

    # ═══════════════════════════════════════════════════
    # 3. 規約リスクについて
    # ═══════════════════════════════════════════════════
    story.append(section_header("3.  規約リスクについて（B・C共通）", color=C_RED))
    story.append(Spacer(1, 4*mm))

    story.append(info_box(
        "選択肢B・CはAmazonの利用規約に違反する可能性があります。"
        "これを「グレーゾーン」と呼ぶ理由と、実態をご説明します。",
        bg=colors.HexColor("#fef2f2"), border=C_RED
    ))
    story.append(Spacer(1, 4*mm))

    risk_items = [
        ("なぜグレーゾーンなのか",
         "Amazonは規約でスクレイピングを禁止しています。"
         "ただし、OutscraperやApifyのような外部サービスが世界中で堂々とサービスを提供しており、"
         "現実には黙認されている状態です。"),
        ("実際のリスク①：サービス停止",
         "Amazonが対策を強化すると、OutscraperやApifyが突然使えなくなることがあります。"
         "過去にも同様のことが起きており、その場合はツールが動かなくなります。"),
        ("実際のリスク②：アカウントBAN",
         "Amazonのアカウント（購入者・出品者）を使ってスクレイピングする場合は"
         "アカウントが停止されるリスクがあります。"
         "ただし選択肢B・Cは外部サービスが処理するため、クライアント側のアカウントリスクはありません。"),
        ("法的リスク",
         "2024年の米国の裁判例では「ログイン前の公開データのスクレイピングは違法ではない」"
         "という判決が出ています。ただしAmazonが訴訟を起こす可能性はゼロではありません。"
         "現実にはBtoB向けの分析ツールとして多くの企業が利用している行為です。"),
    ]
    for title, desc in risk_items:
        story.append(Paragraph(f"■ {title}", s_h3))
        story.append(Paragraph(desc, s_body))
        story.append(Spacer(1, 2*mm))

    story.append(Spacer(1, 6*mm))

    # ═══════════════════════════════════════════════════
    # 4. ご確認事項
    # ═══════════════════════════════════════════════════
    story.append(section_header("4.  ご確認・ご判断のお願い"))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph(
        "以下の3点についてご確認をお願いします。", s_body))
    story.append(Spacer(1, 3*mm))

    qa_items = [
        ("確認①　まずAI要約レビューで試してみてよいですか？",
         "ツールを実際に使ってみて、生成されるアイデアに問題があれば\n"
         "その時点で選択肢B・Cに切り替えることができます。\n"
         "→ それでよければ：選択肢Aのまま進めます（追加費用なし）\n"
         "→ 最初から本物のレビューで始めたい場合：選択肢B・Cを検討します"),
        ("確認②　規約グレーゾーンのリスクを許容できますか？",
         "選択肢B・Cを使う場合、Amazon利用規約違反のリスクが伴います。\n"
         "→ 許容できる場合：B（無料）またはC（有料・安定）を選択\n"
         "→ 許容できない場合：選択肢Aのみが安全な選択です"),
        ("確認③　まず無料でテストしてみますか？（選択肢Bを選ぶ場合）",
         "Outscraper（選択肢B）はクレジットカード不要で500件まで無料でテストできます。\n"
         "Amazon.co.jpで正常に動作するか確認してから判断することも可能です。\n"
         "→ テストする場合：約1〜2時間で動作確認できます"),
    ]
    for q, a in qa_items:
        q_box = Table(
            [[Paragraph(q, s_q)],
             [Paragraph(a.replace("\n", "<br/>"), s_qa)]],
            colWidths=[CW],
        )
        q_box.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (0,0),  colors.HexColor("#eff6ff")),
            ("BACKGROUND",    (0,1), (0,1),  C_LLGRAY),
            ("LINEABOVE",     (0,0), (0,0),  2, C_BLUE),
            ("TOPPADDING",    (0,0), (-1,-1), 8),
            ("BOTTOMPADDING", (0,0), (-1,-1), 8),
            ("LEFTPADDING",   (0,0), (-1,-1), 10),
            ("RIGHTPADDING",  (0,0), (-1,-1), 10),
        ]))
        story.append(q_box)
        story.append(Spacer(1, 4*mm))

    story.append(Spacer(1, 4*mm))

    footer = Table(
        [[Paragraph("推奨", s_section)],
         [Paragraph(
             "まずは選択肢Aのまま実際にツールを使っていただき、"
             "生成されるアイデアに物足りなさを感じた場合に"
             "選択肢B（無料テスト）を試すことをお勧めします。",
             s_body)],
         [HRFlowable(width=CW - 24*mm, color=C_GRAY, thickness=0.5)],
         [Paragraph("本資料に関するご質問はお気軽にお問い合わせください。", s_small)]],
        colWidths=[CW],
    )
    footer.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (0,0),  colors.HexColor("#1e40af")),
        ("BACKGROUND",    (0,1), (0,3),  colors.HexColor("#dbeafe")),
        ("TOPPADDING",    (0,0), (-1,-1), 7),
        ("BOTTOMPADDING", (0,0), (-1,-1), 7),
        ("LEFTPADDING",   (0,0), (-1,-1), 12),
        ("RIGHTPADDING",  (0,0), (-1,-1), 12),
    ]))
    story.append(footer)

    doc.build(story)

    with open(output_path, "wb") as f:
        f.write(buf.getvalue())
    print(f"PDF生成完了: {output_path}")


if __name__ == "__main__":
    import sys
    name = sys.argv[1] if len(sys.argv) > 1 else "client_report_amazon_review.pdf"
    out = os.path.join(os.path.dirname(__file__), name)
    build_pdf(out)
