import html as html_lib
import re
from pathlib import Path


def _plain_text(value):
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def validate_article(settings, article, image_path=None, image_url="", product_price=""):
    """Restituisce una lista di problemi che devono bloccare la pubblicazione."""
    errors = []
    title = str(article.get("title") or "").strip()
    html = str(article.get("html") or "").strip()
    plain = _plain_text(html)
    partner_tag = str(settings.get("amazon_partner_tag") or "").strip()

    if len(title) < 10:
        errors.append("Titolo mancante o troppo corto")

    if len(plain) < 600:
        errors.append("Testo articolo troppo corto")

    local_ok = bool(image_path and Path(image_path).exists())
    remote_ok = bool(str(image_url or "").strip())
    if not local_ok and not remote_ok:
        errors.append("Immagine prodotto mancante")

    if not partner_tag:
        errors.append("Partner Tag Amazon non configurato")

    amazon_links = re.findall(
        r'href=["\'](https?://(?:www\.)?amazon\.it/[^"\']+)["\']',
        html,
        flags=re.I,
    )
    if len(amazon_links) < 2:
        errors.append("Sono richiesti almeno 2 link Amazon nell'articolo")

    if partner_tag and amazon_links:
        expected = f"tag={partner_tag}".lower()
        if any(expected not in link.lower() for link in amazon_links):
            errors.append("Uno o più link Amazon non contengono il Partner Tag corretto")

    lower_plain = plain.lower()
    if "affiliato amazon" not in lower_plain and not ("affiliato" in lower_plain and "amazon" in lower_plain):
        errors.append("Dichiarazione di affiliazione Amazon mancante")

    price = str(product_price or "").strip()
    if price and price.lower() not in html.lower() and price.lower() not in lower_plain:
        errors.append("Prezzo disponibile ma non presente nell'articolo")

    return errors
