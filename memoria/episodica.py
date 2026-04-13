"""
memoria/episodica.py — Memoria episódica de agentes

Fuente principal: aurora-ventas SQLite via HTTP
Fallback: Google Sheets (si aurora-ventas no disponible)
"""
from __future__ import annotations
import logging
import requests
from datetime import datetime
import anthropic
import config

logger = logging.getLogger(__name__)

VENTAS_API_URL = config.VENTAS_API_URL if hasattr(config, 'VENTAS_API_URL') else 'http://127.0.0.1:5000'
_TIMEOUT = 5


def guardar_episodio(
    agente: str,
    pregunta: str,
    respuesta_resumen: str,
    resultado: str = 'ok',
    aprendizaje: str = '',
) -> bool:
    """Guarda un episodio — usa aurora-ventas como fuente principal."""
    if not aprendizaje:
        aprendizaje = _extraer_aprendizaje(agente, pregunta, respuesta_resumen, resultado)

    # Fuente primaria: aurora-ventas
    try:
        resp = requests.post(
            f"{VENTAS_API_URL}/api/agentes/memoria",
            json={
                'agente': agente,
                'pregunta': pregunta[:500],
                'respuesta_resumen': respuesta_resumen[:1000],
                'resultado': resultado,
                'aprendizaje': aprendizaje,
            },
            timeout=_TIMEOUT,
        )
        if resp.ok:
            logger.debug(f"[episodica] Episodio guardado en ventas: agente={agente}")
            return True
    except Exception as e:
        logger.debug(f"[episodica] aurora-ventas no disponible para guardar: {e}")

    # Fallback: Google Sheets (silencioso si falla)
    try:
        from tools.sheets import append_row
        fecha = datetime.now().strftime('%Y-%m-%d %H:%M')
        append_row(config.SHEET_MEMORIA, [fecha, agente, pregunta[:500], respuesta_resumen[:1000], resultado, aprendizaje])
        return True
    except Exception:
        pass

    return False


def get_episodios_agente(agente: str, limit: int = 3) -> list[dict]:
    """Retorna los últimos episodios del agente desde aurora-ventas."""
    try:
        resp = requests.get(
            f"{VENTAS_API_URL}/api/agentes/memoria/{agente}",
            params={'limit': limit},
            timeout=_TIMEOUT,
        )
        if resp.ok:
            return resp.json()
    except Exception:
        pass

    # Fallback Sheets
    try:
        from tools.sheets import get_records_cached
        registros = get_records_cached(config.SHEET_MEMORIA)
        filtrados = [r for r in registros if str(r.get('Agente', '')).lower() == agente.lower()]
        return filtrados[-limit:][::-1]
    except Exception as e:
        logger.debug(f"[episodica] Error leyendo episodios de {agente}: {e}")
        return []


def get_contexto_memoria(agente: str, limit: int = 3) -> str:
    """Bloque de texto con episodios recientes para incluir en prompts."""
    episodios = get_episodios_agente(agente, limit)
    if not episodios:
        return ''

    lineas = ['[Memoria episódica relevante:]']
    for ep in episodios:
        # Compatible con formato aurora-ventas y Sheets
        fecha    = ep.get('fecha', ep.get('Fecha', ''))[:16]
        pregunta = ep.get('pregunta', ep.get('Pregunta', ''))[:100]
        aprend   = ep.get('aprendizaje', ep.get('Aprendizaje', ep.get('respuesta_resumen', ep.get('Respuesta_Resumen', ''))))[:150]
        lineas.append(f"• [{fecha}] {pregunta} → {aprend}")
    return '\n'.join(lineas)


def _extraer_aprendizaje(agente: str, pregunta: str, respuesta: str, resultado: str) -> str:
    """Llama a Claude para extraer un aprendizaje breve."""
    try:
        cliente = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        msg = cliente.messages.create(
            model=config.MODEL,
            max_tokens=60,
            messages=[{
                "role": "user",
                "content": (
                    f"Agente: {agente}\n"
                    f"Pregunta: {pregunta[:200]}\n"
                    f"Respuesta: {respuesta[:300]}\n"
                    f"Resultado: {resultado}\n\n"
                    "En máximo 15 palabras, ¿qué patrón o aprendizaje emerge de esta interacción?"
                ),
            }],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        logger.debug(f"[episodica] No se pudo extraer aprendizaje: {e}")
        return resultado
