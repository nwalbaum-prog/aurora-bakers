"""
memoria/contexto.py — Reemplaza el dict `conversaciones = {}` del main.py original.

Antes (main.py):
    conversaciones[user_id] = [
        {'_tipo': 'mayorista', '_cliente': {...}, '_pedido_guardado': False},  # índice 0: metadata
        {"role": "user", "content": "hola"},  # índice 1+: mensajes
    ]
    meta = conversaciones[user_id][0]
    mensajes = [m for m in conversaciones[user_id] if '_tipo' not in m]

Ahora: ConversacionesStore con estado tipado y acceso explícito.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Literal
import config

logger = logging.getLogger(__name__)

TipoConversacion = Literal['minorista', 'mayorista', 'orquestador']


@dataclass
class ConversacionState:
    tipo:           TipoConversacion
    cliente_data:   dict = field(default_factory=dict)
    pedido_guardado: bool = False
    mensajes:       list  = field(default_factory=list)

    # Para conversaciones de tipo minorista con historial previo
    historial_cliente: list = field(default_factory=list)


class ConversacionesStore:
    """
    Gestiona el estado de todas las conversaciones activas en RAM.
    Thread-safe para uso con Flask (un hilo por request).
    """

    def __init__(self):
        self._store: dict[str, ConversacionState] = {}

    # ── Acceso ────────────────────────────────────────────────────────────────

    def get(self, user_id: str) -> ConversacionState | None:
        return self._store.get(user_id)

    def get_or_create(
        self,
        user_id: str,
        tipo: TipoConversacion,
        cliente_data: dict | None = None,
    ) -> ConversacionState:
        if user_id not in self._store:
            self._store[user_id] = ConversacionState(
                tipo=tipo,
                cliente_data=cliente_data or {},
            )
            logger.debug(f"[contexto] Nueva conversación: user_id={user_id} tipo={tipo}")
        return self._store[user_id]

    def existe(self, user_id: str) -> bool:
        return user_id in self._store

    # ── Mensajes ──────────────────────────────────────────────────────────────

    def get_mensajes(self, user_id: str) -> list:
        estado = self._store.get(user_id)
        return estado.mensajes if estado else []

    def append_mensaje(self, user_id: str, role: str, content: str) -> None:
        estado = self._store.get(user_id)
        if estado is None:
            logger.warning(f"[contexto] append_mensaje: user_id={user_id} no existe")
            return
        estado.mensajes.append({"role": role, "content": content})
        self._trim(user_id)

    def _trim(self, user_id: str) -> None:
        """Mantiene los últimos CONV_TRIM_MAX mensajes para no crecer indefinidamente."""
        estado = self._store.get(user_id)
        if estado and len(estado.mensajes) > config.CONV_TRIM_MAX:
            exceso = len(estado.mensajes) - config.CONV_TRIM_MAX
            estado.mensajes = estado.mensajes[exceso:]

    # ── Estado ────────────────────────────────────────────────────────────────

    def marcar_pedido_guardado(self, user_id: str) -> None:
        estado = self._store.get(user_id)
        if estado:
            estado.pedido_guardado = True

    def actualizar_cliente(self, user_id: str, datos: dict) -> None:
        estado = self._store.get(user_id)
        if estado:
            estado.cliente_data.update(datos)

    def reset(self, user_id: str) -> None:
        """Elimina la conversación (ej: tras confirmar pedido)."""
        self._store.pop(user_id, None)
        logger.debug(f"[contexto] Conversación reseteada: user_id={user_id}")

    def reset_all(self) -> None:
        """Limpia todas las conversaciones (útil para tests)."""
        self._store.clear()

    # ── Compatibilidad con código legado ──────────────────────────────────────

    def to_legacy_list(self, user_id: str) -> list:
        """
        Retorna la conversación en el formato legado de main.py:
        [metadata_dict, msg1, msg2, ...]

        Usar solo durante la migración incremental. Remover cuando
        todos los agentes usen la nueva API.
        """
        estado = self._store.get(user_id)
        if estado is None:
            return []
        meta = {
            '_tipo':            estado.tipo,
            '_cliente':         estado.cliente_data,
            '_pedido_guardado': estado.pedido_guardado,
            '_historial':       estado.historial_cliente,
        }
        return [meta] + estado.mensajes

    def from_legacy_list(self, user_id: str, legacy: list) -> None:
        """
        Carga desde el formato legado. Usar solo durante migración.
        """
        if not legacy:
            return
        meta     = legacy[0] if isinstance(legacy[0], dict) and '_tipo' in legacy[0] else {}
        mensajes = [m for m in legacy if '_tipo' not in m]
        tipo     = meta.get('_tipo', 'minorista')
        estado   = ConversacionState(
            tipo=tipo,
            cliente_data=meta.get('_cliente', {}),
            pedido_guardado=meta.get('_pedido_guardado', False),
            historial_cliente=meta.get('_historial', []),
            mensajes=mensajes,
        )
        self._store[user_id] = estado


# Instancia global — importar desde aquí en todos los agentes
conversaciones = ConversacionesStore()
