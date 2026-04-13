"""
agents/analista.py — Agente analista: tendencias, clientes inactivos, análisis de datos

Fuente de datos preferida: aurora-ventas (SQLite local via HTTP)
Fallback: Google Sheets si aurora-ventas no está disponible.
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta
import anthropic
from tools.sheets import get_records_cached
from tools.ventas_api import get_resumen, get_contexto_ventas_texto, get_clientes
from memoria.episodica import guardar_episodio, get_contexto_memoria
import config

logger = logging.getLogger(__name__)


def clientes_inactivos_minoristas(dias: int = 21) -> list[dict]:
    """Retorna clientes minoristas sin pedidos en los últimos `dias` días."""
    if not config.GOOGLE_SA_JSON:
        return []
    try:
        clientes = get_records_cached(config.SHEET_CLIENTES)
        pedidos  = get_records_cached(config.SHEET_PEDIDOS)
        corte    = datetime.now() - timedelta(days=dias)

        # Mapa: telefono → última fecha de pedido
        ultima_compra: dict[str, datetime] = {}
        for p in pedidos:
            telefono = str(p.get('Telefono', '') or p.get('Cliente', '')).strip()
            fecha_str = str(p.get('Fecha', '')).strip()
            try:
                for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d', '%d/%m/%Y'):
                    try:
                        fecha = datetime.strptime(fecha_str, fmt)
                        if telefono not in ultima_compra or fecha > ultima_compra[telefono]:
                            ultima_compra[telefono] = fecha
                        break
                    except ValueError:
                        continue
            except Exception:
                pass

        inactivos = []
        for c in clientes:
            tipo = str(c.get('Tipo', '')).lower()
            if tipo == 'mayorista':
                continue
            telefono = str(c.get('Telefono', '')).strip()
            ultima = ultima_compra.get(telefono)
            if ultima is None or ultima < corte:
                inactivos.append({
                    'nombre':   c.get('Nombre', ''),
                    'telefono': telefono,
                    'ultima_compra': ultima.strftime('%Y-%m-%d') if ultima else 'nunca',
                    'dias_inactivo': (datetime.now() - ultima).days if ultima else 999,
                })

        inactivos.sort(key=lambda x: x['dias_inactivo'], reverse=True)
        return inactivos

    except Exception as e:
        logger.error(f"[analista] Error en clientes_inactivos_minoristas: {e}")
        return []


def clientes_inactivos_mayoristas(dias: int = 14) -> list[dict]:
    """Retorna clientes mayoristas sin pedidos en los últimos `dias` días."""
    if not config.GOOGLE_SA_JSON:
        return []
    try:
        clientes   = get_records_cached(config.SHEET_CLIENTES)
        mayoristas = get_records_cached(config.SHEET_PEDIDOS_MAYORISTAS)
        corte      = datetime.now() - timedelta(days=dias)

        ultima_compra: dict[str, datetime] = {}
        for p in mayoristas:
            cliente_id = str(p.get('Cliente', '') or p.get('RUT', '')).strip()
            fecha_str  = str(p.get('Fecha', '')).strip()
            try:
                for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d', '%d/%m/%Y'):
                    try:
                        fecha = datetime.strptime(fecha_str, fmt)
                        if cliente_id not in ultima_compra or fecha > ultima_compra[cliente_id]:
                            ultima_compra[cliente_id] = fecha
                        break
                    except ValueError:
                        continue
            except Exception:
                pass

        inactivos = []
        for c in clientes:
            tipo = str(c.get('Tipo', '')).lower()
            if tipo != 'mayorista':
                continue
            cid  = str(c.get('RUT', c.get('Nombre', ''))).strip()
            ultima = ultima_compra.get(cid)
            if ultima is None or ultima < corte:
                inactivos.append({
                    'nombre':   c.get('Nombre', ''),
                    'telefono': str(c.get('Telefono', '')).strip(),
                    'rut':      str(c.get('RUT', '')).strip(),
                    'ultima_compra': ultima.strftime('%Y-%m-%d') if ultima else 'nunca',
                    'dias_inactivo': (datetime.now() - ultima).days if ultima else 999,
                })

        inactivos.sort(key=lambda x: x['dias_inactivo'], reverse=True)
        return inactivos

    except Exception as e:
        logger.error(f"[analista] Error en clientes_inactivos_mayoristas: {e}")
        return []


def get_contexto_negocio() -> str:
    """
    Resumen del estado del negocio para incluir en prompts.
    Usa aurora-ventas si está disponible, sino Sheets.
    """
    # Fuente preferida: aurora-ventas
    ctx = get_contexto_ventas_texto()
    if not ctx.startswith('⚠️'):
        return ctx

    # Fallback: Google Sheets
    try:
        pedidos    = get_records_cached(config.SHEET_PEDIDOS)
        mayoristas = get_records_cached(config.SHEET_PEDIDOS_MAYORISTAS)
        clientes   = get_records_cached(config.SHEET_CLIENTES)
        hoy         = datetime.now()
        semana_atras = hoy - timedelta(days=7)

        def es_reciente(fecha_str: str) -> bool:
            for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d', '%d/%m/%Y'):
                try:
                    return datetime.strptime(str(fecha_str).strip(), fmt) >= semana_atras
                except ValueError:
                    pass
            return False

        pedidos_semana    = sum(1 for p in pedidos    if es_reciente(p.get('Fecha', '')))
        mayoristas_semana = sum(1 for p in mayoristas if es_reciente(p.get('Fecha', '')))
        total_clientes    = len(clientes)
        return (
            f"Contexto del negocio — semana (vía Sheets):\n"
            f"- Pedidos minoristas: {pedidos_semana}\n"
            f"- Pedidos mayoristas: {mayoristas_semana}\n"
            f"- Total clientes: {total_clientes}\n"
            f"- Fecha: {hoy.strftime('%Y-%m-%d')}"
        )
    except Exception as e:
        logger.error(f"[analista] Error en get_contexto_negocio: {e}")
        return "Contexto no disponible"


def ask_analista(user_id: str, mensaje: str) -> str:
    """Responde preguntas analíticas sobre el negocio."""
    try:
        contexto_negocio = get_contexto_negocio()
        memoria          = get_contexto_memoria('analista', limit=2)
        contexto_completo = contexto_negocio
        if memoria:
            contexto_completo += f"\n\n{memoria}"

        cliente = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = cliente.messages.create(
            model=config.MODEL,
            max_tokens=600,
            system=(
                "Eres el agente analista de Aurora Bakers (panadería artesanal en Santiago). "
                "Tu rol es identificar tendencias, oportunidades y problemas en los datos. "
                "Responde con insights concretos y accionables. "
                "Usa formato WhatsApp (negrita con *, no markdown complejo)."
            ),
            messages=[
                {"role": "user", "content": f"{contexto_completo}\n\nConsulta: {mensaje}"},
            ],
        )
        respuesta = resp.content[0].text

        guardar_episodio(
            agente='analista',
            pregunta=mensaje,
            respuesta_resumen=respuesta[:300],
            resultado='ok',
        )
        return respuesta

    except Exception as e:
        logger.error(f"[analista] Error en ask_analista: {e}")
        return f"❌ Error procesando análisis: {e}"
