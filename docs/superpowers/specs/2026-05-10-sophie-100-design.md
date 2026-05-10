# Sophie 100% funcional — Diseño técnico
**Fecha:** 2026-05-10  
**Proyecto:** aurora-bakers  
**Alcance:** Hacer a Sophie completamente funcional: infraestructura estable, conversaciones naturales, gestión de pedidos minorista y mayorista, acceso a aurora-ventas, tareas delegadas por el dueño.

---

## 1. Problema actual

| Capa | Problema | Impacto |
|------|----------|---------|
| Infraestructura | Tunnel trycloudflare — URL cambia en cada reinicio | Sophie no puede responder mensajes |
| Infraestructura | LID cache en RAM | Mensajes de usuarios multi-device ignorados tras redeploy |
| Infraestructura | Conversation state en RAM | Pedidos en curso se pierden con cada redeploy |
| Conversación | Sin saludo inteligente, sin multi-turno robusto | Experiencia robótica |
| Conversación | Token parser frágil (solo línea 1) | Pedidos no se procesan si Claude varía el formato |
| Conversación | Sin order tracking | "¿cuándo llega mi pedido?" no tiene respuesta |
| Conversación | Mayorista sin flujo de confirmación | Pedidos mayoristas incompletos |
| Datos | Sophie no consulta aurora-ventas al conversar | Sin personalización, sin stock real |
| Tareas | Sin delegación dueño → Sophie | No se puede asignar tareas a Sophie por WhatsApp |

---

## 2. Arquitectura objetivo

```
WhatsApp ← → Evolution API (Docker local, puerto 8081)
                    ↕  Cloudflare Named Tunnel
              evolution.panypasta.cl  (URL fija permanente)
                    ↕  webhook POST /webhook/evolution
              Railway — aurora-bakers (main.py)
                    ↕  HTTP + X-Agent-Key
              aurora-ventas (Flask local, 127.0.0.1:5000)
                    ↕
              SQLite aurora.db
```

---

## 3. Infraestructura

### 3.1 Cloudflare Named Tunnel

**Setup único (una vez):**
```bash
cloudflared tunnel login
cloudflared tunnel create aurora-evolution
# → genera tunnel-id UUID
cloudflared tunnel route dns aurora-evolution evolution.panypasta.cl
```

**Archivo de configuración** `~/.cloudflared/config.yml`:
```yaml
tunnel: <tunnel-id>
credentials-file: ~/.cloudflared/<tunnel-id>.json
ingress:
  - hostname: evolution.panypasta.cl
    service: http://localhost:8081
  - service: http_status:404
```

**Instalar como servicio Windows** (arranca automático con Windows):
```powershell
cloudflared service install
```

**Variable Railway a actualizar (una sola vez):**
```
EVOLUTION_API_URL = https://evolution.panypasta.cl
```

### 3.2 LID cache persistente

**Nueva tabla en aurora-ventas (aurora.db):**
```sql
CREATE TABLE IF NOT EXISTS whatsapp_lid_cache (
    lid        TEXT PRIMARY KEY,
    telefono   TEXT NOT NULL,
    push_name  TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
```

**Nuevos endpoints en aurora-ventas:**
```
GET  /api/agentes/lid-cache          → retorna {lid: telefono, ...}
POST /api/agentes/lid-cache          → body: {lid: telefono, ...}  (upsert)
```

**En main.py (Railway):** Al arrancar, carga `_lid_cache` desde este endpoint. Al recibir `contacts.update`, persiste nuevos mapeos.

### 3.3 Conversation state persistente

**Nueva tabla en aurora-ventas:**
```sql
CREATE TABLE IF NOT EXISTS whatsapp_conversaciones (
    telefono     TEXT PRIMARY KEY,
    tipo         TEXT NOT NULL,          -- minorista / mayorista
    mensajes_json TEXT NOT NULL DEFAULT '[]',
    cliente_data_json TEXT DEFAULT '{}',
    pedido_guardado INTEGER DEFAULT 0,
    updated_at   TEXT DEFAULT (datetime('now'))
);
```

TTL: Al leer, si `updated_at` tiene más de 6 horas → se considera conversación nueva.

**Nuevos endpoints en aurora-ventas:**
```
GET  /api/agentes/conversaciones/{telefono}   → estado actual
POST /api/agentes/conversaciones/{telefono}   → guardar/actualizar
DELETE /api/agentes/conversaciones/{telefono} → reset
```

**`ConversacionesStore`** agrega métodos `_load_from_db` y `_save_to_db` que se llaman transparentemente. La interfaz pública no cambia.

---

## 4. Sophie reimaginada

### 4.1 Personalidad

Sophie es cálida, directa y un poco informal. Como la persona del mesón de una panadería artesanal que conoce a sus clientes. No usa frases de call center. Emojis con moderación.

**Reglas de tono:**
- Nunca "¡Claro que sí!" ni "Con gusto le ayudo"
- Respuestas cortas (máximo 3 párrafos, preferir 1-2)
- Usar el nombre del cliente cuando se sabe
- Sugerir alternativas cuando algo no hay, no simplemente decir "no tenemos"

### 4.2 Nuevo system prompt de Sophie

```python
SOPHIE_SYSTEM = """Eres Sophie, la cara de Aurora Bakers (panypasta.cl) en WhatsApp.
Aurora Bakers es una panadería artesanal de Santiago que hace panes de masa madre.

Tu personalidad: cálida, directa, un poco informal. Como el mesón de una panadería artesanal.
Nunca suenas a call center. Usas emojis con moderación (máximo 1-2 por mensaje).
Formato WhatsApp: negrita con *, sin markdown. Máximo 3 párrafos, preferir menos.

CONTEXTO DEL CLIENTE:
{contexto_cliente}

CATÁLOGO ACTUAL:
{catalogo}

DÍAS DE DESPACHO: martes, miércoles, jueves, viernes y sábado.
COMUNAS CON DESPACHO: {comunas}

REGLAS:
- CLIENTES PARTICULARES: guía el pedido multi-turno, confirma antes de registrar.
- CLIENTES MAYORISTAS (RUT, volumen, restaurante/café): usa precios mayoristas, solicita empresa+RUT.
- Detecta mayorista por: menciona RUT, pide >10 unidades, dice restaurante/café/local/empresa.
- NUNCA inventes precios. Si algo no hay, sugiere alternativa.
- Si preguntan por un pedido ya hecho, consulta el historial del contexto del cliente.

FLUJO DE PEDIDO:
1. Recibe qué quieren y para qué día
2. Confirma con resumen ANTES de registrar: "¿Quedamos así? [resumen]"
3. Solo cuando el cliente confirma con sí/ok/dale → emite el token
4. Tras confirmar: mensaje amigable de cierre

TOKENS ESTRUCTURADOS (emitir en línea separada, solo tras confirmación explícita):
PEDIDO_CONFIRMADO|nombre|[{{"producto":"X","cantidad":1,"precio":N}}]|total|dia|tipo_entrega|telefono
PEDIDO_MAYORISTA|empresa|rut|[{{"producto":"X","cantidad":1,"precio":N}}]|total|dia
GENERAR_LINK|nombre_producto|dia_entrega

{memoria}
"""
```

### 4.3 Contexto del cliente (nuevo)

Antes de cada conversación Sophie recibe un bloque `contexto_cliente` con:
```
Cliente: María González (recurrente, 3 pedidos)
Último pedido: 2026-04-28 — 1x Hogaza Campesina, martes
Pedidos pendientes: ninguno
Segmento: CLIENTE (minorista)
```

Esto se obtiene de aurora-ventas (`/api/agentes/pedidos-cliente?telefono=...`).

### 4.4 Token parser robusto

```python
import re

TOKEN_RE = re.compile(
    r'^(PEDIDO_CONFIRMADO|PEDIDO_MAYORISTA|GENERAR_LINK)\|.+',
    re.MULTILINE
)

def _extraer_token(respuesta: str) -> tuple[str, str]:
    """Retorna (token_line, texto_limpio). token_line puede ser ''."""
    match = TOKEN_RE.search(respuesta)
    if not match:
        return '', respuesta
    token_line = match.group(0)
    texto_limpio = respuesta[:match.start()] + respuesta[match.end():]
    return token_line, texto_limpio.strip()
```

### 4.5 Fallback de catálogo

```python
def get_catalogo_texto() -> str:
    """Catálogo desde Jumpseller con fallback a config.RECETAS."""
    try:
        productos = get_productos_jumpseller()
        if productos:
            return _formatear_catalogo_jumpseller(productos)
    except Exception:
        pass
    # Fallback: catálogo hardcodeado
    return _catalogo_desde_config()
```

### 4.6 Order tracking

Nuevo endpoint en aurora-ventas:
```
GET /api/agentes/pedidos-cliente?telefono=56912345678
→ {
    "pedidos": [
      {
        "id": 123,
        "estado": "en_preparacion",
        "fecha_entrega": "2026-05-14",
        "items": "1x Hogaza Campesina, 2x Pan Molde Integral",
        "total": 18900
      }
    ],
    "cliente": {"nombre": "María", "segmento": "CLIENTE", "total_pedidos": 4}
  }
```

Sophie detecta preguntas de seguimiento ("¿cuándo llega?", "¿está listo?", "¿ya salió?") y consulta este endpoint.

---

## 5. Acceso a aurora-ventas durante conversaciones

Sophie consulta aurora-ventas en dos momentos:

**Al inicio de cada conversación (nuevo):**
```python
contexto_cliente = _get_contexto_cliente(telefono)
# → historial, último pedido, segmento
```

**Durante la conversación, bajo demanda:**
- Si el cliente pregunta por estado de pedido → `/api/agentes/pedidos-cliente`
- Si Sophie necesita verificar stock → `/api/agentes/inventario`
- Si Sophie necesita confirmar días de despacho → `/api/agentes/config`

Todos los calls tienen timeout de 3 segundos y fallback silencioso para no bloquear la conversación.

---

## 6. Sistema de tareas delegadas

### 6.1 Flujo de delegación

```
Nico → WhatsApp → Orquestador
                      ↓ clasifica como DELEGAR_SOPHIE
                      ↓ llama a _crear_tarea_sophie(mensaje)
                      ↓ Claude extrae: destinatario, texto, fecha, condición
                      ↓ POST /api/agentes/agenda  (tipo='sophie_tarea')
                      ↓ Orquestador confirma a Nico: "Listo, Sophie lo hace el martes a las 10 🗒️"

Sophie task runner (cada 30 min + al arrancar):
    GET /api/agentes/agenda?tipo=sophie_tarea&pendiente=1
    → filtra tareas vencidas
    → ejecuta cada una (send_whatsapp_safe)
    → marca como completada
```

### 6.2 Tipos de tarea soportados

| Subtipo | Ejemplo del dueño | Acción de Sophie |
|---------|-------------------|-----------------|
| `mensaje_programado` | "Sophie, mañana a las 10 escríbele a Juan" | Envía WhatsApp en fecha/hora indicada |
| `notificacion_despacho` | "Sophie, avísale a María que sale mañana" | Envía notificación con info del pedido |
| `seguimiento_condicional` | "Sophie, si no responde en 2 días escríbele de nuevo" | Revisa si hubo respuesta; si no, envía follow-up |
| `recordatorio_dueno` | "Sophie, recuérdame comprar harina el viernes" | Crea tarea en agenda normal del dueño |

### 6.3 Columnas nuevas en tabla `agenda` (aurora-ventas)

```sql
ALTER TABLE agenda ADD COLUMN tipo_agente TEXT DEFAULT NULL;
-- 'sophie_tarea' marca tareas para Sophie
ALTER TABLE agenda ADD COLUMN telefono_destino TEXT DEFAULT NULL;
ALTER TABLE agenda ADD COLUMN payload_json TEXT DEFAULT NULL;
-- {"subtipo": "mensaje_programado", "mensaje": "...", "condicion": null}
ALTER TABLE agenda ADD COLUMN ejecutado_en TEXT DEFAULT NULL;
```

### 6.4 Nueva intención en Orquestador

```python
INTENCIONES_VALIDAS = {
    ...
    'DELEGAR_SOPHIE': 'asignar tarea a Sophie, mensajes programados a clientes, notificaciones de despacho, seguimientos',
}
```

`_crear_tarea_sophie(mensaje)`: Llama a Claude con un prompt específico para extraer del mensaje del dueño:
- `telefono_destino` (busca en aurora-ventas por nombre si se menciona)
- `mensaje_a_enviar` (redactado por Sophie en tono apropiado)
- `ejecutar_en` (fecha/hora — convierte lenguaje natural a ISO)
- `condicion` (null, o "si_no_respondio")

### 6.5 Sophie task runner

```python
# agents/sophie.py — función nueva
def ejecutar_tareas_pendientes() -> int:
    tareas = _get_tareas_sophie_pendientes()
    ejecutadas = 0
    for tarea in tareas:
        payload = json.loads(tarea.get('payload_json') or '{}')
        subtipo = payload.get('subtipo', 'mensaje_programado')
        if subtipo == 'seguimiento_condicional':
            if _hubo_respuesta_reciente(tarea['telefono_destino']):
                _marcar_completada(tarea['id'], 'cancelada_respondio')
                continue
        ok = send_whatsapp_safe(tarea['telefono_destino'], payload.get('mensaje', ''))
        _marcar_completada(tarea['id'], 'ok' if ok else 'error')
        if ok:
            ejecutadas += 1
    return ejecutadas
```

Nuevo cron en `cron.py`:
```python
_scheduler.add_job(
    lambda: __import__('agents.sophie', fromlist=['ejecutar_tareas_pendientes']).ejecutar_tareas_pendientes(),
    CronTrigger(minute='*/30', timezone=TZ),
    id='sophie_tareas',
    replace_existing=True,
)
```

---

## 7. Cambios por archivo

| Archivo | Cambio |
|---------|--------|
| `cloudflared` config | Nuevo: Named Tunnel `evolution.panypasta.cl` |
| `aurora-ventas/app.py` | +endpoints LID cache, conversaciones, pedidos-cliente; +migraciones tablas |
| `aurora-ventas/app.py` | +cols `tipo_agente`, `telefono_destino`, `payload_json`, `ejecutado_en` en agenda |
| `main.py` | LID cache carga desde DB al arrancar; persiste `contacts.update` |
| `memoria/contexto.py` | `ConversacionesStore` persiste en aurora-ventas DB con TTL 6h |
| `agents/sophie.py` | Nuevo prompt, `_get_contexto_cliente`, token parser regex, fallback catálogo, `ejecutar_tareas_pendientes` |
| `agents/orquestador.py` | Nueva intención `DELEGAR_SOPHIE`, helper `_crear_tarea_sophie` |
| `cron.py` | Nuevo job `sophie_tareas` cada 30 min |
| `config.py` | `MODEL = 'claude-sonnet-4-6'` |
| `tools/ventas_api.py` | Nuevas funciones: `get_pedidos_cliente`, `get_lid_cache`, `save_lid_cache`, `get_conversacion`, `save_conversacion` |

---

## 8. Orden de implementación

1. **Infraestructura primero** — Cloudflare Named Tunnel (sin esto, nada funciona en producción)
2. **Aurora-ventas API** — tablas + endpoints nuevos (LID cache, conversaciones, pedidos-cliente, agenda cols)
3. **Railway main.py** — LID cache persistente
4. **ConversacionesStore** — persistencia en DB
5. **Sophie core** — nuevo prompt, contexto cliente, token parser, fallback catálogo
6. **Order tracking** — Sophie consulta pedidos por teléfono
7. **Tareas delegadas** — Orquestador + Sophie task runner
8. **Deploy y validación** — test end-to-end con número real

---

## 9. Criterios de éxito

- [ ] URL del tunnel fija; Railway nunca necesita actualizar `EVOLUTION_API_URL`
- [ ] Redeploy de Railway no interrumpe conversaciones activas ni pierde LID cache
- [ ] Cliente recurrente recibe saludo personalizado con historial
- [ ] Pedido minorista completo (multi-turno, confirmación, cierre) sin errores de token
- [ ] Pedido mayorista completo con confirmación explícita
- [ ] Cliente puede preguntar estado de pedido y recibir respuesta real
- [ ] Dueño puede delegar mensaje programado a Sophie via WhatsApp y se ejecuta en la fecha indicada
- [ ] Sophie sugiere alternativa cuando un producto no está disponible
