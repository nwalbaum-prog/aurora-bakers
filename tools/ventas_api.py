"""
tools/ventas_api.py — Cliente para el sistema de ventas local (aurora-ventas)

El sistema multi-agente consulta aurora-ventas via HTTP en lugar de Google Sheets,
obteniendo datos más ricos: estado de pago, despacho, HORECA vs CLIENTE, etc.

Configuración:
  VENTAS_API_URL = URL base del sistema de ventas (default: http://127.0.0.1:5000)
  VENTAS_API_KEY = Clave para el endpoint /api/agentes/* (default: aurora_agent_2024)

Para exponer aurora-ventas desde Railway, usar ngrok localmente:
  ngrok http 5000
  → Configurar VENTAS_API_URL en Railway con la URL pública de ngrok
"""
from __future__ import annotations
import logging
import os
import requests
from tools.retry import con_reintento

logger = logging.getLogger(__name__)

import config as _cfg_module
VENTAS_API_URL = os.environ.get('VENTAS_API_URL', 'http://127.0.0.1:5000')
VENTAS_API_KEY = os.environ.get('VENTAS_API_KEY', 'aurora_agent_2024')

def _get_url():
    return getattr(_cfg_module, 'VENTAS_API_URL', VENTAS_API_URL)

_TIMEOUT = 10  # segundos


def _headers() -> dict:
    return {'X-Agent-Key': VENTAS_API_KEY, 'Accept': 'application/json'}


def _disponible() -> bool:
    """Verifica si el sistema de ventas está accesible."""
    try:
        requests.get(f"{VENTAS_API_URL}/api/productos", timeout=3)
        return True
    except Exception:
        return False


# ── Resumen consolidado ────────────────────────────────────────────────────────

@con_reintento(max_intentos=2, delay=2, exceptions=(requests.RequestException,))
def get_resumen(desde: str | None = None, hasta: str | None = None) -> dict | None:
    """
    Retorna el resumen completo del negocio para los agentes.
    Incluye: KPIs, pendientes, despachos, suscripciones, top productos, segmento.
    Retorna None si el servicio no está disponible.
    """
    try:
        params = {'key': VENTAS_API_KEY}
        if desde: params['desde'] = desde
        if hasta: params['hasta'] = hasta

        resp = requests.get(
            f"{VENTAS_API_URL}/api/agentes/resumen",
            params=params,
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        logger.warning("[ventas_api] Sistema de ventas no disponible (¿está corriendo?)")
        return None
    except Exception as e:
        logger.error(f"[ventas_api] Error obteniendo resumen: {e}")
        return None


@con_reintento(max_intentos=2, delay=2, exceptions=(requests.RequestException,))
def get_despachos_fecha(fecha: str) -> dict | None:
    """
    Retorna los despachos pendientes para una fecha específica.
    Útil para el agente de producción.
    """
    try:
        resp = requests.get(
            f"{VENTAS_API_URL}/api/agentes/despachos-hoy",
            params={'fecha': fecha},
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        logger.warning("[ventas_api] Sistema de ventas no disponible")
        return None
    except Exception as e:
        logger.error(f"[ventas_api] Error obteniendo despachos: {e}")
        return None


# ── Ventas ────────────────────────────────────────────────────────────────────

def get_ventas(desde: str, hasta: str, **filtros) -> list[dict]:
    """Retorna ventas en el rango de fechas con filtros opcionales."""
    try:
        params = {'desde': desde, 'hasta': hasta, **filtros}
        resp = requests.get(f"{VENTAS_API_URL}/api/ventas", params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"[ventas_api] Error obteniendo ventas: {e}")
        return []


def get_resumen_ventas() -> dict:
    """Retorna el resumen básico de ventas (hoy/semana/mes y pendientes)."""
    try:
        resp = requests.get(f"{VENTAS_API_URL}/api/ventas/resumen", timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"[ventas_api] Error obteniendo resumen ventas: {e}")
        return {'hoy': {'total': 0, 'count': 0}, 'semana': {'total': 0, 'count': 0},
                'mes': {'total': 0, 'count': 0}, 'pendientes_pago': 0, 'pendientes_despacho': 0}


def get_suscripciones(estado: str = 'activo') -> list[dict]:
    """Retorna las suscripciones filtradas por estado."""
    try:
        params = {'estado': estado} if estado else {}
        resp = requests.get(f"{VENTAS_API_URL}/api/suscripciones", params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"[ventas_api] Error obteniendo suscripciones: {e}")
        return []


def get_clientes(q: str = '') -> list[dict]:
    """Busca clientes por nombre, email o teléfono."""
    try:
        params = {'q': q} if q else {}
        resp = requests.get(f"{VENTAS_API_URL}/api/clientes", params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"[ventas_api] Error obteniendo clientes: {e}")
        return []


def get_kpis(periodo: str = 'mes') -> dict:
    """Retorna KPIs del período (semana/mes/3meses/año)."""
    try:
        resp = requests.get(f"{VENTAS_API_URL}/api/reportes/kpis",
                           params={'periodo': periodo}, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"[ventas_api] Error obteniendo KPIs: {e}")
        return {}


# ── Texto consolidado para prompts ────────────────────────────────────────────

# ── Inventario ────────────────────────────────────────────────────────────────

def get_inventario() -> dict | None:
    """Stock actual e ingredientes con alerta de reposición."""
    try:
        resp = requests.get(f"{_get_url()}/api/agentes/inventario",
                           headers=_headers(), timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"[ventas_api] Error obteniendo inventario: {e}")
        return None


def descontar_inventario(ingredientes: dict) -> bool:
    """Descuenta ingredientes del inventario tras la producción. {nombre: gramos}"""
    try:
        resp = requests.post(
            f"{VENTAS_API_URL}/api/agentes/inventario/descontar",
            json={'ingredientes': ingredientes},
            headers=_headers(), timeout=_TIMEOUT
        )
        return resp.ok
    except Exception as e:
        logger.error(f"[ventas_api] Error descontando inventario: {e}")
        return False


# ── Plan de producción ────────────────────────────────────────────────────────

def get_plan_produccion(fecha: str) -> dict | None:
    """Plan de producción de una fecha + ingredientes necesarios."""
    try:
        resp = requests.get(f"{_get_url()}/api/agentes/produccion/{fecha}",
                           headers=_headers(), timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"[ventas_api] Error obteniendo plan producción: {e}")
        return None


# ── Gastos ────────────────────────────────────────────────────────────────────

def get_gastos_mes() -> dict | None:
    """Gastos del mes actual para el agente de finanzas."""
    try:
        resp = requests.get(f"{_get_url()}/api/agentes/gastos",
                           headers=_headers(), timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"[ventas_api] Error obteniendo gastos: {e}")
        return None


def registrar_gasto_api(descripcion: str, monto: float, categoria: str = 'General',
                         fecha: str | None = None, proveedor: str = '') -> bool:
    """Registra un gasto en aurora-ventas."""
    from datetime import date
    try:
        body = {'descripcion': descripcion, 'monto': monto, 'categoria': categoria,
                'proveedor': proveedor, 'fecha': fecha or date.today().isoformat()}
        resp = requests.post(f"{VENTAS_API_URL}/api/agentes/gastos",
                            json=body, headers=_headers(), timeout=_TIMEOUT)
        return resp.ok
    except Exception as e:
        logger.error(f"[ventas_api] Error registrando gasto: {e}")
        return False


# ── Agenda ────────────────────────────────────────────────────────────────────

def get_agenda() -> dict | None:
    """Tareas y eventos pendientes del dueño."""
    try:
        resp = requests.get(f"{_get_url()}/api/agentes/agenda",
                           headers=_headers(), timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"[ventas_api] Error obteniendo agenda: {e}")
        return None


def crear_tarea_agenda(titulo: str, descripcion: str = '', tipo: str = 'tarea',
                        fecha: str | None = None, prioridad: str = 'media') -> bool:
    """Crea una tarea en la agenda del dueño."""
    from datetime import date
    try:
        body = {'titulo': titulo, 'descripcion': descripcion, 'tipo': tipo,
                'fecha': fecha or date.today().isoformat(), 'prioridad': prioridad}
        resp = requests.post(f"{VENTAS_API_URL}/api/agentes/agenda",
                            json=body, headers=_headers(), timeout=_TIMEOUT)
        return resp.ok
    except Exception as e:
        logger.error(f"[ventas_api] Error creando tarea: {e}")
        return False


# ── Configuración del negocio ─────────────────────────────────────────────────

def get_config_negocio() -> dict | None:
    """Configuración completa del negocio: recetas, precios, días despacho, etc."""
    try:
        resp = requests.get(f"{_get_url()}/api/agentes/config",
                           headers=_headers(), timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"[ventas_api] Error obteniendo config negocio: {e}")
        return None


# ── CRM ───────────────────────────────────────────────────────────────────────

def crm_get_pipeline(modulo: str = 'B2B') -> dict | None:
    """Leads agrupados por etapa."""
    try:
        resp = requests.get(f"{VENTAS_API_URL}/api/agentes/crm/pipeline",
                           params={'modulo': modulo}, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"[ventas_api] Error obteniendo pipeline CRM: {e}")
        return None


def crm_get_metricas() -> dict | None:
    """Métricas globales del CRM."""
    try:
        resp = requests.get(f"{VENTAS_API_URL}/api/agentes/crm/metricas", timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"[ventas_api] Error obteniendo métricas CRM: {e}")
        return None


def crm_get_leads(etapa: str = '', modulo: str = 'B2B') -> list:
    """Lista de leads con filtros opcionales."""
    try:
        params = {}
        if etapa:  params['etapa']  = etapa
        if modulo: params['modulo'] = modulo
        resp = requests.get(f"{VENTAS_API_URL}/api/agentes/crm/leads",
                           params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"[ventas_api] Error obteniendo leads CRM: {e}")
        return []


def crm_crear_lead(datos: dict) -> int | None:
    """Crea un nuevo lead. Retorna el ID o None."""
    try:
        resp = requests.post(f"{VENTAS_API_URL}/api/agentes/crm/leads",
                            json=datos, headers=_headers(), timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get('id')
    except Exception as e:
        logger.error(f"[ventas_api] Error creando lead CRM: {e}")
        return None


def crm_mover_lead(lead_id: int, etapa: str, nota: str = '') -> bool:
    """Mueve un lead a una nueva etapa."""
    try:
        resp = requests.post(
            f"{VENTAS_API_URL}/api/agentes/crm/leads/{lead_id}/mover",
            json={'etapa': etapa, 'nota': nota},
            headers=_headers(), timeout=_TIMEOUT
        )
        return resp.ok
    except Exception as e:
        logger.error(f"[ventas_api] Error moviendo lead {lead_id}: {e}")
        return False


def crm_registrar_interaccion(lead_id: int, tipo: str, contenido: str,
                               resultado: str = 'enviado', asunto: str = '') -> bool:
    """Registra una interacción con un lead."""
    try:
        resp = requests.post(
            f"{VENTAS_API_URL}/api/agentes/crm/leads/{lead_id}/interaccion",
            json={'tipo': tipo, 'contenido': contenido, 'resultado': resultado, 'asunto': asunto},
            headers=_headers(), timeout=_TIMEOUT
        )
        return resp.ok
    except Exception as e:
        logger.error(f"[ventas_api] Error registrando interacción lead {lead_id}: {e}")
        return False


def crm_get_seguimientos() -> dict:
    """Leads que necesitan seguimiento hoy."""
    try:
        resp = requests.get(f"{VENTAS_API_URL}/api/agentes/crm/seguimientos", timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"[ventas_api] Error obteniendo seguimientos CRM: {e}")
        return {'vencidos': [], 'sin_contacto': [], 'total': 0}


def crm_get_lead(lead_id: int) -> dict | None:
    """Retorna un lead con sus interacciones."""
    try:
        resp = requests.get(f"{VENTAS_API_URL}/api/agentes/crm/leads/{lead_id}",
                           timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"[ventas_api] Error obteniendo lead {lead_id}: {e}")
        return None


def crm_programar_contacto(lead_id: int, fecha: str) -> bool:
    """Programa la fecha de próximo contacto."""
    try:
        resp = requests.post(
            f"{VENTAS_API_URL}/api/agentes/crm/leads/{lead_id}/proximo-contacto",
            json={'fecha': fecha}, headers=_headers(), timeout=_TIMEOUT
        )
        return resp.ok
    except Exception as e:
        logger.error(f"[ventas_api] Error programando contacto lead {lead_id}: {e}")
        return False


def confirmar_produccion(fecha: str) -> dict | None:
    """
    Confirma la producción de una fecha: descuenta ingredientes del inventario
    y marca el plan como 'listo'. Retorna resumen con descuentos y alertas de stock.
    """
    try:
        resp = requests.post(
            f"{_get_url()}/api/plan-produccion/{fecha}/confirmar",
            headers=_headers(), timeout=_TIMEOUT,
        )
        if resp.ok:
            return resp.json()
        logger.warning(f"[ventas_api] confirmar_produccion {fecha}: {resp.status_code}")
        return None
    except Exception as e:
        logger.error(f"[ventas_api] Error confirmando producción {fecha}: {e}")
        return None


def get_contexto_ventas_texto() -> str:
    """
    Genera un bloque de texto con los datos clave de ventas para incluir en prompts.
    Intenta obtener datos de aurora-ventas; si no está disponible, retorna aviso.
    """
    resumen = get_resumen()
    if resumen is None:
        return (
            "⚠️ Sistema de ventas local no disponible. "
            "Para acceder a los datos, asegúrate de que aurora-ventas esté corriendo "
            "en http://127.0.0.1:5000 o configura VENTAS_API_URL."
        )

    kpi = resumen.get('kpi', {})
    pend = resumen.get('pendientes', {})
    seg  = resumen.get('segmento', {})
    subs = resumen.get('suscripciones', {})
    desp = resumen.get('despachos', {})
    top  = resumen.get('top_productos', [])

    lineas = [
        f"[Datos de ventas — {resumen.get('fecha', 'hoy')}]",
        f"• Hoy: ${kpi.get('hoy',{}).get('total',0):,.0f} ({kpi.get('hoy',{}).get('count',0)} ventas)",
        f"• Semana: ${kpi.get('semana',{}).get('total',0):,.0f} ({kpi.get('semana',{}).get('count',0)} ventas)",
        f"• Mes: ${kpi.get('mes',{}).get('total',0):,.0f} ({kpi.get('mes',{}).get('count',0)} ventas)",
        f"• Pagos pendientes: {pend.get('pago',0)} ventas sin cobrar",
        f"• Despachos pendientes: {pend.get('despacho',0)} sin enviar",
        f"• Despachos hoy: {len(desp.get('hoy',[]))} | Mañana: {len(desp.get('manana',[]))}",
        f"• Suscripciones activas: {subs.get('activas',0)} | Por renovar: {len(subs.get('por_renovar',[]))}",
        f"• HORECA: ${seg.get('horeca',{}).get('total',0):,.0f} ({seg.get('horeca',{}).get('count',0)} ventas)",
        f"• Clientes: ${seg.get('cliente',{}).get('total',0):,.0f} ({seg.get('cliente',{}).get('count',0)} ventas)",
    ]
    if top:
        lineas.append("• Top productos: " + ", ".join(f"{p['nombre']} ×{p['cantidad']:.0f}" for p in top[:3]))

    inact = resumen.get('clientes_inactivos', [])
    if inact:
        lineas.append(f"• Clientes inactivos (+21 días): {len(inact)}")

    return '\n'.join(lineas)
