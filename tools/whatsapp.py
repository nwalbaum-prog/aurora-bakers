"""
tools/whatsapp.py — Envío de mensajes por WhatsApp via Evolution API
(migrado desde Meta Cloud API — no requiere developer portal)
"""
import logging
import requests
from tools.retry import con_reintento
import config

logger = logging.getLogger(__name__)


@con_reintento(max_intentos=3, delay=2, exceptions=(requests.RequestException,))
def send_whatsapp(to: str, message: str) -> bool:
    """
    Envía un mensaje de texto por WhatsApp via Evolution API.
    `to` debe incluir código de país sin '+' ni '@' (ej: '56912345678').
    Retorna True si el envío fue exitoso.
    """
    if not config.EVOLUTION_API_URL or not config.EVOLUTION_API_KEY or not config.EVOLUTION_INSTANCE:
        logger.warning("[wa] Evolution API no configurada — mensaje no enviado")
        return False

    # Aceptar JID completo (ej: "56912345678@s.whatsapp.net" o "38328439148772@lid")
    # o número limpio (ej: "56912345678")
    if '@' in to:
        numero = to  # pasar JID completo — Evolution API lo maneja directamente
    else:
        numero = to.strip().lstrip('+')

    # Truncar si excede límite de WA
    if len(message) > config.WA_MAX_CHARS:
        message = message[:config.WA_MAX_CHARS - 3] + '...'

    url = f"{config.EVOLUTION_API_URL}/message/sendText/{config.EVOLUTION_INSTANCE}"
    # Evolution API v1 usa "textMessage": {"text": "..."} en vez de "text" directo
    payload = {
        "number": numero,
        "textMessage": {
            "text": message,
        },
    }
    headers = {
        "apikey": config.EVOLUTION_API_KEY,
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "true",  # bypass ngrok browser warning page
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=10)
    resp.raise_for_status()
    logger.info(f"[wa] Mensaje enviado a {numero} ({len(message)} chars)")
    return True


def send_whatsapp_safe(to: str, message: str) -> bool:
    """Versión sin excepción — loguea el error y retorna False."""
    try:
        return send_whatsapp(to, message)
    except Exception as e:
        logger.error(f"[wa] Error enviando a {to}: {e}")
        return False


def get_qr_code() -> dict:
    """
    Obtiene el QR code para conectar la instancia de WhatsApp.
    Retorna {'qrcode': 'data:image/png;base64,...'} o {'error': '...'}.
    """
    try:
        url = f"{config.EVOLUTION_API_URL}/instance/connect/{config.EVOLUTION_INSTANCE}"
        resp = requests.get(url, headers={"apikey": config.EVOLUTION_API_KEY, "ngrok-skip-browser-warning": "true"}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"[wa] Error obteniendo QR: {e}")
        return {"error": str(e)}


def get_connection_status() -> str:
    """Retorna el estado de conexión: 'open', 'connecting', 'close'."""
    try:
        url = f"{config.EVOLUTION_API_URL}/instance/connectionState/{config.EVOLUTION_INSTANCE}"
        resp = requests.get(url, headers={"apikey": config.EVOLUTION_API_KEY, "ngrok-skip-browser-warning": "true"}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        return data.get('instance', {}).get('state', 'unknown')
    except Exception as e:
        logger.error(f"[wa] Error verificando estado: {e}")
        return "error"
