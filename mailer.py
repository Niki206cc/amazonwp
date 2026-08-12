import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path


DEFAULT_POSTIE_CATEGORY = "Consigli per gli acquisti"


def send_article(settings, article, image_path=None):
    host = settings.get("smtp_host", "").strip()
    to_addr = settings.get("smtp_to", "").strip()
    from_addr = settings.get("smtp_from", "").strip() or settings.get("smtp_user", "").strip()

    if not host or not to_addr or not from_addr:
        raise RuntimeError("Configurazione SMTP incompleta")

    msg = EmailMessage()
    prefix = settings.get("email_subject_prefix", "").strip()
    postie_category = (settings.get("postie_category") or DEFAULT_POSTIE_CATEGORY).strip()

    parts = []
    if postie_category:
        parts.append(f"[{postie_category}]")
    if prefix and prefix.lower() != f"[{postie_category}]".lower():
        parts.append(prefix)
    parts.append(article["title"])
    msg["Subject"] = " ".join(parts).strip()
    msg["From"] = from_addr
    msg["To"] = to_addr

    # Postie è configurato per usare la versione HTML degli articoli Amazon.
    msg.set_content(article["html"], subtype="html")

    if image_path and Path(image_path).exists():
        p = Path(image_path)
        mime, _ = mimetypes.guess_type(p.name)
        maintype, subtype = (mime or "application/octet-stream").split("/", 1)
        msg.add_attachment(
            p.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=p.name,
        )

    try:
        port = int(settings.get("smtp_port") or 587)
    except (TypeError, ValueError):
        raise RuntimeError("Porta SMTP non valida")

    security = (settings.get("smtp_security") or "starttls").strip().lower()
    user = settings.get("smtp_user", "").strip()
    password = settings.get("smtp_password", "")

    if port == 465:
        security = "ssl"

    timeout = 60
    context = ssl.create_default_context()
    server = None

    try:
        if security == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
            server.ehlo()
        elif security == "starttls":
            server = smtplib.SMTP(host, port, timeout=timeout)
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
        elif security == "none":
            server = smtplib.SMTP(host, port, timeout=timeout)
            server.ehlo()
        else:
            raise RuntimeError(f"Modalità di sicurezza SMTP non valida: {security}")

        if user:
            server.login(user, password)

        server.send_message(msg)

    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(
            f"Autenticazione SMTP fallita: {exc.smtp_code} {exc.smtp_error.decode(errors='ignore') if isinstance(exc.smtp_error, bytes) else exc.smtp_error}"
        ) from exc
    except smtplib.SMTPConnectError as exc:
        raise RuntimeError(f"Connessione SMTP fallita: {exc.smtp_code} {exc.smtp_error}") from exc
    except (TimeoutError, OSError) as exc:
        raise RuntimeError(
            f"Connessione SMTP non riuscita verso {host}:{port} ({security}): {exc}"
        ) from exc
    except smtplib.SMTPException as exc:
        raise RuntimeError(f"Errore SMTP: {exc}") from exc
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                try:
                    server.close()
                except Exception:
                    pass
