import json

import app as appmod


def init_radar_storage():
    with appmod.get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS opportunities_saved (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            event_date TEXT,
            publish_date TEXT,
            priority TEXT,
            score TEXT,
            area TEXT,
            reason TEXT,
            article_angle TEXT,
            suggested_title TEXT,
            amazon_query TEXT,
            product_ideas TEXT NOT NULL DEFAULT '[]',
            source_url TEXT,
            source_title TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """)
        cols = {row["name"] for row in db.execute("PRAGMA table_info(articles)")}
        if "opportunity_id" not in cols:
            db.execute("ALTER TABLE articles ADD COLUMN opportunity_id INTEGER")


def _fingerprint(row):
    name = str(row.get("name") or "").strip().lower()
    event_date = str(row.get("event_date") or "").strip()
    return f"{name}|{event_date}"


def save_opportunities(rows):
    now = appmod.now()
    with appmod.get_db() as db:
        for row in rows or []:
            fp = _fingerprint(row)
            if not fp.strip("|"):
                continue
            ideas = row.get("product_ideas") or []
            if not isinstance(ideas, list):
                ideas = [str(ideas)]
            values = (
                fp,
                str(row.get("name") or "").strip(),
                str(row.get("event_date") or "").strip(),
                str(row.get("publish_date") or "").strip(),
                str(row.get("priority") or "").strip(),
                str(row.get("score") or "").strip(),
                str(row.get("area") or "").strip(),
                str(row.get("reason") or "").strip(),
                str(row.get("article_angle") or "").strip(),
                str(row.get("suggested_title") or "").strip(),
                str(row.get("amazon_query") or "").strip(),
                json.dumps(ideas, ensure_ascii=False),
                str(row.get("source_url") or "").strip(),
                str(row.get("source_title") or "").strip(),
                now,
                now,
            )
            db.execute(
                """INSERT INTO opportunities_saved(
                       fingerprint,name,event_date,publish_date,priority,score,area,reason,article_angle,
                       suggested_title,amazon_query,product_ideas,source_url,source_title,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(fingerprint) DO UPDATE SET
                       name=excluded.name,event_date=excluded.event_date,publish_date=excluded.publish_date,
                       priority=excluded.priority,score=excluded.score,area=excluded.area,reason=excluded.reason,
                       article_angle=excluded.article_angle,suggested_title=excluded.suggested_title,
                       amazon_query=excluded.amazon_query,product_ideas=excluded.product_ideas,
                       source_url=excluded.source_url,source_title=excluded.source_title,updated_at=excluded.updated_at""",
                values,
            )


def opportunity_payload(row):
    item = dict(row)
    try:
        item["product_ideas"] = json.loads(item.get("product_ideas") or "[]")
    except Exception:
        item["product_ideas"] = []
    item["_id"] = item.get("id")
    return item


def list_opportunities():
    with appmod.get_db() as db:
        rows = db.execute(
            """SELECT o.*,
                      COUNT(a.id) AS article_count,
                      GROUP_CONCAT(a.id) AS article_ids
               FROM opportunities_saved o
               LEFT JOIN articles a ON a.opportunity_id=o.id
               GROUP BY o.id
               ORDER BY CASE WHEN o.publish_date='' THEN 1 ELSE 0 END, o.publish_date, o.event_date, o.id DESC"""
        ).fetchall()
    result = []
    for row in rows:
        item = opportunity_payload(row)
        ids = item.get("article_ids") or ""
        item["article_ids_list"] = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        item["article_count"] = int(item.get("article_count") or 0)
        result.append(item)
    return result


def get_opportunity(opportunity_id):
    if not opportunity_id:
        return None
    with appmod.get_db() as db:
        row = db.execute("SELECT * FROM opportunities_saved WHERE id=?", (opportunity_id,)).fetchone()
    return opportunity_payload(row) if row else None


def delete_opportunity(opportunity_id):
    with appmod.get_db() as db:
        db.execute("UPDATE articles SET opportunity_id=NULL WHERE opportunity_id=?", (opportunity_id,))
        db.execute("DELETE FROM opportunities_saved WHERE id=?", (opportunity_id,))


init_radar_storage()
