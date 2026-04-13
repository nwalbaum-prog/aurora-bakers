"""
agents/produccion.py — Agente de producción: plan de producción e ingredientes

Integra despachos del día desde aurora-ventas si está disponible.
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta
import anthropic
from tools.sheets import get_records_cached
from tools.whatsapp import send_whatsapp_safe
from tools.email_tools import send_email_safe
from tools.ventas_api import get_despachos_fecha, get_plan_produccion, get_inventario, descontar_inventario
from memoria.episodica import guardar_episodio, get_contexto_memoria
import config

logger = logging.getLogger(__name__)


def calcular_ingredientes_produccion(fecha_str: str) -> dict:
    """
    Lee PLAN_PRODUCCION para `fecha_str` y suma los ingredientes de cada producto.
    Retorna dict: {ingrediente: cantidad_total}
    """
    try:
        plan = get_records_cached(config.SHEET_PLAN_PRODUCCION)
        del_dia = [r for r in plan if str(r.get('Fecha', '')).startswith(fecha_str)]

        totales: dict[str, float] = {}
        for row in del_dia:
            codigo   = str(row.get('Codigo', '')).upper().strip()
            cantidad = int(row.get('Cantidad', 0) or 0)
            if codigo not in config.RECETAS or cantidad <= 0:
                continue

            receta = config.RECETAS[codigo]
            factor = cantidad  # 1 receta = 1 unidad
            for ingrediente, gramos in receta['ingredientes'].items():
                totales[ingrediente] = totales.get(ingrediente, 0) + (gramos * factor)

        return totales

    except Exception as e:
        logger.error(f"[produccion] Error calculando ingredientes: {e}")
        return {}


def get_despachos_del_dia(fecha_str: str) -> str:
    """
    Obtiene el listado de despachos pendientes para una fecha desde aurora-ventas.
    Retorna texto formateado para incluir en el plan de producción.
    """
    datos = get_despachos_fecha(fecha_str)
    if datos is None:
        return ''  # aurora-ventas no disponible

    despachos = datos.get('despachos', [])
    if not despachos:
        return f"Sin despachos a domicilio registrados para el {fecha_str}."

    lineas = [f"📦 *Despachos a domicilio — {fecha_str}:*"]
    pendientes = [d for d in despachos if d.get('estado_despacho') == 'PENDIENTE']
    despachados = [d for d in despachos if d.get('estado_despacho') == 'DESPACHADO']

    for d in pendientes:
        items_str = ', '.join(
            f"{i.get('nombre','?')} ×{i.get('cantidad',0):.0f}"
            for i in d.get('items', [])
        )
        lineas.append(
            f"  ⏳ {d.get('cliente_nombre','?')} — {items_str}"
            + (f" | 📍 {d.get('cliente_direccion','')}" if d.get('cliente_direccion') else '')
        )
    if despachados:
        lineas.append(f"  ✅ {len(despachados)} despachos ya enviados")

    return '\n'.join(lineas)


def generar_mensaje_produccion(fecha_str: str) -> str:
    """
    Genera el mensaje de plan de producción para una fecha.
    Fuente preferida: aurora-ventas. Fallback: Google Sheets.
    """
    # ── Fuente 1: aurora-ventas ───────────────────────────────────────────────
    plan_data = get_plan_produccion(fecha_str)
    if plan_data is not None:  # aurora-ventas disponible (aunque plan esté vacío)
        plan   = plan_data.get('plan', [])
        ing    = plan_data.get('ingredientes_necesarios', {})
        total  = plan_data.get('total_piezas', 0)

        if not plan:
            return f"📋 Sin plan de producción para el {fecha_str}. Agrégalo en http://127.0.0.1:5000/produccion"

        lineas = [f"🍞 *Plan de Producción — {fecha_str}*\n"]
        lineas.append("*Productos a hornear:*")
        for p in plan:
            est = '✅' if p['estado'] == 'listo' else ('🔄' if p['estado'] == 'en_proceso' else '⏳')
            lineas.append(f"  {est} {p['nombre_producto']}: {p['cantidad']} unidades")
        lineas.append(f"\n_Total: {total} piezas_")

        if ing:
            lineas.append("\n*Ingredientes necesarios:*")
            for nombre, gramos in sorted(ing.items()):
                lineas.append(f"  • {nombre}: {gramos/1000:.2f} kg" if gramos >= 1000 else f"  • {nombre}: {int(gramos)} g")

        # Alerta de inventario
        inv = get_inventario()
        if inv and inv.get('alertas_reposicion'):
            lineas.append("\n⚠️ *Alerta de stock bajo:*")
            for a in inv['alertas_reposicion']:
                lineas.append(f"  • {a['ingrediente']}: {a['stock_kg']:.2f} kg (mínimo {a['alerta_minimo_kg']:.2f} kg)")

        # Despachos del día
        despachos_str = get_despachos_del_dia(fecha_str)
        if despachos_str:
            lineas.append(f"\n{despachos_str}")

        return '\n'.join(lineas)

    # ── Fallback: Google Sheets ───────────────────────────────────────────────
    try:
        plan = get_records_cached(config.SHEET_PLAN_PRODUCCION)
        del_dia = [r for r in plan if str(r.get('Fecha', '')).startswith(fecha_str)]

        if not del_dia:
            return f"📋 No hay pedidos de producción para el {fecha_str}."

        resumen: dict[str, int] = {}
        for row in del_dia:
            codigo   = str(row.get('Codigo', '')).upper().strip()
            nombre   = row.get('Nombre_Producto', config.RECETAS.get(codigo, {}).get('nombre', codigo))
            cantidad = int(row.get('Cantidad', 0) or 0)
            if cantidad > 0:
                resumen[nombre] = resumen.get(nombre, 0) + cantidad

        ingredientes = calcular_ingredientes_produccion(fecha_str)
        lineas = [f"🍞 *Plan de Producción — {fecha_str}* (vía Sheets)\n"]
        lineas.append("*Productos a hornear:*")
        for nombre, cant in sorted(resumen.items()):
            lineas.append(f"  • {nombre}: {cant} unidades")

        if ingredientes:
            lineas.append("\n*Ingredientes necesarios:*")
            for ing, gramos in sorted(ingredientes.items()):
                lineas.append(f"  • {ing}: {gramos/1000:.2f} kg" if gramos >= 1000 else f"  • {ing}: {int(gramos)} g")

        despachos_str = get_despachos_del_dia(fecha_str)
        if despachos_str:
            lineas.append(f"\n{despachos_str}")

        return '\n'.join(lineas)

    except Exception as e:
        logger.error(f"[produccion] Error generando mensaje: {e}")
        return f"❌ Error generando plan de producción: {e}"


def get_proximos_dias_produccion(n: int = 3) -> list[str]:
    """Retorna las próximas n fechas con pedidos en PLAN_PRODUCCION."""
    try:
        plan = get_records_cached(config.SHEET_PLAN_PRODUCCION)
        hoy  = datetime.now().date()
        fechas = set()
        for r in plan:
            fecha_str = str(r.get('Fecha', '')).strip()[:10]
            try:
                fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
                if fecha >= hoy:
                    fechas.add(fecha_str)
            except ValueError:
                pass
        return sorted(fechas)[:n]
    except Exception as e:
        logger.error(f"[produccion] Error obteniendo próximos días: {e}")
        return []


def enviar_plan_produccion(fecha_str: str | None = None) -> bool:
    """
    Envía el plan de producción por WhatsApp al dueño.
    Si fecha_str es None, usa mañana.
    """
    if fecha_str is None:
        fecha_str = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

    mensaje = generar_mensaje_produccion(fecha_str)
    ok = send_whatsapp_safe(config.OWNER_PHONE, mensaje)
    if ok:
        logger.info(f"[produccion] Plan enviado por WA para {fecha_str}")
    return ok


def ask_produccion(user_id: str, mensaje: str) -> str:
    """Responde preguntas sobre producción usando Claude."""
    try:
        # Determinar fecha de consulta (hoy o mañana por defecto)
        hoy      = datetime.now().strftime('%Y-%m-%d')
        manana   = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
        plan_hoy = generar_mensaje_produccion(hoy)
        plan_man = generar_mensaje_produccion(manana)
        memoria  = get_contexto_memoria('produccion', limit=2)

        contexto = f"{plan_hoy}\n\n{plan_man}"
        if memoria:
            contexto += f"\n\n{memoria}"

        cliente = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = cliente.messages.create(
            model=config.MODEL,
            max_tokens=500,
            system=(
                "Eres el agente de producción de Aurora Bakers (panadería artesanal en Santiago). "
                "Ayudas a planificar la producción diaria: cantidades a hornear, ingredientes, "
                "tiempos y logística. Responde en formato WhatsApp (negrita con *)."
            ),
            messages=[
                {"role": "user", "content": f"{contexto}\n\nConsulta: {mensaje}"},
            ],
        )
        respuesta = resp.content[0].text

        guardar_episodio(
            agente='produccion',
            pregunta=mensaje,
            respuesta_resumen=respuesta[:300],
            resultado='ok',
        )
        return respuesta

    except Exception as e:
        logger.error(f"[produccion] Error en ask_produccion: {e}")
        return f"❌ Error en agente de producción: {e}"
