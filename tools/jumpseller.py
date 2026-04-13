"""
tools/jumpseller.py — Integración con Jumpseller API (catálogo + links de compra)
"""
import time
import logging
import unicodedata
import requests
from tools.retry import con_reintento
import config

logger = logging.getLogger(__name__)

# Caché en memoria para el catálogo (evitar llamadas repetidas)
_cache_productos: list[dict] = []
_cache_ts: float = 0
_CACHE_TTL = 600  # 10 minutos


def _normalizar(texto: str) -> str:
    """Elimina tildes y pasa a minúsculas para comparación fuzzy."""
    nfkd = unicodedata.normalize('NFKD', texto)
    ascii_str = nfkd.encode('ascii', 'ignore').decode('ascii')
    return ascii_str.lower().strip()


@con_reintento(max_intentos=3, delay=2, exceptions=(requests.RequestException,))
def _fetch_productos() -> list[dict]:
    url = "https://api.jumpseller.com/v1/products.json"
    params = {
        'login': config.JUMPSELLER_LOGIN,
        'authtoken': config.JUMPSELLER_AUTH_TOKEN,
        'limit': 100,
        'page': 1,
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    # Jumpseller devuelve lista de {'product': {...}}
    return [item['product'] for item in data if 'product' in item]


def get_productos_jumpseller(force: bool = False) -> list[dict]:
    """Retorna el catálogo de productos con caché de 10 minutos."""
    global _cache_productos, _cache_ts
    ahora = time.time()

    if force or not _cache_productos or (ahora - _cache_ts) > _CACHE_TTL:
        try:
            _cache_productos = _fetch_productos()
            _cache_ts = ahora
            logger.info(f"[jumpseller] Catálogo cargado: {len(_cache_productos)} productos")
        except Exception as e:
            logger.error(f"[jumpseller] Error obteniendo catálogo: {e}")
            if not _cache_productos:
                return []

    return _cache_productos


def generar_link_compra(nombre_producto: str, dia_entrega: str) -> str | None:
    """
    Genera un link de compra directa en Jumpseller para el producto dado.
    Busca por nombre fuzzy en el catálogo.
    Retorna la URL o None si no se encontró el producto.
    """
    productos = get_productos_jumpseller()
    if not productos:
        return None

    nombre_norm = _normalizar(nombre_producto)

    # Búsqueda exacta primero
    for p in productos:
        if _normalizar(p.get('name', '')) == nombre_norm:
            return _construir_link(p, dia_entrega)

    # Búsqueda parcial
    for p in productos:
        if nombre_norm in _normalizar(p.get('name', '')):
            return _construir_link(p, dia_entrega)

    logger.warning(f"[jumpseller] Producto no encontrado: '{nombre_producto}'")
    return None


def _construir_link(producto: dict, dia_entrega: str) -> str:
    """Construye URL de producto con parámetro UTM del día de entrega."""
    base = producto.get('permalink') or f"https://panypasta.cl/products/{producto.get('id')}"
    return f"{base}?utm_source=whatsapp&dia={dia_entrega}"


def get_catalogo_texto() -> str:
    """Retorna el catálogo como texto para incluir en prompts."""
    productos = get_productos_jumpseller()
    if not productos:
        return "Catálogo no disponible en este momento."

    lineas = ["*Catálogo Aurora Bakers:*"]
    for p in productos:
        precio = p.get('price', 0)
        nombre = p.get('name', 'Sin nombre')
        stock  = "✅" if p.get('stock_unlimited') or (p.get('stock', 0) or 0) > 0 else "❌"
        lineas.append(f"{stock} {nombre} — ${precio:,.0f}")

    return "\n".join(lineas)
