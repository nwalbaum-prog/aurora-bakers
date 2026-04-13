"""
agents/finanzas.py — Agente de finanzas: reportes, gastos, análisis financiero

Fuente de datos preferida: aurora-ventas (SQLite local via HTTP)
Fallback: Google Sheets (si aurora-ventas no está disponible)
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta
import anthropic
from tools.sheets import get_records_cached, append_row
from tools.ventas_api import get_resumen, get_kpis, get_contexto_ventas_texto, get_gastos_mes, registrar_gasto_api
from memoria.episodica import guardar_episodio, get_contexto_memoria
import config

logger = logging.getLogger(__name__)


def generar_reporte_financiero() -> str:
    """
    Genera reporte financiero.
    Fuente preferida: aurora-ventas (datos ricos con HORECA, estados, etc.)
    Fallback: Google Sheets si aurora-ventas no está disponible.
    """
    hoy = datetime.now()

    # ── Fuente 1: aurora-ventas ───────────────────────────────────────────────
    resumen = get_resumen()
    if resumen:
        kpi  = resumen.get('kpi', {})
        seg  = resumen.get('segmento', {})
        pend = resumen.get('pendientes', {})
        subs = resumen.get('suscripciones', {})
        top  = resumen.get('top_productos', [])
        desp = resumen.get('despachos', {})

        kpis_mes = get_kpis('mes')
        ingresos_mes = kpi.get('mes', {}).get('total', 0)
        # Gastos desde aurora-ventas (fuente primaria)
        gastos_data = get_gastos_mes()
        gastos_mes = gastos_data.get('total_mes', 0) if gastos_data is not None else 0
        gastos_por_cat = gastos_data.get('por_categoria', []) if gastos_data is not None else []

        margen = ingresos_mes - gastos_mes
        margen_pct = (margen / ingresos_mes * 100) if ingresos_mes > 0 else 0

        reporte = (
            f"📊 *Reporte Financiero — {hoy.strftime('%B %Y')}*\n\n"
            f"💰 Ingresos mes:  ${ingresos_mes:,.0f} ({kpi.get('mes',{}).get('count',0)} ventas)\n"
            f"💸 Gastos mes:    ${gastos_mes:,.0f}\n"
            f"📈 Margen:        ${margen:,.0f} ({margen_pct:.1f}%)\n\n"
            f"*Por segmento:*\n"
            f"  🍽️ HORECA:  ${seg.get('horeca',{}).get('total',0):,.0f} ({seg.get('horeca',{}).get('count',0)} ventas)\n"
            f"  🏠 Clientes: ${seg.get('cliente',{}).get('total',0):,.0f} ({seg.get('cliente',{}).get('count',0)} ventas)\n\n"
            f"⏳ Pagos pendientes:    {pend.get('pago',0)} ventas\n"
            f"🚚 Despachos hoy:       {len(desp.get('hoy',[]))}\n"
            f"📋 Suscripciones activas: {subs.get('activas',0)}\n"
        )
        if top:
            reporte += "\n*Top productos del mes:*\n"
            for p in top[:4]:
                reporte += f"  • {p['nombre']}: {p['cantidad']:.0f} uds / ${p['total']:,.0f}\n"
        return reporte

    # ── Fallback: Google Sheets ───────────────────────────────────────────────
    try:
        gastos     = get_records_cached(config.SHEET_GASTOS)
        ingresos   = get_records_cached(config.SHEET_INGRESOS)
        inicio_mes = hoy.replace(day=1)

        def es_este_mes(fecha_str: str) -> bool:
            for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d', '%d/%m/%Y'):
                try:
                    return datetime.strptime(str(fecha_str).strip(), fmt) >= inicio_mes
                except ValueError:
                    pass
            return False

        ingresos_mes = sum(
            float(str(r.get('Monto', 0)).replace('$','').replace('.','').replace(',','.') or 0)
            for r in ingresos if es_este_mes(r.get('Fecha',''))
        )
        gastos_mes = sum(
            float(str(r.get('Monto', 0)).replace('$','').replace('.','').replace(',','.') or 0)
            for r in gastos if es_este_mes(r.get('Fecha',''))
        )
        margen = ingresos_mes - gastos_mes
        margen_pct = (margen / ingresos_mes * 100) if ingresos_mes > 0 else 0
        return (
            f"📊 *Reporte Financiero — {hoy.strftime('%B %Y')}* (vía Sheets)\n\n"
            f"💰 Ingresos: ${ingresos_mes:,.0f}\n"
            f"💸 Gastos:   ${gastos_mes:,.0f}\n"
            f"📈 Margen:   ${margen:,.0f} ({margen_pct:.1f}%)\n"
            f"\n_(Sistema de ventas local no disponible — datos parciales)_"
        )
    except Exception as e:
        logger.error(f"[finanzas] Error generando reporte: {e}")
        return f"❌ Error generando reporte financiero: {e}"


def registrar_gasto(
    descripcion: str,
    monto: float,
    categoria: str = 'General',
    fecha: str | None = None,
    proveedor: str = '',
) -> bool:
    """Registra un gasto — usa aurora-ventas como fuente principal, Sheets como fallback."""
    if fecha is None:
        fecha = datetime.now().strftime('%Y-%m-%d')
    # Fuente primaria: aurora-ventas
    ok = registrar_gasto_api(descripcion, monto, categoria, fecha, proveedor)
    if ok:
        logger.info(f"[finanzas] Gasto registrado en ventas: {descripcion} ${monto}")
        return True
    # Fallback: Google Sheets
    try:
        append_row(config.SHEET_GASTOS, [fecha, descripcion, categoria, monto])
        logger.info(f"[finanzas] Gasto registrado en Sheets (fallback): {descripcion} ${monto}")
        return True
    except Exception as e:
        logger.error(f"[finanzas] Error registrando gasto: {e}")
        return False


def ask_finanzas(user_id: str, mensaje: str) -> str:
    """
    Responde consultas financieras usando Claude + datos de aurora-ventas (o Sheets).
    """
    try:
        reporte  = generar_reporte_financiero()
        # Agregar contexto de ventas detallado si está disponible
        ctx_ventas = get_contexto_ventas_texto()
        memoria    = get_contexto_memoria('finanzas', limit=2)
        contexto   = f"{reporte}\n\n{ctx_ventas}"
        if memoria:
            contexto += f"\n\n{memoria}"

        cliente = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = cliente.messages.create(
            model=config.MODEL,
            max_tokens=600,
            system=(
                "Eres el agente de finanzas de Aurora Bakers (panadería artesanal en Santiago). "
                "Analiza los datos financieros y responde con claridad y brevedad. "
                "Usa formato WhatsApp (negrita con *, no markdown complejo). "
                "Si ves oportunidades de mejora o alertas, menciónalas proactivamente."
            ),
            messages=[
                {"role": "user", "content": f"Datos actuales:\n{contexto}\n\nConsulta: {mensaje}"},
            ],
        )
        respuesta = resp.content[0].text

        guardar_episodio(
            agente='finanzas',
            pregunta=mensaje,
            respuesta_resumen=respuesta[:300],
            resultado='ok',
        )
        return respuesta

    except Exception as e:
        logger.error(f"[finanzas] Error en ask_finanzas: {e}")
        return f"❌ Error procesando consulta financiera: {e}"
