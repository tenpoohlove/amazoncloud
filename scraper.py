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
MAX_SIM_PRODUCTS  = 22    # ページから収集するURL上限（scrape_all で実際の件数を制御する）

# Amazon.co.jp の /dp/ ページは filterByStar・pageNumber に関わらず同じ8件を返す
# /product-reviews/ はログイン必須。これ以上は取得不可。
STAR_FILTER = {
    1: "one_star",
}

_CHROME_PROFILES = [
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "platform": '"Windows"',
        "impersonate": "chrome131",
    },
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "ch_ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "platform": '"Windows"',
        "impersonate": "chrome124",
    },
    {
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "platform": '"macOS"',
        "impersonate": "chrome131",
    },
]


# ─────────────────────────────────────────────
# ユーティリティ
# ─────────────────────────────────────────────
def _headers(referer: str = "") -> dict:
    p = random.choice(_CHROME_PROFILES)
    h = {
        "User-Agent": p["ua"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin" if referer else "none",
        "Sec-Fetch-User": "?1",
        "sec-ch-ua": p["ch_ua"],
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": p["platform"],
        "Cache-Control": "max-age=0",
        "DNT": "1",
    }
    if referer:
        h["Referer"] = referer
    return h


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
        or "enter the characters you see below" in lower
        or "type the characters" in lower
    )


def _warm_session(domain: str, session: requests.Session) -> None:
    """セッションウォーミング: トップページを踏んでCookieを取得する"""
    try:
        time.sleep(random.uniform(0.5, 1.0))
        session.get(domain, headers=_headers(), timeout=10)
        time.sleep(random.uniform(0.5, 1.0))
    except Exception:
        pass


def _get_with_retry(
    url: str,
    session: requests.Session,
    referer: str = "",
    max_retries: int = 1,
) -> "requests.Response | None":
    """ブロック検知時に待機してリトライする"""
    for attempt in range(max_retries + 1):
        if attempt > 0:
            wait = random.uniform(3.0, 5.0) * attempt
            print(f"[scraper] ブロック検知 → {wait:.1f}秒待機してリトライ ({attempt}/{max_retries})")
            time.sleep(wait)
        try:
            resp = session.get(url, headers=_headers(referer), timeout=15)
            resp.raise_for_status()
            if not _is_blocked(resp.text):
                return resp
        except Exception as e:
            print(f"[scraper] リクエストエラー attempt{attempt+1}: {e}")
    return None


# ─────────────────────────────────────────────
# Amazon検索結果ページから商品URLを収集
# ─────────────────────────────────────────────
def scrape_amazon_search(
    keyword: str,
    domain: str,
    session: requests.Session,
    max_urls: int = 20,
    exclude_asins: set | None = None,
) -> list[str]:
    """
    amazon.co.jp/s?k=キーワード から実在する商品URLを取得する。
    1ページで最大48件のオーガニック結果が得られる。
    """
    from urllib.parse import quote
    exclude_asins = set(exclude_asins or [])
    seen = set(exclude_asins)
    urls = []

    search_url = f"{domain}/s?k={quote(keyword)}&language=ja_JP"
    time.sleep(random.uniform(1.5, 2.5))
    resp = _get_with_retry(search_url, session, max_retries=1)
    if resp is None:
        print(f"[scraper] Amazon検索ブロック検出: {keyword}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    divs = soup.find_all("div", attrs={"data-component-type": "s-search-result"})
    for d in divs:
        if "AdHolder" in d.get("class", []):
            continue
        asin = d.get("data-asin", "").strip()
        if asin and asin not in seen:
            seen.add(asin)
            urls.append(f"{domain}/dp/{asin}")
            if len(urls) >= max_urls:
                break

    print(f"[scraper] Amazon検索「{keyword}」: {len(urls)}件取得")
    return urls


# ─────────────────────────────────────────────
# 商品ページ取得
# ─────────────────────────────────────────────
def scrape_product_page(url: str, session: requests.Session) -> dict:
    time.sleep(random.uniform(0.5, 1.0))
    resp = _get_with_retry(url, session, referer="https://www.amazon.co.jp/", max_retries=1)
    if resp is None:
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
def _parse_star_from_el(el) -> int:
    """レビュー要素から星数を取得する。取得できなければ 0 を返す。"""
    # data-hook="review-star-rating" などから取得
    for sel in [
        {"data-hook": "review-star-rating"},
        {"data-hook": "cmps-review-star-rating"},
        {"class": "a-icon-alt"},
    ]:
        star_el = el.find_previous("span", sel) or el.find_next("span", sel)
        if star_el:
            m = re.search(r"(\d)[.,]\d", star_el.get_text())
            if m:
                return int(m.group(1))
    return 0


def _collect_from_product_reviews_page(
    asin: str,
    domain: str,
    session: requests.Session,
    max_pages: int = 5,
    sort_by: str = "recent",
) -> list[dict]:
    """
    /product-reviews/ ページから複数ページにわたってレビューを収集する。
    ログインリダイレクトが来た場合は空リストを返す。
    """
    results = []
    seen_texts: set[str] = set()
    referer = f"{domain}/dp/{asin}"

    for page in range(1, max_pages + 1):
        url = (
            f"{domain}/product-reviews/{asin}"
            f"?ie=UTF8&reviewerType=all_reviews"
            f"&sortBy={sort_by}&pageNumber={page}"
        )
        time.sleep(random.uniform(1.5, 2.5))
        resp = _get_with_retry(url, session, referer=referer, max_retries=1)
        if resp is None:
            break
        if "ap/signin" in resp.url.lower() or "ap/signin" in resp.text.lower()[:500]:
            break
        soup = BeautifulSoup(resp.text, "lxml")
        page_results = []
        for el in soup.find_all("span", {"data-hook": "review-body"}):
            text = el.get_text(" ", strip=True)
            key = text[:20]
            if len(text) > 10 and key not in seen_texts:
                seen_texts.add(key)
                star = _parse_star_from_el(el)
                page_results.append({"star": star, "text": text})
        if not page_results:
            break
        results.extend(page_results)
        print(f"[scraper]   /product-reviews/ p{page}({sort_by}): {len(page_results)}件")
        referer = url

    return results


def collect_reviews(
    asin: str,
    domain: str,
    session: requests.Session,
) -> list[dict]:
    """
    レビューを収集する。
    1. /product-reviews/ sortBy=recent（最大5ページ）
    2. 0件なら sortBy=helpful でリトライ
    3. それでも0件なら /dp/ ページにフォールバック
    """
    results = _collect_from_product_reviews_page(
        asin, domain, session, max_pages=5, sort_by="recent"
    )

    if not results:
        results = _collect_from_product_reviews_page(
            asin, domain, session, max_pages=3, sort_by="helpful"
        )

    if results:
        print(f"[scraper]   レビュー合計: {len(results)}件（/product-reviews/）")
        return results

    # フォールバック: /dp/ ページ
    url = f"{domain}/dp/{asin}"
    time.sleep(random.uniform(1.5, 2.5))
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
                star = _parse_star_from_el(el)
                results.append({"star": star, "text": text})
        print(f"[scraper]   レビュー: {len(results)}件取得（/dp/ fallback）")
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
    max_similar_products: int = 0,
    api_key: str | None = None,
    use_gemini_reviews: bool = False,
) -> dict:
    """
    Amazon URL を受け取り商品情報・レビューを収集する。

    max_similar_products=0: 対象商品のみ（高速モード）
    max_similar_products=N: 類似品N件 × 8件ずつ収集

    Returns dict:
      {
        asin, url, title, description, bullets, total_reviews,
        reviews        : list[{"star": int, "text": str}],
        similar_data   : list[{url, asin, title, reviews}],
        sources        : list[{url, title, asin, type, type_label,
                               stars_collected, review_count, total_on_amazon}],
        include_similar: bool,
        mode           : "main_only" | "with_similar",
      }
    """
    asin = extract_asin(url)
    if not asin:
        raise ValueError(f"ASIN を URL から抽出できませんでした: {url}")

    # include_similar フラグとの後方互換性
    if max_similar_products == 0 and include_similar:
        max_similar_products = 5  # 旧デフォルト相当
    n_similar = max_similar_products

    domain = _domain(url)
    _profile = random.choice(_CHROME_PROFILES)
    session = requests.Session(impersonate=_profile["impersonate"])
    _warm_session(domain, session)
    sources = []

    def _prog(msg: str, pct: int):
        if progress_callback:
            progress_callback(msg, pct)
        else:
            print(f"[{pct:3d}%] {msg}")

    # ── 対象商品のページ情報（常に取得）──────────────
    _prog("対象商品のページを解析中...", 5)
    try:
        product = scrape_product_page(url, session)
        amazon_accessible = True
        _fallback_reviews = []
    except RuntimeError:
        _prog("Amazon直接取得不可 → AI検索で商品情報を取得中...", 8)
        product = fetch_product_info_via_gemini(url, asin, api_key=api_key)
        _fallback_reviews = product.pop("_fallback_reviews", [])
        amazon_accessible = False
    product["asin"] = asin
    product["include_similar"] = n_similar > 0
    product["mode"] = "with_similar" if n_similar > 0 else "main_only"

    # ── モード分岐 ────────────────────────────────────
    if n_similar == 0:
        # ────────────────────────────────────────────
        # チェックなし: Amazon(8件) + Gemini検索(100件)
        # ────────────────────────────────────────────
        if amazon_accessible:
            _prog("対象商品のレビューを収集中（Amazon）...", 10)
            amz_reviews = collect_reviews(asin, domain, session)
        else:
            amz_reviews = []
        if use_gemini_reviews or not amazon_accessible:
            _prog(f"Amazon {len(amz_reviews)}件 → AI Web検索でレビューを収集中...", 25)
            if _fallback_reviews:
                # fallback reviews already collected in fetch_product_info_via_gemini
                gemini_reviews = _fallback_reviews + collect_reviews_via_gemini_search(
                    product["title"], api_key=api_key
                )
            else:
                gemini_reviews = collect_reviews_via_gemini_search(product["title"], api_key=api_key)
            product["gemini_review_count"] = len(gemini_reviews)
        else:
            gemini_reviews = _fallback_reviews  # use fallback reviews even in amazon-only mode
            product["gemini_review_count"] = len(gemini_reviews)
        main_reviews = amz_reviews + gemini_reviews
        product["amazon_review_count"] = len(amz_reviews)
        _prog(f"対象商品レビュー {len(main_reviews)}件 取得完了", 75)

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
        # 類似品モード: Amazon(8件) + Gemini検索 + 類似品n_similar商品
        # ────────────────────────────────────────────
        if amazon_accessible:
            _prog("対象商品のレビューを収集中（Amazon）...", 5)
            amz_reviews = collect_reviews(asin, domain, session)
        else:
            amz_reviews = []
        if use_gemini_reviews or not amazon_accessible:
            _prog(f"Amazon {len(amz_reviews)}件 → AI Web検索でレビューを収集中...", 8)
            if _fallback_reviews:
                gemini_reviews = _fallback_reviews + collect_reviews_via_gemini_search(
                    product["title"], api_key=api_key
                )
            else:
                gemini_reviews = collect_reviews_via_gemini_search(product["title"], api_key=api_key)
            product["gemini_review_count"] = len(gemini_reviews)
        else:
            gemini_reviews = _fallback_reviews
            product["gemini_review_count"] = len(gemini_reviews)
        main_reviews = amz_reviews + gemini_reviews
        product["reviews"] = main_reviews
        product["amazon_review_count"] = len(amz_reviews)
        _prog(f"対象商品レビュー {len(main_reviews)}件 取得完了", 13)

        if not amazon_accessible:
            # Amazon未アクセス: 類似品収集はスキップ
            _prog("Amazon未アクセスのため類似品収集をスキップ", 15)
            related = []
        else:
            # Amazon検索で類似品URLを取得（実在するURL、ハルシネーションなし）
            category_words = [
                t for t in re.split(r'[\s\[\]【】（）()「」、。・/\-_]+', product["title"])
                if len(t) >= 3 and re.search(r'[ぁ-んァ-ン一-龥]', t)
            ][:3]
            search_keyword = " ".join(category_words) if category_words else product["title"][:20]
            _prog(f"Amazon検索「{search_keyword}」で類似品を収集中...", 14)
            related = scrape_amazon_search(
                search_keyword, domain, session,
                max_urls=min(n_similar * 2, 40),
                exclude_asins={asin},
            )
        _prog(f"類似品候補 {len(related)}商品 取得", 15)

        targets = related  # n_similar 件集まるまで全候補を試す
        _prog(f"類似品候補 {len(targets)}商品 → 最大{n_similar}件収集します...", 16)

        # 元商品タイトルのキーワード（3文字以上の日本語トークン）を抽出してフィルタリングに使う
        main_keywords = {
            t for t in re.split(r'[\s\[\]【】（）()「」、。・/\-_]+', product["title"])
            if len(t) >= 3 and re.search(r'[ぁ-んァ-ン一-龥]', t)
        }

        similar_data = []
        for i, sim_url in enumerate(targets):
            if len(similar_data) >= n_similar:
                break
            sim_asin = extract_asin(sim_url)
            if not sim_asin:
                continue
            try:
                pct = 15 + int(len(similar_data) / max(n_similar, 1) * 60)
                _prog(f"類似品 {len(similar_data)+1}/{n_similar}「{sim_url[-30:]}」を収集中...", pct)

                sim_page = scrape_product_page(sim_url, session)
                sim_title = sim_page.get("title", "")
                sim_total = sim_page.get("total_reviews", 0)

                # タイトルが元商品と全く無関係なら除外（ハルシネーションASIN対策）
                if main_keywords:
                    # トークン完全一致 OR いずれかのキーワードがタイトルに含まれる
                    sim_words = set(re.split(r'[\s\[\]【】（）()「」、。・/\-_]+', sim_title))
                    token_match = bool(main_keywords & sim_words)
                    substr_match = any(kw in sim_title for kw in main_keywords)
                    if not (token_match or substr_match):
                        print(f"[scraper] カテゴリ不一致スキップ: {sim_title[:40]}")
                        continue

                sim_reviews = collect_reviews(sim_asin, domain, session)

                similar_data.append({
                    "url": sim_url,
                    "asin": sim_asin,
                    "title": sim_title,
                    "reviews": sim_reviews,
                })
                sources.append({
                    "url": sim_url,
                    "title": sim_title,
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
# Gemini検索グラウンディングによるレビュー収集 / 類似品検索
# ─────────────────────────────────────────────
def fetch_product_info_via_gemini(
    url: str,
    asin: str,
    api_key: str | None = None,
) -> dict:
    """
    AmazonがブロックされたときにGemini検索でレビューを収集し、
    レビューテキストから商品名を抽出するフォールバック。
    Returns dict with keys: url, title, description, bullets, total_reviews, related_urls
    """
    try:
        from google import genai
        from google.genai import types as gtypes
    except ImportError:
        return {"url": url, "title": f"Amazon商品 ({asin})", "description": "", "bullets": [], "total_reviews": 0, "related_urls": []}

    from dotenv import load_dotenv
    load_dotenv()
    _api_key = api_key or os.getenv("GEMINI_API_KEY")
    if not _api_key:
        return {"url": url, "title": f"Amazon商品 ({asin})", "description": "", "bullets": [], "total_reviews": 0, "related_urls": []}

    client = genai.Client(api_key=_api_key)
    _SEARCH_CONFIG = gtypes.GenerateContentConfig(
        tools=[gtypes.Tool(google_search=gtypes.GoogleSearch())],
        thinking_config=gtypes.ThinkingConfig(thinking_budget=0),
    )

    # Step 1: レビューを収集（URLベースの検索）
    review_prompt = (
        f'Search for user reviews of the Amazon Japan product at this URL: {url} '
        f'(ASIN: {asin}). Collect 10+ Japanese user reviews from Amazon, '
        f'shopping sites, and blogs. Format: one review per line starting with "・"'
    )
    title = f"Amazon商品 ({asin})"
    description = ""
    bullets = []
    review_texts = []

    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=review_prompt,
            config=_SEARCH_CONFIG,
        )
        review_raw = (resp.text or "").strip()
        review_texts = _parse_review_lines(review_raw)
        print(f"[scraper] Gemini URLベース検索: {len(review_texts)}件のレビュー取得")
    except Exception as e:
        print(f"[scraper] Gemini review search error: {e}")

    # Step 2: レビューから商品名を抽出（検索グラウンディングなし）
    if review_texts:
        sample = "\n".join(review_texts[:5])
        extract_prompt = (
            f"以下はAmazon商品（URL: {url}, ASIN: {asin}）のユーザーレビューです。\n"
            f"レビューの内容から、この商品の日本語商品名を推測してください。\n"
            f"レビュー:\n{sample}\n\n"
            f"商品名（25文字以内）:"
        )
        try:
            resp2 = client.models.generate_content(model="gemini-2.5-flash", contents=extract_prompt)
            extracted = (resp2.text or "").strip()
            if extracted and len(extracted) < 80 and "不明" not in extracted and "わかり" not in extracted:
                title = extracted.split("\n")[0].strip()[:60]
        except Exception as e:
            print(f"[scraper] Gemini title extraction error: {e}")

    print(f"[scraper] Geminiフォールバック完了: title={title[:30]}, reviews={len(review_texts)}")
    reviews = [{"star": 0, "text": t} for t in review_texts]
    return {
        "url": url,
        "title": title,
        "description": description,
        "bullets": bullets,
        "total_reviews": 0,
        "related_urls": [],
        "_fallback_reviews": reviews,
    }


def find_similar_products_via_gemini(
    title: str,
    domain: str = "https://www.amazon.co.jp",
    api_key: str | None = None,
    target_count: int = 6,
    existing_asins: set | None = None,
) -> list[str]:
    """
    Gemini検索で同カテゴリ他ブランド商品のAmazon URLを取得する。
    target_count に達するまで最大3回リトライする。
    """
    try:
        from google import genai
        from google.genai import types as gtypes
    except ImportError:
        return []

    from dotenv import load_dotenv
    load_dotenv()
    _api_key = api_key or os.getenv("GEMINI_API_KEY")
    if not _api_key:
        return []

    client = genai.Client(api_key=_api_key)
    seen_asins: set[str] = set(existing_asins or [])
    urls: list[str] = []

    for attempt in range(3):
        remaining = target_count - len(urls)
        if remaining <= 0:
            break

        exclude_note = ""
        if urls:
            collected = ", ".join(u.split("/dp/")[1] for u in urls)
            exclude_note = f"\n※ 以下のASINは既に取得済みなので除外してください: {collected}"

        # 商品タイトルからカテゴリキーワードだけ抽出（長いブランド名等を除いてシンプルに）
        category_words = [
            t for t in re.split(r'[\s\[\]【】（）()「」、。・/\-_]+', title)
            if len(t) >= 3 and re.search(r'[ぁ-んァ-ン一-龥]', t)
        ][:3]
        category_hint = " ".join(category_words) if category_words else title[:20]

        prompt = f"""Google検索で「{category_hint} site:amazon.co.jp」を検索して、検索結果に表示されたAmazon.co.jpの商品ページURLを{remaining}件収集してください。

【重要】
- Google検索結果に実際に表示されたURLのみ出力してください（URLを自分で作らないこと）
- amazon.co.jp/dp/で始まる商品ページURLのみ
- 「{category_hint}」カテゴリの商品のみ（全く異なる商品は除外）
- 元商品「{title[:30]}」と異なるブランドを優先{exclude_note}

出力形式（1商品1行）:
https://www.amazon.co.jp/dp/ASIN | 商品名
"""

        try:
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=gtypes.GenerateContentConfig(
                    tools=[gtypes.Tool(google_search=gtypes.GoogleSearch())],
                    thinking_config=gtypes.ThinkingConfig(thinking_budget=0),
                ),
            )
            text = (resp.text or "").strip()
        except Exception as e:
            print(f"[scraper] Gemini similar search error (attempt {attempt+1}): {e}", flush=True)
            raise  # 上位のtry-exceptに伝播させてprogress経由で表示

        found_this_round = 0
        for m in re.finditer(r"amazon\.co\.jp/(?:dp|gp/product)/([A-Z0-9]{10})", text):
            a = m.group(1)
            if a not in seen_asins:
                seen_asins.add(a)
                urls.append(f"{domain}/dp/{a}")
                found_this_round += 1
                if len(urls) >= target_count:
                    break
        if not found_this_round:
            for m in re.finditer(r"\b(B0[A-Z0-9]{8})\b", text):
                a = m.group(1)
                if a not in seen_asins:
                    seen_asins.add(a)
                    urls.append(f"{domain}/dp/{a}")
                    found_this_round += 1
                    if len(urls) >= target_count:
                        break

        print(f"[scraper] Gemini類似品検索 attempt {attempt+1}: {found_this_round}件取得 (合計{len(urls)}件)")
        if found_this_round == 0:
            break

    return urls


def _parse_review_lines(text: str) -> list[str]:
    """箇条書きテキストからレビュー文字列リストを抽出する"""
    results = []
    for line in text.split("\n"):
        line = line.strip()
        m = re.match(r"^[・\-\*\d\.\)]\s*(.+)", line)
        if m:
            body = m.group(1).strip()
            if len(body) > 10:
                results.append(body)
    return results


def _dedup_reviews(existing: list[str], new_items: list[str]) -> list[str]:
    """先頭15文字が一致するものを重複とみなして除外する"""
    seen = {r[:15] for r in existing}
    added = []
    for item in new_items:
        key = item[:15]
        if key not in seen:
            seen.add(key)
            added.append(item)
    return added


def collect_reviews_via_gemini_search(
    title: str,
    api_key: str | None = None,
    target_count: int = 100,
) -> list[dict]:
    """
    Gemini の Google検索グラウンディング機能を使って
    Webからレビュー・口コミを収集する。
    2回の検索（全評価 + 低評価特化）を行いマージ・重複除去する。
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

    _SEARCH_CONFIG = gtypes.GenerateContentConfig(
        tools=[gtypes.Tool(google_search=gtypes.GoogleSearch())],
        thinking_config=gtypes.ThinkingConfig(thinking_budget=0),
    )

    def _call(prompt: str) -> list[str]:
        try:
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=_SEARCH_CONFIG,
            )
            text = resp.text or ""
            return _parse_review_lines(text.strip())
        except Exception as e:
            print(f"[scraper] Gemini search error: {e}")
            return []

    # ── 検索1: 全評価の口コミを幅広く収集 ──────────────
    prompt_general = f"""「{title}」のユーザーレビュー・口コミをWeb検索して、
日本語レビューを{target_count}件以上収集してください。

【収集先】Amazon、楽天市場、価格.com、Yahoo!ショッピング、個人ブログ、SNSなどあらゆるソース
【出力形式】1件1行、行頭に「・」をつけて、実際のユーザーの声をそのまま引用または要約。
評価の高低に関わらず、できる限り多く（{target_count}件以上）集めてください。
"""
    general = _call(prompt_general)
    print(f"[scraper] Gemini検索①（全評価）: {len(general)}件")

    # ── 検索2: 不満・低評価・問題点に特化 ────────────
    prompt_negative = f"""「{title}」について、**不満・問題点・デメリット・★1〜★3の低評価レビュー**をWeb検索して収集してください。

【重点収集先】Amazon低評価レビュー、価格.com辛口レビュー、Twitter/X の不満投稿、比較記事のデメリット欄
【出力形式】1件1行、行頭に「・」をつけて、実際のユーザーの不満・問題点・改善要望をそのまま引用または要約。
できる限り多く（{target_count}件以上）集めてください。
"""
    negative_raw = _call(prompt_negative)
    negative = _dedup_reviews(general, negative_raw)
    print(f"[scraper] Gemini検索②（低評価特化）: {len(negative_raw)}件 → 重複除去後{len(negative)}件追加")

    all_texts = general + negative
    reviews = [{"star": 0, "text": t} for t in all_texts]
    print(f"[scraper] Gemini検索レビュー合計: {len(reviews)}件取得")
    return reviews



# ─────────────────────────────────────────────
# Makuakeページ取得
# ─────────────────────────────────────────────
def fetch_makuake_references(keyword: str = "", n: int = 2) -> list[dict]:
    """
    キーワードでMakuakeを検索し、関連する成功プロジェクトの文章を取得する。
    検索失敗時は固定URLにフォールバック。
    """
    session = requests.Session(impersonate="chrome124")
    project_urls = []

    # Makuake検索でキーワード関連プロジェクトURLを収集
    if keyword:
        from urllib.parse import quote
        search_url = f"https://www.makuake.com/search/project/?word={quote(keyword)}&sort=funded_ratio"
        try:
            time.sleep(random.uniform(1.0, 2.0))
            resp = session.get(search_url, headers=_headers(), timeout=20)
            soup = BeautifulSoup(resp.text, "lxml")
            for a in soup.find_all("a", href=re.compile(r"/project/[^/]+/$")):
                href = a["href"]
                full = href if href.startswith("http") else f"https://www.makuake.com{href}"
                if full not in project_urls:
                    project_urls.append(full)
                if len(project_urls) >= n * 2:
                    break
            print(f"[scraper] Makuake検索「{keyword}」: {len(project_urls)}件のプロジェクト発見")
        except Exception as e:
            print(f"[scraper] Makuake検索エラー: {e}")

    # フォールバック: 固定URL補完
    _FALLBACK = [
        "https://www.makuake.com/project/weecap/",
        "https://www.makuake.com/project/mobi-lock2/",
        "https://www.makuake.com/project/orbitkey3/",
        "https://www.makuake.com/project/couverture/",
        "https://www.makuake.com/project/colofly/",
    ]
    for u in _FALLBACK:
        if u not in project_urls:
            project_urls.append(u)
        if len(project_urls) >= n * 3:
            break

    results = []
    for url in project_urls[:n * 2]:  # 多めに試してn件集まるまで
        if len(results) >= n:
            break
        try:
            time.sleep(random.uniform(0.8, 1.5))
            resp = session.get(url, headers=_headers(), timeout=20)
            if resp.status_code >= 400:
                continue
            soup = BeautifulSoup(resp.text, "lxml")
            title_el = soup.find("h1") or soup.find("title")
            title = title_el.get_text(strip=True)[:80] if title_el else ""
            catch_el = soup.find("p", class_=re.compile(r"catch|subtitle|lead", re.I))
            catch = catch_el.get_text(strip=True)[:150] if catch_el else ""
            body_els = soup.find_all("p")
            body_texts = [p.get_text(strip=True) for p in body_els if len(p.get_text(strip=True)) > 30]
            body = "\n".join(body_texts[:8])[:600]
            if title:
                results.append({"url": url, "title": title, "catch": catch, "body": body})
        except Exception:
            continue

    return results
