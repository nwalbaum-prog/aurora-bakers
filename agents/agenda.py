"""
agents/agenda.py — Agente de agenda: tareas, eventos, resumen diario

Fuente principal: aurora-ventas (http://127.0.0.1:5000/api/agentes/agenda)
Fallback: Google Sheets
"""
from __future__ import annotations
import logging
from datetime import datetime
import anthropic
from tools.sheets import get_records_cached, append_row
from tools.ventas_api import get_agenda, crear_tarea_agenda
from memoria.episodica import guardar_episodio
import config

logger = logging.getLogger(__name__)


def agregar_tarea(titulo: str, descripcion: str = '', prioridad: str = 'media') -> bool:
    """Agrega una tarea — usa aurora-ventas como fuente principal, Sheets como fallback."""
    # Fuente primaria: aurora-ventas
    ok = crear_tarea_agenda(titulo, descripcion, tipo='tarea', prioridad=prioridad)
    if ok:
        logger.info(f"[agenda] Tarea creada en ventas: {titulo}")
        return True
    # Fallback: Google Sheets
    try:
        fecha = datetime.now().strftime('%Y-%m-%d %H:%M')
        append_row(config.SHEET_TAREAS, [fecha, titulo, descripcion, prioridad, 'pendiente'])
        logger.info(f"[agenda] Tarea creada en Sheets (fallback): {titulo}")
        return True
    except Exception as e:
        logger.error(f"[agenda] Error agregando tarea: {e}")
        return False


def agregar_evento(titulo: str, fecha_evento: str, descripcion: str = '') -> bool:
    """Agrega un evento a aurora-ventas."""
    ok = crear_tarea_agenda(titulo, descripcion, tipo='evento', fecha=fecha_evento)
    if ok:
        logger.info(f"[agenda] Evento creado en ventas: {titulo} para {fecha_evento}")
        return True
    try:
        fecha_registro = datetime.now().strftime('%Y-%m-%d %H:%M')
        append_row(config.SHEET_AGENDA, [fecha_registro, fecha_evento, titulo, descripcion])
        return True
    except Exception as e:
        logger.error(f"[agenda] Error agregando evento: {e}")
        return False


def get_agenda_resumen(dias: int = 7) -> str:
    """
    Retorna el resumen de la agenda: tareas pendientes + próximos eventos.
    Fuente preferida: aurora-ventas. Fallback: Google Sheets.
    """
    hoy = datetime.now()

    # ── Fuente 1: aurora-ventas ───────────────────────────────────────────────
    agenda_data = get_agenda()
    if agenda_data is not None:
        lineas = [f"📅 *Agenda — {hoy.strftime('%d/%m/%Y')}*\n"]

        hoy_items = agenda_data.get('hoy', [])
        vencidos  = agenda_data.get('vencidos', [])
        proximos  = agenda_data.get('proximos', [])

        if vencidos:
            lineas.append(f"⚠️ *{len(vencidos)} tarea(s) vencida(s):*")
            for t in vencidos[:3]:
                lineas.append(f"  🔴 {t['titulo']} ({t['fecha']})")

        if hoy_items:
            lineas.append("\n*Para hoy:*")
            for t in hoy_items:
                icono = '🔴' if t['prioridad'] == 'alta' else ('🟡' if t['prioridad'] == 'media' else '⚪')
                hora_str = f" {t['hora']}" if t.get('hora') else ''
                lineas.append(f"  {icono} {t['titulo']}{hora_str}")
        else:
            lineas.append("✅ Sin tareas para hoy")

        if proximos:
            lineas.append("\n*Próximos:*")
            for t in proximos[:5]:
                lineas.append(f"  📌 {t['fecha']}: {t['titulo']}")

        return '\n'.join(lineas)

    # ── Fallback: Google Sheets ───────────────────────────────────────────────
    try:
        tareas  = get_records_cached(config.SHEET_TAREAS)
        eventos = get_records_cached(config.SHEET_AGENDA)

        lineas = [f"📅 *Agenda — {hoy.strftime('%d/%m/%Y')}* (vía Sheets)\n"]

        pendientes = [t for t in tareas if str(t.get('Estado', '')).lower() == 'pendiente']
        if pendientes:
            lineas.append("*Tareas pendientes:*")
            for t in pendientes[:10]:
                prioridad = str(t.get('Prioridad', 'media')).lower()
                icono = '🔴' if prioridad == 'alta' else ('🟡' if prioridad == 'media' else '⚪')
                lineas.append(f"  {icono} {t.get('Titulo', '')}")
        else:
            lineas.append("✅ Sin tareas pendientes")

        lineas.append("\n*Próximos eventos:*")
        proximos = []
        for e in eventos:
            fecha_str = str(e.get('Fecha_Evento', '')).strip()[:10]
            try:
                fecha_ev = datetime.strptime(fecha_str, '%Y-%m-%d')
                if fecha_ev >= hoy:
                    proximos.append((fecha_ev, e))
            except ValueError:
                pass

        proximos.sort(key=lambda x: x[0])
        if proximos:
            for fecha_ev, e in proximos[:5]:
                lineas.append(f"  📌 {fecha_ev.strftime('%d/%m')}: {e.get('Titulo', '')}")
        else:
            lineas.append("  Sin eventos próximos")

        return '\n'.join(lineas)

    except Exception as e:
        logger.error(f"[agenda] Error en get_agenda_resumen: {e}")
        return f"❌ Error obteniendo agenda: {e}"


def ask_agenda(user_id: str, mensaje: str) -> str:
    """Procesa consultas y comandos de agenda con Claude."""
    try:
        resumen = get_agenda_resumen()
        cliente = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = cliente.messages.create(
            model=config.MODEL,
            max_tokens=400,
            system=(
                "Eres el agente de agenda de Aurora Bakers (panadería artesanal en Santiago). "
                "Gestionas tareas y eventos del negocio. Cuando el usuario pide agregar algo, "
                "responde con el token estructurado adecuado al inicio:\n"
                "- Para tarea: AGREGAR_TAREA|titulo|descripcion|prioridad\n"
                "- Para evento: AGREGAR_EVENTO|titulo|fecha_YYYY-MM-DD|descripcion\n"
                "Luego confirma en lenguaje natural. Usa formato WhatsApp."
            ),
            messages=[
                {"role": "user", "content": f"{resumen}\n\nMensaje: {mensaje}"},
            ],
        )
        respuesta = resp.content[0].text

        _procesar_tokens_agenda(respuesta)

        guardar_episodio(
            agente='agenda',
            pregunta=mensaje,
            respuesta_resumen=respuesta[:200],
            resultado='ok',
        )

        lineas = respuesta.split('\n')
        lineas_limpias = [l for l in lineas if not l.startswith('AGREGAR_')]
        return '\n'.join(lineas_limpias).strip()

    except Exception as e:
        logger.error(f"[agenda] Error en ask_agenda: {e}")
        return f"❌ Error en agente de agenda: {e}"


def _procesar_tokens_agenda(respuesta: str) -> None:
    """Extrae y ejecuta tokens AGREGAR_TAREA y AGREGAR_EVENTO del texto."""
    for linea in respuesta.split('\n'):
        linea = linea.strip()
        if linea.startswith('AGREGAR_TAREA|'):
            partes = linea.split('|')
            if len(partes) >= 2:
                titulo      = partes[1].strip()
                descripcion = partes[2].strip() if len(partes) > 2 else ''
                prioridad   = partes[3].strip().lower() if len(partes) > 3 else 'media'
                agregar_tarea(titulo, descripcion, prioridad)

        elif linea.startswith('AGREGAR_EVENTO|'):
            partes = linea.split('|')
            if len(partes) >= 3:
                titulo      = partes[1].strip()
                fecha_ev    = partes[2].strip()
                descripcion = partes[3].strip() if len(partes) > 3 else ''
                agregar_evento(titulo, fecha_ev, descripcion)
