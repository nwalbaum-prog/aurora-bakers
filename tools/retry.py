"""
tools/retry.py — Decorador de reintentos con backoff exponencial
"""
import time
import functools
import logging

logger = logging.getLogger(__name__)


def con_reintento(max_intentos: int = 3, delay: float = 2.0, exceptions: tuple = (Exception,)):
    """
    Decorador que reintenta una función con backoff exponencial.

    Uso:
        @con_reintento(max_intentos=3, delay=2, exceptions=(gspread.exceptions.APIError,))
        def mi_funcion():
            ...

    El delay entre reintentos es: delay * 2^intento  (2s, 4s, 8s, ...)
    """
    def decorador(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            ultimo_error = None
            for intento in range(max_intentos):
                try:
                    return fn(*args, **kwargs)
                except exceptions as e:
                    ultimo_error = e
                    if intento < max_intentos - 1:
                        espera = delay * (2 ** intento)
                        logger.warning(
                            f"[retry] {fn.__name__} falló (intento {intento + 1}/{max_intentos}): "
                            f"{e}. Reintentando en {espera:.1f}s..."
                        )
                        time.sleep(espera)
                    else:
                        logger.error(
                            f"[retry] {fn.__name__} falló tras {max_intentos} intentos: {e}"
                        )
            raise ultimo_error
        return wrapper
    return decorador
