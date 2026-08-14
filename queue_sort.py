from flask import request

import app as appmod


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
    slots = appmod.next_publish_slots(settings, len(items))

    for index, item in enumerate(items):
        if item.get("scheduled_date"):
            parts = item["scheduled_date"].split("-")
            item["manual_scheduled_date"] = (
                f"{parts[2]}/{parts[1]}/{parts[0]}" if len(parts) == 3 else item["scheduled_date"]
            )
            item["scheduled_at"] = ""
            item["sort_date"] = item["scheduled_date"] + " 00:00"
        else:
            item["manual_scheduled_date"] = ""
            if index < len(slots):
                item["scheduled_at"] = slots[index].strftime("%d/%m/%Y %H:%M")
                item["sort_date"] = slots[index].strftime("%Y-%m-%d %H:%M")
            else:
                item["scheduled_at"] = ""
                item["sort_date"] = "9999-12-31 23:59"

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
