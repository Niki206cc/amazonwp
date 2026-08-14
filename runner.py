import json
import re

from flask import flash, redirect, request, url_for

import app as appmod


@appmod.app.post("/discover/manual-link")
def discover_manual_link():
    opportunity_json = request.form.get("opportunity_json", "").strip()
    amazon_url = request.form.get("amazon_url", "").strip()
    opportunity = None
    if opportunity_json:
        try:
            parsed = json.loads(opportunity_json)
            if isinstance(parsed, dict):
                opportunity = parsed
        except Exception:
            opportunity = None

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

        result = appmod.generate_article(
            settings,
            appmod.article_payload(product, settings),
            opportunity=opportunity,
        )
        result["html"] = appmod.ensure_affiliate_links(
            result.get("html", ""), settings.get("amazon_partner_tag", "")
        )

        scheduled_date = ""
        if opportunity:
            candidate = str(opportunity.get("publish_date") or "").strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate) and candidate >= appmod.now()[:10]:
                scheduled_date = candidate

        with appmod.get_db() as db:
            cur = db.execute(
                """INSERT INTO articles(product_id,title,alt_titles,meta_description,excerpt,html,ai_engine,status,created_at,updated_at,scheduled_date)
                   VALUES(?,?,?,?,?,?,?,'draft',?,?,?)""",
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
                ),
            )
            article_id = cur.lastrowid

        if duplicate:
            flash(
                f"Articolo generato dal link. Attenzione: il prodotto risultava già presente nell’articolo “{duplicate['article_title']}”.",
                "warning",
            )
        elif scheduled_date:
            flash(
                f"Articolo generato dal link con il contesto del Radar. Data consigliata preimpostata: {scheduled_date}.",
                "success",
            )
        else:
            flash("Articolo generato dal link con tutto il contesto del Radar.", "success")
        return redirect(url_for("article_edit", article_id=article_id))

    except Exception as exc:
        appmod.log(f"Generazione da link Radar fallita: {exc}", "ERROR")
        flash(str(exc), "error")
        query = opportunity.get("amazon_query", "") if opportunity else ""
        return redirect(url_for("discover", query=query, opportunity=opportunity_json))


if __name__ == "__main__":
    appmod.app.run(host=appmod.HOST, port=appmod.PORT, debug=False)
