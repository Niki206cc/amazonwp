# Amazon Articoli MP

Pannello Docker/Portainer per gestire prodotti Amazon, generare articoli con OpenAI o Gemini, revisionarli, approvarli e inviarli via SMTP a Postie/WordPress.

## Funzioni incluse

- Inserimento manuale prodotto con link, ASIN, immagine, prezzo, caratteristiche e note.
- Ricerca Amazon intelligente: casuali, trend, best seller, novità, offerte e filtri sotto 30/50/100 euro.
- Controllo duplicati tramite ASIN e URL Amazon.
- Generazione di un singolo articolo usando il motore AI selezionato.
- 5 titoli alternativi, meta description ed excerpt.
- Editor HTML + anteprima affiancata.
- Approvazione manuale obbligatoria prima della coda.
- Riordino della coda e pubblicazione immediata del prossimo elemento.
- Scheduler giornaliero/configurabile.
- SMTP con allegato immagine verso Postie.
- SQLite persistente in `./data`.

## Avvio con Docker Compose / Portainer

1. Copia l'intera cartella sul server oppure clonala da GitHub.
2. Modifica `docker-compose.yml` e cambia `APP_SECRET=change-me-now` con una stringa lunga casuale.
3. In Portainer crea uno Stack usando il contenuto di `docker-compose.yml`, oppure da terminale esegui:

```bash
docker compose up -d --build
```

4. Apri:

```text
http://IP-DEL-SERVER:8085
```

5. Vai in **Impostazioni** e configura prima AI e SMTP/Postie.
6. Configura Amazon Creators API solo se il tuo account dispone delle credenziali necessarie.

## GitHub Desktop / line endings

Il progetto contiene `.gitattributes` che forza LF per i file di codice e configurazione. Questo evita che GitHub Desktop trasformi automaticamente i file principali da LF a CRLF su Windows.

Apri in GitHub Desktop la cartella che contiene direttamente questo `README.md` e il file `docker-compose.yml`.

## Nota Amazon Creators API

La parte Amazon è isolata in `amazon.py`. Le API Amazon Associates/Creators possono differire per account, versione e endpoint abilitati. Nel pannello sono quindi modificabili `Token URL` e `API base URL` senza dover cambiare il resto dell'applicazione.

L'inserimento manuale e tutto il flusso AI/coda/Postie funzionano anche senza Creators API.

## Flusso

```text
Amazon / manuale
      ↓
prodotto
      ↓
controllo duplicati
      ↓
genera articolo
      ↓
revisione e modifica
      ↓
approvazione
      ↓
coda
      ↓
SMTP
      ↓
Postie
      ↓
WordPress
```

## Sicurezza

Il database contiene API key e password SMTP in chiaro nel volume locale. Non pubblicare mai la cartella `data` né un database reale su GitHub. `.gitignore` esclude i database generati automaticamente.
