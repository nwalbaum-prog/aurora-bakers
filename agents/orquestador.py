"""
agents/orquestador.py — Orquestador con clasificación de intención y confianza

Mejoras sobre el main.py original:
  - Clasificación con confianza 0-1 (pide aclaración si < 0.7)
  - Detección de sub-tareas múltiples (ejecuta ambos agentes)
  - Memoria episódica integrada
  - Tokens AGREGAR_EVENTO/AGREGAR_TAREA procesados aquí
"""
from __future__ import annotations
import json
import logging
import anthropic
from agents.finanzas import ask_finanzas
from agents.analista import ask_analista, get_contexto_negocio
from agents.produccion import ask_produccion, confirmar_produccion_hoy
from agents.agenda import ask_agenda, _procesar_tokens_agenda
from agents.crm import get_metricas_crm, get_leads_para_seguimiento, generar_reporte_semanal
from memoria.episodica import guardar_episodio, get_contexto_memoria
import config

logger = logging.getLogger(__name__)

INTENCIONES_VALIDAS = {
    'FINANZAS':       'consultas financieras, gastos, ingresos, reportes, márgenes, P&L',
    'ANALISTA':       'tendencias, análisis, clientes inactivos, estadísticas',
    'PRODUCCION':     'plan de producción, ingredientes, hornear, cantidades, inventario',
    'AGENDA':         'tareas, eventos, recordatorios, qué hacer hoy',
    'SOPHIE':         'pedidos de clientes, ventas, precios, catálogo',
    'CRM':            'leads, prospectos, pipeline, seguimientos, nuevos clientes B2B',
    'DELEGAR_SOPHIE': 'asignar tarea a Sophie, mensajes programados a clientes, notificaciones de despacho, seguimientos automáticos a clientes por WhatsApp',
    'GENERAL':        'saludos, preguntas generales sobre el negocio',
}

ORQUESTADOR_SYSTEM = """Eres el orquestador de Aurora Bakers (panadería artesanal en Santiago).
Coordinas un equipo de agentes especializados y respondes preguntas generales del dueño.

Cuando el dueño te escribe:
1. Responde directamente si es una pregunta general.
2. Si la respuesta requiere datos específicos, menciona qué agente debería responder.
3. Eres eficiente: no hagas preguntas innecesarias.
4. Usa formato WhatsApp (negrita con *, máximo 3 párrafos).

Contexto del negocio:
{contexto}

{memoria}
"""


def clasificar_intencion_avanzado(
    mensaje: str,
    contexto_memoria: str = '',
) -> tuple[str, float, list[str]]:
    """
    Clasifica la intención del mensaje.
    Retorna: (intencion, confianza, sub_tareas)

    Usa una llamada rápida a Claude con respuesta JSON.
    """
    try:
        cliente = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        intenciones_desc = '\n'.join(
            f"- {k}: {v}" for k, v in INTENCIONES_VALIDAS.items()
        )
        resp = cliente.messages.create(
            model=config.MODEL,
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": (
                    f"Clasifica esta consulta del dueño de una panadería:\n"
                    f"Mensaje: {mensaje}\n\n"
                    f"Intenciones disponibles:\n{intenciones_desc}\n\n"
                    f"Responde SOLO con JSON válido:\n"
                    f'{{"intencion":"FINANZAS","confianza":0.9,"sub_tareas":[]}}\n'
                    f"Si el mensaje mezcla dos temas, incluye ambos en sub_tareas.\n"
                    f"confianza entre 0 y 1."
                ),
            }],
        )
        texto = resp.content[0].text.strip()
        # Extraer JSON
        inicio = texto.find('{')
        fin    = texto.rfind('}') + 1
        if inicio >= 0 and fin > inicio:
            data = json.loads(texto[inicio:fin])
            intencion  = data.get('intencion', 'GENERAL').upper()
            confianza  = float(data.get('confianza', 0.8))
            sub_tareas = data.get('sub_tareas', [])
            if intencion not in INTENCIONES_VALIDAS:
                intencion = 'GENERAL'
            return intencion, confianza, sub_tareas
    except Exception as e:
        logger.warning(f"[orquestador] Error clasificando intención: {e}")

    # Fallback: clasificación keyword simple
    return _clasificar_keyword(mensaje), 0.7, []


def _clasificar_keyword(mensaje: str) -> str:
    """Clasificación de respaldo por palabras clave."""
    msg = mensaje.lower()
    if any(w in msg for w in ['plata', 'dinero', 'ingreso', 'gasto', 'margen', 'venta', 'reporte']):
        return 'FINANZAS'
    if any(w in msg for w in ['producción', 'produccion', 'hornear', 'horno', 'ingrediente', 'confirmé', 'confirme', 'confirmar', 'terminé producción', 'termine produccion', 'listo hornear', 'ya horneé']):
        return 'PRODUCCION'
    if any(w in msg for w in ['tarea', 'evento', 'agenda', 'recordatorio', 'qué hay']):
        return 'AGENDA'
    if any(w in msg for w in ['cliente', 'tendencia', 'análisis', 'inactivo', 'estadística']):
        return 'ANALISTA'
    if any(w in msg for w in ['pedido', 'precio', 'catálogo', 'producto', 'sophie']):
        return 'SOPHIE'
    if any(w in msg for w in ['lead', 'prospecto', 'pipeline', 'crm', 'seguimiento', 'b2b', 'restaurant', 'café', 'hotel']):
        return 'CRM'
    return 'GENERAL'


def ask_orquestador(user_id: str, mensaje: str) -> str:
    """
    Enruta el mensaje del dueño al agente correcto.
    Si confianza < 0.7, pide aclaración.
    Si hay sub_tareas, ejecuta múltiples agentes.
    """
    try:
        memoria = get_contexto_memoria('orquestador', limit=2)
        intencion, confianza, sub_tareas = clasificar_intencion_avanzado(mensaje, memoria)

        logger.info(f"[orquestador] intencion={intencion} confianza={confianza:.2f} sub_tareas={sub_tareas}")

        # Pedir aclaración si la confianza es muy baja
        if confianza < 0.7:
            return (
                f"No estoy seguro de lo que necesitas. ¿Me puedes precisar si es sobre:\n"
                f"• 💰 Finanzas (ventas, gastos, márgenes)\n"
                f"• 🍞 Producción (qué hornear, ingredientes)\n"
                f"• 📅 Agenda (tareas, eventos)\n"
                f"• 📊 Análisis (tendencias, clientes)\n"
                f"• 🛒 Ventas (pedidos, catálogo)"
            )

        # Multi-agente: hay sub-tareas adicionales
        respuestas = []
        if sub_tareas:
            for sub in sub_tareas[:2]:  # máximo 2 sub-tareas
                sub_upper = sub.upper()
                if sub_upper in INTENCIONES_VALIDAS and sub_upper != intencion:
                    r = _despachar(user_id, mensaje, sub_upper)
                    if r:
                        respuestas.append(r)

        # Agente principal
        respuesta_principal = _despachar(user_id, mensaje, intencion)
        respuestas.insert(0, respuesta_principal)

        respuesta_final = '\n\n---\n\n'.join(r for r in respuestas if r)

        guardar_episodio(
            agente='orquestador',
            pregunta=mensaje,
            respuesta_resumen=respuesta_final[:300],
            resultado=f'intencion={intencion} confianza={confianza:.2f}',
        )

        return respuesta_final

    except Exception as e:
        logger.error(f"[orquestador] Error: {e}")
        return f"❌ Error en el orquestador: {e}"


def _despachar(user_id: str, mensaje: str, intencion: str) -> str:
    """Despacha al agente correcto según la intención."""
    if intencion == 'FINANZAS':
        return ask_finanzas(user_id, mensaje)
    elif intencion == 'ANALISTA':
        return ask_analista(user_id, mensaje)
    elif intencion == 'PRODUCCION':
        msg_lower = mensaje.lower()
        if any(w in msg_lower for w in ['confirmé', 'confirme', 'confirmar producción', 'confirmar produccion', 'terminé producción', 'termine produccion', 'ya horneé', 'ya hornee', 'listo hornear']):
            from datetime import datetime
            fecha = datetime.now().strftime('%Y-%m-%d')
            return confirmar_produccion_hoy(fecha)
        return ask_produccion(user_id, mensaje)
    elif intencion == 'AGENDA':
        return ask_agenda(user_id, mensaje)
    elif intencion == 'SOPHIE':
        return (
            "Esa consulta es de ventas. Sophie (el bot de clientes) maneja ese flujo. "
            "¿Quieres que yo te dé un resumen de los últimos pedidos en cambio?"
        )
    elif intencion == 'CRM':
        return _respuesta_crm(user_id, mensaje)
    elif intencion == 'DELEGAR_SOPHIE':
        return _crear_tarea_sophie(mensaje)
    else:
        return _respuesta_general(user_id, mensaje)


def _respuesta_crm(user_id: str, mensaje: str) -> str:
    """Responde consultas de CRM usando métricas y pipeline actuales."""
    try:
        metricas  = get_metricas_crm()
        followup  = get_leads_para_seguimiento()
        contexto  = (
            f"CRM Aurora Bakers:\n"
            f"Total leads: {metricas.get('total',0)}\n"
            f"Clientes: {metricas.get('clientes_total',0)}\n"
            f"Tasa conversión: {metricas.get('tasa_conversion',0)}%\n"
            f"Follow-ups pendientes hoy: {metricas.get('followup_hoy',0)}\n"
            f"Nuevos esta semana: {metricas.get('nuevos_esta_semana',0)}\n"
            f"Por estado: {metricas.get('por_estado',{})}"
        )
        if followup:
            contexto += f"\n\nLeads que necesitan contacto hoy:\n"
            for l in followup[:5]:
                contexto += f"  • {l.get('Nombre','')} ({l.get('Estado','')}) — {l.get('Tipo','')}\n"

        cliente = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = cliente.messages.create(
            model=config.MODEL,
            max_tokens=400,
            system=(
                "Eres el agente de CRM de Aurora Bakers. "
                "Analizas el pipeline de ventas B2B y das recomendaciones concretas. "
                "Usa formato WhatsApp (negrita con *)."
            ),
            messages=[{"role": "user", "content": f"{contexto}\n\nConsulta: {mensaje}"}],
        )
        return resp.content[0].text
    except Exception as e:
        logger.error(f"[orquestador] Error en CRM: {e}")
        return f"❌ Error consultando CRM: {e}"


def _respuesta_general(user_id: str, mensaje: str) -> str:
    """Respuesta directa del orquestador para preguntas generales."""
    try:
        contexto = get_contexto_negocio()
        memoria  = get_contexto_memoria('orquestador', limit=2)

        cliente = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = cliente.messages.create(
            model=config.MODEL,
            max_tokens=400,
            system=ORQUESTADOR_SYSTEM.format(contexto=contexto, memoria=memoria),
            messages=[{"role": "user", "content": mensaje}],
        )
        return resp.content[0].text
    except Exception as e:
        logger.error(f"[orquestador] Error en respuesta general: {e}")
        return f"❌ Error procesando tu consulta: {e}"


def _crear_tarea_sophie(mensaje: str) -> str:
    """
    Extrae la tarea delegada del mensaje del dueño y la crea en aurora-ventas.
    Usa Claude para parsear: destinatario, mensaje, fecha/hora, condición.
    """
    try:
        from datetime import datetime as _dt
        import requests as _req
        from tools.ventas_api import VENTAS_API_URL, VENTAS_API_KEY, crear_tarea_agenda
        hoy = _dt.now().isoformat()

        cliente = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = cliente.messages.create(
            model=config.MODEL,
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": (
                    f"Hoy es {hoy}. El dueño de Aurora Bakers quiere que Sophie "
                    f"(asistente WhatsApp) haga lo siguiente:\n\"{mensaje}\"\n\n"
                    "Extrae la tarea. Responde SOLO con JSON válido:\n"
                    '{"telefono_destino": "56912345678 o null si es recordatorio del dueño", '
                    '"mensaje": "texto exacto que Sophie enviará", '
                    '"ejecutar_en": "2026-05-11T10:00:00", '
                    '"subtipo": "mensaje_programado", '
                    '"condicion": null}\n\n'
                    "subtipo: mensaje_programado | notificacion_despacho | "
                    "seguimiento_condicional | recordatorio_dueno\n"
                    "condicion: null o 'si_no_respondio'\n"
                    "Si no hay hora específica, usa 10:00 del día indicado.\n"
                    "Para 'mañana', usa la fecha de mañana."
                ),
            }],
        )
        texto = resp.content[0].text.strip()
        inicio = texto.find('{')
        fin    = texto.rfind('}') + 1
        if inicio < 0 or fin <= inicio:
            return "No pude entender la tarea. ¿Puedes ser más específico? 🙏"

        data    = json.loads(texto[inicio:fin])
        subtipo = data.get('subtipo', 'mensaje_programado')

        if subtipo == 'recordatorio_dueno':
            ok = crear_tarea_agenda(
                titulo=mensaje[:100],
                descripcion=data.get('mensaje', mensaje),
                tipo='tarea',
                fecha=data.get('ejecutar_en', hoy)[:10],
                prioridad='media',
            )
            return "Listo, te lo recuerdo en tu agenda 🗒️" if ok else "No pude crear el recordatorio."

        telefono = data.get('telefono_destino')
        if not telefono:
            return "No identifiqué el número del cliente. ¿Puedes indicar el teléfono?"

        ejecutar_en = data.get('ejecutar_en', hoy)
        headers = {'X-Agent-Key': VENTAS_API_KEY, 'Content-Type': 'application/json'}
        body = {
            'titulo':           f"Sophie: {subtipo}",
            'descripcion':      data.get('mensaje', ''),
            'tipo':             'tarea',
            'fecha':            ejecutar_en[:10],
            'hora':             ejecutar_en[11:16] if len(ejecutar_en) > 10 else '10:00',
            'prioridad':        'alta',
            'tipo_agente':      'sophie_tarea',
            'telefono_destino': str(telefono),
            'payload_json':     json.dumps(data, ensure_ascii=False),
        }
        r = _req.post(
            f"{VENTAS_API_URL}/api/agentes/agenda",
            json=body, headers=headers, timeout=8,
        )
        if r.ok:
            hora_str = ejecutar_en[11:16] if len(ejecutar_en) > 10 else '10:00'
            return f"Listo, Sophie lo hace el {ejecutar_en[:10]} a las {hora_str} 🗒️"
        return "No pude programar la tarea. ¿Repites?"

    except Exception as e:
        logger.error(f"[orquestador] Error creando tarea Sophie: {e}")
        return f"Error creando la tarea: {e}"
