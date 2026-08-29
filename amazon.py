import random
import re
from urllib.parse import urlparse, parse_qs

import requests

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
    match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)", url, re.I)
    if match:
        return match.group(1).upper()
    try:
        query_string = parse_qs(urlparse(url).query)
        for key in ("asin", "ASIN"):
            if query_string.get(key):
                return query_string[key][0].upper()
    except Exception:
        pass
    return ""


def _token(settings):
    token_url = (settings.get("amazon_token_url") or "https://api.amazon.co.uk/auth/o2/token").strip()
    credential_id = settings.get("amazon_credential_id", "").strip()
    secret = settings.get("amazon_secret", "").strip()
    if not credential_id or not secret:
        raise RuntimeError("Credenziali Amazon Creators API non configurate")
    payload = {"grant_type": "client_credentials", "client_id": credential_id, "client_secret": secret, "scope": "creatorsapi::default"}
    try:
        response = requests.post(token_url, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
    except requests.RequestException as exc:
        raise RuntimeError(f"Errore connessione Amazon OAuth: {exc}") from exc
    if not response.ok:
        raise RuntimeError(f"Amazon OAuth: HTTP {response.status_code} - {response.text[:500]}")
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("Amazon OAuth ha restituito una risposta non JSON") from exc
    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError(f"Amazon OAuth non ha restituito access_token: {data}")
    return access_token


def search_products(settings, mode="random", query="", max_price=None):
    query = (query or "").strip()
    search_term = query if query else random.choice(SEARCH_PRESETS.get(mode, SEARCH_PRESETS["random"]))
    token = _token(settings)
    base = (settings.get("amazon_api_base") or "https://creatorsapi.amazon").strip().rstrip("/")
    partner_tag = settings.get("amazon_partner_tag", "").strip()
    marketplace = (settings.get("amazon_marketplace") or "www.amazon.it").strip()
    if not partner_tag:
        raise RuntimeError("Partner Tag Amazon non configurato")

    url = f"{base}/catalog/v1/searchItems"
    payload = {
        "keywords": search_term,
        "partnerTag": partner_tag,
        "marketplace": marketplace,
        "itemCount": 10,
        "resources": [
            "images.primary.large",
            "itemInfo.title",
            "itemInfo.byLineInfo",
            "itemInfo.classifications",
            "itemInfo.features",
            "itemInfo.productInfo",
            "itemInfo.technicalInfo",
            "browseNodeInfo.browseNodes",
            "offersV2.listings.price",
            "offersV2.listings.availability",
        ],
    }
    if max_price not in (None, ""):
        try:
            price_filter = float(str(max_price).replace(",", "."))
            if price_filter < 0:
                payload["minPrice"] = int(round(abs(price_filter) * 100))
            elif price_filter > 0:
                payload["maxPrice"] = int(round(price_filter * 100))
        except (TypeError, ValueError):
            raise RuntimeError(f"Prezzo non valido: {max_price}")

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json", "x-marketplace": marketplace}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=45)
    except requests.RequestException as exc:
        raise RuntimeError(f"Errore connessione Amazon SearchItems: {exc}") from exc
    if not response.ok:
        raise RuntimeError(f"Amazon SearchItems: HTTP {response.status_code} - {response.text[:1000]}")
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("Amazon SearchItems ha restituito una risposta non JSON") from exc

    search_result = data.get("searchResult") or data.get("SearchResult") or {}
    items = search_result.get("items") or search_result.get("Items") or data.get("items") or data.get("Items") or []
    normalized = []
    for item in items:
        try:
            product = _normalize_item(item, partner_tag, marketplace)
            if product.get("asin") or product.get("title"):
                normalized.append(product)
        except Exception:
            continue
    return normalized


def _display(value):
    if isinstance(value, dict):
        result = value.get("displayValue")
        if result is None:
            result = value.get("DisplayValue")
        return result
    return value


def _format_value(value):
    value = _display(value)
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, bool):
        return "Sì" if value else "No"
    if isinstance(value, list):
        parts = [_format_value(v) for v in value]
        return ", ".join(p for p in parts if p)
    if isinstance(value, dict):
        parts = []
        for key, val in value.items():
            formatted = _format_value(val)
            if formatted:
                parts.append(f"{key}: {formatted}")
        return "; ".join(parts)
    return str(value)


def _normalize_item(item, partner_tag, marketplace):
    asin = item.get("asin") or item.get("ASIN") or ""
    item_info = item.get("itemInfo") or item.get("ItemInfo") or {}
    title_data = item_info.get("title") or item_info.get("Title") or {}
    title = _display(title_data) or item.get("title") or "Prodotto Amazon"
    images = item.get("images") or item.get("Images") or {}
    primary = images.get("primary") or images.get("Primary") or {}
    large = primary.get("large") or primary.get("Large") or {}
    medium = primary.get("medium") or primary.get("Medium") or {}
    image_url = large.get("url") or large.get("URL") or medium.get("url") or medium.get("URL") or item.get("image") or item.get("image_url") or ""
    price = ""
    offers_v2 = item.get("offersV2") or item.get("OffersV2") or {}
    listings = offers_v2.get("listings") or offers_v2.get("Listings") or []
    if not listings:
        offers = item.get("offers") or item.get("Offers") or {}
        listings = offers.get("listings") or offers.get("Listings") or []
    if listings:
        listing = listings[0] or {}
        price_data = listing.get("price") or listing.get("Price") or {}
        money = price_data.get("money") or price_data.get("Money") or price_data
        price = money.get("displayAmount") or money.get("DisplayAmount") or price_data.get("displayAmount") or price_data.get("DisplayAmount") or ""
        if not price:
            amount = money.get("amount") if isinstance(money, dict) else None
            if amount is None and isinstance(money, dict): amount = money.get("Amount")
            currency = ""
            if isinstance(money, dict): currency = money.get("currency") or money.get("Currency") or money.get("currencyCode") or money.get("CurrencyCode") or ""
            if amount not in (None, ""): price = f"{amount} €" if currency == "EUR" else (f"{amount} {currency}" if currency else str(amount))
    classifications = item_info.get("classifications") or item_info.get("Classifications") or {}
    category = _display(classifications.get("productGroup") or classifications.get("ProductGroup")) or _display(classifications.get("binding") or classifications.get("Binding")) or ""
    browse_info = item.get("browseNodeInfo") or item.get("BrowseNodeInfo") or {}
    browse_nodes = browse_info.get("browseNodes") or browse_info.get("BrowseNodes") or []
    if not category and browse_nodes:
        node = browse_nodes[0] or {}; category = node.get("contextFreeName") or node.get("ContextFreeName") or node.get("displayName") or node.get("DisplayName") or ""
    features_data = item_info.get("features") or item_info.get("Features") or {}
    feature_values = features_data.get("displayValues") or features_data.get("DisplayValues") or [] if isinstance(features_data, dict) else []
    if not feature_values and isinstance(features_data, dict):
        single = features_data.get("displayValue") or features_data.get("DisplayValue")
        if single: feature_values = single if isinstance(single, list) else [single]
    features = "\n".join(str(v).strip() for v in feature_values if str(v).strip())
    note_parts = []
    byline = item_info.get("byLineInfo") or item_info.get("ByLineInfo") or {}
    for label, keys in [("Marca", ("brand", "Brand")), ("Produttore", ("manufacturer", "Manufacturer")), ("Colore", ("color", "Color")), ("Dimensione", ("size", "Size"))]:
        source = byline if label in ("Marca", "Produttore") else (item_info.get("productInfo") or item_info.get("ProductInfo") or {})
        raw = None
        for key in keys:
            if isinstance(source, dict) and source.get(key) not in (None, ""): raw = source.get(key); break
        value = _format_value(raw)
        if value: note_parts.append(f"{label}: {value}")
    technical = item_info.get("technicalInfo") or item_info.get("TechnicalInfo") or {}
    tech_text = _format_value(technical)
    if tech_text: note_parts.append(f"Dati tecnici: {tech_text}")
    notes = "\n".join(note_parts)
    detail_url = item.get("detailPageURL") or item.get("DetailPageURL") or item.get("url") or ""
    if not detail_url and asin: detail_url = f"https://{marketplace}/dp/{asin}?tag={partner_tag}"
    return {"asin": asin, "title": title, "image_url": image_url, "price": price, "amazon_url": detail_url, "category": category, "features": features, "notes": notes}
