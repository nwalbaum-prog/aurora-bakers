"""
agents/prospector.py — Agente de prospección: descubre y califica leads

Flujo:
  1. buscar_y_guardar_leads(tipo, comuna) → busca en internet, filtra duplicados, guarda en LEADS
  2. calificar_lead(lead_id) → Claude analiza el negocio y le asigna puntuación
  3. get_leads_para_contactar() → leads DESCUBIERTO con puntuación >= umbral
"""
from __future__ import annotations
import json
import logging
import hashlib
from datetime import datetime
import anthropic
from tools.web_search import buscar_leads
from tools.sheets import (
    append_row, get_records_cached, invalidar_cache, find_row,
    get_or_create_worksheet, get_sheet_client,
)
from memoria.episodica import guardar_episodio
import config

logger = logging.getLogger(__name__)

HEADERS_LEADS = [
    'ID', 'Nombre', 'Tipo', 'Telefono', 'Email', 'Web',
    'Direccion', 'Comuna', 'Contacto', 'Estado', 'Fuente',
    'Puntuacion', 'Fecha_Descubierto', 'Ultima_Actividad', 'Notas',
]

HEADERS_INTERACCIONES = [
    'ID_Interaccion', 'ID_Lead', 'Fecha', 'Canal',
    'Tipo_Mensaje', 'Contenido_Resumen', 'Resultado', 'Agente',
]


# ── Inicialización ─────────────────────────────────────────────────────────────

def _asegurar_hojas() -> None:
    get_or_create_worksheet(config.SHEET_LEADS, HEADERS_LEADS)
    get_or_create_worksheet(config.SHEET_LEAD_INTERACCIONES, HEADERS_INTERACCIONES)


def _make_lead_id(nombre: str, comuna: str) -> str:
    raw = f"{nombre.lower().strip()}::{comuna.lower().strip()}"
    return 'L' + hashlib.md5(raw.encode()).hexdigest()[:9].upper()


# ── Búsqueda y guardado ────────────────────────────────────────────────────────

def buscar_y_guardar_leads(
    tipo: str,
    comuna: str,
    limit: int = 20,
) -> dict:
    """
    Busca leads online, filtra los ya existentes y guarda los nuevos.
    Retorna: {'buscados': N, 'nuevos': N, 'duplicados': N, 'leads': [...]}
    """
    _asegurar_hojas()
    encontrados = buscar_leads(tipo, comuna, limit)

    # Cargar IDs ya existentes para deduplicar
    leads_existentes = get_records_cached(config.SHEET_LEADS)
    ids_existentes   = {r.get('ID', '') for r in leads_existentes}

    nuevos = []
    duplicados = 0

    for lead in encontrados:
        lead_id = _make_lead_id(lead.get('nombre', ''), lead.get('comuna', comuna))
        if lead_id in ids_existentes:
            duplicados += 1
            continue

        puntuacion = _puntuacion_inicial(lead)
        fecha      = datetime.now().strftime('%Y-%m-%d %H:%M')

        fila = [
            lead_id,
            lead.get('nombre', ''),
            lead.get('tipo', tipo),
            lead.get('telefono', ''),
            lead.get('email', ''),
            lead.get('web', ''),
            lead.get('direccion', ''),
            lead.get('comuna', comuna),
            '',            # Contacto (persona de contacto)
            'DESCUBIERTO',
            lead.get('fuente', ''),
            puntuacion,
            fecha,
            fecha,
            '',
        ]
        append_row(config.SHEET_LEADS, fila)
        ids_existentes.add(lead_id)
        nuevos.append({**lead, 'id': lead_id, 'puntuacion': puntuacion})

    guardar_episodio(
        agente='prospector',
        pregunta=f"Búsqueda {tipo} en {comuna}",
        respuesta_resumen=f"{len(nuevos)} leads nuevos, {duplicados} duplicados",
        resultado='ok',
    )

    logger.info(f"[prospector] {tipo}/{comuna}: {len(nuevos)} nuevos, {duplicados} dups")
    return {
        'buscados':   len(encontrados),
        'nuevos':     len(nuevos),
        'duplicados': duplicados,
        'leads':      nuevos,
    }


# ── Calificación con Claude ────────────────────────────────────────────────────

def calificar_lead(lead_id: str) -> float:
    """
    Claude analiza el lead y retorna una puntuación 0-100.
    Considera: tipo de negocio, ubicación, web visible, tamaño estimado.
    Guarda la puntuación en la hoja.
    """
    _asegurar_hojas()
    try:
        leads = get_records_cached(config.SHEET_LEADS)
        lead  = next((r for r in leads if r.get('ID') == lead_id), None)
        if not lead:
            return 0.0

        cliente = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = cliente.messages.create(
            model=config.MODEL,
            max_tokens=60,
            messages=[{
                "role": "user",
                "content": (
                    f"Aurora Bakers es una panadería artesanal de masa madre en Santiago que busca clientes B2B "
                    f"(restaurantes, cafés, hoteles, oficinas) para venderles pan al por mayor.\n\n"
                    f"Evalúa este prospecto del 0 al 100 según su fit como cliente mayorista:\n"
                    f"Nombre: {lead.get('Nombre')}\n"
                    f"Tipo: {lead.get('Tipo')}\n"
                    f"Comuna: {lead.get('Comuna')}\n"
                    f"Web: {lead.get('Web') or 'sin web'}\n\n"
                    f"Responde SOLO con un número entero del 0 al 100."
                ),
            }],
        )
        score = float(resp.content[0].text.strip().split()[0])
        score = max(0, min(100, score))

        # Actualizar puntuación en la hoja
        fila_num = find_row(config.SHEET_LEADS, 'ID', lead_id)
        if fila_num:
            sp   = get_sheet_client()
            hoja = sp.worksheet(config.SHEET_LEADS)
            col_puntuacion = HEADERS_LEADS.index('Puntuacion') + 1
            hoja.update_cell(fila_num, col_puntuacion, score)
            invalidar_cache(config.SHEET_LEADS)

        return score

    except Exception as e:
        logger.error(f"[prospector] Error calificando {lead_id}: {e}")
        return 50.0


def _puntuacion_inicial(lead: dict) -> int:
    """Puntuación rápida sin Claude, basada en datos disponibles."""
    score = 30  # base
    tipo  = lead.get('tipo', '').lower()
    # Tipos de alto valor
    if any(t in tipo for t in ['restaurante', 'hotel', 'catering', 'bistró']):
        score += 30
    elif any(t in tipo for t in ['café', 'cafetería', 'deli', 'gourmet']):
        score += 25
    elif any(t in tipo for t in ['oficina', 'cowork']):
        score += 15
    # Tiene web o teléfono
    if lead.get('web'):
        score += 10
    if lead.get('telefono'):
        score += 10
    # Rating alto
    if float(lead.get('rating') or 0) >= 4.0:
        score += 10
    return min(100, score)


# ── Consultas ──────────────────────────────────────────────────────────────────

def get_leads_para_contactar(min_score: int = 50, limit: int = 20) -> list[dict]:
    """Retorna leads en estado DESCUBIERTO con puntuación suficiente."""
    _asegurar_hojas()
    leads = get_records_cached(config.SHEET_LEADS)
    return [
        r for r in leads
        if r.get('Estado') == 'DESCUBIERTO'
        and float(r.get('Puntuacion', 0) or 0) >= min_score
    ][:limit]


def get_leads_por_estado(estado: str) -> list[dict]:
    _asegurar_hojas()
    leads = get_records_cached(config.SHEET_LEADS)
    return [r for r in leads if r.get('Estado') == estado]


def get_todos_leads(force: bool = False) -> list[dict]:
    _asegurar_hojas()
    return get_records_cached(config.SHEET_LEADS, force=force)


def importar_leads_manual(leads_data: list[dict]) -> dict:
    """
    Importa leads desde una lista de dicts (ej: pegados desde CSV o formulario).
    Cada dict debe tener al menos: nombre, tipo, comuna.
    """
    _asegurar_hojas()
    leads_existentes = get_records_cached(config.SHEET_LEADS)
    ids_existentes   = {r.get('ID', '') for r in leads_existentes}
    nuevos = 0

    for lead in leads_data:
        lead_id = _make_lead_id(lead.get('nombre', ''), lead.get('comuna', ''))
        if lead_id in ids_existentes:
            continue
        fecha = datetime.now().strftime('%Y-%m-%d %H:%M')
        fila  = [
            lead_id,
            lead.get('nombre', ''),
            lead.get('tipo', ''),
            lead.get('telefono', ''),
            lead.get('email', ''),
            lead.get('web', ''),
            lead.get('direccion', ''),
            lead.get('comuna', ''),
            lead.get('contacto', ''),
            'DESCUBIERTO',
            'manual',
            _puntuacion_inicial(lead),
            fecha, fecha,
            lead.get('notas', ''),
        ]
        append_row(config.SHEET_LEADS, fila)
        ids_existentes.add(lead_id)
        nuevos += 1

    return {'importados': nuevos, 'total_enviados': len(leads_data)}
