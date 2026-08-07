from pathlib import Path
import json
import shutil
import urllib.request
import re
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory

from config import SECRET_KEY, HOST, PORT, UPLOAD_DIR
from database import init_db, get_db, get_settings, set_settings, now, log
from ai import generate_article
from amazon import extract_asin, search_products
from mailer import send_article
from scheduler import configure_scheduler, start_scheduler

app = Flask(__name__)
app.secret_key = SECRET_KEY


def affiliate_url(url, asin="", partner_tag=""):
    """Restituisce un URL Amazon con il Partner Tag configurato."""
    url = (url or "").strip()
    asin = (asin or "").strip().upper()
    partner_tag = (partner_tag or "").strip()

    if not url and asin:
        url = f"https://www.amazon.it/dp/{asin}"
    if not url or not partner_tag:
        return url

    try:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        if "amazon." not in host:
            return url
        query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() != "tag"]
        query.append(("tag", partner_tag))
        return urlunparse((parsed.scheme or "https", parsed.netloc, parsed.path, parsed.params, urlencode(query), parsed.fragment))
    except Exception:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}tag={partner_tag}"


def ensure_affiliate_links(html, partner_tag):
    """Aggiunge il Partner Tag a tutti gli URL Amazon presenti nell'HTML generato."""
    if not html or not partner_tag:
        return html

    pattern = re.compile(r'https?://(?:www\.)?amazon\.it/[^\s"\'<>]+', re.I)
    return pattern.sub(lambda m: affiliate_url(m.group(0), partner_tag=partner_tag), html)


def rowdict(r):
    return dict(r) if r else None


def save_remote_image(url, product_id):
    if not url:
        return None
    target = UPLOAD_DIR / f"product_{product_id}.jpg"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return str(target)
    except Exception:
        return None


def article_payload(product, settings=None):
    settings = settings or get_settings()
    partner_tag = settings.get("amazon_partner_tag", "")
    affiliated = affiliate_url(
        product.get("amazon_url", ""),
        product.get("asin", ""),
        partner_tag,
    )
    return {
        "asin": product.get("asin", ""),
        "title": product.get("title", ""),
        "amazon_url": affiliated,
        "image_url": product.get("image_url", ""),
        "price": product.get("price", ""),
        "category": product.get("category", ""),
        "features": product.get("features", ""),
        "notes": product.get("notes", ""),
    }


def duplicate_for(asin, amazon_url, exclude_id=None):
    with get_db() as db:
        q = """SELECT p.id product_id,p.title product_title,a.id article_id,a.title article_title,a.status,a.published_at,a.created_at
               FROM products p LEFT JOIN articles a ON a.product_id=p.id
               WHERE (p.asin<>'' AND p.asin=?) OR (p.amazon_url<>'' AND p.amazon_url=?)"""
        rows = db.execute(q, (asin or "", amazon_url or "")).fetchall()
        for r in rows:
            if exclude_id and r["product_id"] == exclude_id:
                continue
            if r["article_id"]:
                return dict(r)
    return None


def publish_next():
    with app.app_context():
        with get_db() as db:
            item = db.execute("""
                SELECT q.id qid,a.*,p.local_image,p.image_url
                FROM queue q JOIN articles a ON a.id=q.article_id JOIN products p ON p.id=a.product_id
                ORDER BY q.position ASC LIMIT 1
            """).fetchone()
        if not item:
            log("Scheduler: coda vuota")
            return
        try:
            settings = get_settings()
            image = item["local_image"]
            send_article(settings, dict(item), image)
            with get_db() as db:
                db.execute("UPDATE articles SET status='published',published_at=?,updated_at=? WHERE id=?", (now(), now(), item["id"]))
                db.execute("DELETE FROM queue WHERE id=?", (item["qid"],))
            normalize_queue()
            log(f"Articolo pubblicato: {item['title']}")
        except Exception as e:
            with get_db() as db:
                db.execute("UPDATE articles SET error=?,updated_at=? WHERE id=?", (str(e), now(), item["id"]))
            log(f"Errore pubblicazione: {e}", "ERROR")


def normalize_queue():
    with get_db() as db:
        rows = db.execute("SELECT id FROM queue ORDER BY position,id").fetchall()
        for pos, r in enumerate(rows, 1):
            db.execute("UPDATE queue SET position=? WHERE id=?", (pos, r["id"]))


@app.route("/")
def index():
    with get_db() as db:
        products = db.execute("SELECT * FROM products ORDER BY id DESC LIMIT 50").fetchall()
        queue_count = db.execute("SELECT COUNT(*) c FROM queue").fetchone()["c"]
    return render_template("index.html", products=products, queue_count=queue_count)


@app.route("/product/new", methods=["GET", "POST"])
def product_new():
    if request.method == "POST":
        url = request.form.get("amazon_url", "").strip()
        asin = request.form.get("asin", "").strip().upper() or extract_asin(url)
        product = {
            "asin": asin,
            "title": request.form.get("title", "").strip(),
            "amazon_url": url,
            "image_url": request.form.get("image_url", "").strip(),
            "price": request.form.get("price", "").strip(),
            "category": request.form.get("category", "").strip(),
            "features": request.form.get("features", "").strip(),
            "notes": request.form.get("notes", "").strip(),
        }
        if not product["title"]:
            flash("Inserisci almeno il titolo del prodotto.", "error")
            return render_template("product_form.html", product=product)
        dup = duplicate_for(asin, url)
        with get_db() as db:
            cur = db.execute("""INSERT INTO products(asin,title,amazon_url,image_url,price,category,features,notes,source,status,created_at,updated_at)
                              VALUES(?,?,?,?,?,?,?,?,?,'draft',?,?)""",
                             (asin, product["title"], url, product["image_url"], product["price"], product["category"], product["features"], product["notes"], "manual", now(), now()))
            pid = cur.lastrowid
            local = None
            upload = request.files.get("image_file")
            if upload and upload.filename:
                suffix = Path(upload.filename).suffix.lower() or ".jpg"
                path = UPLOAD_DIR / f"product_{pid}{suffix}"
                upload.save(path)
                local = str(path)
            elif product["image_url"]:
                local = save_remote_image(product["image_url"], pid)
            if local:
                db.execute("UPDATE products SET local_image=? WHERE id=?", (local, pid))
        if dup:
            flash(f"Attenzione: prodotto già presente in un articolo ({dup['article_title']}). Puoi comunque generarlo.", "warning")
        return redirect(url_for("product_edit", product_id=pid))
    return render_template("product_form.html", product={})


@app.route("/product/<int:product_id>", methods=["GET", "POST"])
def product_edit(product_id):
    with get_db() as db:
        product = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        articles = db.execute("SELECT * FROM articles WHERE product_id=? ORDER BY id DESC", (product_id,)).fetchall()
    if not product:
        return "Prodotto non trovato", 404
    if request.method == "POST":
        url = request.form.get("amazon_url", "").strip()
        asin = request.form.get("asin", "").strip().upper() or extract_asin(url)
        with get_db() as db:
            db.execute("""UPDATE products SET asin=?,title=?,amazon_url=?,image_url=?,price=?,category=?,features=?,notes=?,updated_at=? WHERE id=?""",
                       (asin, request.form.get("title",""), url, request.form.get("image_url",""), request.form.get("price",""), request.form.get("category",""), request.form.get("features",""), request.form.get("notes",""), now(), product_id))
        flash("Prodotto aggiornato.", "success")
        return redirect(url_for("product_edit", product_id=product_id))
    dup = duplicate_for(product["asin"], product["amazon_url"], product_id)
    return render_template("product_edit.html", product=product, articles=articles, duplicate=dup)


@app.post("/product/<int:product_id>/generate")
def product_generate(product_id):
    with get_db() as db:
        p = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if not p:
        return "Prodotto non trovato", 404
    try:
        settings = get_settings()
        result = generate_article(settings, article_payload(dict(p), settings))
        result["html"] = ensure_affiliate_links(result.get("html", ""), settings.get("amazon_partner_tag", ""))
        with get_db() as db:
            cur = db.execute("""INSERT INTO articles(product_id,title,alt_titles,meta_description,excerpt,html,ai_engine,status,created_at,updated_at)
                              VALUES(?,?,?,?,?,?,?,'draft',?,?)""",
                             (product_id, result["title"], json.dumps(result["alt_titles"], ensure_ascii=False), result.get("meta_description",""), result.get("excerpt",""), result["html"], result["engine"], now(), now()))
            aid = cur.lastrowid
        flash("Articolo generato.", "success")
        return redirect(url_for("article_edit", article_id=aid))
    except Exception as e:
        log(f"Generazione AI fallita: {e}", "ERROR")
        flash(str(e), "error")
        return redirect(url_for("product_edit", product_id=product_id))


@app.route("/article/<int:article_id>", methods=["GET", "POST"])
def article_edit(article_id):
    with get_db() as db:
        a = db.execute("SELECT a.*,p.title product_title,p.amazon_url,p.local_image,p.image_url FROM articles a JOIN products p ON p.id=a.product_id WHERE a.id=?", (article_id,)).fetchone()
    if not a:
        return "Articolo non trovato", 404
    if request.method == "POST":
        titles = [request.form.get(f"alt_title_{i}", "").strip() for i in range(1,6)]
        html = ensure_affiliate_links(request.form.get("html", ""), get_settings().get("amazon_partner_tag", ""))
        with get_db() as db:
            db.execute("""UPDATE articles SET title=?,alt_titles=?,meta_description=?,excerpt=?,html=?,updated_at=? WHERE id=?""",
                       (request.form.get("title",""), json.dumps(titles, ensure_ascii=False), request.form.get("meta_description",""), request.form.get("excerpt",""), html, now(), article_id))
        flash("Articolo salvato.", "success")
        return redirect(url_for("article_edit", article_id=article_id))
    alt_titles = json.loads(a["alt_titles"] or "[]")
    alt_titles = (alt_titles + [""]*5)[:5]
    preview_image = ""
    if a["local_image"]:
        preview_image = url_for("uploads", filename=Path(a["local_image"]).name)
    elif a["image_url"]:
        preview_image = a["image_url"]
    return render_template("article_edit.html", article=a, alt_titles=alt_titles, preview_image=preview_image)


@app.post("/article/<int:article_id>/approve")
def article_approve(article_id):
    with get_db() as db:
        existing = db.execute("SELECT 1 FROM queue WHERE article_id=?", (article_id,)).fetchone()
        if not existing:
            pos = db.execute("SELECT COALESCE(MAX(position),0)+1 p FROM queue").fetchone()["p"]
            db.execute("INSERT INTO queue(article_id,position,approved_at) VALUES(?,?,?)", (article_id, pos, now()))
            db.execute("UPDATE articles SET status='queued',updated_at=? WHERE id=?", (now(), article_id))
    flash("Articolo approvato e messo in coda.", "success")
    return redirect(url_for("queue_page"))


@app.post("/article/<int:article_id>/send-now")
def article_send_now(article_id):
    with get_db() as db:
        a = db.execute("SELECT a.*,p.local_image FROM articles a JOIN products p ON p.id=a.product_id WHERE a.id=?", (article_id,)).fetchone()
    try:
        send_article(get_settings(), dict(a), a["local_image"])
        flash("Email inviata a Postie.", "success")
    except Exception as e:
        flash(str(e), "error")
    return redirect(url_for("article_edit", article_id=article_id))


@app.route("/queue")
def queue_page():
    with get_db() as db:
        items = db.execute("""SELECT q.id qid,q.position,a.id article_id,a.title,a.status,p.title product_title
                              FROM queue q JOIN articles a ON a.id=q.article_id JOIN products p ON p.id=a.product_id ORDER BY q.position""").fetchall()
    return render_template("queue.html", items=items)


@app.post("/queue/<int:qid>/<direction>")
def queue_move(qid, direction):
    with get_db() as db:
        item = db.execute("SELECT * FROM queue WHERE id=?", (qid,)).fetchone()
        if item:
            op = "<" if direction == "up" else ">"
            order = "DESC" if direction == "up" else "ASC"
            other = db.execute(f"SELECT * FROM queue WHERE position {op} ? ORDER BY position {order} LIMIT 1", (item["position"],)).fetchone()
            if other:
                db.execute("UPDATE queue SET position=? WHERE id=?", (other["position"], item["id"]))
                db.execute("UPDATE queue SET position=? WHERE id=?", (item["position"], other["id"]))
    return redirect(url_for("queue_page"))


@app.post("/queue/<int:qid>/remove")
def queue_remove(qid):
    with get_db() as db:
        q = db.execute("SELECT article_id FROM queue WHERE id=?", (qid,)).fetchone()
        if q:
            db.execute("DELETE FROM queue WHERE id=?", (qid,))
            db.execute("UPDATE articles SET status='draft',updated_at=? WHERE id=?", (now(), q["article_id"]))
    normalize_queue()
    return redirect(url_for("queue_page"))


@app.route("/discover", methods=["GET", "POST"])
def discover():
    products = []
    error = None
    mode = request.values.get("mode", "random")
    query = request.values.get("query", "")
    max_price = request.values.get("max_price", "") or None
    if request.method == "POST":
        try:
            products = search_products(get_settings(), mode=mode, query=query, max_price=max_price)
            with get_db() as db:
                dismissed = {r["asin"] for r in db.execute("SELECT asin FROM dismissed_products")}
                existing = {r["asin"] for r in db.execute("SELECT asin FROM products WHERE asin<>''")}
            for p in products:
                p["dismissed"] = p["asin"] in dismissed
                p["duplicate"] = p["asin"] in existing
        except Exception as e:
            error = str(e)
    return render_template("discover.html", products=products, error=error, mode=mode, query=query, max_price=max_price or "")


@app.post("/discover/add")
def discover_add():
    asin = request.form.get("asin", "")
    settings = get_settings()
    amazon_url = affiliate_url(request.form.get("amazon_url", ""), asin, settings.get("amazon_partner_tag", ""))
    with get_db() as db:
        cur = db.execute("""INSERT INTO products(asin,title,amazon_url,image_url,price,category,features,notes,source,status,created_at,updated_at)
                          VALUES(?,?,?,?,?,'','','','amazon','draft',?,?)""",
                         (asin, request.form.get("title",""), amazon_url, request.form.get("image_url",""), request.form.get("price",""), now(), now()))
        pid = cur.lastrowid
        local = save_remote_image(request.form.get("image_url", ""), pid)
        if local:
            db.execute("UPDATE products SET local_image=? WHERE id=?", (local, pid))
    return redirect(url_for("product_edit", product_id=pid))


@app.post("/discover/dismiss")
def discover_dismiss():
    asin = request.form.get("asin", "")
    if asin:
        with get_db() as db:
            db.execute("INSERT OR REPLACE INTO dismissed_products(asin,title,dismissed_at) VALUES(?,?,?)", (asin, request.form.get("title",""), now()))
    return redirect(url_for("discover"))


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    if request.method == "POST":
        allowed = [
            "ai_engine","openai_api_key","openai_model","gemini_api_key","gemini_model",
            "smtp_host","smtp_port","smtp_user","smtp_password","smtp_from","smtp_to","smtp_security",
            "amazon_credential_id","amazon_secret","amazon_partner_tag","amazon_marketplace","amazon_token_url","amazon_api_base",
            "publish_days","publish_time","timezone",
        ]
        values = {k: request.form.get(k, "").strip() for k in allowed}
        set_settings(values)
        configure_scheduler(get_settings(), publish_next)
        flash("Impostazioni salvate.", "success")
        return redirect(url_for("settings_page"))
    return render_template("settings.html", settings=get_settings())


@app.route("/uploads/<path:filename>")
def uploads(filename):
    return send_from_directory(UPLOAD_DIR, filename)


init_db()
start_scheduler()
configure_scheduler(get_settings(), publish_next)

if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False)
