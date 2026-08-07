import json
import re
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

Inserisci il link Amazon in modo naturale almeno una volta usando rel=\"sponsored nofollow\".
Chiudi con una breve dichiarazione trasparente: \"In qualità di Affiliato Amazon, Montagne & Paesi riceve un guadagno dagli acquisti idonei.\"
Restituisci SOLO JSON valido con chiavi: title, facebook_title, meta_description, excerpt, html.
"""


def build_prompt(product):
    return SYSTEM_RULES + "\n\nDATI PRODOTTO:\n" + json.dumps(product, ensure_ascii=False, indent=2)


def _extract_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    return json.loads(text)


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

    # Manteniamo il campo storico alt_titles nel database per compatibilità,
    # ma dalla v1.1.3 contiene esclusivamente il Titolo Facebook.
    result["alt_titles"] = [facebook_title]
    result["engine"] = engine
    return result
