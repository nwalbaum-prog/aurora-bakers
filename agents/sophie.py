"""
agents/sophie.py — Sophie: agente de ventas WhatsApp Aurora Bakers

Flujo:
  1. _get_contexto_cliente: obtiene historial del cliente desde aurora-ventas
  2. ask_sophie: llama Claude con prompt enriquecido
  3. _extraer_token: parsea token con regex (robusto, busca en cualquier línea)
  4. _procesar_token: ejecuta la acción del token
  5. ejecutar_tareas_pendientes: runner de tareas delegadas por el dueño
"""
from __future__ import annotations
import json
import logging
import re
from datetime import datetime
import anthropic
from tools.jumpseller import get_catalogo_texto, generar_link_compra
from tools.sheets import append_row
from memoria.contexto import conversaciones
from memoria.episodica import guardar_episodio, get_contexto_memoria
import config

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(
    r'^(PEDIDO_CONFIRMADO|PEDIDO_MAYORISTA|GENERAR_LINK)\|.+',
    re.MULTILINE,
)

SOPHIE_SYSTEM = """Eres Sophie, la cara de Aurora Bakers (panypasta.cl) en WhatsApp.
Aurora Bakers es una panadería artesanal de Santiago que hace panes de masa madre.

Tu personalidad: cálida, directa, un poco informal. Como el mesón de una panadería artesanal.
Nunca suenas a call center. Emojis con moderación (máximo 1-2 por mensaje).
Formato WhatsApp: negrita con *. Máximo 3 párrafos, preferir menos.

CONTEXTO DEL CLIENTE:
{contexto_cliente}

CATÁLOGO ACTUAL:
{catalogo}

DÍAS DE DESPACHO: martes, miércoles, jueves, viernes y sábado.
COMUNAS CON DESPACHO: Providencia, Ñuñoa, Santiago Centro, Recoleta, Independencia, Las Condes, Vitacura, La Reina, Macul, San Miguel.

REGLAS:
- Detecta mayorista por: menciona RUT, pide >10 unidades, dice restaurante/café/local/empresa/factura.
- Si cliente recurrente, salúdalo por nombre y menciona su último pedido.
- Si preguntan por su pedido ("¿cuándo llega?", "¿está listo?", "¿ya salió?"), usa el pedido activo del CONTEXTO DEL CLIENTE.
- NUNCA inventes precios. Si un producto no hay, sugiere la alternativa más parecida.
- Para pedidos nuevos: confirma el resumen ANTES de registrar.

FLUJO DE PEDIDO (seguir este orden):
1. Entiende qué quieren y para qué día.
2. Reúne: nombre del cliente, productos+cantidades, día de despacho, teléfono.
3. Resume el pedido y confirma: "¿Quedamos así? [resumen breve]"
4. Cuando el cliente dice sí/ok/dale/listo/perfecto/claro/va → DEBES emitir el TOKEN correspondiente en una línea propia, solo él, sin texto adicional en esa línea.
5. Después del token: mensaje de cierre amigable con día de entrega.

⚠️ CRÍTICO — TOKENS OBLIGATORIOS:
El token ES la acción. Si no emites el token, el pedido NO se registra, el link NO se genera.
Sin token = nada pasa. SIEMPRE emite el token cuando corresponde.

PRECIOS MAYORISTAS (solo si es cliente mayorista):
{precios_mayoristas}

TOKENS (exactamente en este formato, en su propia línea):
PEDIDO_CONFIRMADO|nombre|[{{"producto":"X","cantidad":1,"precio":N}}]|total|dia|tipo_entrega|telefono
PEDIDO_MAYORISTA|empresa|rut|[{{"producto":"X","cantidad":1,"precio":N}}]|total|dia
GENERAR_LINK|nombre_producto|dia_entrega

Ejemplo correcto (pedido minorista):
Cliente confirmó. Tu respuesta debe ser:
Perfecto [nombre], queda todo registrado 🍞 Te esperamos el [día].
PEDIDO_CONFIRMADO|Maria|[{{"producto":"Pan Molde Integral","cantidad":2,"precio":4200}}]|8400|viernes|despacho|56911111111

Ejemplo correcto (link de pago):
Cliente pidió link. Tu respuesta debe ser:
¡Aquí va tu link! Paga y coordinamos el despacho 🔗
GENERAR_LINK|Pan Molde Integral|viernes

{memoria}
"""


def ask_sophie(user_id: str, mensaje: str, canal: str = 'whatsapp') -> str:
    """Procesa un mensaje de cliente y retorna la respuesta de Sophie."""
    estado = conversaciones.get(user_id)
    tipo   = estado.tipo if estado else _detectar_tipo(mensaje)
    estado = conversaciones.get_or_create(user_id, tipo)
    conversaciones.append_mensaje(user_id, 'user', mensaje)

    try:
        catalogo          = _get_catalogo_con_fallback()
        memoria           = get_contexto_memoria('sophie', limit=2)
        contexto_cliente  = _get_contexto_cliente(user_id)
        precios_mayoristas = _formato_precios_mayoristas()

        system = SOPHIE_SYSTEM.format(
            catalogo=catalogo,
            memoria=memoria,
            contexto_cliente=contexto_cliente,
            precios_mayoristas=precios_mayoristas,
        )

        mensajes = conversaciones.get_mensajes(user_id)
        cliente_ai = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = cliente_ai.messages.create(
            model=config.MODEL,
            max_tokens=600,
            system=system,
            messages=mensajes,
        )
        respuesta_raw = resp.content[0].text

        token_line, respuesta_limpia = _extraer_token(respuesta_raw)

        if token_line:
            respuesta_limpia = _procesar_token(user_id, token_line, respuesta_limpia, tipo)

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
        return "Lo siento, tuve un problema técnico. ¿Puedes repetir? 🙏"


# ── Helpers de contexto ───────────────────────────────────────────────────────

def _get_contexto_cliente(telefono: str) -> str:
    """Obtiene historial del cliente desde aurora-ventas para personalizar."""
    try:
        from tools.ventas_api import get_pedidos_cliente
        data = get_pedidos_cliente(telefono)
        cliente = data.get('cliente')
        pedidos = data.get('pedidos', [])

        if not cliente:
            return 'Cliente nuevo (sin historial previo).'

        nombre        = cliente.get('nombre', 'Cliente')
        total_pedidos = cliente.get('total_pedidos', 0)
        tipo          = cliente.get('tipo', 'CLIENTE')

        lineas = [f"Cliente: {nombre} ({tipo.lower()}, {total_pedidos} pedidos previos)"]

        activos   = [p for p in pedidos if 'PENDIENTE' in p.get('estado', '').upper()]
        recientes = [p for p in pedidos if 'PENDIENTE' not in p.get('estado', '').upper()]

        if activos:
            p = activos[0]
            lineas.append(
                f"Pedido activo: {p.get('items','?')} — {p.get('estado','')} — "
                f"entrega {p.get('fecha_entrega','?')}"
            )
        elif recientes:
            p = recientes[0]
            lineas.append(f"Último pedido: {p.get('items','?')} ({p.get('fecha_entrega','?')})")

        return '\n'.join(lineas)
    except Exception:
        return 'Historial no disponible.'


def _get_catalogo_con_fallback() -> str:
    """Catálogo desde Jumpseller; si falla usa config.RECETAS."""
    try:
        catalogo = get_catalogo_texto()
        if 'no disponible' not in catalogo.lower() and len(catalogo) > 30:
            return catalogo
    except Exception:
        pass
    lineas = ['*Catálogo Aurora Bakers:*']
    for codigo, r in config.RECETAS.items():
        lineas.append(f"✅ {r['nombre']}")
    return '\n'.join(lineas)


# ── Token parser ──────────────────────────────────────────────────────────────

def _extraer_token(respuesta: str) -> tuple[str, str]:
    """
    Busca un token estructurado en cualquier línea del response.
    Retorna (token_line, texto_limpio).
    """
    match = _TOKEN_RE.search(respuesta)
    if not match:
        return '', respuesta
    token_line    = match.group(0)
    texto_antes   = respuesta[:match.start()].strip()
    texto_despues = respuesta[match.end():].strip()
    texto_limpio  = '\n'.join(filter(None, [texto_antes, texto_despues]))
    return token_line, texto_limpio


def _procesar_token(user_id: str, token_line: str, texto: str, tipo: str) -> str:
    """Despacha el token al handler correcto."""
    if token_line.startswith('PEDIDO_CONFIRMADO|'):
        _manejar_pedido_confirmado(user_id, token_line)
        return texto or "✅ ¡Pedido registrado! Nos vemos el día de entrega 🍞"

    if token_line.startswith('GENERAR_LINK|'):
        partes = token_line.split('|')
        if len(partes) >= 3:
            link = generar_link_compra(partes[1].strip(), partes[2].strip())
            if link:
                return f"{texto}\n\n🔗 {link}".strip()
        return texto

    if token_line.startswith('PEDIDO_MAYORISTA|'):
        _manejar_pedido_mayorista(user_id, token_line)
        return texto or "✅ Pedido mayorista registrado. Te enviamos la factura pronto 🍞"

    return texto


# ── Handlers de pedidos ───────────────────────────────────────────────────────

def _manejar_pedido_confirmado(user_id: str, token: str) -> None:
    """Guarda pedido minorista en Sheets y aurora-ventas."""
    try:
        partes = token.split('|')
        if len(partes) < 7:
            return
        nombre       = partes[1].strip()
        items_raw    = partes[2].strip()
        total        = _parse_monto(partes[3])
        dia          = partes[4].strip()
        tipo_entrega = partes[5].strip()
        telefono     = partes[6].strip() if len(partes) > 6 else user_id

        items     = json.loads(items_raw) if items_raw.startswith('[') else []
        items_str = ', '.join(f"{i.get('cantidad',1)}x {i.get('producto','')}" for i in items)
        fecha     = datetime.now().strftime('%Y-%m-%d %H:%M')

        append_row(config.SHEET_PEDIDOS, [
            fecha, nombre, telefono, items_str, total, dia, tipo_entrega, 'pendiente', 'whatsapp'
        ])
        append_row(config.SHEET_INGRESOS, [fecha, total, f'Pedido {nombre}', 'whatsapp'])
        _sincronizar_venta_aurora(nombre, telefono, items, total, dia, tipo_entrega, 'CLIENTE')
        conversaciones.marcar_pedido_guardado(user_id)
        logger.info(f"[sophie] Pedido confirmado: {nombre} ${total}")
    except Exception as e:
        logger.error(f"[sophie] Error guardando pedido confirmado: {e}")


def _manejar_pedido_mayorista(user_id: str, token: str) -> None:
    """Guarda pedido mayorista en Sheets y aurora-ventas."""
    try:
        partes    = token.split('|')
        if len(partes) < 5:
            return
        empresa   = partes[1].strip()
        rut       = partes[2].strip()
        items_raw = partes[3].strip()
        total     = _parse_monto(partes[4])
        dia       = partes[5].strip() if len(partes) > 5 else ''

        items     = json.loads(items_raw) if items_raw.startswith('[') else []
        items_str = ', '.join(f"{i.get('cantidad',1)}x {i.get('producto','')}" for i in items)
        fecha     = datetime.now().strftime('%Y-%m-%d %H:%M')

        append_row(config.SHEET_PEDIDOS_MAYORISTAS, [
            fecha, empresa, rut, items_str, total, dia, 'pendiente'
        ])
        append_row(config.SHEET_INGRESOS, [fecha, total, f'Mayorista {empresa}', 'whatsapp'])
        _sincronizar_venta_aurora(empresa, rut, items, total, dia, 'despacho', 'HORECA',
                                   notas=f'RUT: {rut}')
        conversaciones.marcar_pedido_guardado(user_id)
        logger.info(f"[sophie] Pedido mayorista: {empresa} ${total}")
    except Exception as e:
        logger.error(f"[sophie] Error guardando pedido mayorista: {e}")


# ── Task runner ───────────────────────────────────────────────────────────────

def ejecutar_tareas_pendientes() -> int:
    """Revisa y ejecuta tareas sophie_tarea vencidas. Retorna N ejecutadas."""
    try:
        from tools.ventas_api import get_tareas_sophie_pendientes, marcar_tarea_completada
        from tools.whatsapp import send_whatsapp_safe

        tareas     = get_tareas_sophie_pendientes()
        ejecutadas = 0

        for tarea in tareas:
            try:
                payload  = json.loads(tarea.get('payload_json') or '{}')
                subtipo  = payload.get('subtipo', 'mensaje_programado')
                telefono = tarea.get('telefono_destino') or payload.get('telefono_destino', '')
                mensaje  = tarea.get('descripcion') or payload.get('mensaje', '')

                if not telefono or not mensaje:
                    marcar_tarea_completada(tarea['id'], 'error_datos')
                    continue

                if subtipo == 'seguimiento_condicional':
                    if _hubo_respuesta_reciente(telefono, horas=48):
                        marcar_tarea_completada(tarea['id'], 'cancelada_respondio')
                        continue

                ok = send_whatsapp_safe(telefono, mensaje)
                marcar_tarea_completada(tarea['id'], 'ok' if ok else 'error_envio')
                if ok:
                    ejecutadas += 1
                    logger.info(f"[sophie] Tarea ejecutada: id={tarea['id']} → {telefono}")

            except Exception as e:
                logger.error(f"[sophie] Error ejecutando tarea {tarea.get('id')}: {e}")

        return ejecutadas
    except Exception as e:
        logger.error(f"[sophie] Error en task runner: {e}")
        return 0


def _hubo_respuesta_reciente(telefono: str, horas: int = 48) -> bool:
    """Verifica si hubo actividad de este número en las últimas N horas."""
    try:
        from tools.ventas_api import get_conversacion
        from datetime import timedelta
        data = get_conversacion(telefono)
        if not data or not data.get('updated_at'):
            return False
        dt = datetime.fromisoformat(data['updated_at'])
        return datetime.now() - dt < timedelta(hours=horas)
    except Exception:
        return False


# ── Sincronización aurora-ventas ──────────────────────────────────────────────

def _sincronizar_venta_aurora(
    nombre: str, telefono: str, items: list, total: float,
    dia_entrega: str, tipo_entrega: str,
    segmento: str = 'CLIENTE', notas: str = '',
) -> None:
    """Crea o actualiza el cliente y la venta en aurora-ventas via API."""
    try:
        import requests as _req
        from tools.ventas_api import VENTAS_API_URL, VENTAS_API_KEY, get_clientes

        headers = {'X-Agent-Key': VENTAS_API_KEY, 'Content-Type': 'application/json'}

        # Buscar o crear cliente
        cliente_id = None
        clientes   = get_clientes(q=nombre)
        for c in clientes:
            tel_c = str(c.get('telefono', '')).replace(' ', '').replace('-', '')
            tel_n = str(telefono).replace(' ', '').replace('-', '')
            if tel_c == tel_n or c.get('nombre', '').lower() == nombre.lower():
                cliente_id = c.get('id')
                break

        if not cliente_id:
            r = _req.post(
                f"{VENTAS_API_URL}/api/clientes",
                json={'nombre': nombre, 'telefono': telefono,
                      'tipo': segmento, 'canal': 'whatsapp', 'activo': True},
                headers=headers, timeout=8,
            )
            if r.ok:
                cliente_id = r.json().get('id')

        # Crear venta (items como notas — WhatsApp orders no tienen producto_id)
        items_str = ', '.join(
            f"{i.get('cantidad',1)}x {i.get('producto','')}" for i in items
        ) or 'Pedido WhatsApp'

        venta_body = {
            'cliente_id':      cliente_id,
            'canal':           'whatsapp',
            'total':           total,
            'notas':           notas or items_str,
            'fecha_despacho':  dia_entrega,
            'con_despacho':    1 if tipo_entrega == 'despacho' else 0,
            'tipo_cliente':    segmento,
            'estado_pago':     'PENDIENTE',
            'estado_despacho': 'PENDIENTE',
            'items':           [],  # sin FK de producto — solo notas
        }
        r = _req.post(
            f"{VENTAS_API_URL}/api/ventas",
            json=venta_body, headers=headers, timeout=8,
        )
        if r.ok:
            logger.info(f"[sophie→ventas] Venta creada: ${total} cliente={nombre}")
        else:
            logger.warning(f"[sophie→ventas] No se pudo crear venta: {r.text[:100]}")

    except Exception as e:
        logger.warning(f"[sophie→ventas] aurora-ventas no disponible: {e}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detectar_tipo(mensaje: str) -> str:
    msg_lower = mensaje.lower()
    señales = ['rut', 'restaurante', 'café', 'cafe', 'local', 'negocio',
               'empresa', 'factura', 'mayorista', 'pedido grande']
    return 'mayorista' if any(s in msg_lower for s in señales) else 'minorista'


def _parse_monto(valor: str) -> float:
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
