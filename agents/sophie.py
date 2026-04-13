"""
agents/sophie.py — Sophie: agente de ventas (minorista y mayorista)

Maneja conversaciones de clientes por WhatsApp.
Detecta tipo de cliente, recopila pedido, genera link Jumpseller o guarda pedido mayorista.

Tokens estructurados que puede emitir en respuesta:
  PEDIDO_CONFIRMADO|nombre|items_json|total|dia|tipo_entrega
  GENERAR_LINK|nombre_producto|dia_entrega
  PEDIDO_MAYORISTA|cliente|rut|items_json|total|dia
"""
from __future__ import annotations
import json
import logging
from datetime import datetime
import anthropic
from tools.jumpseller import get_catalogo_texto, generar_link_compra
from tools.sheets import append_row, get_records_cached
from memoria.contexto import conversaciones
from memoria.episodica import guardar_episodio, get_contexto_memoria
import config

logger = logging.getLogger(__name__)


# ── Prompts ───────────────────────────────────────────────────────────────────

SOPHIE_SYSTEM = """Eres Sophie, la asistente virtual de Aurora Bakers (panypasta.cl), una panadería artesanal de Santiago que vende panes de masa madre.

Tu rol es ayudar a los clientes a hacer sus pedidos por WhatsApp de forma amigable y eficiente.

PRODUCTOS Y PRECIOS:
{catalogo}

DÍAS DE DESPACHO: martes, miércoles, jueves, viernes y sábado.

REGLAS IMPORTANTES:
- Para CLIENTES MINORISTAS (particulares): guía el pedido, confirma los productos y el día de entrega, luego emite el token GENERAR_LINK o PEDIDO_CONFIRMADO.
- Para CLIENTES MAYORISTAS (negocios con RUT): solicita razón social, RUT, lista de productos y cantidad, luego emite PEDIDO_MAYORISTA.
- Detecta si es mayorista por: menciona RUT, "pedido para el restaurante/café/local", pide > 10 unidades o pide ciabattas en múltiplos de 6.
- Usa lenguaje cálido pero conciso. Máximo 3 párrafos por respuesta.
- Formato WhatsApp: negrita con *, sin markdown complejo.
- NUNCA inventes precios. Si no hay stock de algo, di "por el momento no tenemos".

TOKENS ESTRUCTURADOS (escríbelos en la primera línea, NUNCA en medio de una oración):
  PEDIDO_CONFIRMADO|nombre_cliente|[{{"producto":"X","cantidad":1}}]|total_clp|dia_entrega|despacho|telefono
  GENERAR_LINK|nombre_producto|dia_entrega
  PEDIDO_MAYORISTA|empresa|rut|[{{"producto":"X","cantidad":1}}]|total_clp|dia_entrega

{memoria}
"""

SOPHIE_MAYORISTA_SYSTEM = """Eres Sophie de Aurora Bakers. Estás atendiendo a un CLIENTE MAYORISTA (negocio).

PRECIOS MAYORISTAS:
{precios_mayoristas}

Solicita: empresa, RUT, productos con cantidad, día de despacho.
Cuando tengas todo, emite: PEDIDO_MAYORISTA|empresa|rut|items_json|total|dia

{memoria}
"""


# ── Función principal ─────────────────────────────────────────────────────────

def ask_sophie(user_id: str, mensaje: str, canal: str = 'whatsapp') -> str:
    """
    Procesa un mensaje de cliente y retorna la respuesta de Sophie.
    Maneja el estado de conversación vía ConversacionesStore.
    """
    # Determinar tipo de conversación
    estado = conversaciones.get(user_id)
    tipo   = estado.tipo if estado else _detectar_tipo(mensaje)

    # Obtener o crear estado
    estado = conversaciones.get_or_create(user_id, tipo)

    # Agregar mensaje del usuario
    conversaciones.append_mensaje(user_id, 'user', mensaje)

    try:
        # Construir prompt de sistema
        catalogo = get_catalogo_texto()
        memoria  = get_contexto_memoria('sophie', limit=2)

        if tipo == 'mayorista':
            precios = _formato_precios_mayoristas()
            system  = SOPHIE_MAYORISTA_SYSTEM.format(
                precios_mayoristas=precios,
                memoria=memoria,
            )
        else:
            system = SOPHIE_SYSTEM.format(
                catalogo=catalogo,
                memoria=memoria,
            )

        mensajes = conversaciones.get_mensajes(user_id)

        cliente = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = cliente.messages.create(
            model=config.MODEL,
            max_tokens=500,
            system=system,
            messages=mensajes,
        )
        respuesta_raw = resp.content[0].text

        # Procesar tokens estructurados
        respuesta_limpia = _procesar_tokens_sophie(user_id, respuesta_raw, tipo)

        # Guardar respuesta del asistente
        conversaciones.append_mensaje(user_id, 'assistant', respuesta_limpia)

        guardar_episodio(
            agente='sophie',
            pregunta=mensaje[:200],
            respuesta_resumen=respuesta_limpia[:300],
            resultado='ok',
        )

        return respuesta_limpia

    except Exception as e:
        logger.error(f"[sophie] Error: {e}")
        return "Lo siento, tuve un problema técnico. ¿Puedes repetir tu consulta? 🙏"


# ── Procesamiento de tokens ───────────────────────────────────────────────────

def _procesar_tokens_sophie(user_id: str, respuesta: str, tipo: str) -> str:
    """
    Detecta y ejecuta tokens estructurados en la respuesta de Claude.
    Retorna el texto limpio (sin la línea del token).
    """
    lineas = respuesta.strip().split('\n')
    primera = lineas[0].strip()

    if primera.startswith('PEDIDO_CONFIRMADO|'):
        _manejar_pedido_confirmado(user_id, primera)
        return '\n'.join(lineas[1:]).strip() or "✅ Pedido registrado. ¡Gracias!"

    if primera.startswith('GENERAR_LINK|'):
        partes = primera.split('|')
        if len(partes) >= 3:
            nombre_producto = partes[1].strip()
            dia_entrega     = partes[2].strip()
            link = generar_link_compra(nombre_producto, dia_entrega)
            texto_restante = '\n'.join(lineas[1:]).strip()
            if link:
                return f"{texto_restante}\n\n🔗 {link}".strip()
        return '\n'.join(lineas[1:]).strip()

    if primera.startswith('PEDIDO_MAYORISTA|'):
        _manejar_pedido_mayorista(user_id, primera)
        return '\n'.join(lineas[1:]).strip() or "✅ Pedido mayorista registrado. ¡Gracias!"

    return respuesta


def _manejar_pedido_confirmado(user_id: str, token: str) -> None:
    """Guarda un pedido minorista en Sheets."""
    try:
        partes = token.split('|')
        if len(partes) < 7:
            return

        nombre        = partes[1].strip()
        items_raw     = partes[2].strip()
        total         = _parse_monto(partes[3])
        dia           = partes[4].strip()
        tipo_entrega  = partes[5].strip()
        telefono      = partes[6].strip() if len(partes) > 6 else user_id

        items = json.loads(items_raw) if items_raw.startswith('[') else []
        items_str = ', '.join(f"{i.get('cantidad',1)}x {i.get('producto','')}" for i in items)

        fecha = datetime.now().strftime('%Y-%m-%d %H:%M')
        append_row(config.SHEET_PEDIDOS, [
            fecha, nombre, telefono, items_str, total, dia, tipo_entrega, 'pendiente', 'whatsapp'
        ])
        append_row(config.SHEET_INGRESOS, [fecha, total, f'Pedido {nombre}', 'whatsapp'])

        conversaciones.marcar_pedido_guardado(user_id)
        logger.info(f"[sophie] Pedido confirmado: {nombre} ${total}")

    except Exception as e:
        logger.error(f"[sophie] Error guardando pedido confirmado: {e}")


def _manejar_pedido_mayorista(user_id: str, token: str) -> None:
    """Guarda un pedido mayorista en Sheets."""
    try:
        partes = token.split('|')
        if len(partes) < 5:
            return

        empresa   = partes[1].strip()
        rut       = partes[2].strip()
        items_raw = partes[3].strip()
        total     = _parse_monto(partes[4])
        dia       = partes[5].strip() if len(partes) > 5 else ''

        items = json.loads(items_raw) if items_raw.startswith('[') else []
        items_str = ', '.join(f"{i.get('cantidad',1)}x {i.get('producto','')}" for i in items)

        fecha = datetime.now().strftime('%Y-%m-%d %H:%M')
        append_row(config.SHEET_PEDIDOS_MAYORISTAS, [
            fecha, empresa, rut, items_str, total, dia, 'pendiente'
        ])
        append_row(config.SHEET_INGRESOS, [fecha, total, f'Mayorista {empresa}', 'whatsapp'])

        conversaciones.marcar_pedido_guardado(user_id)
        logger.info(f"[sophie] Pedido mayorista: {empresa} ${total}")

    except Exception as e:
        logger.error(f"[sophie] Error guardando pedido mayorista: {e}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detectar_tipo(mensaje: str) -> str:
    """Heurística rápida para detectar si es mayorista."""
    msg_lower = mensaje.lower()
    señales_mayorista = ['rut', 'restaurante', 'café', 'cafe', 'local', 'negocio',
                         'empresa', 'factura', 'mayorista', 'pedido grande']
    for señal in señales_mayorista:
        if señal in msg_lower:
            return 'mayorista'
    return 'minorista'


def _parse_monto(valor: str) -> float:
    """Convierte string de monto CLP a float."""
    try:
        limpio = str(valor).replace('$', '').replace('.', '').replace(',', '.').strip()
        return float(limpio)
    except (ValueError, AttributeError):
        return 0.0


def _formato_precios_mayoristas() -> str:
    lineas = []
    for producto, datos in config.PRECIOS_MAYORISTAS.items():
        lineas.append(f"• {producto}: ${datos['precio']:,} ({datos['formato']})")
    return '\n'.join(lineas)
