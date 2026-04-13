"""
tools/sheets.py — Cliente Google Sheets singleton con caché TTL de 5 minutos
"""
import os
import json
import time
import logging
import gspread
from google.oauth2.service_account import Credentials
from tools.retry import con_reintento
import config

logger = logging.getLogger(__name__)

# Solo reintenta errores de red/API; RuntimeError (credenciales faltantes) falla inmediato
_RETRY_EXC = (gspread.exceptions.APIError, gspread.exceptions.GSpreadException, OSError)

# ── Singleton state ───────────────────────────────────────────────────────────
_spreadsheet    = None
_last_connect   = 0
_RECONNECT_SECS = 3600  # reconectar cada hora

# ── Cache state ───────────────────────────────────────────────────────────────
_cache:     dict[str, list] = {}
_cache_ttl: dict[str, float] = {}
CACHE_TTL = 300  # 5 minutos

SCOPES = [
    'https://spreadsheets.google.com/feeds',
    'https://www.googleapis.com/auth/drive',
]


# ── Conexión ──────────────────────────────────────────────────────────────────

def get_sheet_client() -> gspread.Spreadsheet:
    """Retorna el spreadsheet (singleton). Reconecta si llevamos más de 1h sin usar."""
    global _spreadsheet, _last_connect

    ahora = time.time()
    if _spreadsheet is None or (ahora - _last_connect) > _RECONNECT_SECS:
        sa_json = config.GOOGLE_SA_JSON
        if not sa_json:
            raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON no configurado")

        info = json.loads(sa_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        gc = gspread.authorize(creds)
        _spreadsheet = gc.open_by_key(config.GOOGLE_SHEET_ID)
        _last_connect = ahora
        logger.info("[sheets] Conexión establecida con Google Sheets")

    return _spreadsheet


# ── Lectura con caché ─────────────────────────────────────────────────────────

def get_records_cached(nombre: str, force: bool = False) -> list[dict]:
    """
    Lee todos los registros de una hoja con caché TTL de 5 minutos.
    `force=True` salta el caché y actualiza.
    """
    ahora = time.time()
    if (
        not force
        and nombre in _cache
        and ahora < _cache_ttl.get(nombre, 0)
    ):
        return _cache[nombre]

    registros = _leer_sheet(nombre)
    _cache[nombre] = registros
    _cache_ttl[nombre] = ahora + CACHE_TTL
    return registros


@con_reintento(max_intentos=3, delay=2, exceptions=_RETRY_EXC)
def _leer_sheet(nombre: str) -> list[dict]:
    sp = get_sheet_client()
    hoja = sp.worksheet(nombre)
    return hoja.get_all_records()


def invalidar_cache(nombre: str) -> None:
    """Fuerza refresco en la próxima lectura de esa hoja."""
    _cache.pop(nombre, None)
    _cache_ttl.pop(nombre, None)


# ── Escritura ─────────────────────────────────────────────────────────────────

@con_reintento(max_intentos=3, delay=2, exceptions=_RETRY_EXC)
def append_row(nombre: str, row: list) -> None:
    """Agrega una fila al final de la hoja e invalida su caché."""
    sp = get_sheet_client()
    hoja = sp.worksheet(nombre)
    hoja.append_row(row, value_input_option='USER_ENTERED')
    invalidar_cache(nombre)


@con_reintento(max_intentos=3, delay=2, exceptions=_RETRY_EXC)
def batch_update(nombre: str, updates: list[dict]) -> None:
    """
    Actualización masiva.
    `updates` es lista de dicts con claves 'range' (A1 notation) y 'values' ([[...]]).
    """
    sp = get_sheet_client()
    hoja = sp.worksheet(nombre)
    hoja.batch_update(updates)
    invalidar_cache(nombre)


@con_reintento(max_intentos=3, delay=2, exceptions=_RETRY_EXC)
def get_or_create_worksheet(nombre: str, headers: list[str]) -> gspread.Worksheet:
    """Retorna la hoja existente o la crea con los headers dados."""
    sp = get_sheet_client()
    try:
        return sp.worksheet(nombre)
    except gspread.exceptions.WorksheetNotFound:
        hoja = sp.add_worksheet(title=nombre, rows=1000, cols=len(headers))
        hoja.append_row(headers, value_input_option='USER_ENTERED')
        logger.info(f"[sheets] Hoja '{nombre}' creada con {len(headers)} columnas")
        return hoja


@con_reintento(max_intentos=3, delay=2, exceptions=_RETRY_EXC)
def update_cell(nombre: str, row: int, col: int, value) -> None:
    """Actualiza una celda específica (1-indexed)."""
    sp = get_sheet_client()
    hoja = sp.worksheet(nombre)
    hoja.update_cell(row, col, value)
    invalidar_cache(nombre)


@con_reintento(max_intentos=3, delay=2, exceptions=_RETRY_EXC)
def find_row(nombre: str, columna: str, valor: str) -> int | None:
    """
    Busca la primera fila donde `columna` == `valor`.
    Retorna el índice 1-based de la fila (incluyendo header) o None si no encuentra.
    """
    registros = get_records_cached(nombre, force=True)
    for i, row in enumerate(registros, start=2):  # start=2: fila 1 es header
        if str(row.get(columna, '')).strip() == str(valor).strip():
            return i
    return None
