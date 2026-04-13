"""
memoria/semantica.py — Memoria semántica: hoja CONOCIMIENTO con hechos aprendidos.

Columnas: ID | Categoria | Concepto | Valor | Confianza | Ultima_Actualizacion | Fuente

Esta hoja almacena hechos duraderos extraídos de conversaciones:
  - preferencias de clientes
  - patrones de pedidos
  - hechos del negocio aprendidos en tiempo real

A diferencia de MEMORIA (episodios), CONOCIMIENTO es un mapa clave→valor upserteable.
"""
from __future__ import annotations
import logging
import hashlib
from datetime import datetime
import gspread
from tools.sheets import (
    get_records_cached,
    append_row,
    update_cell,
    find_row,
    get_or_create_worksheet,
    invalidar_cache,
)
import config

logger = logging.getLogger(__name__)

HEADERS_CONOCIMIENTO = [
    'ID', 'Categoria', 'Concepto', 'Valor', 'Confianza',
    'Ultima_Actualizacion', 'Fuente',
]


def _asegurar_hoja() -> None:
    """Crea la hoja CONOCIMIENTO con sus headers si no existe."""
    get_or_create_worksheet(config.SHEET_CONOCIMIENTO, HEADERS_CONOCIMIENTO)


def _make_id(categoria: str, concepto: str) -> str:
    """ID determinístico: hash corto de categoria+concepto."""
    raw = f"{categoria.lower()}::{concepto.lower()}"
    return hashlib.md5(raw.encode()).hexdigest()[:10]


def actualizar_conocimiento(
    categoria: str,
    concepto: str,
    valor: str,
    fuente: str = 'sistema',
    confianza: float = 0.8,
) -> bool:
    """
    Upsert: actualiza el valor si el concepto existe, lo inserta si no.
    Retorna True si la operación fue exitosa.
    """
    _asegurar_hoja()
    entry_id = _make_id(categoria, concepto)
    fecha    = datetime.now().strftime('%Y-%m-%d %H:%M')
    confianza_str = f"{confianza:.2f}"

    try:
        fila_num = find_row(config.SHEET_CONOCIMIENTO, 'ID', entry_id)

        if fila_num:
            # Actualizar: columnas Valor(4), Confianza(5), Ultima_Actualizacion(6), Fuente(7)
            from tools.sheets import get_sheet_client
            sp   = get_sheet_client()
            hoja = sp.worksheet(config.SHEET_CONOCIMIENTO)
            hoja.batch_update([{
                'range': f'D{fila_num}:G{fila_num}',
                'values': [[valor, confianza_str, fecha, fuente]],
            }])
            invalidar_cache(config.SHEET_CONOCIMIENTO)
            logger.debug(f"[semantica] Actualizado: {categoria}/{concepto}")
        else:
            # Insertar nueva fila
            append_row(config.SHEET_CONOCIMIENTO, [
                entry_id, categoria, concepto, valor, confianza_str, fecha, fuente,
            ])
            logger.debug(f"[semantica] Insertado: {categoria}/{concepto}")

        return True

    except Exception as e:
        logger.error(f"[semantica] Error en actualizar_conocimiento: {e}")
        return False


def get_conocimiento(categoria: str | None = None) -> list[dict]:
    """
    Lee todos los hechos semánticos, opcionalmente filtrados por categoría.
    """
    _asegurar_hoja()
    try:
        registros = get_records_cached(config.SHEET_CONOCIMIENTO)
        if categoria:
            cat_lower = categoria.lower()
            registros = [
                r for r in registros
                if str(r.get('Categoria', '')).lower() == cat_lower
            ]
        return registros
    except Exception as e:
        logger.error(f"[semantica] Error leyendo conocimiento: {e}")
        return []


def get_conocimiento_texto(categoria: str | None = None) -> str:
    """
    Retorna hechos semánticos como texto para incluir en prompts.
    """
    hechos = get_conocimiento(categoria)
    if not hechos:
        return ''

    lineas = [f'[Conocimiento semántico{f" ({categoria})" if categoria else ""}:]']
    for h in hechos:
        conf = float(h.get('Confianza', 0.8))
        lineas.append(
            f"• [{h.get('Categoria')}] {h.get('Concepto')}: {h.get('Valor')} "
            f"(confianza: {conf:.0%})"
        )
    return '\n'.join(lineas)


def eliminar_conocimiento(categoria: str, concepto: str) -> bool:
    """Elimina un hecho semántico por categoria+concepto."""
    _asegurar_hoja()
    entry_id = _make_id(categoria, concepto)
    try:
        fila_num = find_row(config.SHEET_CONOCIMIENTO, 'ID', entry_id)
        if not fila_num:
            return False

        from tools.sheets import get_sheet_client
        sp   = get_sheet_client()
        hoja = sp.worksheet(config.SHEET_CONOCIMIENTO)
        hoja.delete_rows(fila_num)
        invalidar_cache(config.SHEET_CONOCIMIENTO)
        logger.info(f"[semantica] Eliminado: {categoria}/{concepto}")
        return True
    except Exception as e:
        logger.error(f"[semantica] Error eliminando conocimiento: {e}")
        return False
