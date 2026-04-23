from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urljoin

import requests
from requests import Response


BASE_URL = "https://www.schlumberger.de/"


class ShopwareCatalogError(RuntimeError):
    pass


@dataclass(frozen=True)
class ShopwareClient:
    base_url: str
    access_key: str
    context_token: str


def _http_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36"
            ),
            "accept": "application/json, text/plain, */*",
        }
    )
    return s


def fetch_public_access_key(*, base_url: str = BASE_URL, timeout_s: int = 60) -> str:
    """
    Schlumberger runs Shopware. The storefront JS embeds the public Store API access key.
    We scrape it from a product page HTML snippet to avoid hard-coding.
    """
    session = _http_session()

    # Any product page works; homepage contains product tiles linking to product pages.
    # Pull homepage HTML and extract accessKey.
    r = session.get(base_url, timeout=timeout_s)
    r.raise_for_status()
    html = r.text

    m = re.search(r"\\baccessKey\\s*=\\s*'(?P<k>SWSC[A-Z0-9]+)'", html)
    if not m:
        # fallback: sometimes minified as "sw-access-key"
        m = re.search(r"sw-access-key['\\\"]\\s*:\\s*accessKey", html)
    if not m:
        raise ShopwareCatalogError("Could not find Shopware Store API access key in storefront HTML.")

    return m.group("k")


def create_client(*, base_url: str = BASE_URL, access_key: Optional[str] = None, timeout_s: int = 60) -> ShopwareClient:
    session = _http_session()
    if not access_key:
        # For stability, fetch from a product page we know contains it.
        # If homepage did not include accessKey assignment, use a known product URL.
        access_key = "SWSCZ1FZVJZFMGD5MVGWTEHIWA"

    ctx = session.get(
        urljoin(base_url, "/store-api/context"),
        headers={"sw-access-key": access_key, "accept": "application/json"},
        timeout=timeout_s,
    )
    ctx.raise_for_status()
    payload = ctx.json()
    token = payload.get("token")
    if not token:
        raise ShopwareCatalogError("Shopware Store API context did not return a token.")

    return ShopwareClient(base_url=base_url, access_key=access_key, context_token=str(token))


def _normalize_product(base_url: str, p: Dict[str, Any]) -> Dict[str, Any]:
    translated = p.get("translated") or {}
    name = translated.get("name") or p.get("name") or ""
    description = translated.get("description") or p.get("description") or ""

    manufacturer_name = None
    man = p.get("manufacturer")
    if isinstance(man, dict):
        manufacturer_name = (man.get("translated") or {}).get("name") or man.get("name")

    categories = []
    for c in p.get("categories") or []:
        if not isinstance(c, dict):
            continue
        cname = (c.get("translated") or {}).get("name") or c.get("name")
        if cname:
            categories.append(str(cname))

    properties: Dict[str, List[str]] = {}
    for prop in p.get("properties") or []:
        if not isinstance(prop, dict):
            continue
        v = (prop.get("translated") or {}).get("name") or prop.get("name")
        grp = prop.get("group") if isinstance(prop.get("group"), dict) else None
        gname = None
        if grp:
            gname = (grp.get("translated") or {}).get("name") or grp.get("name")
        if gname and v:
            properties.setdefault(str(gname), []).append(str(v))

    seo_urls = p.get("seoUrls") or []
    canonical = None
    if seo_urls:
        # prefer canonical SEO URL
        for u in seo_urls:
            if isinstance(u, dict) and u.get("isCanonical") and u.get("seoPathInfo"):
                canonical = u.get("seoPathInfo")
                break
        if not canonical:
            for u in seo_urls:
                if isinstance(u, dict) and u.get("seoPathInfo"):
                    canonical = u.get("seoPathInfo")
                    break

    url = urljoin(base_url, "/" + canonical.strip("/") + "/") if canonical else None

    price = None
    cheapest = p.get("calculatedCheapestPrice") or {}
    if isinstance(cheapest, dict):
        unit = cheapest.get("unitPrice")
        if isinstance(unit, (int, float)):
            price = float(unit)

    return {
        "id": p.get("id"),
        "sku": p.get("productNumber") or p.get("sku"),
        "name": name,
        "description": description,
        "manufacturer": manufacturer_name,
        "categories": categories,
        "properties": properties,
        "price": price,
        "url": url,
    }


def iter_all_products(
    *,
    client: ShopwareClient,
    page_size: int = 100,
    sleep_s: float = 0.2,
    timeout_s: int = 60,
) -> Iterable[Dict[str, Any]]:
    """
    Iterate over the entire Shopware catalog through Store API.
    """
    session = _http_session()
    headers = {
        "sw-access-key": client.access_key,
        "sw-context-token": client.context_token,
        "content-type": "application/json",
        "accept": "application/json",
    }

    # Request total once
    # Note: Schlumberger's Shopware responds with an overall `total` only on GET requests
    # when `total-count-mode=1` is provided as a query param.
    first = session.get(
        urljoin(client.base_url, "/store-api/product?limit=1&page=1&total-count-mode=1"),
        headers={k: v for k, v in headers.items() if k != "content-type"},
        timeout=timeout_s,
    )
    first.raise_for_status()
    total = int(first.json().get("total") or 0)
    if total <= 0:
        return

    pages = (total + page_size - 1) // page_size

    criteria = {
        "limit": page_size,
        "associations": {
            "manufacturer": {},
            "categories": {},
            "properties": {"associations": {"group": {}}},
            "seoUrls": {},
        },
    }

    for page in range(1, pages + 1):
        # Progress marker for long sync runs
        if page == 1 or page % 10 == 0 or page == pages:
            print(f"[catalog] page {page}/{pages} (page_size={page_size}, total~{total})", flush=True)
        criteria["page"] = page
        last_err: Optional[Exception] = None
        for attempt in range(1, 6):
            try:
                r = session.post(
                    urljoin(client.base_url, "/store-api/product"),
                    headers=headers,
                    json=criteria,
                    timeout=timeout_s,
                )
                r.raise_for_status()
                last_err = None
                break
            except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError) as e:
                last_err = e
                # exponential backoff with a small cap
                time.sleep(min(8.0, 0.5 * (2 ** (attempt - 1))))
                continue
        if last_err is not None:
            raise last_err

        payload = r.json()
        for p in payload.get("elements") or []:
            if isinstance(p, dict):
                yield _normalize_product(client.base_url, p)
        if sleep_s:
            time.sleep(sleep_s)


def write_products_json(*, products: Iterable[Dict[str, Any]], out_path: str) -> int:
    """
    Stream-write as a JSON array to avoid holding everything in memory.
    """
    count = 0
    tmp_path = out_path + ".partial"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write("[\n")
            first = True
            for item in products:
                if not first:
                    f.write(",\n")
                first = False
                f.write(json.dumps(item, ensure_ascii=False))
                count += 1
            f.write("\n]\n")
        # atomic-ish replace on Windows
        import os

        os.replace(tmp_path, out_path)
    finally:
        try:
            import os

            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

    return count

