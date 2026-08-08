import mimetypes
import smtplib
import ssl
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

    # Postie deve trovare l'HTML come corpo principale del messaggio.
    # Evitiamo il multipart/alternative con un fallback text/plain,
    # perché alcune configurazioni di Postie pubblicano il primo blocco
    # testuale invece della versione HTML.
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

    # La porta 465 usa SSL/TLS implicito. Se in configurazione è rimasto
    # STARTTLS per errore, forziamo comunque SSL per evitare timeout.
    if port == 465:
        security = "ssl"

    timeout = 60
    context = ssl.create_default_context()
    server = None

    try:
        if security == "ssl":
            server = smtplib.SMTP_SSL(
                host,
                port,
                timeout=timeout,
                context=context,
            )
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
            raise RuntimeError(
                f"Modalità di sicurezza SMTP non valida: {security}"
            )

        if user:
            server.login(user, password)

        server.send_message(msg)

    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(
            f"Autenticazione SMTP fallita: {exc.smtp_code} {exc.smtp_error.decode(errors='ignore') if isinstance(exc.smtp_error, bytes) else exc.smtp_error}"
        ) from exc

    except smtplib.SMTPConnectError as exc:
        raise RuntimeError(
            f"Connessione SMTP fallita: {exc.smtp_code} {exc.smtp_error}"
        ) from exc

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
