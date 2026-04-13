"""
agents/crm.py — CRM: pipeline B2B, contacto, seguimiento, reportes

Fuente de datos: aurora-ventas (http://127.0.0.1:5000/api/agentes/crm/*)
La generación de mensajes (Claude) y el envío (WhatsApp/email) siguen aquí.
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta
import anthropic
from tools.ventas_api import (
    crm_get_pipeline, crm_get_metricas, crm_get_leads, crm_crear_lead,
    crm_mover_lead, crm_registrar_interaccion, crm_get_seguimientos,
    crm_get_lead, crm_programar_contacto,
)
from tools.whatsapp import send_whatsapp_safe
from tools.email_tools import send_email_safe
from memoria.episodica import guardar_episodio
import config

logger = logging.getLogger(__name__)

# ── Pipeline etapas B2B ───────────────────────────────────────────────────────
ETAPAS_B2B = ['PROSPECTO','CONTACTADO','MUESTRA_ENVIADA','NEGOCIACION',
               'CONTRATO_FIRMADO','ONBOARDING','CUENTA_ACTIVA','EN_RIESGO']

FOLLOWUP_DIAS = {
    'CONTACTADO':      3,
    'MUESTRA_ENVIADA': 2,
    'NEGOCIACION':     4,
    'ONBOARDING':      2,
    'EN_RIESGO':       1,
}


# ── Pipeline ──────────────────────────────────────────────────────────────────

def get_pipeline() -> dict:
    """Retorna todos los leads B2B agrupados por etapa (desde aurora-ventas)."""
    data = crm_get_pipeline('B2B')
    if data:
        return data.get('pipeline', {})
    return {e: [] for e in ETAPAS_B2B}


def get_metricas_crm() -> dict:
    """Métricas del CRM desde aurora-ventas."""
    data = crm_get_metricas()
    if data:
        return {
            'total':                data.get('total', 0),
            'clientes_total':       data.get('convertidos', 0),
            'tasa_conversion':      data.get('tasa_conversion', 0),
            'nuevos_esta_semana':   data.get('nuevos_semana', 0),
            'interacciones_semana': data.get('interacciones_semana', 0),
            'hot':                  data.get('hot', 0),
            'por_estado':           data.get('pipeline_b2b', {}),
            'followup_hoy':         len(crm_get_seguimientos().get('vencidos', [])),
        }
    return {'total': 0, 'clientes_total': 0, 'tasa_conversion': 0,
            'nuevos_esta_semana': 0, 'interacciones_semana': 0, 'por_estado': {}, 'followup_hoy': 0}


# ── Leads ─────────────────────────────────────────────────────────────────────

def get_todos_leads() -> list:
    return crm_get_leads(modulo='B2B')


def get_leads_por_estado(estado: str) -> list:
    return crm_get_leads(etapa=estado, modulo='B2B')


# ── Mover lead ────────────────────────────────────────────────────────────────

def mover_lead(lead_id: int | str, nueva_etapa: str, nota: str = '') -> bool:
    if not nueva_etapa:
        return False
    ok = crm_mover_lead(int(lead_id), nueva_etapa, nota)
    if ok:
        logger.info(f"[crm] Lead {lead_id} → {nueva_etapa}")
    return ok


# ── Contacto ──────────────────────────────────────────────────────────────────

def contactar_lead(
    lead_id: int | str,
    canal: str = 'email',
    tipo_mensaje: str = 'primer_contacto',
    mensaje_custom: str = '',
) -> dict:
    """
    Genera mensaje con Claude, lo envía y registra en aurora-ventas.
    canal: 'email' | 'whatsapp'
    tipo_mensaje: 'primer_contacto' | 'seguimiento' | 'propuesta' | 'cierre'
    """
    lead_data = crm_get_lead(int(lead_id))
    if not lead_data:
        return {'enviado': False, 'error': 'Lead no encontrado'}

    lead = lead_data['lead']
    mensaje = mensaje_custom or generar_mensaje_prospecting(lead, tipo_mensaje, canal)

    enviado = False
    destino = ''

    if canal == 'whatsapp' and lead.get('telefono'):
        destino = lead['telefono']
        enviado = send_whatsapp_safe(destino, mensaje)

    elif canal == 'email' and lead.get('email'):
        destino = lead['email']
        asunto  = _asunto_email(tipo_mensaje, lead.get('nombre', ''))
        enviado = send_email_safe(destino, asunto, mensaje)
    else:
        return {'enviado': False, 'error': f'Lead sin {canal} configurado'}

    # Registrar interacción en aurora-ventas
    resultado = 'enviado' if enviado else 'error_envio'
    crm_registrar_interaccion(
        int(lead_id), canal, mensaje[:500], resultado,
        _asunto_email(tipo_mensaje, lead.get('nombre','')) if canal == 'email' else ''
    )

    # Avanzar etapa si fue primer contacto
    if enviado and lead.get('etapa') == 'PROSPECTO':
        mover_lead(lead_id, 'CONTACTADO')

    # Programar próximo seguimiento
    if enviado:
        dias = FOLLOWUP_DIAS.get('CONTACTADO', 3)
        prox = (datetime.now() + timedelta(days=dias)).strftime('%Y-%m-%d')
        crm_programar_contacto(int(lead_id), prox)

    guardar_episodio(
        agente='crm',
        pregunta=f"Contacto {tipo_mensaje} a {lead.get('nombre')} via {canal}",
        respuesta_resumen=mensaje[:200],
        resultado=resultado,
    )

    return {'enviado': enviado, 'mensaje': mensaje, 'canal': canal, 'destino': destino}


def generar_mensaje_prospecting(lead: dict, tipo: str = 'primer_contacto', canal: str = 'email') -> str:
    """Claude genera mensaje personalizado para el lead."""
    try:
        largo = '150-200 palabras' if canal == 'email' else '3-4 líneas'
        tono  = 'formal pero cálido' if canal == 'email' else 'casual y directo'
        empresa = lead.get('empresa') or lead.get('nombre', '')
        zona    = lead.get('zona', 'Santiago')
        tipo_negocio = lead.get('cargo') or 'negocio'

        prompts = {
            'primer_contacto': (
                f"Escribe un mensaje de primer contacto de Aurora Bakers para {empresa} "
                f"({tipo_negocio} en {zona}, Santiago). "
                f"Aurora Bakers ofrece pan artesanal de masa madre para negocios: entrega semanal, sin aditivos, "
                f"precio mayorista. Tono {tono}, {largo}. Incluye llamada a la acción (degustación o cotización)."
            ),
            'seguimiento': (
                f"Escribe un follow-up breve para {empresa} que no respondió el mensaje anterior. "
                f"Amigable, sin presión. {largo}. Canal: {canal}."
            ),
            'propuesta': (
                f"Escribe propuesta de valor para {empresa} que mostró interés. "
                f"Incluye: ciabattas $2.400/6u, hogazas desde $6.500, entrega semanal, mínimo de compra. {largo}."
            ),
            'cierre': (
                f"Escribe mensaje de cierre para {empresa} que recibió propuesta. "
                f"Urgencia suave (cupos limitados de despacho semanal). {largo}. Canal: {canal}."
            ),
        }

        cliente = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = cliente.messages.create(
            model=config.MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompts.get(tipo, prompts['primer_contacto'])}],
        )
        return resp.content[0].text.strip()

    except Exception as e:
        logger.error(f"[crm] Error generando mensaje: {e}")
        return (
            f"Hola, somos Aurora Bakers — panadería artesanal de masa madre en Santiago. "
            f"Ofrecemos pan fresco para negocios como {lead.get('empresa', 'el suyo')}. "
            f"¿Les interesa una degustación sin compromiso?"
        )


# ── Follow-up ─────────────────────────────────────────────────────────────────

def get_leads_para_seguimiento() -> list:
    """Leads que necesitan seguimiento (desde aurora-ventas)."""
    data = crm_get_seguimientos()
    return data.get('vencidos', []) + data.get('sin_contacto', [])


def ejecutar_seguimientos_automaticos(limit: int = 10) -> dict:
    """Envía follow-ups automáticos a los leads que lo requieren."""
    leads = get_leads_para_seguimiento()[:limit]
    enviados = 0
    errores  = 0

    for lead in leads:
        canal = 'email' if lead.get('email') else ('whatsapp' if lead.get('telefono') else None)
        if not canal:
            continue
        tipo  = _tipo_followup(lead.get('etapa', ''))
        result = contactar_lead(lead['id'], canal, tipo)
        if result.get('enviado'):
            enviados += 1
        else:
            errores += 1

    return {'procesados': len(leads), 'enviados': enviados, 'errores': errores}


def _tipo_followup(etapa: str) -> str:
    if etapa in ('PROSPECTO', 'CONTACTADO'):
        return 'seguimiento'
    if etapa in ('MUESTRA_ENVIADA',):
        return 'propuesta'
    if etapa in ('NEGOCIACION',):
        return 'cierre'
    return 'seguimiento'


# ── Reporte semanal ────────────────────────────────────────────────────────────

def generar_reporte_semanal() -> str:
    """Reporte semanal de CRM con análisis Claude."""
    metricas = get_metricas_crm()
    seguimientos = crm_get_seguimientos()

    datos_texto = (
        f"SEMANA: {(datetime.now() - timedelta(days=7)).strftime('%d/%m')} - {datetime.now().strftime('%d/%m/%Y')}\n"
        f"Total leads B2B: {metricas['total']}\n"
        f"Nuevos esta semana: {metricas['nuevos_esta_semana']}\n"
        f"Interacciones: {metricas['interacciones_semana']}\n"
        f"Clientes activos: {metricas['clientes_total']}\n"
        f"Tasa conversión: {metricas['tasa_conversion']}%\n"
        f"Leads calientes: {metricas.get('hot', 0)}\n"
        f"Follow-ups vencidos: {len(seguimientos.get('vencidos',[]))}\n"
        f"Pipeline: {metricas['por_estado']}"
    )

    try:
        cliente = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = cliente.messages.create(
            model=config.MODEL,
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": (
                    f"Eres el analista de ventas B2B de Aurora Bakers (panadería artesanal en Santiago). "
                    f"Genera reporte semanal de desarrollo comercial en formato WhatsApp (usa *, no markdown). "
                    f"Incluye: resumen ejecutivo, logros, alertas, próximos pasos.\n\nDatos:\n{datos_texto}"
                ),
            }],
        )
        analisis = resp.content[0].text.strip()
    except Exception as e:
        logger.error(f"[crm] Error generando análisis del reporte: {e}")
        analisis = datos_texto

    return f"📊 *Reporte Semanal CRM — Aurora Bakers*\n\n{analisis}"


def enviar_reporte_semanal() -> bool:
    """Genera y envía el reporte semanal por WhatsApp y email."""
    reporte = generar_reporte_semanal()
    ok_wa   = send_whatsapp_safe(config.OWNER_PHONE, reporte)
    ok_mail = send_email_safe(
        config.OWNER_EMAIL,
        f"Reporte Semanal CRM — Aurora Bakers {datetime.now().strftime('%d/%m/%Y')}",
        reporte,
    )
    return ok_wa or ok_mail


# ── Prospecting ────────────────────────────────────────────────────────────────

def buscar_y_guardar_leads(tipo: str, zona: str, limit: int = 15) -> dict:
    """Busca negocios y los guarda como leads en aurora-ventas."""
    from agents.prospector import buscar_y_guardar_leads as _buscar
    resultado = _buscar(tipo, zona, limit)

    # Los leads ya se guardan en Sheets en prospector.py
    # Aquí los sincronizamos a aurora-ventas
    nuevos_en_ventas = 0
    for lead_data in resultado.get('leads', []):
        lid = crm_crear_lead({
            'modulo':       'B2B',
            'nombre':       lead_data.get('nombre', ''),
            'empresa':      lead_data.get('nombre', ''),
            'email':        lead_data.get('email', ''),
            'telefono':     lead_data.get('telefono', ''),
            'zona':         zona,
            'cargo':        tipo,
            'canal_origen': 'prospecting',
            'etapa':        'PROSPECTO',
            'temperatura':  'COLD',
            'notas':        lead_data.get('notas', ''),
        })
        if lid:
            nuevos_en_ventas += 1

    resultado['guardados_en_ventas'] = nuevos_en_ventas
    return resultado


# ── Helpers ────────────────────────────────────────────────────────────────────

def registrar_respuesta(lead_id: int | str, contenido: str, canal: str = 'email') -> bool:
    """Registra que el lead respondió y avanza a MUESTRA_ENVIADA."""
    crm_registrar_interaccion(int(lead_id), canal, contenido, 'respondio')
    lead_data = crm_get_lead(int(lead_id))
    if lead_data and lead_data['lead'].get('etapa') == 'CONTACTADO':
        mover_lead(lead_id, 'MUESTRA_ENVIADA')
    return True


def get_interacciones_lead(lead_id: int | str) -> list:
    """Retorna interacciones de un lead desde aurora-ventas."""
    data = crm_get_lead(int(lead_id))
    return data.get('interacciones', []) if data else []


def _asunto_email(tipo: str, nombre: str) -> str:
    asuntos = {
        'primer_contacto': f'Pan artesanal de masa madre para {nombre} 🍞',
        'seguimiento':     f'Seguimiento — Aurora Bakers para {nombre}',
        'propuesta':       f'Propuesta comercial Aurora Bakers × {nombre}',
        'cierre':          f'Última disponibilidad de entrega semanal — {nombre}',
    }
    return asuntos.get(tipo, f'Aurora Bakers — {nombre}')
