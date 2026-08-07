import random
import re
from urllib.parse import urlparse, parse_qs

import requests


SEARCH_PRESETS = {
    "random": [
        "smart home",
        "accessori auto",
        "cucina",
        "fai da te",
        "giardino",
        "sport",
        "gadget utili",
    ],
    "trend": [
        "prodotti di tendenza",
        "gadget tecnologia",
        "casa intelligente",
    ],
    "bestseller": [
        "best seller",
        "più venduti",
    ],
    "new": [
        "novità",
        "nuovi arrivi",
    ],
    "deals": [
        "offerte",
        "occasioni",
    ],
}


def extract_asin(url):
    """
    Estrae l'ASIN da un URL Amazon.
    """
    if not url:
        return ""

    match = re.search(
        r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)",
        url,
        re.I,
    )

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
    """
    Ottiene il token OAuth 2.0 per Amazon Creators API.
    """

    token_url = (
        settings.get("amazon_token_url")
        or "https://api.amazon.co.uk/auth/o2/token"
    ).strip()

    credential_id = settings.get(
        "amazon_credential_id",
        "",
    ).strip()

    secret = settings.get(
        "amazon_secret",
        "",
    ).strip()

    if not credential_id or not secret:
        raise RuntimeError(
            "Credenziali Amazon Creators API non configurate"
        )

    payload = {
        "grant_type": "client_credentials",
        "client_id": credential_id,
        "client_secret": secret,
        "scope": "creatorsapi::default",
    }

    try:
        response = requests.post(
            token_url,
            json=payload,
            headers={
                "Content-Type": "application/json",
            },
            timeout=30,
        )

    except requests.RequestException as exc:
        raise RuntimeError(
            f"Errore connessione Amazon OAuth: {exc}"
        ) from exc

    if not response.ok:
        raise RuntimeError(
            f"Amazon OAuth: HTTP {response.status_code} - "
            f"{response.text[:500]}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "Amazon OAuth ha restituito una risposta non JSON"
        ) from exc

    access_token = data.get("access_token")

    if not access_token:
        raise RuntimeError(
            f"Amazon OAuth non ha restituito access_token: {data}"
        )

    return access_token


def search_products(
    settings,
    mode="random",
    query="",
    max_price=None,
):
    """
    Cerca prodotti tramite Amazon Creators API SearchItems.

    Restituisce una lista normalizzata contenente:
    asin
    title
    image_url
    price
    amazon_url
    """

    query = (query or "").strip()

    if query:
        search_term = query
    else:
        presets = SEARCH_PRESETS.get(
            mode,
            SEARCH_PRESETS["random"],
        )
        search_term = random.choice(presets)

    token = _token(settings)

    base = (
        settings.get("amazon_api_base")
        or "https://creatorsapi.amazon"
    ).strip().rstrip("/")

    partner_tag = settings.get(
        "amazon_partner_tag",
        "",
    ).strip()

    marketplace = (
        settings.get("amazon_marketplace")
        or "www.amazon.it"
    ).strip()

    if not partner_tag:
        raise RuntimeError(
            "Partner Tag Amazon non configurato"
        )

    # Endpoint ufficiale Creators API
    url = f"{base}/catalog/v1/searchItems"

    payload = {
        "keywords": search_term,
        "partnerTag": partner_tag,
        "marketplace": marketplace,
        "itemCount": 10,
        "resources": [
            "images.primary.large",
            "itemInfo.title",
            "offersV2.listings.price",
        ],
    }

    # Amazon vuole il prezzo nell'unità minima:
    # 30,00 € -> 3000
    if max_price not in (None, ""):
        try:
            max_price_float = float(
                str(max_price).replace(",", ".")
            )

            if max_price_float > 0:
                payload["maxPrice"] = int(
                    round(max_price_float * 100)
                )

        except (TypeError, ValueError):
            raise RuntimeError(
                f"Prezzo massimo non valido: {max_price}"
            )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-marketplace": marketplace,
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=45,
        )

    except requests.RequestException as exc:
        raise RuntimeError(
            f"Errore connessione Amazon SearchItems: {exc}"
        ) from exc

    if not response.ok:
        raise RuntimeError(
            f"Amazon SearchItems: HTTP "
            f"{response.status_code} - "
            f"{response.text[:1000]}"
        )

    try:
        data = response.json()

    except ValueError as exc:
        raise RuntimeError(
            "Amazon SearchItems ha restituito "
            "una risposta non JSON"
        ) from exc

    # Creators API usa normalmente:
    #
    # {
    #   "searchResult": {
    #       "items": [...]
    #   }
    # }

    search_result = (
        data.get("searchResult")
        or data.get("SearchResult")
        or {}
    )

    items = (
        search_result.get("items")
        or search_result.get("Items")
        or data.get("items")
        or data.get("Items")
        or []
    )

    normalized = []

    for item in items:
        try:
            product = _normalize_item(
                item,
                partner_tag,
                marketplace,
            )

            if product.get("asin") or product.get("title"):
                normalized.append(product)

        except Exception:
            # Un prodotto malformato non deve bloccare
            # tutta la ricerca.
            continue

    return normalized


def _normalize_item(item, partner_tag, marketplace):
    """
    Normalizza un prodotto restituito dalla Creators API.

    Gestisce sia la struttura nuova in camelCase
    sia alcune strutture PA-API precedenti.
    """

    asin = (
        item.get("asin")
        or item.get("ASIN")
        or ""
    )

    # -------------------------
    # TITOLO
    # -------------------------

    item_info = (
        item.get("itemInfo")
        or item.get("ItemInfo")
        or {}
    )

    title_data = (
        item_info.get("title")
        or item_info.get("Title")
        or {}
    )

    title = (
        title_data.get("displayValue")
        or title_data.get("DisplayValue")
        or item.get("title")
        or "Prodotto Amazon"
    )

    # -------------------------
    # IMMAGINE
    # -------------------------

    images = (
        item.get("images")
        or item.get("Images")
        or {}
    )

    primary = (
        images.get("primary")
        or images.get("Primary")
        or {}
    )

    large = (
        primary.get("large")
        or primary.get("Large")
        or {}
    )

    medium = (
        primary.get("medium")
        or primary.get("Medium")
        or {}
    )

    image_url = (
        large.get("url")
        or large.get("URL")
        or medium.get("url")
        or medium.get("URL")
        or item.get("image")
        or item.get("image_url")
        or ""
    )

    # -------------------------
    # PREZZO
    # -------------------------

    price = ""

    # Nuova Creators API / OffersV2
    offers_v2 = (
        item.get("offersV2")
        or item.get("OffersV2")
        or {}
    )

    listings = (
        offers_v2.get("listings")
        or offers_v2.get("Listings")
        or []
    )

    # Fallback per eventuali risposte compatibili
    # con la vecchia PA-API.
    if not listings:
        offers = (
            item.get("offers")
            or item.get("Offers")
            or {}
        )

        listings = (
            offers.get("listings")
            or offers.get("Listings")
            or []
        )

    if listings:
        listing = listings[0] or {}

        price_data = (
            listing.get("price")
            or listing.get("Price")
            or {}
        )

        price = (
            price_data.get("displayAmount")
            or price_data.get("DisplayAmount")
            or ""
        )

        # Alcune risposte possono fornire
        # amount + currency anziché displayAmount.
        if not price:
            amount = (
                price_data.get("amount")
                or price_data.get("Amount")
            )

            currency = (
                price_data.get("currency")
                or price_data.get("Currency")
                or price_data.get("currencyCode")
                or price_data.get("CurrencyCode")
                or ""
            )

            if amount not in (None, ""):
                if currency == "EUR":
                    price = f"{amount} €"
                elif currency:
                    price = f"{amount} {currency}"
                else:
                    price = str(amount)

    # -------------------------
    # LINK AMAZON
    # -------------------------

    detail_url = (
        item.get("detailPageURL")
        or item.get("DetailPageURL")
        or item.get("url")
        or ""
    )

    if not detail_url and asin:
        detail_url = (
            f"https://{marketplace}/dp/{asin}"
            f"?tag={partner_tag}"
        )

    return {
        "asin": asin,
        "title": title,
        "image_url": image_url,
        "price": price,
        "amazon_url": detail_url,
    }
