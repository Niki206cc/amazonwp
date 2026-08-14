import json
import re
from datetime import date, datetime, timedelta

import requests


RADAR_RULES = """Sei il radar editoriale-commerciale di Montagne & Paesi.
Devi proporre opportunità future che possano diventare articoli con prodotti Amazon utili e pertinenti.

CONTESTO EDITORIALE:
- Pubblico principalmente di Bergamo, Brescia, Val Seriana, Val Camonica, Sebino e Lombardia.
- Priorità a montagna, outdoor, famiglia, scuola, meteo stagionale, astronomia, viaggi, auto, casa, tecnologia utile, festività, ricorrenze ed eventi locali/nazionali.
- Non proporre prodotti regolamentati, pericolosi o inadatti all'affiliazione.

REGOLE TEMPORALI:
- Considera solo eventi/opportunità future rispetto alla data odierna fornita.
- La data di pubblicazione deve precedere l'evento abbastanza da permettere al lettore di acquistare e ricevere il prodotto.
- Anticipo minimo standard: 2 giorni.
- Usa 3-5 giorni quando il prodotto richiede scelta o pianificazione.
- Usa 7-10 giorni per scuola, vacanze, festività o acquisti più ragionati.
- Se l'opportunità è continuativa o stagionale, scegli una data di pubblicazione imminente sensata.

PER OGNI OPPORTUNITÀ restituisci:
- name: nome breve dell'opportunità
- event_date: data evento YYYY-MM-DD, oppure data rappresentativa per opportunità stagionali
- publish_date: data consigliata YYYY-MM-DD
- priority: alta, media o bassa
- score: intero 0-100 basato su interesse, pertinenza con Montagne & Paesi e probabilità di acquisto
- area: territorio interessato
- reason: perché vale la pena pubblicarla
- article_angle: consiglio editoriale preciso da passare all'AI che scriverà l'articolo prodotto
- suggested_title: esempio di titolo editoriale, senza inventare caratteristiche del prodotto
- amazon_query: query breve da usare per cercare prodotti su Amazon
- product_ideas: massimo 4 tipologie di prodotto utili

Evita duplicati e idee generiche senza una vera ragione temporale.
Restituisci SOLO JSON valido con chiave opportunities contenente una lista.
"""


def _extract_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    return json.loads(text)


def _prompt(today, days):
    end = today + timedelta(days=days)
    return (
        RADAR_RULES
        + f"\n\nDATA ODIERNA: {today.isoformat()}"
        + f"\nORIZZONTE: fino al {end.isoformat()} ({days} giorni)."
        + "\nGenera da 6 a 10 opportunità concrete, ordinate per publish_date e poi score decrescente."
    )


def _call_openai(settings, prompt):
    key = settings.get("openai_api_key", "").strip()
    if not key:
        raise RuntimeError("API key OpenAI non configurata")
    payload = {"model": settings.get("openai_model") or "gpt-5-mini", "input": prompt}
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


def _call_gemini(settings, prompt):
    key = settings.get("gemini_api_key", "").strip()
    if not key:
        raise RuntimeError("API key Gemini non configurata")
    model = settings.get("gemini_model") or "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    r = requests.post(url, json=payload, timeout=90)
    if not r.ok:
        raise RuntimeError(f"Gemini: HTTP {r.status_code} - {r.text[:500]}")
    data = r.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return _extract_json(text)


def _parse_date(value):
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%d").date()
    except ValueError:
        return None


def generate_opportunities(settings, days=30, today=None):
    today = today or date.today()
    try:
        days = max(7, min(int(days), 60))
    except (TypeError, ValueError):
        days = 30

    engine = (settings.get("ai_engine") or "openai").lower()
    prompt = _prompt(today, days)
    result = _call_gemini(settings, prompt) if engine == "gemini" else _call_openai(settings, prompt)
    rows = result.get("opportunities") or []
    normalized = []
    limit = today + timedelta(days=days)

    for row in rows:
        event_date = _parse_date(row.get("event_date"))
        publish_date = _parse_date(row.get("publish_date"))
        if not event_date or not publish_date:
            continue
        if event_date < today or event_date > limit:
            continue
        if publish_date > event_date:
            publish_date = max(today, event_date - timedelta(days=2))
        if publish_date < today:
            publish_date = today

        try:
            score = max(0, min(int(row.get("score", 0)), 100))
        except (TypeError, ValueError):
            score = 0

        priority = str(row.get("priority") or "media").lower()
        if priority not in ("alta", "media", "bassa"):
            priority = "media"

        product_ideas = row.get("product_ideas") or []
        if isinstance(product_ideas, str):
            product_ideas = [x.strip() for x in product_ideas.split(",") if x.strip()]

        normalized.append({
            "name": str(row.get("name") or "Opportunità").strip(),
            "event_date": event_date.isoformat(),
            "publish_date": publish_date.isoformat(),
            "priority": priority,
            "score": score,
            "area": str(row.get("area") or "").strip(),
            "reason": str(row.get("reason") or "").strip(),
            "article_angle": str(row.get("article_angle") or "").strip(),
            "suggested_title": str(row.get("suggested_title") or "").strip(),
            "amazon_query": str(row.get("amazon_query") or "").strip(),
            "product_ideas": product_ideas[:4],
        })

    normalized.sort(key=lambda x: (x["publish_date"], -x["score"]))
    return normalized
