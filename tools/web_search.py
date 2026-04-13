"""
tools/web_search.py — Búsqueda de leads en internet

Fuentes soportadas (en orden de prioridad):
  1. Google Places API (si GOOGLE_PLACES_API_KEY está configurada)
  2. SerpAPI (si SERPAPI_KEY está configurada)
  3. Búsqueda asistida por Claude (genera prospects sintéticos para investigación manual)
"""
from __future__ import annotations
import logging
import requests
import anthropic
import config
from tools.retry import con_reintento

logger = logging.getLogger(__name__)

PLACES_BASE = 'https://maps.googleapis.com/maps/api/place'
SERPAPI_BASE = 'https://serpapi.com/search'


# ── Google Places API ─────────────────────────────────────────────────────────

@con_reintento(max_intentos=2, delay=3, exceptions=(requests.RequestException,))
def buscar_google_places(tipo: str, comuna: str, limit: int = 20) -> list[dict]:
    """
    Busca negocios via Google Places Text Search.
    Retorna lista de dicts normalizados con campos: nombre, telefono, email, web, direccion, comuna
    """
    if not config.GOOGLE_PLACES_API_KEY:
        logger.warning("[web_search] GOOGLE_PLACES_API_KEY no configurada")
        return []

    query  = f"{tipo} en {comuna} Santiago Chile"
    params = {
        'query':    query,
        'language': 'es',
        'region':   'cl',
        'key':      config.GOOGLE_PLACES_API_KEY,
    }
    resp = requests.get(f"{PLACES_BASE}/textsearch/json", params=params, timeout=15)
    resp.raise_for_status()
    resultados = resp.json().get('results', [])

    leads = []
    for r in resultados[:limit]:
        detalle = _get_place_details(r.get('place_id', ''))
        leads.append({
            'nombre':    r.get('name', ''),
            'tipo':      tipo,
            'telefono':  detalle.get('formatted_phone_number', ''),
            'email':     '',  # Places no da email
            'web':       detalle.get('website', ''),
            'direccion': r.get('formatted_address', ''),
            'comuna':    comuna,
            'fuente':    'google_places',
            'place_id':  r.get('place_id', ''),
            'rating':    r.get('rating', 0),
        })
    return leads


@con_reintento(max_intentos=2, delay=3, exceptions=(requests.RequestException,))
def _get_place_details(place_id: str) -> dict:
    if not place_id:
        return {}
    params = {
        'place_id': place_id,
        'fields':   'formatted_phone_number,website',
        'key':      config.GOOGLE_PLACES_API_KEY,
    }
    resp = requests.get(f"{PLACES_BASE}/details/json", params=params, timeout=10)
    resp.raise_for_status()
    return resp.json().get('result', {})


# ── SerpAPI ───────────────────────────────────────────────────────────────────

@con_reintento(max_intentos=2, delay=3, exceptions=(requests.RequestException,))
def buscar_serpapi(tipo: str, comuna: str, limit: int = 20) -> list[dict]:
    """Busca negocios usando SerpAPI (Google Maps results)."""
    if not config.SERPAPI_KEY:
        logger.warning("[web_search] SERPAPI_KEY no configurada")
        return []

    params = {
        'engine':      'google_maps',
        'q':           f"{tipo} {comuna} Santiago",
        'hl':          'es',
        'gl':          'cl',
        'api_key':     config.SERPAPI_KEY,
    }
    resp = requests.get(SERPAPI_BASE, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    leads = []
    for r in data.get('local_results', [])[:limit]:
        leads.append({
            'nombre':    r.get('title', ''),
            'tipo':      tipo,
            'telefono':  r.get('phone', ''),
            'email':     '',
            'web':       r.get('website', ''),
            'direccion': r.get('address', ''),
            'comuna':    comuna,
            'fuente':    'serpapi',
            'rating':    r.get('rating', 0),
        })
    return leads


# ── Claude-asistido (fallback inteligente) ─────────────────────────────────────

def buscar_claude_asistido(
    tipo: str,
    comuna: str,
    limit: int = 10,
    contexto_adicional: str = '',
) -> list[dict]:
    """
    Cuando no hay APIs disponibles, Claude genera una lista de prospectos
    basada en su conocimiento de negocios en Santiago.
    El resultado debe ser verificado/completado manualmente.
    """
    try:
        cliente = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        prompt = (
            f"Eres un experto en el mercado gastronómico de Santiago de Chile.\n"
            f"Genera una lista de {limit} {tipo}s reales y conocidos en {comuna}, Santiago.\n"
            f"Para cada uno, incluye el nombre, dirección aproximada y sitio web si lo conoces.\n"
            f"{contexto_adicional}\n\n"
            f"Responde SOLO con un JSON array:\n"
            f'[{{"nombre":"...", "tipo":"{tipo}", "direccion":"...", "web":"...", "telefono":"", "email":"", "comuna":"{comuna}", "fuente":"claude_research"}}]'
        )
        resp = cliente.messages.create(
            model=config.MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        texto = resp.content[0].text.strip()
        inicio = texto.find('[')
        fin    = texto.rfind(']') + 1
        if inicio >= 0 and fin > inicio:
            import json
            return json.loads(texto[inicio:fin])
    except Exception as e:
        logger.error(f"[web_search] Error en búsqueda Claude-asistida: {e}")
    return []


# ── Función unificada ──────────────────────────────────────────────────────────

def buscar_leads(tipo: str, comuna: str, limit: int = 20) -> list[dict]:
    """
    Busca leads usando la mejor fuente disponible.
    Prioridad: Google Places → SerpAPI → Claude-asistido
    """
    if config.GOOGLE_PLACES_API_KEY:
        results = buscar_google_places(tipo, comuna, limit)
        if results:
            return results

    if config.SERPAPI_KEY:
        results = buscar_serpapi(tipo, comuna, limit)
        if results:
            return results

    logger.info(f"[web_search] Usando búsqueda Claude-asistida para {tipo} en {comuna}")
    return buscar_claude_asistido(tipo, comuna, limit)
