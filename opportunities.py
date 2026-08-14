import json
import re
from datetime import date, datetime, timedelta

import requests


RADAR_RULES = """Sei il radar editoriale-commerciale di Montagne & Paesi.
Devi cercare sul web eventi REALI e FUTURI e trasformarli in opportunità editoriali con prodotti Amazon utili e pertinenti.

OBIETTIVO:
Non limitarti agli eventi locali. Ragiona come un calendario commerciale/editoriale nazionale italiano e cerca occasioni che possano generare interesse e acquisti nei giorni o mesi successivi.

CERCA ATTIVAMENTE SUL WEB, includendo quando pertinenti:
- calendario scolastico: rientro a scuola, inizio lezioni, vacanze, esami, università;
- astronomia: eclissi di Sole/Luna, stelle cadenti, superlune, congiunzioni, fenomeni osservabili dall'Italia;
- sport: Mondiali ed Europei di calcio, Olimpiadi, ciclismo, sci, Formula 1, grandi gare e manifestazioni;
- festività e ricorrenze: Natale, Pasqua, Carnevale, Halloween, Festa della mamma/papà, San Valentino, ponti e festività italiane;
- stagionalità: estate, autunno, inverno, primavera, caldo, freddo, neve, funghi, trekking, campeggio, rientro dalle ferie;
- viaggi e mobilità: partenze, rientri, obblighi stagionali, vacanze, weekend e ponti;
- tecnologia e casa: eventi o periodi dell'anno che rendano utili determinati prodotti;
- eventi nazionali italiani di forte interesse mediatico;
- eventi di Lombardia, Bergamo, Brescia, Val Seriana, Val Camonica e Sebino quando abbastanza rilevanti.

FONTI:
- Usa fonti web aggiornate e affidabili.
- Preferisci siti ufficiali, enti pubblici, federazioni sportive, organizzatori, istituti astronomici e testate affidabili.
- Non inventare date o eventi.
- Per ogni opportunità indica almeno una fonte web che supporti l'esistenza o la data dell'evento.

CONTESTO EDITORIALE:
- Pubblico principalmente di Bergamo, Brescia, Val Seriana, Val Camonica, Sebino e Lombardia, ma sono valide anche opportunità nazionali forti.
- Priorità a montagna, outdoor, famiglia, scuola, meteo stagionale, astronomia, viaggi, auto, casa, tecnologia utile, festività, ricorrenze e grandi eventi.
- Non proporre prodotti regolamentati, pericolosi o inadatti all'affiliazione.

REGOLE TEMPORALI:
- Considera solo eventi/opportunità future rispetto alla DATA ODIERNA fornita.
- La data di pubblicazione deve precedere l'evento abbastanza da permettere al lettore di acquistare e ricevere il prodotto.
- Anticipo minimo standard: 2 giorni.
- Usa 3-5 giorni quando il prodotto richiede scelta o pianificazione.
- Usa 7-14 giorni per scuola, vacanze, festività, grandi competizioni sportive o acquisti più ragionati.
- Per eventi molto importanti puoi proporre una prima pubblicazione anche 2-4 settimane prima, purché abbia senso commerciale.
- Se l'opportunità è continuativa o stagionale, scegli una data evento rappresentativa e una pubblicazione imminente sensata.

PER OGNI OPPORTUNITÀ restituisci:
- name: nome breve dell'opportunità
- event_date: data evento YYYY-MM-DD
- publish_date: data consigliata YYYY-MM-DD
- priority: alta, media o bassa
- score: intero 0-100 basato su interesse, pertinenza con Montagne & Paesi e probabilità di acquisto
- area: territorio interessato (Italia, Lombardia, Bergamo, ecc.)
- reason: perché vale la pena pubblicarla e quale bisogno del lettore intercetta
- article_angle: consiglio editoriale preciso da passare all'AI che scriverà l'articolo prodotto
- suggested_title: esempio di titolo editoriale specifico per l'occasione
- amazon_query: query breve da usare per cercare prodotti su Amazon
- product_ideas: massimo 4 tipologie di prodotto utili
- source_title: nome breve della fonte principale
- source_url: URL completo della fonte principale

ESEMPIO DI RAGIONAMENTO:
Se tra alcuni giorni è prevista un'eclissi visibile dall'Italia, non proporre genericamente "astronomia": proponi occhiali/filtri adeguati e un articolo da pubblicare almeno 2-4 giorni prima, con un titolo legato esplicitamente all'eclissi.
Se a settembre iniziano le scuole, proponi zaini, borracce, etichette o accessori con pubblicazione 7-14 giorni prima.
Se sta per iniziare una grande competizione sportiva, proponi prodotti coerenti con la visione dell'evento, lo sport o il tifo senza usare marchi/loghi protetti in modo improprio.

Evita duplicati, eventi già passati e idee generiche senza una vera ragione temporale.
Restituisci SOLO JSON valido con chiave opportunities contenente una lista da 8 a 15 elementi quando esistono occasioni valide.
"""


def _extract_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    return json.loads(text)


def _prompt(today, days):
    end = today + timedelta(days=days)
    return (
        RADAR_RULES
        + f"\n\nDATA ODIERNA: {today.isoformat()}"
        + f"\nORIZZONTE: cerca opportunità con evento tra oggi e {end.isoformat()} ({days} giorni)."
        + "\nFai più ricerche web mirate per categorie diverse prima di rispondere."
        + "\nIncludi sia eventi imminenti (prossimi 7-30 giorni) sia grandi appuntamenti più avanti nell'orizzonte selezionato."
        + "\nOrdina le opportunità per publish_date e, a parità, per score decrescente."
    )


def _output_text(data):
    text = data.get("output_text")
    if text:
        return text
    chunks = []
    for item in data.get("output", []):
        for c in item.get("content", []):
            if c.get("type") in ("output_text", "text") and c.get("text"):
                chunks.append(c["text"])
    return "\n".join(chunks)


def _call_openai(settings, prompt):
    key = settings.get("openai_api_key", "").strip()
    if not key:
        raise RuntimeError("API key OpenAI non configurata")
    payload = {
        "model": settings.get("openai_model") or "gpt-5-mini",
        "input": prompt,
        "tools": [{"type": "web_search"}],
        "tool_choice": "auto",
    }
    r = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=180,
    )
    if not r.ok:
        raise RuntimeError(f"OpenAI Radar web: HTTP {r.status_code} - {r.text[:800]}")
    return _extract_json(_output_text(r.json()))


def _call_gemini(settings, prompt):
    key = settings.get("gemini_api_key", "").strip()
    if not key:
        raise RuntimeError("API key Gemini non configurata")
    model = settings.get("gemini_model") or "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
    }
    r = requests.post(url, json=payload, timeout=180)
    if not r.ok:
        raise RuntimeError(f"Gemini Radar Google Search: HTTP {r.status_code} - {r.text[:800]}")
    data = r.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini Radar non ha restituito risultati")
    parts = candidates[0].get("content", {}).get("parts", [])
    text = "\n".join(p.get("text", "") for p in parts if p.get("text"))
    return _extract_json(text)


def _parse_date(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    m = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass
    for fmt in ("%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


def _normalize_url(value):
    value = str(value or "").strip()
    return value if value.startswith(("https://", "http://")) else ""


def generate_opportunities(settings, days=90, today=None):
    today = today or date.today()
    try:
        days = max(7, min(int(days), 365))
    except (TypeError, ValueError):
        days = 90

    engine = (settings.get("ai_engine") or "openai").lower()
    prompt = _prompt(today, days)
    result = _call_gemini(settings, prompt) if engine == "gemini" else _call_openai(settings, prompt)
    rows = result.get("opportunities") or []
    if not isinstance(rows, list):
        rows = []

    normalized = []
    limit = today + timedelta(days=days)
    seen = set()

    for row in rows:
        if not isinstance(row, dict):
            continue
        event_date = _parse_date(row.get("event_date"))
        publish_date = _parse_date(row.get("publish_date"))
        if not event_date:
            continue
        if event_date < today or event_date > limit:
            continue
        if not publish_date:
            publish_date = max(today, event_date - timedelta(days=2))
        if publish_date >= event_date:
            publish_date = max(today, event_date - timedelta(days=2))
        if publish_date < today:
            publish_date = today

        name = str(row.get("name") or "Opportunità").strip()
        dedupe_key = (name.lower(), event_date.isoformat())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        try:
            score = max(0, min(int(row.get("score", 0)), 100))
        except (TypeError, ValueError):
            score = 0

        priority = str(row.get("priority") or "media").lower()
        if priority not in ("alta", "media", "bassa"):
            priority = "media"

        product_ideas = row.get("product_ideas") or []
        if isinstance(product_ideas, str):
            product_ideas = [x.strip() for x in re.split(r"[,;|]", product_ideas) if x.strip()]

        normalized.append({
            "name": name,
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
            "source_title": str(row.get("source_title") or "").strip(),
            "source_url": _normalize_url(row.get("source_url")),
        })

    normalized.sort(key=lambda x: (x["publish_date"], -x["score"]))
    return normalized
