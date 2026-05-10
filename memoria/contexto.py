"""
memoria/contexto.py — ConversacionesStore con persistencia en aurora-ventas.

Estado en RAM + respaldo en SQLite via HTTP.
TTL de 6 horas: conversaciones inactivas más de 6h se consideran nuevas.
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal
import config

logger = logging.getLogger(__name__)

TipoConversacion = Literal['minorista', 'mayorista', 'orquestador']


@dataclass
class ConversacionState:
    tipo:            TipoConversacion
    cliente_data:    dict = field(default_factory=dict)
    pedido_guardado: bool = False
    mensajes:        list = field(default_factory=list)
    historial_cliente: list = field(default_factory=list)


class ConversacionesStore:
    TTL_HORAS = 6

    def __init__(self):
        self._store: dict[str, ConversacionState] = {}

    # ── Acceso ────────────────────────────────────────────────────────────────

    def get(self, user_id: str) -> ConversacionState | None:
        if user_id in self._store:
            return self._store[user_id]
        return self._load_from_db(user_id)

    def get_or_create(
        self,
        user_id: str,
        tipo: TipoConversacion,
        cliente_data: dict | None = None,
    ) -> ConversacionState:
        estado = self.get(user_id)
        if estado is None:
            estado = ConversacionState(tipo=tipo, cliente_data=cliente_data or {})
            self._store[user_id] = estado
            logger.debug(f"[contexto] Nueva conversación: {user_id} tipo={tipo}")
        return estado

    def existe(self, user_id: str) -> bool:
        return self.get(user_id) is not None

    # ── Mensajes ──────────────────────────────────────────────────────────────

    def get_mensajes(self, user_id: str) -> list:
        estado = self._store.get(user_id)
        return estado.mensajes if estado else []

    def append_mensaje(self, user_id: str, role: str, content: str) -> None:
        estado = self._store.get(user_id)
        if estado is None:
            logger.warning(f"[contexto] append_mensaje: {user_id} no existe")
            return
        estado.mensajes.append({"role": role, "content": content})
        self._trim(user_id)
        self._save_to_db(user_id)

    def _trim(self, user_id: str) -> None:
        estado = self._store.get(user_id)
        if estado and len(estado.mensajes) > config.CONV_TRIM_MAX:
            exceso = len(estado.mensajes) - config.CONV_TRIM_MAX
            estado.mensajes = estado.mensajes[exceso:]

    # ── Estado ────────────────────────────────────────────────────────────────

    def marcar_pedido_guardado(self, user_id: str) -> None:
        estado = self._store.get(user_id)
        if estado:
            estado.pedido_guardado = True
            self._save_to_db(user_id)

    def actualizar_cliente(self, user_id: str, datos: dict) -> None:
        estado = self._store.get(user_id)
        if estado:
            estado.cliente_data.update(datos)
            self._save_to_db(user_id)

    def reset(self, user_id: str) -> None:
        self._store.pop(user_id, None)
        try:
            from tools.ventas_api import delete_conversacion
            delete_conversacion(user_id)
        except Exception:
            pass
        logger.debug(f"[contexto] Conversación reseteada: {user_id}")

    def reset_all(self) -> None:
        self._store.clear()

    # ── Persistencia ──────────────────────────────────────────────────────────

    def _load_from_db(self, user_id: str) -> ConversacionState | None:
        try:
            from tools.ventas_api import get_conversacion
            data = get_conversacion(user_id)
            if not data:
                return None
            updated = data.get('updated_at', '')
            if updated:
                dt = datetime.fromisoformat(updated)
                if datetime.now() - dt > timedelta(hours=self.TTL_HORAS):
                    return None
            estado = ConversacionState(
                tipo=data.get('tipo', 'minorista'),
                mensajes=json.loads(data.get('mensajes_json', '[]')),
                cliente_data=json.loads(data.get('cliente_data_json', '{}')),
                pedido_guardado=bool(data.get('pedido_guardado', 0)),
            )
            self._store[user_id] = estado
            logger.debug(f"[contexto] Conversación cargada desde DB: {user_id}")
            return estado
        except Exception as e:
            logger.debug(f"[contexto] No se pudo cargar conversación de DB: {e}")
            return None

    def _save_to_db(self, user_id: str) -> None:
        estado = self._store.get(user_id)
        if not estado:
            return
        try:
            from tools.ventas_api import save_conversacion
            save_conversacion(user_id, {
                'tipo': estado.tipo,
                'mensajes_json': json.dumps(estado.mensajes, ensure_ascii=False),
                'cliente_data_json': json.dumps(estado.cliente_data, ensure_ascii=False),
                'pedido_guardado': int(estado.pedido_guardado),
            })
        except Exception:
            pass  # RAM state is still valid

    # ── Compatibilidad legado ──────────────────────────────────────────────────

    def to_legacy_list(self, user_id: str) -> list:
        estado = self._store.get(user_id)
        if not estado:
            return []
        meta = {
            '_tipo': estado.tipo,
            '_cliente': estado.cliente_data,
            '_pedido_guardado': estado.pedido_guardado,
            '_historial': estado.historial_cliente,
        }
        return [meta] + estado.mensajes

    def from_legacy_list(self, user_id: str, legacy: list) -> None:
        if not legacy:
            return
        meta = legacy[0] if isinstance(legacy[0], dict) and '_tipo' in legacy[0] else {}
        mensajes = [m for m in legacy if '_tipo' not in m]
        estado = ConversacionState(
            tipo=meta.get('_tipo', 'minorista'),
            cliente_data=meta.get('_cliente', {}),
            pedido_guardado=meta.get('_pedido_guardado', False),
            historial_cliente=meta.get('_historial', []),
            mensajes=mensajes,
        )
        self._store[user_id] = estado


conversaciones = ConversacionesStore()
