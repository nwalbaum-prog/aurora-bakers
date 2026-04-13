"""
cron.py — Tareas programadas de Aurora Bakers

Usa APScheduler (BackgroundScheduler) con zona horaria de Santiago.
Se inicia desde main.py al arrancar la app (compatible con gunicorn --workers 1).

Horario de tareas (hora Chile / America/Santiago):
  07:00 L-V   plan_produccion  — plan del día siguiente por WhatsApp
  08:00 diario agenda_diaria   — resumen de agenda al dueño
  09:00 lunes  crm_semanal     — reporte CRM + seguimientos automáticos
  09:30 lunes  reporte_financiero — P&L semanal por email + WhatsApp
  10:00 mar/jue reactivacion   — mensajes a clientes inactivos
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import config

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None

TZ = 'America/Santiago'


# ── Tareas ────────────────────────────────────────────────────────────────────

def _tarea_agenda_diaria() -> None:
    """08:00 diario — Envía resumen de agenda al dueño por WhatsApp."""
    try:
        from agents.agenda import get_agenda_resumen
        from tools.whatsapp import send_whatsapp_safe
        resumen = get_agenda_resumen()
        ok = send_whatsapp_safe(config.OWNER_PHONE, resumen)
        logger.info(f"[cron] agenda_diaria enviada: ok={ok}")
    except Exception as e:
        logger.error(f"[cron] agenda_diaria error: {e}")


def _tarea_plan_produccion() -> None:
    """07:00 L-V — Envía plan de producción del día siguiente al dueño."""
    try:
        from agents.produccion import enviar_plan_produccion
        fecha_manana = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
        ok = enviar_plan_produccion(fecha_manana)
        logger.info(f"[cron] plan_produccion {fecha_manana}: ok={ok}")
    except Exception as e:
        logger.error(f"[cron] plan_produccion error: {e}")


def _tarea_crm_semanal() -> None:
    """Lunes 09:00 — Reporte CRM semanal + seguimientos automáticos."""
    try:
        from agents.crm import enviar_reporte_semanal, ejecutar_seguimientos_automaticos
        reporte_ok = enviar_reporte_semanal()
        seguimientos = ejecutar_seguimientos_automaticos(limit=20)
        logger.info(
            f"[cron] crm_semanal: reporte={reporte_ok}, "
            f"seguimientos enviados={seguimientos.get('enviados', 0)}"
        )
    except Exception as e:
        logger.error(f"[cron] crm_semanal error: {e}")


def _tarea_reporte_financiero() -> None:
    """Lunes 09:30 — P&L semanal por WhatsApp y email al dueño."""
    try:
        from agents.finanzas import generar_reporte_financiero
        from tools.whatsapp import send_whatsapp_safe
        from tools.email_tools import send_email_safe
        reporte = generar_reporte_financiero()
        send_whatsapp_safe(config.OWNER_PHONE, reporte)
        send_email_safe(config.OWNER_EMAIL, 'Reporte financiero semanal — Aurora Bakers', reporte)
        logger.info("[cron] reporte_financiero enviado")
    except Exception as e:
        logger.error(f"[cron] reporte_financiero error: {e}")


def _tarea_recordatorio_produccion() -> None:
    """L-V 16:00 — Recuerda confirmar producción si aún hay ítems pendientes."""
    try:
        from tools.ventas_api import get_plan_produccion
        from tools.whatsapp import send_whatsapp_safe
        from datetime import datetime
        hoy = datetime.now().strftime('%Y-%m-%d')
        plan_data = get_plan_produccion(hoy)
        if plan_data is None:
            return  # ventas no disponible
        pendientes = [p for p in plan_data.get('plan', []) if p.get('estado') != 'listo']
        if pendientes:
            msg = (
                f"🍞 *Recordatorio producción {hoy}*\n"
                f"Tienes {len(pendientes)} ítem(s) sin confirmar.\n"
                f"Responde *'confirmé la producción de hoy'* para descontar el inventario automáticamente."
            )
            send_whatsapp_safe(config.OWNER_PHONE, msg)
            logger.info(f"[cron] recordatorio_produccion enviado: {len(pendientes)} pendientes")
    except Exception as e:
        logger.error(f"[cron] recordatorio_produccion error: {e}")


def _tarea_reactivacion() -> None:
    """Martes y jueves 10:00 — Mensajes de reactivación a clientes inactivos."""
    try:
        from agents.analista import clientes_inactivos_minoristas, clientes_inactivos_mayoristas
        from tools.whatsapp import send_whatsapp_safe
        inactivos_min = clientes_inactivos_minoristas(dias=21)
        inactivos_may = clientes_inactivos_mayoristas(dias=14)
        enviados = 0

        for c in inactivos_min[:5]:
            msg = (
                f"Hola {c['nombre'].split()[0]}! 👋 Es Aurora Bakers. "
                f"Hace {c['dias_inactivo']} días que no te vemos. "
                f"¿Te tienta un pan de masa madre esta semana? 🍞"
            )
            if send_whatsapp_safe(c['telefono'], msg):
                enviados += 1

        for c in inactivos_may[:3]:
            msg = (
                f"Hola {c['nombre']}! 👋 Aurora Bakers acá. "
                f"¿Cómo va el stock? Tenemos disponibilidad para esta semana. "
                f"¿Coordinamos un pedido?"
            )
            if send_whatsapp_safe(c['telefono'], msg):
                enviados += 1

        logger.info(
            f"[cron] reactivacion: {len(inactivos_min)} min + {len(inactivos_may)} may "
            f"inactivos, {enviados} mensajes enviados"
        )
    except Exception as e:
        logger.error(f"[cron] reactivacion error: {e}")


# ── Control del scheduler ─────────────────────────────────────────────────────

def iniciar_scheduler() -> None:
    """
    Inicia APScheduler. Llamar una sola vez desde main.py.
    Seguro con gunicorn --workers 1.
    """
    global _scheduler
    if _scheduler is not None:
        logger.warning("[cron] Scheduler ya iniciado, ignorando llamada duplicada")
        return

    _scheduler = BackgroundScheduler(timezone=TZ)

    # 07:00 L-V — plan de producción
    _scheduler.add_job(
        _tarea_plan_produccion,
        CronTrigger(day_of_week='mon-fri', hour=7, minute=0, timezone=TZ),
        id='plan_produccion',
        replace_existing=True,
    )

    # 08:00 diario — agenda
    _scheduler.add_job(
        _tarea_agenda_diaria,
        CronTrigger(hour=8, minute=0, timezone=TZ),
        id='agenda_diaria',
        replace_existing=True,
    )

    # Lunes 09:00 — CRM semanal
    _scheduler.add_job(
        _tarea_crm_semanal,
        CronTrigger(day_of_week='mon', hour=9, minute=0, timezone=TZ),
        id='crm_semanal',
        replace_existing=True,
    )

    # Lunes 09:30 — reporte financiero
    _scheduler.add_job(
        _tarea_reporte_financiero,
        CronTrigger(day_of_week='mon', hour=9, minute=30, timezone=TZ),
        id='reporte_financiero',
        replace_existing=True,
    )

    # Martes y jueves 10:00 — reactivación
    _scheduler.add_job(
        _tarea_reactivacion,
        CronTrigger(day_of_week='tue,thu', hour=10, minute=0, timezone=TZ),
        id='reactivacion',
        replace_existing=True,
    )

    # L-V 16:00 — recordatorio confirmar producción
    _scheduler.add_job(
        _tarea_recordatorio_produccion,
        CronTrigger(day_of_week='mon-fri', hour=16, minute=0, timezone=TZ),
        id='recordatorio_produccion',
        replace_existing=True,
    )

    _scheduler.start()
    _jobs = [j.id for j in _scheduler.get_jobs()]
    logger.info(f"[cron] Scheduler iniciado con {len(_jobs)} tareas: {_jobs}")


def detener_scheduler() -> None:
    """Detiene el scheduler limpiamente (ej. en teardown de tests)."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("[cron] Scheduler detenido")


def get_proxima_ejecucion() -> dict:
    """Retorna la próxima ejecución de cada tarea (para /health o debug)."""
    if not _scheduler:
        return {}
    return {
        job.id: job.next_run_time.isoformat() if job.next_run_time else None
        for job in _scheduler.get_jobs()
    }
