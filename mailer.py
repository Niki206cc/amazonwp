import mimetypes
import smtplib
from email.message import EmailMessage
from pathlib import Path


def send_article(settings, article, image_path=None):
    host = settings.get("smtp_host", "").strip()
    to_addr = settings.get("smtp_to", "").strip()
    from_addr = settings.get("smtp_from", "").strip() or settings.get("smtp_user", "").strip()
    if not host or not to_addr or not from_addr:
        raise RuntimeError("Configurazione SMTP incompleta")

    msg = EmailMessage()
    prefix = settings.get("email_subject_prefix", "").strip()
    msg["Subject"] = f"{prefix} {article['title']}".strip()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content("Questo messaggio contiene una versione HTML dell'articolo.")
    msg.add_alternative(article["html"], subtype="html")

    if image_path and Path(image_path).exists():
        p = Path(image_path)
        mime, _ = mimetypes.guess_type(p.name)
        maintype, subtype = (mime or "application/octet-stream").split("/", 1)
        msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)

    port = int(settings.get("smtp_port") or 587)
    security = settings.get("smtp_security") or "starttls"
    user = settings.get("smtp_user", "")
    password = settings.get("smtp_password", "")

    if security == "ssl":
        server = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
    try:
        server.ehlo()
        if security == "starttls":
            server.starttls()
            server.ehlo()
        if user:
            server.login(user, password)
        server.send_message(msg)
    finally:
        server.quit()
