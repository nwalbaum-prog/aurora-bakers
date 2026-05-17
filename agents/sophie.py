"""
agents/sophie.py — Sophie: agente de ventas WhatsApp Aurora Bakers

Flujo:
  1. _get_contexto_cliente: obtiene historial del cliente desde aurora-ventas
  2. ask_sophie: llama Claude con tools (function calling) para acciones estructuradas
  3. _ejecutar_herramienta: ejecuta la herramienta llamada por Claude
  4. ejecutar_tareas_pendientes: runner de tareas delegadas por el dueño
"""
from __future__ import annotations
import json
import logging
from datetime import datetime
import anthropic
from tools.jumpseller import get_catalogo_texto, generar_link_compra
from tools.info_web import get_info_web
from tools.sheets import append_row
from memoria.contexto import conversaciones
from memoria.episodica import guardar_episodio, get_contexto_memoria
import config

logger = logging.getLogger(__name__)

SOPHIE_TOOLS = [
    {
        "name": "registrar_pedido_minorista",
        "description": "Registra un pedido confirmado de cliente minorista. Usar SOLO cuando el cliente ha confirmado explícitamente (dijo sí/ok/dale/listo/perfecto/va).",
        "input_schema": {
            "type": "object",
            "properties": {
                "nombre":       {"type": "string",  "description": "Nombre completo del cliente"},
                "telefono":     {"type": "string",  "description": "Teléfono del cliente (con código país si está disponible)"},
                "items": {
                    "type": "array",
                    "description": "Lista de productos del pedido",
                    "items": {
                        "type": "object",
                        "properties": {
                            "producto":  {"type": "string"},
                            "cantidad":  {"type": "integer"},
                            "precio":    {"type": "number"}
                        },
                        "required": ["producto", "cantidad", "precio"]
                    }
                },
                "total":        {"type": "number", "description": "Total en pesos CLP"},
                "dia":          {"type": "string", "description": "Día de entrega (ej: viernes, sabado)"},
                "tipo_entrega": {"type": "string", "enum": ["despacho", "retiro"], "description": "Modalidad de entrega"}
            },
            "required": ["nombre", "telefono", "items", "total", "dia", "tipo_entrega"]
        }
    },
    {
        "name": "registrar_pedido_mayorista",
        "description": "Registra un pedido de cliente mayorista (empresa, restaurante, café, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "empresa": {"type": "string"},
                "rut":     {"type": "string"},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "producto": {"type": "string"},
                            "cantidad": {"type": "integer"},
                            "precio":   {"type": "number"}
                        },
                        "required": ["producto", "cantidad", "precio"]
                    }
                },
                "total": {"type": "number"},
                "dia":   {"type": "string"}
            },
            "required": ["empresa", "rut", "items", "total", "dia"]
        }
    },
    {
        "name": "generar_link_pago",
        "description": "Genera un link de pago online de Jumpseller para el cliente.",
        "input_schema": {
            "type": "object",
            "properties": {
                "nombre_producto": {"type": "string", "description": "Nombre del producto en catálogo"},
                "dia_entrega":     {"type": "string", "description": "Día de entrega solicitado"}
            },
            "required": ["nombre_producto", "dia_entrega"]
        }
    }
]

SOPHIE_SYSTEM = """Eres Sophie, la cara de Aurora Bakers (panypasta.cl) en WhatsApp.
Aurora Bakers es una panadería artesanal de Santiago que hace panes de masa madre.

Tu personalidad: cálida, directa, un poco informal. Como el mesón de una panadería artesanal.
Nunca suenas a call center. Emojis con moderación (máximo 1-2 por mensaje).
Formato WhatsApp: negrita con *. Máximo 3 párrafos, preferir menos.

CONTEXTO DEL CLIENTE:
{contexto_cliente}

CATÁLOGO ACTUAL:
{catalogo}
INFO SITIO WEB (panypasta.cl):
{info_web}

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
2. Reúne: nombre, productos+cantidades, día, teléfono.
3. Resume el pedido y confirma: "¿Quedamos así? [resumen]"
4. Cuando el cliente confirma (sí/ok/dale/perfecto) → llama la herramienta correspondiente para registrar.
5. Después de llamar la herramienta: cierra con mensaje amigable.

PRECIOS MAYORISTAS (solo si es cliente mayorista):
{precios_mayoristas}

{memoria}
"""


def ask_sophie(user_id: str, mensaje: str, canal: str = 'whatsapp') -> str:
    """Procesa un mensaje de cliente y retorna la respuesta de Sophie."""
    estado = conversaciones.get(user_id)
    tipo   = estado.tipo if estado else _detectar_tipo(mensaje)
    estado = conversaciones.get_or_create(user_id, tipo)
    conversaciones.append_mensaje(user_id, 'user', mensaje)

    try:
        catalogo           = _get_catalogo_con_fallback()
        info_web           = get_info_web()
        memoria            = get_contexto_memoria('sophie', limit=2)
        contexto_cliente   = _get_contexto_cliente(user_id)
        precios_mayoristas = _formato_precios_mayoristas()

        system = SOPHIE_SYSTEM.format(
            catalogo=catalogo,
            memoria=memoria,
            contexto_cliente=contexto_cliente,
            precios_mayoristas=precios_mayoristas,
            info_web=info_web,
        )

        mensajes   = conversaciones.get_mensajes(user_id)
        cliente_ai = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = cliente_ai.messages.create(
            model=config.MODEL,
            max_tokens=600,
            system=system,
            messages=mensajes,
            tools=SOPHIE_TOOLS,
            tool_choice={"type": "auto"},
        )

        # Extraer texto y/o tool call del response
        respuesta_texto = ''
        tool_call = None
        for block in resp.content:
            if block.type == 'text':
                respuesta_texto += block.text
            elif block.type == 'tool_use':
                tool_call = block

        if tool_call:
            respuesta_texto = _ejecutar_herramienta(user_id, tool_call, respuesta_texto.strip(), tipo)

        respuesta_limpia = respuesta_texto.strip()
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


# ── Ejecutor de herramientas ──────────────────────────────────────────────────

def _ejecutar_herramienta(user_id: str, tool_call, texto_previo: str, tipo: str) -> str:
    """Ejecuta la herramienta llamada por Claude y retorna mensaje de cierre."""
    nombre = tool_call.name
    args   = tool_call.input
    logger.info(f"[sophie] Tool call: {nombre} args={json.dumps(args, ensure_ascii=False)[:200]}")

    if nombre == 'registrar_pedido_minorista':
        items        = args.get('items', [])
        total        = float(args.get('total', 0))
        dia          = args.get('dia', '')
        tipo_entrega = args.get('tipo_entrega', 'despacho')
        nombre_c     = args.get('nombre', '')
        telefono     = args.get('telefono', user_id)

        logger.info(f"[sophie] TOOL registrar_pedido_minorista: {nombre_c} ${total} {dia}")
        try:
            items_str = ', '.join(f"{i.get('cantidad',1)}x {i.get('producto','')}" for i in items)
            fecha     = datetime.now().strftime('%Y-%m-%d %H:%M')
            append_row(config.SHEET_PEDIDOS, [
                fecha, nombre_c, telefono, items_str, total, dia, tipo_entrega, 'pendiente', 'whatsapp'
            ])
            append_row(config.SHEET_INGRESOS, [fecha, total, f'Pedido {nombre_c}', 'whatsapp'])
        except Exception as e:
            logger.warning(f"[sophie] Sheets no disponible: {e}")

        try:
            _sincronizar_venta_aurora(nombre_c, telefono, items, total, dia, tipo_entrega, 'CLIENTE')
        except Exception as e:
            logger.warning(f"[sophie] ventas_aurora error: {e}")

        conversaciones.marcar_pedido_guardado(user_id)
        logger.info(f"[sophie] Pedido minorista registrado OK: {nombre_c} ${total}")
        return texto_previo or f"✅ ¡Pedido registrado, {nombre_c}! Te esperamos el {dia} 🍞"

    if nombre == 'registrar_pedido_mayorista':
        empresa   = args.get('empresa', '')
        rut       = args.get('rut', '')
        items     = args.get('items', [])
        total     = float(args.get('total', 0))
        dia       = args.get('dia', '')

        logger.info(f"[sophie] TOOL registrar_pedido_mayorista: {empresa} ${total} {dia}")
        try:
            items_str = ', '.join(f"{i.get('cantidad',1)}x {i.get('producto','')}" for i in items)
            fecha     = datetime.now().strftime('%Y-%m-%d %H:%M')
            append_row(config.SHEET_PEDIDOS_MAYORISTAS, [
                fecha, empresa, rut, items_str, total, dia, 'pendiente'
            ])
            append_row(config.SHEET_INGRESOS, [fecha, total, f'Mayorista {empresa}', 'whatsapp'])
        except Exception as e:
            logger.warning(f"[sophie] Sheets no disponible: {e}")

        try:
            _sincronizar_venta_aurora(empresa, rut, items, total, dia, 'despacho', 'HORECA',
                                      notas=f'RUT: {rut}')
        except Exception as e:
            logger.warning(f"[sophie] ventas_aurora error: {e}")

        conversaciones.marcar_pedido_guardado(user_id)
        logger.info(f"[sophie] Pedido mayorista registrado OK: {empresa} ${total}")
        return texto_previo or f"✅ Pedido mayorista de {empresa} registrado. Te enviamos la factura pronto 🍞"

    if nombre == 'generar_link_pago':
        nombre_prod = args.get('nombre_producto', '')
        dia_entrega = args.get('dia_entrega', '')
        link = generar_link_compra(nombre_prod, dia_entrega)
        if link:
            cierre = texto_previo or '¡Aquí va tu link de pago! 🔗'
            return f"{cierre}\n\n🔗 {link}".strip()
        return texto_previo or "Tenemos un problema con el link en este momento. ¿Te lo coordino por otro medio?"

    return texto_previo


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
        }
        r = _req.post(
            f"{VENTAS_API_URL}/api/agentes/ventas",
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


def _formato_precios_mayoristas() -> str:
    lineas = []
    for producto, datos in config.PRECIOS_MAYORISTAS.items():
        lineas.append(f"• {producto}: ${datos['precio']:,} ({datos['formato']})")
    return '\n'.join(lineas)
