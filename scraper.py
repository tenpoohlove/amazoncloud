"""
scraper.py: Amazon 商品ページ・レビューを収集する

【2モード】
  チェックなし (include_similar=False):
    対象商品の ★1 レビューを最大200件
    （★1 が足りない場合は ★2 で補完）

  チェックあり (include_similar=True):
    対象商品の ★1 レビューを最大200件
    ＋ 類似品4商品 × ★1 を最大50件ずつ
    （★1 が足りない場合は ★2 で補完）

どちらのモードでも対象商品のページ情報（タイトル・特徴・説明）は取得する。
参照したURLはすべて sources リストに記録する。
"""

import re
import time
import random
import os
from curl_cffi import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from typing import Optional

# ─────────────────────────────────────────────
# 定数
# ─────────────────────────────────────────────
MAX_MAIN_REVIEWS  = 8     # 対象商品レビュー上限（/dp/ ページは8件固定）
MAX_SIM_REVIEWS   = 8     # 類似品1件あたりの上限（同上）
MAX_SIM_PRODUCTS  = 4     # チェックあり: 類似品の収集件数（合計最大40件）

# Amazon.co.jp の /dp/ ページは filterByStar・pageNumber に関わらず同じ8件を返す
# /product-reviews/ はログイン必須。これ以上は取得不可。
STAR_FILTER = {
    1: "one_star",
}

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


# ─────────────────────────────────────────────
# ユーティリティ
# ─────────────────────────────────────────────
def _headers() -> dict:
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    }


def extract_asin(url: str) -> Optional[str]:
    for pat in [
        r"/dp/([A-Z0-9]{10})",
        r"/gp/product/([A-Z0-9]{10})",
        r"asin=([A-Z0-9]{10})",
        r"/product/([A-Z0-9]{10})",
        r"[?&]ASIN=([A-Z0-9]{10})",
        r"/([A-Z0-9]{10})(?:[/?]|$)",
    ]:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def _domain(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _is_blocked(text: str) -> bool:
    lower = text.lower()
    return (
        "robot check" in lower
        or "captcha" in lower
        or "api-services-support" in lower
        or "sorry, we just need to make sure" in lower
    )


# ─────────────────────────────────────────────
# 商品ページ取得
# ─────────────────────────────────────────────
def scrape_product_page(url: str, session: requests.Session) -> dict:
    time.sleep(random.uniform(1.5, 3.0))
    resp = session.get(url, headers=_headers(), timeout=30)
    resp.raise_for_status()

    if _is_blocked(resp.text):
        raise RuntimeError(
            "Amazonがアクセスをブロックしています。"
            "しばらく待つか VPN / ScraperAPI をご利用ください。"
        )

    soup = BeautifulSoup(resp.text, "lxml")
    asin_self = extract_asin(url)
    domain = _domain(url)

    title_el = soup.find("span", id="productTitle")
    title = title_el.get_text(strip=True) if title_el else "不明"

    desc_el = soup.find("div", id="productDescription")
    description = desc_el.get_text(" ", strip=True) if desc_el else ""

    bullets = [
        el.get_text(strip=True)
        for el in soup.select("#feature-bullets li span.a-list-item")
        if el.get_text(strip=True)
    ]

    total_reviews = _get_total_review_count(soup)

    # バリアント（色違い・サイズ違い）のASINを事前収集して除外
    variant_asins = set()
    for var_sec in ["twister_feature_div", "variation_color_name", "variation_size_name",
                    "tp-inline-twister-dim-values-container"]:
        sec = soup.find(id=var_sec) or soup.find(class_=re.compile(var_sec, re.I))
        if sec:
            for a in sec.find_all("a", href=re.compile(r"/dp/[A-Z0-9]{10}")):
                v = extract_asin(a["href"])
                if v:
                    variant_asins.add(v)
    # data-defaultasin / data-asin でバリアントを取得
    for el in soup.find_all(attrs={"data-defaultasin": True}):
        variant_asins.add(el["data-defaultasin"])
    for li in soup.select("li[data-asin]"):
        variant_asins.add(li["data-asin"])

    # 関連商品URL（同カテゴリ商品カルーセルから優先取得）
    seen = variant_asins | ({asin_self} if asin_self else set())
    related_urls = []

    _ACCESSORY_PAT = re.compile(
        r"recommendation|accessory|accs|bundle|add.?on|sponsored|ad_"
        r"|frequently.bought|customers.also|navSwm|nav-swm|nav-main",
        re.I,
    )

    # 優先1: 従来のIDベースカルーセル
    sim_section_ids = [
        "similarities_feature_div",
        "sims-consolidated-2", "sims-consolidated-1",
        "discovery-and-inspiration_feature_div",
        "p13n-sc-carousel-desktop_dp-sims-feature-2",
        "p13n-sc-carousel-desktop_dp-sims-feature-1",
        "sims-fbt", "sims-fbt-content",
        "purchase-sims-feature",
        "sp_detail_thematic-0", "sp_detail_thematic-1",
    ]
    for sec_id in sim_section_ids:
        section = soup.find(id=sec_id)
        if not section:
            continue
        for a in section.find_all("a", href=re.compile(r"/dp/[A-Z0-9]{10}")):
            a_asin = extract_asin(a["href"])
            if a_asin and a_asin not in seen:
                seen.add(a_asin)
                related_urls.append(f"{domain}/dp/{a_asin}")
            if len(related_urls) >= MAX_SIM_PRODUCTS + 2:
                break
        if len(related_urls) >= MAX_SIM_PRODUCTS + 2:
            break

    # 優先2: マルチブランドビデオ（他ブランドの同カテゴリ商品を優先）
    if len(related_urls) < MAX_SIM_PRODUCTS:
        for el in soup.find_all(class_=re.compile(r"multi.brand", re.I)):
            for a in el.find_all("a", href=re.compile(r"/dp/[A-Z0-9]{10}")):
                a_asin = extract_asin(a["href"])
                if a_asin and a_asin not in seen:
                    seen.add(a_asin)
                    related_urls.append(f"{domain}/dp/{a_asin}")
                if len(related_urls) >= MAX_SIM_PRODUCTS + 2:
                    break
            if len(related_urls) >= MAX_SIM_PRODUCTS + 2:
                break

    # 優先3: 比較テーブル（同ブランド内の類似モデル）
    if len(related_urls) < MAX_SIM_PRODUCTS:
        for el in soup.find_all(class_=re.compile(r"apm-tablemodule-imagerows|apm-tablemodule-table", re.I)):
            for a in el.find_all("a", href=re.compile(r"/dp/[A-Z0-9]{10}")):
                a_asin = extract_asin(a["href"])
                if a_asin and a_asin not in seen:
                    seen.add(a_asin)
                    related_urls.append(f"{domain}/dp/{a_asin}")
                if len(related_urls) >= MAX_SIM_PRODUCTS + 2:
                    break
            if len(related_urls) >= MAX_SIM_PRODUCTS + 2:
                break

    # 優先3: 画像付きリンクから（nav・アクセサリー系除外）
    if len(related_urls) < MAX_SIM_PRODUCTS:
        for a in soup.find_all("a", href=re.compile(r"/dp/[A-Z0-9]{10}")):
            if not a.find("img"):
                continue
            # 親要素のクラス/idでアクセサリー系・ナビ系を除外
            skip = False
            p = a.parent
            for _ in range(5):
                if p is None:
                    break
                p_id = p.get("id", "") + " " + " ".join(p.get("class", []))
                if _ACCESSORY_PAT.search(p_id):
                    skip = True
                    break
                p = p.parent
            if skip:
                continue
            a_asin = extract_asin(a["href"])
            if a_asin and a_asin not in seen:
                seen.add(a_asin)
                related_urls.append(f"{domain}/dp/{a_asin}")
            if len(related_urls) >= MAX_SIM_PRODUCTS + 2:
                break

    return {
        "url": url,
        "title": title,
        "description": description,
        "bullets": bullets,
        "total_reviews": total_reviews,
        "related_urls": related_urls,
    }


def _get_total_review_count(soup: BeautifulSoup) -> int:
    for selector in [
        {"data-hook": "total-review-count"},
        {"id": "acrCustomerReviewText"},
    ]:
        el = soup.find("span", selector)
        if el:
            m = re.search(r"[\d,，]+", el.get_text())
            if m:
                return int(m.group().replace(",", "").replace("，", ""))
    for el in soup.find_all("span"):
        m = re.match(r"^([\d,，]+)\s*(件|ratings|reviews)", el.get_text(strip=True))
        if m:
            return int(m.group(1).replace(",", "").replace("，", ""))
    return 0


# ─────────────────────────────────────────────
# レビュー収集
# ─────────────────────────────────────────────
def collect_reviews(
    asin: str,
    domain: str,
    session: requests.Session,
) -> list[dict]:
    """
    /dp/ ページからレビューを取得する。
    Amazon.co.jp は filterByStar/pageNumber を無視して常に同じ8件を返す。
    → フィルターなしで商品ページを1回だけ取得する。
    """
    url = f"{domain}/dp/{asin}"
    time.sleep(random.uniform(1.5, 2.5))
    results = []
    try:
        resp = session.get(url, headers=_headers(), timeout=30)
        resp.raise_for_status()
        if _is_blocked(resp.text):
            return results
        if "ap/signin" in resp.url.lower():
            return results
        soup = BeautifulSoup(resp.text, "lxml")
        for el in soup.find_all("span", {"data-hook": "review-body"}):
            text = el.get_text(" ", strip=True)
            if len(text) > 10:
                results.append({"star": 0, "text": text})
        print(f"[scraper]   レビュー: {len(results)}件取得")
    except Exception as e:
        print(f"[scraper] collect_reviews: {e}")
    return results


# 後方互換性のためのエイリアス
def collect_low_reviews(asin, domain, session, max_total=None):
    return collect_reviews(asin, domain, session)


# ─────────────────────────────────────────────
# メインエントリーポイント
# ─────────────────────────────────────────────
def scrape_all(
    url: str,
    include_similar: bool = True,
    progress_callback=None,
) -> dict:
    """
    Amazon URL を受け取り商品情報・レビューを収集する。

    include_similar=False (チェックなし):
      対象商品の ★1 レビュー最大200件

    include_similar=True (チェックあり):
      類似品4商品 × ★1 最大50件ずつ = 合計最大200件

    Returns dict:
      {
        asin, url, title, description, bullets, total_reviews,
        reviews        : list[{"star": int, "text": str}],
        similar_data   : list[{url, asin, title, reviews}],  # チェックありのみ
        sources        : list[{url, title, asin, type, type_label,
                               stars_collected, review_count, total_on_amazon}],
        include_similar: bool,
        mode           : "main_only" | "with_similar",
      }
    """
    asin = extract_asin(url)
    if not asin:
        raise ValueError(f"ASIN を URL から抽出できませんでした: {url}")

    domain = _domain(url)
    session = requests.Session(impersonate="chrome124")
    sources = []

    def _prog(msg: str, pct: int):
        if progress_callback:
            progress_callback(msg, pct)
        else:
            print(f"[{pct:3d}%] {msg}")

    # ── 対象商品のページ情報（常に取得）──────────────
    _prog("対象商品のページを解析中...", 5)
    product = scrape_product_page(url, session)
    product["asin"] = asin
    product["include_similar"] = include_similar
    product["mode"] = "with_similar" if include_similar else "main_only"

    # ── モード分岐 ────────────────────────────────────
    if not include_similar:
        # ────────────────────────────────────────────
        # チェックなし: Amazon(8件) + Gemini検索(100件)
        # ────────────────────────────────────────────
        _prog("対象商品のレビューを収集中（Amazon）...", 10)
        main_reviews = collect_reviews(asin, domain, session)
        _prog(f"Amazon {len(main_reviews)}件 → Gemini検索でWeb収集中...", 25)
        gemini_reviews = collect_reviews_via_gemini_search(product["title"])
        main_reviews = main_reviews + gemini_reviews
        _prog(f"対象商品レビュー 合計{len(main_reviews)}件 取得完了", 75)

        product["reviews"] = main_reviews
        product["similar_data"] = []

        sources.append({
            "url": url,
            "title": product["title"],
            "asin": asin,
            "type": "main",
            "type_label": "対象商品",
            "stars_collected": "★1〜★5",
            "review_count": len(main_reviews),
            "total_on_amazon": product["total_reviews"],
        })

    else:
        # ────────────────────────────────────────────
        # チェックあり: Amazon(8件) + Gemini検索(100件) + 類似品4商品
        # ────────────────────────────────────────────
        _prog("対象商品のレビューを収集中（Amazon）...", 5)
        main_reviews = collect_reviews(asin, domain, session)
        _prog(f"Amazon {len(main_reviews)}件 → Gemini検索でWeb収集中...", 8)
        gemini_reviews = collect_reviews_via_gemini_search(product["title"])
        main_reviews = main_reviews + gemini_reviews
        product["reviews"] = main_reviews
        _prog(f"対象商品レビュー 合計{len(main_reviews)}件 取得完了", 13)

        related = [u for u in product["related_urls"] if extract_asin(u) != asin]
        targets = related[:MAX_SIM_PRODUCTS]

        _prog(f"類似品 {len(targets)}商品 のレビューを収集します...", 10)

        similar_data = []
        for i, sim_url in enumerate(targets):
            sim_asin = extract_asin(sim_url)
            if not sim_asin:
                continue
            try:
                pct = 15 + int(i / MAX_SIM_PRODUCTS * 60)
                _prog(f"類似品 {i+1}/{len(targets)}「{sim_url[-30:]}」を収集中...", pct)

                sim_page = scrape_product_page(sim_url, session)
                sim_total = sim_page.get("total_reviews", 0)

                sim_reviews = collect_reviews(sim_asin, domain, session)

                similar_data.append({
                    "url": sim_url,
                    "asin": sim_asin,
                    "title": sim_page.get("title", "不明"),
                    "reviews": sim_reviews,
                })
                sources.append({
                    "url": sim_url,
                    "title": sim_page.get("title", "不明"),
                    "asin": sim_asin,
                    "type": "similar",
                    "type_label": "類似品",
                    "stars_collected": "★1〜★5",
                    "review_count": len(sim_reviews),
                    "total_on_amazon": sim_total,
                })
                _prog(f"類似品 {i+1} → {len(sim_reviews)}件 取得", pct + 8)

            except Exception as e:
                print(f"[scraper] similar {sim_url}: {e}")

        product["similar_data"] = similar_data
        total_sim = sum(len(s["reviews"]) for s in similar_data)
        _prog(f"類似品 {len(similar_data)}件 合計 {total_sim}件レビュー 取得完了", 78)

        # 対象商品もソースに記録
        sources.insert(0, {
            "url": url,
            "title": product["title"],
            "asin": asin,
            "type": "main",
            "type_label": "対象商品",
            "stars_collected": "★1〜★5",
            "review_count": len(main_reviews),
            "total_on_amazon": product["total_reviews"],
        })

    product["sources"] = sources
    return product


# ─────────────────────────────────────────────
# Gemini検索グラウンディングによるレビュー収集
# ─────────────────────────────────────────────
def collect_reviews_via_gemini_search(
    title: str,
    api_key: str | None = None,
    target_count: int = 100,
) -> list[dict]:
    """
    Gemini の Google検索グラウンディング機能を使って
    Webからレビュー・口コミを収集する。
    スクレイピング不要・ログイン不要・Amazonの制限を回避。
    """
    try:
        from google import genai
        from google.genai import types as gtypes
    except ImportError:
        print("[scraper] google-genai not installed")
        return []

    from dotenv import load_dotenv
    load_dotenv()
    _api_key = api_key or os.getenv("GEMINI_API_KEY")
    if not _api_key:
        print("[scraper] GEMINI_API_KEY not found")
        return []
    client = genai.Client(api_key=_api_key)

    prompt = f"""「{title}」のユーザーレビュー・口コミをWeb検索して、
以下の形式で日本語レビューを{target_count}件以上収集してください。

【収集先】Amazon、楽天市場、価格.com、Yahoo!ショッピング、個人ブログ、SNSなどあらゆるソース

【出力形式】1件1行、行頭に「・」をつけて、実際のユーザーの声（不満・良い点・気になった点）をそのまま引用または要約してください。
評価の高低に関わらず、できる限り多く（{target_count}件以上）集めてください。
"""

    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=gtypes.GenerateContentConfig(
                tools=[gtypes.Tool(google_search=gtypes.GoogleSearch())],
            ),
        )
        text = resp.text.strip()
    except Exception as e:
        print(f"[scraper] Gemini search error: {e}")
        return []

    # 箇条書き行をパース
    reviews = []
    for line in text.split("\n"):
        line = line.strip()
        # 「・」「-」「*」「数字.」などで始まる行
        m = re.match(r"^[・\-\*\d\.\)]\s*(.+)", line)
        if m:
            text_body = m.group(1).strip()
            if len(text_body) > 10:
                reviews.append({"star": 0, "text": text_body})

    print(f"[scraper] Gemini検索レビュー: {len(reviews)}件取得")
    return reviews


# ─────────────────────────────────────────────
# Makuakeページ取得
# ─────────────────────────────────────────────
_MAKUAKE_URLS = [
    "https://www.makuake.com/project/weecap/",
    "https://www.makuake.com/project/mobi-lock2/",
    "https://www.makuake.com/project/orbitkey3/",
    "https://www.makuake.com/project/couverture/",
    "https://www.makuake.com/project/colofly/",
]

def fetch_makuake_references(n: int = 3) -> list[dict]:
    """Makuakeの売れ筋商品ページからCFページの構成・文章パターンを取得する。"""
    session = requests.Session(impersonate="chrome124")
    results = []
    for url in _MAKUAKE_URLS[:n]:
        try:
            resp = session.get(url, timeout=20)
            soup = BeautifulSoup(resp.text, "lxml")
            # タイトル
            title_el = soup.find("h1") or soup.find("title")
            title = title_el.get_text(strip=True)[:80] if title_el else ""
            # キャッチコピー的なサブタイトル
            catch_el = soup.find("p", class_=re.compile(r"catch|subtitle|lead", re.I))
            catch = catch_el.get_text(strip=True)[:150] if catch_el else ""
            # 本文テキスト（最初の600文字）
            body_els = soup.find_all("p")
            body_texts = [p.get_text(strip=True) for p in body_els if len(p.get_text(strip=True)) > 30]
            body = "\n".join(body_texts[:8])[:600]
            if title:
                results.append({"url": url, "title": title, "catch": catch, "body": body})
            time.sleep(1.0)
        except Exception:
            continue
    return results
