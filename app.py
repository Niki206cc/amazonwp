from pathlib import Path
import json
import shutil
import urllib.request
import re
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
    """Restituisce sempre un URL Amazon canonico con il Partner Tag configurato."""
    url = (url or "").strip()
    asin = (asin or "").strip().upper() or extract_asin(url)
    partner_tag = (partner_tag or "").strip()

    if not asin:
        return url

    canonical = f"https://www.amazon.it/dp/{asin}"
    if partner_tag:
        canonical += f"?tag={partner_tag}"
    return canonical


def ensure_affiliate_links(html, partner_tag):
    if not html or not partner_tag:
        return html

    pattern = re.compile(r'https?://(?:www\.)?amazon\.it/[^\s"\'<>]+', re.I)
    return pattern.sub(lambda m: affiliate_url(m.group(0), partner_tag=partner_tag), html)


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
    affiliated = affiliate_url(
        product.get("amazon_url", ""),
        product.get("asin", ""),
        settings.get("amazon_partner_tag", ""),
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
        rows = db.execute(
            """SELECT p.id product_id,p.title product_title,a.id article_id,a.title article_title,a.status,a.published_at,a.created_at
               FROM products p LEFT JOIN articles a ON a.product_id=p.id
               WHERE (p.asin<>'' AND p.asin=?) OR (p.amazon_url<>'' AND p.amazon_url=?)""",
            (asin or "", amazon_url or ""),
        ).fetchall()
        for row in rows:
            if exclude_id and row["product_id"] == exclude_id:
                continue
            if row["article_id"]:
                return dict(row)
    return None


def normalize_queue():
    with get_db() as db:
        rows = db.execute("SELECT id FROM queue ORDER BY position,id").fetchall()
        for pos, row in enumerate(rows, 1):
            db.execute("UPDATE queue SET position=? WHERE id=?", (pos, row["id"]))


def publish_next():
    with app.app_context():
        with get_db() as db:
            item = db.execute(
                """SELECT q.id qid,a.*,p.local_image,p.image_url
                   FROM queue q JOIN articles a ON a.id=q.article_id JOIN products p ON p.id=a.product_id
                   ORDER BY q.position ASC LIMIT 1"""
            ).fetchone()
        if not item:
            log("Scheduler: coda vuota")
            return
        try:
            send_article(get_settings(), dict(item), item["local_image"])
            with get_db() as db:
                db.execute("UPDATE articles SET status='published',published_at=?,updated_at=? WHERE id=?", (now(), now(), item["id"]))
                db.execute("DELETE FROM queue WHERE id=?", (item["qid"],))
            normalize_queue()
            log(f"Articolo pubblicato: {item['title']}")
        except Exception as exc:
            with get_db() as db:
                db.execute("UPDATE articles SET error=?,updated_at=? WHERE id=?", (str(exc), now(), item["id"]))
            log(f"Errore pubblicazione: {exc}", "ERROR")


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
        duplicate = duplicate_for(asin, url)
        with get_db() as db:
            cur = db.execute(
                """INSERT INTO products(asin,title,amazon_url,image_url,price,category,features,notes,source,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,'draft',?,?)""",
                (asin, product["title"], url, product["image_url"], product["price"], product["category"], product["features"], product["notes"], "manual", now(), now()),
            )
            product_id = cur.lastrowid
            local = None
            upload = request.files.get("image_file")
            if upload and upload.filename:
                suffix = Path(upload.filename).suffix.lower() or ".jpg"
                path = UPLOAD_DIR / f"product_{product_id}{suffix}"
                upload.save(path)
                local = str(path)
            elif product["image_url"]:
                local = save_remote_image(product["image_url"], product_id)
            if local:
                db.execute("UPDATE products SET local_image=? WHERE id=?", (local, product_id))
        if duplicate:
            flash(f"Attenzione: prodotto già presente in un articolo ({duplicate['article_title']}). Puoi comunque generarlo.", "warning")
        return redirect(url_for("product_edit", product_id=product_id))
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
            db.execute(
                """UPDATE products SET asin=?,title=?,amazon_url=?,image_url=?,price=?,category=?,features=?,notes=?,updated_at=? WHERE id=?""",
                (asin, request.form.get("title", ""), url, request.form.get("image_url", ""), request.form.get("price", ""), request.form.get("category", ""), request.form.get("features", ""), request.form.get("notes", ""), now(), product_id),
            )
        flash("Prodotto aggiornato.", "success")
        return redirect(url_for("product_edit", product_id=product_id))
    duplicate = duplicate_for(product["asin"], product["amazon_url"], product_id)
    return render_template("product_edit.html", product=product, articles=articles, duplicate=duplicate)


@app.post("/product/<int:product_id>/delete")
def product_delete(product_id):
    with get_db() as db:
        product = db.execute("SELECT title,local_image FROM products WHERE id=?", (product_id,)).fetchone()
        if not product:
            flash("Prodotto non trovato.", "error")
            return redirect(url_for("index"))
        local_image = product["local_image"]
        db.execute("DELETE FROM products WHERE id=?", (product_id,))
    if local_image:
        try:
            Path(local_image).unlink(missing_ok=True)
        except Exception:
            pass
    normalize_queue()
    flash("Prodotto eliminato insieme agli articoli collegati.", "success")
    return redirect(url_for("index"))


@app.post("/product/<int:product_id>/generate")
def product_generate(product_id):
    with get_db() as db:
        product = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if not product:
        return "Prodotto non trovato", 404
    try:
        settings = get_settings()
        result = generate_article(settings, article_payload(dict(product), settings))
        result["html"] = ensure_affiliate_links(result.get("html", ""), settings.get("amazon_partner_tag", ""))
        with get_db() as db:
            cur = db.execute(
                """INSERT INTO articles(product_id,title,alt_titles,meta_description,excerpt,html,ai_engine,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,'draft',?,?)""",
                (product_id, result["title"], json.dumps(result["alt_titles"], ensure_ascii=False), result.get("meta_description", ""), result.get("excerpt", ""), result["html"], result["engine"], now(), now()),
            )
            article_id = cur.lastrowid
        flash("Articolo generato.", "success")
        return redirect(url_for("article_edit", article_id=article_id))
    except Exception as exc:
        log(f"Generazione AI fallita: {exc}", "ERROR")
        flash(str(exc), "error")
        return redirect(url_for("product_edit", product_id=product_id))


@app.route("/article/<int:article_id>", methods=["GET", "POST"])
def article_edit(article_id):
    with get_db() as db:
        article = db.execute(
            "SELECT a.*,p.title product_title,p.amazon_url,p.local_image,p.image_url FROM articles a JOIN products p ON p.id=a.product_id WHERE a.id=?",
            (article_id,),
        ).fetchone()
    if not article:
        return "Articolo non trovato", 404
    if request.method == "POST":
        titles = [request.form.get(f"alt_title_{i}", "").strip() for i in range(1, 6)]
        html = ensure_affiliate_links(request.form.get("html", ""), get_settings().get("amazon_partner_tag", ""))
        with get_db() as db:
            db.execute(
                """UPDATE articles SET title=?,alt_titles=?,meta_description=?,excerpt=?,html=?,updated_at=? WHERE id=?""",
                (request.form.get("title", ""), json.dumps(titles, ensure_ascii=False), request.form.get("meta_description", ""), request.form.get("excerpt", ""), html, now(), article_id),
            )
        flash("Articolo salvato.", "success")
        return redirect(url_for("article_edit", article_id=article_id))
    alt_titles = json.loads(article["alt_titles"] or "[]")
    alt_titles = (alt_titles + [""] * 5)[:5]
    preview_image = ""
    if article["local_image"]:
        preview_image = url_for("uploads", filename=Path(article["local_image"]).name)
    elif article["image_url"]:
        preview_image = article["image_url"]
    return render_template("article_edit.html", article=article, alt_titles=alt_titles, preview_image=preview_image)


@app.post("/article/<int:article_id>/delete")
def article_delete(article_id):
    with get_db() as db:
        article = db.execute("SELECT product_id,title FROM articles WHERE id=?", (article_id,)).fetchone()
        if not article:
            flash("Articolo non trovato.", "error")
            return redirect(url_for("index"))
        product_id = article["product_id"]
        db.execute("DELETE FROM queue WHERE article_id=?", (article_id,))
        db.execute("DELETE FROM articles WHERE id=?", (article_id,))
    normalize_queue()
    flash("Articolo eliminato.", "success")
    return redirect(url_for("product_edit", product_id=product_id))


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
        article = db.execute("SELECT a.*,p.local_image FROM articles a JOIN products p ON p.id=a.product_id WHERE a.id=?", (article_id,)).fetchone()
    try:
        send_article(get_settings(), dict(article), article["local_image"])
        flash("Email inviata a Postie.", "success")
    except Exception as exc:
        flash(str(exc), "error")
    return redirect(url_for("article_edit", article_id=article_id))


@app.route("/queue")
def queue_page():
    with get_db() as db:
        items = db.execute(
            """SELECT q.id qid,q.position,a.id article_id,a.title,a.status,p.title product_title
               FROM queue q JOIN articles a ON a.id=q.article_id JOIN products p ON p.id=a.product_id ORDER BY q.position"""
        ).fetchall()
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
        item = db.execute("SELECT article_id FROM queue WHERE id=?", (qid,)).fetchone()
        if item:
            db.execute("DELETE FROM queue WHERE id=?", (qid,))
            db.execute("UPDATE articles SET status='draft',updated_at=? WHERE id=?", (now(), item["article_id"]))
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
                dismissed = {row["asin"] for row in db.execute("SELECT asin FROM dismissed_products")}
                existing = {row["asin"] for row in db.execute("SELECT asin FROM products WHERE asin<>''")}
            for product in products:
                product["dismissed"] = product["asin"] in dismissed
                product["duplicate"] = product["asin"] in existing
        except Exception as exc:
            error = str(exc)
    return render_template("discover.html", products=products, error=error, mode=mode, query=query, max_price=max_price or "")


@app.post("/discover/add")
def discover_add():
    asin = request.form.get("asin", "")
    settings = get_settings()
    amazon_url = affiliate_url(request.form.get("amazon_url", ""), asin, settings.get("amazon_partner_tag", ""))
    with get_db() as db:
        cur = db.execute(
            """INSERT INTO products(asin,title,amazon_url,image_url,price,category,features,notes,source,status,created_at,updated_at)
               VALUES(?,?,?,?,?,'','','','amazon','draft',?,?)""",
            (asin, request.form.get("title", ""), amazon_url, request.form.get("image_url", ""), request.form.get("price", ""), now(), now()),
        )
        product_id = cur.lastrowid
        local = save_remote_image(request.form.get("image_url", ""), product_id)
        if local:
            db.execute("UPDATE products SET local_image=? WHERE id=?", (local, product_id))
    return redirect(url_for("product_edit", product_id=product_id))


@app.post("/discover/dismiss")
def discover_dismiss():
    asin = request.form.get("asin", "")
    if asin:
        with get_db() as db:
            db.execute("INSERT OR REPLACE INTO dismissed_products(asin,title,dismissed_at) VALUES(?,?,?)", (asin, request.form.get("title", ""), now()))
    return redirect(url_for("discover"))


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    if request.method == "POST":
        allowed = [
            "ai_engine", "openai_api_key", "openai_model", "gemini_api_key", "gemini_model",
            "smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_from", "smtp_to", "smtp_security",
            "amazon_credential_id", "amazon_secret", "amazon_partner_tag", "amazon_marketplace", "amazon_token_url", "amazon_api_base",
            "publish_days", "publish_time", "timezone",
        ]
        values = {key: request.form.get(key, "").strip() for key in allowed}
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
