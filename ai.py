import json
import re
from html import escape

import requests

SYSTEM_RULES = """Sei un redattore italiano che prepara articoli prodotto per Montagne & Paesi.
Usa solo le informazioni fornite. Non dichiarare mai di aver provato, testato, acquistato o usato il prodotto.
Non inventare specifiche, certificazioni, recensioni, prezzi, sconti o esperienze.
Scrivi in italiano naturale, giornalistico, scorrevole e utile.
L'HTML deve essere semplice e compatibile WordPress: paragrafi, <strong>, ul/li e link. Non usare tag h1/h2/h3.
Il titolo deve essere separato dal corpo HTML.

TITOLO ARTICOLO:
- Genera un vero titolo editoriale: NON limitarti a copiare il titolo commerciale Amazon fornito nei dati prodotto.
- Il titolo deve essere diretto, naturale e interessante, mantenendo il nome del prodotto o del marchio quando utile.
- Rispetta rigorosamente le maiuscole della lingua italiana: usa la maiuscola all'inizio del titolo e per nomi propri, marchi, sigle o denominazioni che la richiedono.
- NON usare il Title Case inglese e NON mettere la maiuscola a parole comuni come Friggitrice, Aria, Cucina, Protezione, Solare, Tua, Molto, Altro.
- Esempio sbagliato: \"La Cosori Turbo Blaze: Frittura ad Aria e Molto Altro per la Tua Cucina\".
- Esempio corretto: \"Cosori Turbo Blaze: la friggitrice ad aria che semplifica la cucina di ogni giorno\".

TITOLO FACEBOOK:
- Genera un solo titolo Facebook distinto dal titolo dell'articolo.
- Deve essere più coinvolgente e incuriosire al clic, ma senza clickbait ingannevole, promesse non dimostrate o informazioni inventate.
- Anche il titolo Facebook deve rispettare le normali maiuscole italiane e NON deve usare il Title Case inglese.

PREZZO E LINK AMAZON:
- Se nei dati prodotto è presente il campo price, cita sempre il prezzo nell'articolo in modo naturale e ben visibile.
- Specifica che il prezzo è quello indicato al momento della preparazione dell'articolo e che può cambiare su Amazon.
- Non inventare mai un prezzo se il campo price è vuoto.
- Inserisci il link Amazon affiliato in modo naturale 2 o 3 volte nell'articolo, distribuito nel testo e non tutto nello stesso punto.
- Ogni link deve usare rel=\"sponsored nofollow\".
- Usa inviti sobri come \"vedi il prodotto su Amazon\", \"controlla prezzo e disponibilità su Amazon\" o equivalenti, senza falsa urgenza.

Chiudi con una breve dichiarazione trasparente: \"In qualità di Affiliato Amazon, Montagne & Paesi riceve un guadagno dagli acquisti idonei.\"
Restituisci SOLO JSON valido con chiavi: title, facebook_title, meta_description, excerpt, html.
"""


def build_prompt(product):
    return SYSTEM_RULES + "\n\nDATI PRODOTTO:\n" + json.dumps(product, ensure_ascii=False, indent=2)


def _extract_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    return json.loads(text)


def _affiliate_link(url, label):
    safe_url = escape(url, quote=True)
    return f'<a href="{safe_url}" rel="sponsored nofollow">{escape(label)}</a>'


def _ensure_price_and_links(html_text, product):
    """Garantisce prezzo e almeno 3 richiami affiliati nell'HTML generato."""
    html_text = str(html_text or "").strip()
    amazon_url = str(product.get("amazon_url") or "").strip()
    price = str(product.get("price") or "").strip()

    if price:
        # Se l'AI ha omesso il prezzo, lo aggiungiamo in modo trasparente.
        if price.lower() not in html_text.lower():
            price_block = (
                f'<p><strong>Prezzo indicato:</strong> {escape(price)}. '
                'Il prezzo può variare nel tempo; verifica sempre quello aggiornato su Amazon.</p>'
            )
            first_p = html_text.lower().find("</p>")
            if first_p >= 0:
                insert_at = first_p + 4
                html_text = html_text[:insert_at] + "\n" + price_block + html_text[insert_at:]
            else:
                html_text = price_block + "\n" + html_text

    if not amazon_url:
        return html_text

    # Conta i link Amazon già presenti nell'HTML.
    link_count = len(re.findall(r'href=["\'][^"\']*amazon\.it[^"\']*["\']', html_text, flags=re.I))
    needed = max(0, 3 - link_count)
    if needed == 0:
        return html_text

    ctas = [
        f'<p>{_affiliate_link(amazon_url, "Vedi il prodotto su Amazon")}.</p>',
        f'<p>{_affiliate_link(amazon_url, "Controlla prezzo e disponibilità su Amazon")}.</p>',
        f'<p>{_affiliate_link(amazon_url, "Scopri il prodotto su Amazon")}.</p>',
    ]

    additions = ctas[:needed]

    # Prova a distribuire il primo richiamo subito dopo il primo paragrafo.
    if additions:
        first_p = html_text.lower().find("</p>")
        if first_p >= 0:
            insert_at = first_p + 4
            html_text = html_text[:insert_at] + "\n" + additions.pop(0) + html_text[insert_at:]

    # Gli altri richiami vengono inseriti prima della dichiarazione di affiliazione,
    # se presente, altrimenti in fondo all'articolo.
    if additions:
        disclosure = "In qualità di Affiliato Amazon"
        pos = html_text.find(disclosure)
        if pos >= 0:
            para_start = html_text.rfind("<p", 0, pos)
            insert_at = para_start if para_start >= 0 else pos
            html_text = html_text[:insert_at] + "\n" + "\n".join(additions) + "\n" + html_text[insert_at:]
        else:
            html_text = html_text + "\n" + "\n".join(additions)

    return html_text


def generate_openai(settings, product):
    key = settings.get("openai_api_key", "").strip()
    if not key:
        raise RuntimeError("API key OpenAI non configurata")
    payload = {
        "model": settings.get("openai_model") or "gpt-5-mini",
        "input": build_prompt(product),
    }
    r = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=90,
    )
    if not r.ok:
        raise RuntimeError(f"OpenAI: HTTP {r.status_code} - {r.text[:500]}")
    data = r.json()
    text = data.get("output_text")
    if not text:
        chunks = []
        for item in data.get("output", []):
            for c in item.get("content", []):
                if c.get("type") in ("output_text", "text") and c.get("text"):
                    chunks.append(c["text"])
        text = "\n".join(chunks)
    return _extract_json(text)


def generate_gemini(settings, product):
    key = settings.get("gemini_api_key", "").strip()
    if not key:
        raise RuntimeError("API key Gemini non configurata")
    model = settings.get("gemini_model") or "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    payload = {
        "contents": [{"parts": [{"text": build_prompt(product)}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    r = requests.post(url, json=payload, timeout=90)
    if not r.ok:
        raise RuntimeError(f"Gemini: HTTP {r.status_code} - {r.text[:500]}")
    data = r.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return _extract_json(text)


def generate_article(settings, product):
    engine = (settings.get("ai_engine") or "openai").lower()
    result = generate_gemini(settings, product) if engine == "gemini" else generate_openai(settings, product)

    title = str(result.get("title") or "").strip()
    if not title:
        title = product.get("title", "Prodotto Amazon")
    result["title"] = title

    facebook_title = str(result.get("facebook_title") or "").strip()
    if not facebook_title:
        facebook_title = title
    result["facebook_title"] = facebook_title

    result["html"] = _ensure_price_and_links(result.get("html", ""), product)

    # Manteniamo il campo storico alt_titles nel database per compatibilità,
    # ma dalla v1.1.3 contiene esclusivamente il Titolo Facebook.
    result["alt_titles"] = [facebook_title]
    result["engine"] = engine
    return result
