"""
scraper.py: Amazon 商品ページ・レビューを収集する

【2モード】
  チェックなし (include_similar=False):
    対象商品の ★1 レビューを最大200件
    （★1 が足りない場合は ★2 で補完）

  チェックあり (include_similar=True):
    同ページに表示されている類似品4商品
    各商品から ★1 を最大50件 → 合計最大200件
    （★1 が足りない場合は ★2 で補完）

どちらのモードでも対象商品のページ情報（タイトル・特徴・説明）は取得する。
参照したURLはすべて sources リストに記録する。
"""

import re
import time
import random
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from typing import Optional

# ─────────────────────────────────────────────
# 定数
# ─────────────────────────────────────────────
MAX_MAIN_REVIEWS  = 200   # チェックなし: 対象商品レビュー上限
MAX_SIM_REVIEWS   = 50    # チェックあり: 類似品1件あたりの上限
MAX_SIM_PRODUCTS  = 4     # チェックあり: 類似品の収集件数

STAR_FILTER = {
    1: "one_star",
    2: "two_star",
    3: "three_star",
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

    # 関連商品URL（最大 MAX_SIM_PRODUCTS + 2 件 先頭から）
    seen = set()
    related_urls = []
    for a in soup.find_all("a", href=re.compile(r"/dp/[A-Z0-9]{10}")):
        asin = extract_asin(a["href"])
        if asin and asin not in seen and asin != asin_self:
            seen.add(asin)
            related_urls.append(f"{domain}/dp/{asin}")
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
def _fetch_star(
    asin: str,
    domain: str,
    session: requests.Session,
    star: int,
    max_count: int,
) -> list[dict]:
    """指定した星評価のレビューを最大 max_count 件取得する。"""
    results = []
    max_pages = min(20, (max_count // 10) + 1)

    for page in range(1, max_pages + 1):
        if len(results) >= max_count:
            break
        url = (
            f"{domain}/product-reviews/{asin}/"
            f"?filterByStar={STAR_FILTER[star]}&pageNumber={page}"
            f"&reviewerType=all_reviews"
        )
        time.sleep(random.uniform(1.8, 3.0))
        try:
            resp = session.get(url, headers=_headers(), timeout=30)
            resp.raise_for_status()
            if _is_blocked(resp.text):
                break
            soup = BeautifulSoup(resp.text, "lxml")
            bodies = soup.find_all("span", {"data-hook": "review-body"})
            if not bodies:
                break
            for el in bodies:
                text = el.get_text(" ", strip=True)
                if len(text) > 10:
                    results.append({"star": star, "text": text})
        except Exception as e:
            print(f"[scraper] ★{star} p{page}: {e}")
            break

    return results[:max_count]


def collect_low_reviews(
    asin: str,
    domain: str,
    session: requests.Session,
    max_total: int,
) -> list[dict]:
    """
    ★1 を優先して max_total 件収集する。
    ★1 が不足する場合は ★2 で補完する。
    戻り値: [{"star": int, "text": str}, ...]（★1 先頭）
    """
    # ★1 を最大 max_total 件収集
    reviews = _fetch_star(asin, domain, session, star=1, max_count=max_total)

    # ★1 だけで上限に達した場合はそのまま返す
    if len(reviews) >= max_total:
        return reviews[:max_total]

    # 不足分を ★2 で補完
    remaining = max_total - len(reviews)
    r2 = _fetch_star(asin, domain, session, star=2, max_count=remaining)
    reviews.extend(r2)

    return reviews[:max_total]


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
    session = requests.Session()
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
        # チェックなし: 対象商品 ★1 → 200件
        # ────────────────────────────────────────────
        _prog(f"対象商品のレビューを収集中（★1 最大{MAX_MAIN_REVIEWS}件）...", 15)
        main_reviews = collect_low_reviews(
            asin, domain, session, max_total=MAX_MAIN_REVIEWS
        )
        _prog(f"対象商品レビュー {len(main_reviews)}件 取得完了", 75)

        product["reviews"] = main_reviews
        product["similar_data"] = []

        sources.append({
            "url": url,
            "title": product["title"],
            "asin": asin,
            "type": "main",
            "type_label": "対象商品",
            "stars_collected": "★1（不足時★2で補完）",
            "review_count": len(main_reviews),
            "total_on_amazon": product["total_reviews"],
        })

    else:
        # ────────────────────────────────────────────
        # チェックあり: 類似品4商品 × ★1 → 各50件
        # ────────────────────────────────────────────
        # 対象商品のページ情報は取得済み。レビューは不要。
        product["reviews"] = []

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

                sim_reviews = collect_low_reviews(
                    sim_asin, domain, session, max_total=MAX_SIM_REVIEWS
                )

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
                    "stars_collected": "★1（不足時★2で補完）",
                    "review_count": len(sim_reviews),
                    "total_on_amazon": sim_total,
                })
                _prog(f"類似品 {i+1} → {len(sim_reviews)}件 取得", pct + 8)

            except Exception as e:
                print(f"[scraper] similar {sim_url}: {e}")

        product["similar_data"] = similar_data
        total_sim = sum(len(s["reviews"]) for s in similar_data)
        _prog(f"類似品 {len(similar_data)}件 合計 {total_sim}件レビュー 取得完了", 78)

        # 対象商品もソースに記録（ページ情報取得済みのため）
        sources.insert(0, {
            "url": url,
            "title": product["title"],
            "asin": asin,
            "type": "main",
            "type_label": "対象商品（ページ情報のみ）",
            "stars_collected": "レビュー未収集",
            "review_count": 0,
            "total_on_amazon": product["total_reviews"],
        })

    product["sources"] = sources
    return product
