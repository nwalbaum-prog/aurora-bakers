# CRM SQLite — Migración desde Google Sheets y expansión de módulos por agente

**Fecha:** 2026-04-12
**Proyecto:** aurora-bakers
**Estado:** Aprobado

---

## Objetivo

Reemplazar Google Sheets como fuente de datos primaria por una base de datos SQLite (montada en Railway Volume). El CRM Flask se expande con un módulo completo por agente (ventas, producción, finanzas, leads, agenda, actividad). Sheets queda como espejo de respaldo sincronizado cada hora.

---

## Decisiones de arquitectura

| Decisión | Elección | Razón |
|---|---|---|
| Base de datos | SQLite + Railway Volume | Sin costo extra, misma API que aurora-ventas |
| Relación con Sheets | CRM primario, Sheets espejo | Sheets como backup legible, no fuente de verdad |
| Patrón de acceso a datos | Repositorios por dominio | Interfaz limpia por dominio, fácil testear |
| Módulos CRM | Control total (CRUD + acciones) | El usuario opera todo desde el CRM sin WhatsApp |

---

## 1. Base de datos

### Stack
- SQLAlchemy (ORM) + SQLite
- Archivo: `aurora_crm.db` en Railway Volume (`/data/aurora_crm.db`)
- Variable de entorno: `DB_PATH` (default: `./aurora_crm.db` para dev local)

### Modelos (`db/models.py`)

```
Pedido            → reemplaza hoja PEDIDOS
PedidoMayorista   → reemplaza hoja PEDIDOS_MAYORISTAS
Cliente           → reemplaza hoja CLIENTES
Ingreso           → reemplaza hoja INGRESOS
Gasto             → reemplaza hoja GASTOS
ItemProduccion    → reemplaza hoja PLAN_PRODUCCION
MemoriaEpisodica  → reemplaza hoja MEMORIA
Conocimiento      → reemplaza hoja CONOCIMIENTO
EventoAgenda      → reemplaza hoja AGENDA
Tarea             → reemplaza hoja TAREAS
Lead              → reemplaza hoja LEADS
LeadInteraccion   → reemplaza hoja LEAD_INTERACCIONES
ActividadAgente   → NUEVA: log de actividad por agente
MetaDB            → NUEVA: control de migraciones y estado
```

### Modelo ActividadAgente (nuevo)
Columnas: `id`, `timestamp`, `agente`, `tipo_accion`, `detalle` (texto), `resultado` (`ok`/`error`/`warning`), `duracion_ms`

Propósito: base de la sección de reportería — cada agente registra una fila por acción ejecutada.

### Inicialización (`db/init_db.py`)
1. Crear tablas si no existen (idempotente)
2. Consultar `MetaDB` si la migración inicial fue ejecutada
3. Si no: importar datos desde Sheets tabla por tabla (one-time migration)
4. Marcar migración como completa en `MetaDB`

Se llama al arranque de `main.py` antes de levantar Flask.

---

## 2. Capa de repositorios

Carpeta `db/repos/` — un archivo por dominio. Los repos son la única forma en que los agentes acceden a datos. Nunca acceden a SQLAlchemy directamente.

### Archivos

| Archivo | Funciones principales |
|---|---|
| `db/session.py` | `get_session()` — sesión SQLAlchemy, gestión de conexión |
| `db/repos/pedidos_repo.py` | `get_pedidos()`, `crear_pedido()`, `pedidos_por_fecha()`, `pedidos_por_cliente()` |
| `db/repos/clientes_repo.py` | `get_clientes()`, `upsert_cliente()`, `cliente_por_telefono()`, `clientes_inactivos()` |
| `db/repos/produccion_repo.py` | `get_plan_fecha()`, `agregar_item_plan()`, `editar_item_plan()`, `eliminar_item_plan()`, `calcular_ingredientes()` |
| `db/repos/finanzas_repo.py` | `get_ingresos()`, `registrar_ingreso()`, `get_gastos()`, `registrar_gasto()`, `get_margen_mes()` |
| `db/repos/memoria_repo.py` | `guardar_episodio()`, `get_episodios_agente()`, `get_contexto_memoria()` — reemplaza `memoria/episodica.py` |
| `db/repos/leads_repo.py` | `get_pipeline()`, `mover_lead()`, `get_leads_seguimiento()`, `registrar_interaccion()` |
| `db/repos/agenda_repo.py` | `get_tareas()`, `crear_tarea()`, `completar_tarea()`, `get_agenda_fecha()` |
| `db/repos/actividad_repo.py` | `registrar_actividad()`, `get_actividad_agente()`, `get_actividad_reciente()`, `get_metricas_agente()` |

### Cambio en agentes (ejemplo)
```python
# Antes
from tools.sheets import get_records_cached
plan = get_records_cached(config.SHEET_PLAN_PRODUCCION)

# Después
from db.repos.produccion_repo import get_plan_fecha
plan = get_plan_fecha(fecha_str)
```

Cada agente agrega al final de cada acción significativa:
```python
from db.repos.actividad_repo import registrar_actividad
registrar_actividad(agente='produccion', tipo_accion='generar_plan', detalle=fecha_str, resultado='ok')
```

---

## 3. Módulos CRM

### Estructura de templates

```
templates/crm/
├── base.html                       # Layout con sidebar: Dashboard|Ventas|Producción|Finanzas|Leads|Agenda|Agentes
├── dashboard.html                  # KPIs globales + actividad reciente de todos los agentes
│
├── ventas/
│   ├── index.html                  # Lista pedidos + KPIs hoy/semana/mes
│   ├── pedido_form.html            # Crear/editar pedido minorista
│   ├── mayoristas.html             # Lista pedidos mayoristas
│   └── clientes.html               # CRUD clientes con historial de compras
│
├── produccion/
│   ├── index.html                  # Plan semanal en vista de tabla por fecha
│   ├── plan_form.html              # Agregar/editar ítem al plan (fecha, código producto, cantidad)
│   └── ingredientes.html           # Ingredientes calculados automáticamente para una fecha
│
├── finanzas/
│   ├── index.html                  # Dashboard: ingresos, gastos, margen mes actual con gráficos Chart.js
│   ├── gasto_form.html             # Registrar gasto (descripción, monto, categoría, fecha)
│   └── reporte.html                # Reporte mensual detallado
│
├── leads/                          # Módulo existente (migrado a subcarpeta)
│   ├── dashboard.html
│   ├── index.html
│   ├── lead_detail.html
│   ├── prospecting.html
│   └── reporte.html
│
├── agenda/
│   ├── index.html                  # Tareas del día + próximas + completadas recientes
│   └── tarea_form.html             # Crear/editar tarea
│
└── agentes/
    ├── index.html                  # Timeline de actividad todos los agentes + métricas globales
    └── detalle.html                # Actividad de un agente específico, métricas, acciones manuales
```

### Rutas Flask (nuevas en `main.py`)

```
GET  /crm/                          → dashboard global
GET  /crm/ventas/                   → lista pedidos
POST /crm/ventas/nuevo              → crear pedido
GET  /crm/ventas/clientes           → lista clientes
GET  /crm/ventas/mayoristas         → pedidos mayoristas
GET  /crm/produccion/               → plan semanal
POST /crm/produccion/nuevo          → agregar ítem plan
GET  /crm/produccion/ingredientes   → cálculo ingredientes por fecha
GET  /crm/finanzas/                 → dashboard financiero
POST /crm/finanzas/gasto            → registrar gasto
GET  /crm/finanzas/reporte          → reporte mensual
GET  /crm/leads/                    → pipeline (rutas existentes migradas)
GET  /crm/agenda/                   → tareas y agenda
POST /crm/agenda/nueva-tarea        → crear tarea
GET  /crm/agentes/                  → actividad global agentes
GET  /crm/agentes/<nombre>          → detalle agente específico
POST /crm/agentes/<nombre>/ejecutar → disparar acción manual del agente
```

### Módulo Agentes — reportería
- Timeline cronológico de acciones (filtrable por agente, fecha, tipo, resultado)
- Métricas por agente: acciones hoy / semana / total, tasa de éxito (ok/total)
- Acciones manuales por agente:
  - Producción: "Enviar plan de mañana"
  - Finanzas: "Generar reporte mensual"
  - CRM: "Ejecutar seguimientos pendientes"
  - Prospector: "Iniciar búsqueda de leads"
  - Analista: "Identificar clientes inactivos"

---

## 4. Sincronización Sheets

### `tools/sheets_sync.py`

Exporta CRM → Sheets. Sincronización unidireccional. Sheets nunca sobreescribe el CRM.

```python
def sync_all() -> dict:
    """Exporta todas las tablas a sus hojas correspondientes. Retorna resumen."""

def sync_tabla(nombre_tabla: str) -> int:
    """Exporta una tabla específica. Retorna cantidad de filas escritas."""
```

### Endpoint cron
```
GET /cron/sync-sheets?token=<CRON_SECRET>
```
- Ejecuta `sync_all()`
- Registra resultado en `ActividadAgente` (agente='sync', tipo='sheets_export')
- Responde JSON: `{tablas: N, filas: N, errores: []}`

### Frecuencia
Railway Cron: `0 * * * *` (cada hora en punto)

### Comportamiento si Sheets no está disponible
- Log warning, no exception
- El sistema CRM sigue operando sin interrupciones
- Próximo cron intentará de nuevo

---

## 5. Archivos afectados

### Nuevos
```
db/__init__.py
db/models.py
db/session.py
db/init_db.py
db/repos/__init__.py
db/repos/pedidos_repo.py
db/repos/clientes_repo.py
db/repos/produccion_repo.py
db/repos/finanzas_repo.py
db/repos/memoria_repo.py
db/repos/leads_repo.py
db/repos/agenda_repo.py
db/repos/actividad_repo.py
tools/sheets_sync.py
templates/crm/base.html (reescrito)
templates/crm/dashboard.html (reescrito)
templates/crm/ventas/ (4 templates nuevos)
templates/crm/produccion/ (3 templates nuevos)
templates/crm/finanzas/ (3 templates nuevos)
templates/crm/leads/ (migración de templates existentes)
templates/crm/agenda/ (2 templates nuevos)
templates/crm/agentes/ (2 templates nuevos)
```

### Modificados
```
main.py               → init_db al arranque + rutas CRM nuevas + ruta cron sync
agents/sophie.py      → imports → db/repos/pedidos_repo + actividad_repo
agents/produccion.py  → imports → db/repos/produccion_repo + actividad_repo
agents/finanzas.py    → imports → db/repos/finanzas_repo + actividad_repo
agents/analista.py    → imports → db/repos/clientes_repo + actividad_repo
agents/crm.py         → imports → db/repos/leads_repo + actividad_repo
agents/agenda.py      → imports → db/repos/agenda_repo + actividad_repo
agents/prospector.py  → imports → db/repos/leads_repo + actividad_repo
memoria/episodica.py  → delegar a db/repos/memoria_repo (backward compat wrapper)
config.py             → agregar DB_PATH
requirements.txt      → agregar SQLAlchemy
```

---

## 6. Orden de implementación

1. `db/models.py` + `db/session.py` — definir todos los modelos
2. `db/init_db.py` — inicialización y migración one-time desde Sheets
3. `db/repos/` — todos los repositorios
4. Actualizar agentes (cambiar imports)
5. `tools/sheets_sync.py` + endpoint cron
6. Templates CRM nuevos + rutas Flask
7. Validar en local → deploy Railway con Volume

---

## Criterios de éxito

- Todos los agentes leen y escriben desde SQLite, no desde Sheets
- El CRM muestra módulo completo para cada agente con CRUD funcional
- La sección `/crm/agentes/` muestra actividad y métricas de cada agente
- El plan de producción se crea y edita desde `/crm/produccion/`
- Sheets recibe una exportación completa cada hora sin errores
- Si Sheets falla, el sistema sigue operando sin interrupción
