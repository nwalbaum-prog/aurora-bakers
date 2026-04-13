"""
tools/whatsapp.py — Envío de mensajes por WhatsApp Meta API
"""
import logging
import requests
from tools.retry import con_reintento
import config

logger = logging.getLogger(__name__)


@con_reintento(max_intentos=3, delay=2, exceptions=(requests.RequestException,))
def send_whatsapp(to: str, message: str) -> bool:
    """
    Envía un mensaje de texto por WhatsApp.
    `to` debe incluir código de país sin '+' (ej: '56912345678').
    Retorna True si el envío fue exitoso.
    """
    if not config.META_PAGE_ACCESS_TOKEN or not config.WHATSAPP_PHONE_NUMBER_ID:
        logger.warning("[wa] Credenciales de WhatsApp no configuradas — mensaje no enviado")
        return False

    # Truncar si excede límite de WA
    if len(message) > config.WA_MAX_CHARS:
        message = message[:config.WA_MAX_CHARS - 3] + '...'

    url = f"https://graph.facebook.com/v17.0/{config.WHATSAPP_PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message},
    }
    headers = {
        "Authorization": f"Bearer {config.META_PAGE_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=10)
    resp.raise_for_status()
    logger.info(f"[wa] Mensaje enviado a {to} ({len(message)} chars)")
    return True


def send_whatsapp_safe(to: str, message: str) -> bool:
    """Versión sin excepción — loguea el error y retorna False."""
    try:
        return send_whatsapp(to, message)
    except Exception as e:
        logger.error(f"[wa] Error enviando a {to}: {e}")
        return False
