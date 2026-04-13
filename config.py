"""
config.py — Todas las constantes de Aurora Bakers extraídas de main.py
"""
import os

# ── Modelo Claude ────────────────────────────────────────────────────────────
MODEL = 'claude-sonnet-4-20250514'  # NO CAMBIAR

# ── Entorno ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY     = os.environ.get('ANTHROPIC_API_KEY', '')
GOOGLE_SHEET_ID       = os.environ.get('GOOGLE_SHEET_ID', '1P9lOcepVdrTGBlUO3pUPenWjY_KidnD3lgFGxhUn4zw')
GOOGLE_SA_JSON        = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '')

JUMPSELLER_LOGIN      = os.environ.get('JUMPSELLER_LOGIN', '')
JUMPSELLER_AUTH_TOKEN = os.environ.get('JUMPSELLER_AUTH_TOKEN', '')

# ── CRM / Prospecting ────────────────────────────────────────────────────────
GOOGLE_PLACES_API_KEY = os.environ.get('GOOGLE_PLACES_API_KEY', '')  # optional
SERPAPI_KEY           = os.environ.get('SERPAPI_KEY', '')             # optional

META_VERIFY_TOKEN     = os.environ.get('META_VERIFY_TOKEN', 'aurora_bakers_2024')
META_PAGE_ACCESS_TOKEN = os.environ.get('META_PAGE_ACCESS_TOKEN', '')
WHATSAPP_PHONE_NUMBER_ID = os.environ.get('WHATSAPP_PHONE_NUMBER_ID', '')

OWNER_PHONE  = os.environ.get('OWNER_PHONE', '56994891724')
DANIEL_PHONE = os.environ.get('DANIEL_PHONE', '56994891724')
OWNER_EMAIL  = os.environ.get('OWNER_EMAIL', 'nwalbaum@gmail.com')
SMTP_USER    = os.environ.get('SMTP_USER', 'nwalbaum@panypasta.cl')
SMTP_PASS    = os.environ.get('SMTP_PASS', '')

CRON_SECRET  = os.environ.get('CRON_SECRET', 'aurora_cron_2024')

# ── Aurora Ventas (fuente única de datos) ────────────────────────────────────
VENTAS_API_URL = os.environ.get('VENTAS_API_URL', 'http://127.0.0.1:5000')
VENTAS_API_KEY = os.environ.get('VENTAS_API_KEY', 'aurora_agent_2024')

# ── Hojas CRM ────────────────────────────────────────────────────────────────
SHEET_LEADS              = 'LEADS'
SHEET_LEAD_INTERACCIONES = 'LEAD_INTERACCIONES'

# ── Pipeline estados (orden lógico) ──────────────────────────────────────────
PIPELINE_ESTADOS = [
    'DESCUBIERTO',   # encontrado, sin contactar
    'CONTACTADO',    # primer mensaje enviado
    'RESPONDIO',     # respondió el contacto
    'INTERESADO',    # expresó interés
    'PROPUESTA',     # propuesta/precio enviado
    'CLIENTE',       # convertido
    'PERDIDO',       # no interesado o sin respuesta
]

# Días sin actividad para considerar follow-up necesario
FOLLOWUP_DIAS = {
    'CONTACTADO': 3,
    'RESPONDIO':  1,
    'INTERESADO': 2,
    'PROPUESTA':  4,
}

# Tipos de negocio objetivo para prospección
TIPOS_NEGOCIO_OBJETIVO = [
    'restaurante', 'café', 'cafetería', 'hotel', 'hostal',
    'oficina', 'cowork', 'catering', 'deli', 'bistró',
    'panadería complementaria', 'tienda gourmet',
]

# Comunas de Santiago con mayor densidad de negocios objetivo
COMUNAS_PROSPECTING = [
    'Providencia', 'Las Condes', 'Vitacura', 'Ñuñoa',
    'Santiago Centro', 'Recoleta', 'Barrio Italia',
    'San Miguel', 'La Reina', 'Miraflores',
]

# ── WhatsApp ─────────────────────────────────────────────────────────────────
WA_MAX_CHARS = 4000

# ── Memoria ──────────────────────────────────────────────────────────────────
MEMORIA_MAX_FILAS   = 10_000
MEMORIA_ROTAR_DESDE = 8_000
CONV_TRIM_MAX       = 20   # mensajes máximos en conversación activa

# ── Hojas Google Sheets ───────────────────────────────────────────────────────
SHEET_PEDIDOS            = 'PEDIDOS'
SHEET_PEDIDOS_MAYORISTAS = 'PEDIDOS_MAYORISTAS'
SHEET_CLIENTES           = 'CLIENTES'
SHEET_INGRESOS           = 'INGRESOS'
SHEET_COSTOS             = 'COSTOS'
SHEET_GASTOS             = 'GASTOS'
SHEET_PLAN_PRODUCCION    = 'PLAN_PRODUCCION'
SHEET_MEMORIA            = 'MEMORIA'
SHEET_CONOCIMIENTO       = 'CONOCIMIENTO'  # nueva hoja Fase 1
SHEET_AGENDA             = 'AGENDA'
SHEET_TAREAS             = 'TAREAS'

# ── Costos de ingredientes (CLP por unidad) ──────────────────────────────────
COSTOS_INGREDIENTES = {
    'HC':    465,    # Hogaza Campesina
    'HCN':   1336,   # Hogaza Campesina Nueces
    'HI':    627,    # Hogaza Integral
    'HIM':   795,    # Hogaza Integral Multisemilla
    'PMB':   506,    # Pan Molde Blanco
    'PMI':   667,    # Pan Molde Integral
    'PMIM':  836,    # Pan Molde Integral Multisemilla
    'CIA':   184,    # Ciabatta
}

# ── Costos fijos mensuales (CLP) ─────────────────────────────────────────────
COSTOS_FIJOS_MENSUALES = {
    'Luz':             56_516,
    'Gas':             47_831,
    'Arriendo':       350_000,
    'Internet':        19_990,
    'Seguro':          15_000,
    'Mantención':      20_000,
    'Contabilidad':    50_000,
    'Packaging':       30_000,
}

# ── Recetas (ingredientes y parámetros de producción) ────────────────────────
RECETAS = {
    'HC': {
        'nombre':       'Hogaza Campesina',
        'harina_base':  500,
        'ingredientes': {
            'harina_blanca':    500,
            'agua':             375,
            'sal':              10,
            'masa_madre':       100,
        },
        'tiempo_total_hrs': 22,
        'rendimiento_g':    700,
    },
    'HCN': {
        'nombre':       'Hogaza Campesina con Nueces',
        'harina_base':  500,
        'ingredientes': {
            'harina_blanca':    500,
            'agua':             375,
            'sal':              10,
            'masa_madre':       100,
            'nueces':           150,
        },
        'tiempo_total_hrs': 22,
        'rendimiento_g':    750,
    },
    'HI': {
        'nombre':       'Hogaza Integral',
        'harina_base':  500,
        'ingredientes': {
            'harina_integral':  500,
            'agua':             400,
            'sal':              10,
            'masa_madre':       100,
        },
        'tiempo_total_hrs': 24,
        'rendimiento_g':    700,
    },
    'HIM': {
        'nombre':       'Hogaza Integral Multisemilla',
        'harina_base':  500,
        'ingredientes': {
            'harina_integral':  500,
            'agua':             400,
            'sal':              10,
            'masa_madre':       100,
            'semillas_mix':     80,
        },
        'tiempo_total_hrs': 24,
        'rendimiento_g':    750,
    },
    'PMB': {
        'nombre':       'Pan Molde Blanco',
        'harina_base':  500,
        'ingredientes': {
            'harina_blanca':    500,
            'agua':             350,
            'sal':              10,
            'masa_madre':       100,
            'aceite':           30,
        },
        'tiempo_total_hrs': 20,
        'rendimiento_g':    600,
    },
    'PMI': {
        'nombre':       'Pan Molde Integral',
        'harina_base':  500,
        'ingredientes': {
            'harina_integral':  500,
            'agua':             370,
            'sal':              10,
            'masa_madre':       100,
            'aceite':           30,
        },
        'tiempo_total_hrs': 20,
        'rendimiento_g':    600,
    },
    'PMIM': {
        'nombre':       'Pan Molde Integral Multisemilla',
        'harina_base':  500,
        'ingredientes': {
            'harina_integral':  500,
            'agua':             370,
            'sal':              10,
            'masa_madre':       100,
            'aceite':           30,
            'semillas_mix':     60,
        },
        'tiempo_total_hrs': 20,
        'rendimiento_g':    600,
    },
    'CIA': {
        'nombre':       'Ciabatta',
        'harina_base':  100,
        'ingredientes': {
            'harina_blanca':    100,
            'agua':             80,
            'sal':              2,
            'masa_madre':       20,
        },
        'tiempo_total_hrs': 18,
        'rendimiento_g':    120,
    },
}

# ── Precios mayoristas ────────────────────────────────────────────────────────
PRECIOS_MAYORISTAS = {
    'ciabatta':               {'precio': 2_400,  'formato': '6 unidades'},
    'hogaza_campesina':       {'precio': 6_500,  'formato': '1 unidad'},
    'hogaza_integral':        {'precio': 6_500,  'formato': '1 unidad'},
    'pan_molde_blanco':       {'precio': 5_800,  'formato': '1 unidad'},
    'pan_molde_integral':     {'precio': 5_800,  'formato': '1 unidad'},
    'combo_mixto':            {'precio': 24_000, 'formato': '4 hogazas'},
}

# ── Días de despacho ──────────────────────────────────────────────────────────
DIAS_DESPACHO = ['martes', 'miércoles', 'jueves', 'viernes', 'sábado']

# ── Comunas con despacho disponible ──────────────────────────────────────────
COMUNAS_DESPACHO = [
    'Providencia', 'Ñuñoa', 'Santiago Centro', 'Recoleta',
    'Independencia', 'Las Condes', 'Vitacura', 'La Reina',
    'Macul', 'San Miguel',
]
