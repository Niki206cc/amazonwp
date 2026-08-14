import json
import re

from flask import flash, redirect, request, url_for

import app as appmod
from radar_persistence import (
    delete_opportunity,
    get_opportunity,
    list_opportunities,
    save_opportunities,
)


def _parse_opportunity(raw):
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _recommended_date(opportunity):
    if not opportunity:
        return ""
    candidate = str(opportunity.get("publish_date") or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate) and candidate >= appmod.now()[:10]:
        return candidate
    return ""


def opportunities_persistent():
    error = None
    try:
        days = max(7, min(int(request.values.get("days", 30)), 60))
    except Exception:
        days = 30

    if request.method == "POST":
        try:
            fresh = appmod.generate_opportunities(appmod.get_settings(), days=days)
            save_opportunities(fresh)
            flash(f"Radar aggiornato: {len(fresh)} opportunità trovate. Le opportunità restano salvate finché non le elimini.", "success")
        except Exception as exc:
            error = str(exc)

    rows = list_opportunities()
    return appmod.render_template("opportunities.html", opportunities=rows, error=error, days=days)


def opportunity_search_persistent():
    opportunity_id = request.form.get("opportunity_id", "").strip()
    opportunity = get_opportunity(opportunity_id)
    if not opportunity:
        flash("Opportunità non trovata.", "error")
        return redirect(url_for("opportunities"))
    payload = json.dumps(opportunity, ensure_ascii=False, separators=(",", ":"))
    return redirect(
        url_for(
            "discover",
            query=opportunity.get("amazon_query", "") or opportunity.get("name", ""),
            opportunity=payload,
        )
    )


@appmod.app.post("/opportunities/<int:opportunity_id>/delete")
def opportunity_delete(opportunity_id):
    delete_opportunity(opportunity_id)
    flash("Opportunità eliminata dal Radar. Gli eventuali articoli creati restano disponibili.", "success")
    return redirect(url_for("opportunities"))


@appmod.app.post("/discover/manual-link")
def discover_manual_link():
    opportunity_json = request.form.get("opportunity_json", "").strip()
    amazon_url = request.form.get("amazon_url", "").strip()
    opportunity = _parse_opportunity(opportunity_json)

    try:
        if not amazon_url:
            raise RuntimeError("Incolla un link prodotto Amazon.it.")

        product = appmod.scrape_amazon_product(amazon_url)
        settings = appmod.get_settings()
        asin = (product.get("asin") or appmod.extract_asin(amazon_url) or "").strip().upper()
        canonical_url = appmod.affiliate_url(
            product.get("amazon_url") or amazon_url,
            asin,
            settings.get("amazon_partner_tag", ""),
        )
        product["asin"] = asin
        product["amazon_url"] = canonical_url
        duplicate = appmod.duplicate_for(asin, canonical_url)

        with appmod.get_db() as db:
            cur = db.execute(
                """INSERT INTO products(asin,title,amazon_url,image_url,price,category,features,notes,source,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,'draft',?,?)""",
                (
                    asin,
                    product.get("title", "") or "Prodotto Amazon",
                    canonical_url,
                    product.get("image_url", ""),
                    product.get("price", ""),
                    product.get("category", ""),
                    product.get("features", ""),
                    product.get("notes", ""),
                    "radar-link",
                    appmod.now(),
                    appmod.now(),
                ),
            )
            product_id = cur.lastrowid

        if product.get("image_url"):
            local = appmod.save_remote_image(product.get("image_url", ""), product_id)
            if local:
                with appmod.get_db() as db:
                    db.execute("UPDATE products SET local_image=? WHERE id=?", (local, product_id))

        if duplicate:
            flash(
                f"Prodotto importato. Attenzione: risulta già presente nell’articolo “{duplicate['article_title']}”. Controlla i dati prima di generare.",
                "warning",
            )
        else:
            flash("Prodotto importato dal link. Controlla e completa i dati, poi genera l’articolo.", "success")
        return redirect(url_for("product_edit", product_id=product_id, opportunity=opportunity_json))

    except Exception as exc:
        appmod.log(f"Importazione link Radar fallita: {exc}", "ERROR")
        flash(str(exc), "error")
        query = opportunity.get("amazon_query", "") if opportunity else ""
        return redirect(url_for("discover", query=query, opportunity=opportunity_json))


def product_edit_with_opportunity(product_id):
    opportunity_json = request.args.get("opportunity", "") or request.form.get("opportunity_json", "")
    opportunity_json = opportunity_json.strip()
    opportunity = _parse_opportunity(opportunity_json)

    with appmod.get_db() as db:
        product = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        articles = db.execute("SELECT * FROM articles WHERE product_id=? ORDER BY id DESC", (product_id,)).fetchall()
    if not product:
        return "Prodotto non trovato", 404

    if request.method == "POST":
        url = request.form.get("amazon_url", "").strip()
        asin = request.form.get("asin", "").strip().upper() or appmod.extract_asin(url)
        url = appmod.affiliate_url(url, asin, appmod.get_settings().get("amazon_partner_tag", ""))
        new_image_url = request.form.get("image_url", "").strip()
        with appmod.get_db() as db:
            db.execute(
                """UPDATE products SET asin=?,title=?,amazon_url=?,image_url=?,price=?,category=?,features=?,notes=?,updated_at=? WHERE id=?""",
                (
                    asin,
                    request.form.get("title", ""),
                    url,
                    new_image_url,
                    request.form.get("price", ""),
                    request.form.get("category", ""),
                    request.form.get("features", ""),
                    request.form.get("notes", ""),
                    appmod.now(),
                    product_id,
                ),
            )
        if new_image_url and new_image_url != (product["image_url"] or ""):
            local = appmod.save_remote_image(new_image_url, product_id)
            if local:
                with appmod.get_db() as db:
                    db.execute("UPDATE products SET local_image=? WHERE id=?", (local, product_id))
        flash("Prodotto aggiornato. Il contesto del Radar è stato mantenuto.", "success")
        if opportunity_json:
            return redirect(url_for("product_edit", product_id=product_id, opportunity=opportunity_json))
        return redirect(url_for("product_edit", product_id=product_id))

    return appmod.render_template(
        "product_edit.html",
        product=product,
        articles=articles,
        duplicate=appmod.duplicate_for(product["asin"], product["amazon_url"], product_id),
        opportunity=opportunity,
        opportunity_json=opportunity_json,
    )


def product_generate_with_opportunity(product_id):
    with appmod.get_db() as db:
        product = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if not product:
        return "Prodotto non trovato", 404

    opportunity_json = request.form.get("opportunity_json", "").strip()
    opportunity = _parse_opportunity(opportunity_json)

    try:
        settings = appmod.get_settings()
        result = appmod.generate_article(
            settings,
            appmod.article_payload(dict(product), settings),
            opportunity=opportunity,
        )
        result["html"] = appmod.ensure_affiliate_links(
            result.get("html", ""), settings.get("amazon_partner_tag", "")
        )
        scheduled_date = _recommended_date(opportunity)
        opportunity_id = None
        if opportunity:
            try:
                opportunity_id = int(opportunity.get("_id")) if opportunity.get("_id") else None
            except Exception:
                opportunity_id = None

        with appmod.get_db() as db:
            cur = db.execute(
                """INSERT INTO articles(product_id,title,alt_titles,meta_description,excerpt,html,ai_engine,status,created_at,updated_at,scheduled_date,opportunity_id)
                   VALUES(?,?,?,?,?,?,?,'draft',?,?,?,?)""",
                (
                    product_id,
                    result["title"],
                    json.dumps(result.get("alt_titles", []), ensure_ascii=False),
                    result.get("meta_description", ""),
                    result.get("excerpt", ""),
                    result["html"],
                    result["engine"],
                    appmod.now(),
                    appmod.now(),
                    scheduled_date,
                    opportunity_id,
                ),
            )
            article_id = cur.lastrowid

        if scheduled_date:
            flash(f"Articolo generato con il contesto del Radar. Data consigliata preimpostata: {scheduled_date}.", "success")
        elif opportunity:
            flash("Articolo generato con tutti i parametri dell’opportunità Radar.", "success")
        else:
            flash("Articolo generato.", "success")
        return redirect(url_for("article_edit", article_id=article_id))

    except Exception as exc:
        appmod.log(f"Generazione AI fallita: {exc}", "ERROR")
        flash(str(exc), "error")
        if opportunity_json:
            return redirect(url_for("product_edit", product_id=product_id, opportunity=opportunity_json))
        return redirect(url_for("product_edit", product_id=product_id))


appmod.app.view_functions["opportunities"] = opportunities_persistent
appmod.app.view_functions["opportunity_search"] = opportunity_search_persistent
appmod.app.view_functions["product_edit"] = product_edit_with_opportunity
appmod.app.view_functions["product_generate"] = product_generate_with_opportunity


if __name__ == "__main__":
    appmod.app.run(host=appmod.HOST, port=appmod.PORT, debug=False)
