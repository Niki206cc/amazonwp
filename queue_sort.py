from flask import request

import app as appmod


def _assign_effective_slots(settings, items):
    """Assegna prima gli slot agli articoli con data manuale, poi riempie i vuoti con gli altri."""
    if not items or settings.get("scheduler_enabled") != "1":
        return

    # Generiamo un orizzonte ampio: serve a gestire articoli manuali anche molto avanti nel tempo.
    slots = appmod.next_publish_slots(settings, max(400, len(items) * 10))
    if not slots:
        return

    used = set()

    manual_items = sorted(
        [item for item in items if item.get("scheduled_date")],
        key=lambda item: (item.get("scheduled_date") or "", item.get("position", 0)),
    )
    automatic_items = sorted(
        [item for item in items if not item.get("scheduled_date")],
        key=lambda item: item.get("position", 0),
    )

    # Gli articoli con data scelta dall'utente prenotano per primi il primo slot utile
    # dalla loro data in poi. Se più articoli competono per lo stesso slot, i successivi
    # slittano automaticamente alle fasce/giornate successive.
    for item in manual_items:
        target_date = item.get("scheduled_date") or ""
        assigned = None
        for idx, slot in enumerate(slots):
            if idx in used:
                continue
            if slot.strftime("%Y-%m-%d") >= target_date:
                assigned = (idx, slot)
                break
        if assigned:
            idx, slot = assigned
            used.add(idx)
            item["effective_slot"] = slot
            item["scheduled_at"] = slot.strftime("%d/%m/%Y %H:%M")
            item["sort_date"] = slot.strftime("%Y-%m-%d %H:%M")

    # Gli articoli automatici usano solo gli slot rimasti liberi. Quindi, se un articolo
    # manuale occupa una fascia, quello automatico viene mostrato direttamente nella prima
    # fascia successiva disponibile invece di risultare in collisione.
    free_slots = [(idx, slot) for idx, slot in enumerate(slots) if idx not in used]
    for item, (_, slot) in zip(automatic_items, free_slots):
        item["effective_slot"] = slot
        item["scheduled_at"] = slot.strftime("%d/%m/%Y %H:%M")
        item["sort_date"] = slot.strftime("%Y-%m-%d %H:%M")


def queue_page_sorted():
    settings = appmod.get_settings()
    sort_by = (request.args.get("sort") or "position").lower()
    direction = (request.args.get("dir") or "asc").lower()
    if sort_by not in ("position", "date"):
        sort_by = "position"
    if direction not in ("asc", "desc"):
        direction = "asc"

    with appmod.get_db() as db:
        rows = db.execute(
            """SELECT q.id qid,q.position,a.id article_id,a.title,a.status,a.error,a.scheduled_date,
                      p.title product_title,p.local_image,p.image_url,p.category
               FROM queue q
               JOIN articles a ON a.id=q.article_id
               JOIN products p ON p.id=a.product_id
               ORDER BY q.position"""
        ).fetchall()

    items = [dict(row) for row in rows]

    for item in items:
        item["scheduled_at"] = ""
        item["sort_date"] = "9999-12-31 23:59"
        if item.get("scheduled_date"):
            parts = item["scheduled_date"].split("-")
            item["manual_scheduled_date"] = (
                f"{parts[2]}/{parts[1]}/{parts[0]}" if len(parts) == 3 else item["scheduled_date"]
            )
        else:
            item["manual_scheduled_date"] = ""

    _assign_effective_slots(settings, items)

    if sort_by == "date":
        items.sort(
            key=lambda item: (item.get("sort_date", "9999-12-31 23:59"), item.get("position", 0)),
            reverse=(direction == "desc"),
        )
    else:
        items.sort(key=lambda item: item.get("position", 0), reverse=(direction == "desc"))

    for index, item in enumerate(items):
        item["similar_warning"] = index > 0 and appmod.are_similar_products(items[index - 1], item)

    return appmod.render_template(
        "queue.html",
        items=items,
        scheduler_enabled=settings.get("scheduler_enabled") == "1",
        sort_by=sort_by,
        direction=direction,
    )


appmod.app.view_functions["queue_page"] = queue_page_sorted
