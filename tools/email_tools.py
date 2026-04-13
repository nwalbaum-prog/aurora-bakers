"""
tools/email_tools.py — Envío de emails vía SMTP
"""
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from tools.retry import con_reintento
import config

logger = logging.getLogger(__name__)


@con_reintento(max_intentos=2, delay=3, exceptions=(smtplib.SMTPException, OSError))
def send_email(to: str, subject: str, body: str, html: bool = False) -> bool:
    """
    Envía un email.
    `html=True` para cuerpo en HTML, `html=False` para plain text.
    Retorna True si fue exitoso.
    """
    if not config.SMTP_USER or not config.SMTP_PASS:
        logger.warning("[email] Credenciales SMTP no configuradas — email no enviado")
        return False

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = config.SMTP_USER
    msg['To']      = to

    mime_type = 'html' if html else 'plain'
    msg.attach(MIMEText(body, mime_type, 'utf-8'))

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(config.SMTP_USER, config.SMTP_PASS)
        server.sendmail(config.SMTP_USER, to, msg.as_string())

    logger.info(f"[email] Email enviado a {to}: {subject}")
    return True


def send_email_safe(to: str, subject: str, body: str, html: bool = False) -> bool:
    """Versión sin excepción — loguea el error y retorna False."""
    try:
        return send_email(to, subject, body, html)
    except Exception as e:
        logger.error(f"[email] Error enviando a {to}: {e}")
        return False
