import random
import re
import requests
from urllib.parse import urlparse, parse_qs

SEARCH_PRESETS = {
    "random": ["smart home", "accessori auto", "cucina", "fai da te", "giardino", "sport", "gadget utili"],
    "trend": ["prodotti di tendenza", "gadget tecnologia", "casa intelligente"],
    "bestseller": ["best seller", "più venduti"],
    "new": ["novità", "nuovi arrivi"],
    "deals": ["offerte", "occasioni"],
}


def extract_asin(url):
    if not url:
        return ""
    m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)", url, re.I)
    if m:
        return m.group(1).upper()
    qs = parse_qs(urlparse(url).query)
    for k in ("asin", "ASIN"):
        if qs.get(k):
            return qs[k][0].upper()
    return ""


def _token(settings):
    token_url = settings.get("amazon_token_url") or "https://api.amazon.co.uk/auth/o2/token"
    cid = settings.get("amazon_credential_id", "").strip()
    secret = settings.get("amazon_secret", "").strip()
    if not cid or not secret:
        raise RuntimeError("Credenziali Amazon Creators API non configurate")
    r = requests.post(
        token_url,
        data={"grant_type": "client_credentials", "client_id": cid, "client_secret": secret},
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"Amazon OAuth: HTTP {r.status_code} - {r.text[:400]}")
    return r.json().get("access_token")


def search_products(settings, mode="random", query="", max_price=None):
    """Adapter Creators API. Se l'account/API usa un endpoint diverso, basta modificare amazon_api_base nelle impostazioni.
    La risposta viene normalizzata quando contiene strutture Item/Items compatibili con SearchItems.
    """
    term = query.strip() or random.choice(SEARCH_PRESETS.get(mode, SEARCH_PRESETS["random"]))
    token = _token(settings)
    base = (settings.get("amazon_api_base") or "https://creatorsapi.amazon").rstrip("/")
    partner = settings.get("amazon_partner_tag", "").strip()
    marketplace = settings.get("amazon_marketplace") or "www.amazon.it"

    payload = {
        "Keywords": term,
        "PartnerTag": partner,
        "Marketplace": marketplace,
        "ItemCount": 10,
        "Resources": [
            "Images.Primary.Large",
            "ItemInfo.Title",
            "Offers.Listings.Price",
        ],
    }
    if max_price:
        payload["MaxPrice"] = int(float(max_price) * 100)

    candidates = [
        f"{base}/searchitems",
        f"{base}/paapi5/searchitems",
        f"{base}/catalog/v1/searchitems",
    ]
    last_error = None
    for url in candidates:
        try:
            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=45,
            )
            if r.status_code == 404:
                last_error = f"Endpoint non trovato: {url}"
                continue
            if not r.ok:
                raise RuntimeError(f"Amazon SearchItems: HTTP {r.status_code} - {r.text[:500]}")
            data = r.json()
            items = data.get("SearchResult", {}).get("Items") or data.get("Items") or data.get("items") or []
            return [_normalize_item(i, partner, marketplace) for i in items]
        except requests.RequestException as e:
            last_error = str(e)
    raise RuntimeError(last_error or "Nessun endpoint Amazon Creators API utilizzabile")


def _normalize_item(item, partner, marketplace):
    asin = item.get("ASIN") or item.get("asin") or ""
    title = (((item.get("ItemInfo") or {}).get("Title") or {}).get("DisplayValue")
             or item.get("title") or "Prodotto Amazon")
    image = (((((item.get("Images") or {}).get("Primary") or {}).get("Large") or {}).get("URL"))
             or item.get("image") or item.get("image_url") or "")
    price = ""
    listings = (((item.get("Offers") or {}).get("Listings")) or [])
    if listings:
        p = (listings[0].get("Price") or {})
        price = p.get("DisplayAmount") or ""
    detail = item.get("DetailPageURL") or item.get("url") or ""
    if not detail and asin:
        detail = f"https://{marketplace}/dp/{asin}?tag={partner}"
    return {"asin": asin, "title": title, "image_url": image, "price": price, "amazon_url": detail}
