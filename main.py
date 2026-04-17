"""
main.py — Aurora Bakers Multi-Agent System (refactorizado)

Backward-compatible con todos los endpoints del sistema anterior.
Añade endpoints nuevos: /health, /memoria/*, /autonomo/*, /debug/*, /crm/*
"""
import os
import json
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from flask import Flask, request, jsonify, Response, render_template, redirect, url_for, flash

import config
from cron import iniciar_scheduler, get_proxima_ejecucion
from memoria.contexto import conversaciones
from agents.sophie import ask_sophie
from agents.orquestador import ask_orquestador
from agents.produccion import (
    generar_mensaje_produccion,
    enviar_plan_produccion,
    get_proximos_dias_produccion,
)
from agents.finanzas import generar_reporte_financiero, registrar_gasto
from agents.agenda import get_agenda_resumen, agregar_tarea, agregar_evento
from agents.analista import clientes_inactivos_minoristas, clientes_inactivos_mayoristas
from agents.crm import (
    get_pipeline, get_metricas_crm, mover_lead, contactar_lead,
    get_leads_para_seguimiento, get_interacciones_lead,
    generar_reporte_semanal, enviar_reporte_semanal,
    ejecutar_seguimientos_automaticos, registrar_respuesta,
)
from agents.prospector import (
    buscar_y_guardar_leads, get_todos_leads, importar_leads_manual,
    calificar_lead, get_leads_por_estado,
)
from tools.whatsapp import send_whatsapp_safe
from tools.email_tools import send_email_safe
from tools.sheets import get_records_cached

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder='templates')
app.secret_key = os.environ.get('SECRET_KEY', 'aurora-bakers-crm-secret')

# Filtro Jinja2 para enumerate
@app.template_filter('enumerate')
def jinja_enumerate(iterable):
    return list(enumerate(iterable))


# ── Health ────────────────────────────────────────────────────────────────────

@app.route('/health')
def health():
    from tools.ventas_api import get_resumen
    ventas_ok = get_resumen() is not None
    return jsonify({
        'status': 'ok',
        'ts': datetime.now().isoformat(),
        'ventas_api': ventas_ok,
        'cron': get_proxima_ejecucion(),
    })


# ── Webhook WhatsApp Evolution API ───────────────────────────────────────────

@app.route('/webhook/evolution', methods=['POST'])
def webhook_evolution():
    """
    Webhook para Evolution API v2.
    Formato del payload:
      { "event": "messages.upsert", "instance": "...",
        "data": { "key": {"remoteJid": "56912345678@s.whatsapp.net", "fromMe": false},
                  "message": {"conversation": "Hola"}, "messageType": "conversation" } }
    """
    try:
        data = request.get_json(silent=True) or {}
        event = data.get('event', '')

        # Solo procesar mensajes entrantes nuevos
        if event not in ('messages.upsert', 'message.upsert', 'messages.set'):
            return jsonify({'status': 'ignored', 'event': event}), 200

        msg_data = data.get('data', {})

        # Algunos webhooks envían lista; tomamos el primero
        if isinstance(msg_data, list):
            msg_data = msg_data[0] if msg_data else {}

        key = msg_data.get('key', {})

        # Ignorar mensajes propios (enviados por el bot)
        if key.get('fromMe', False):
            return jsonify({'status': 'own_message'}), 200

        # Extraer número: "56912345678@s.whatsapp.net" → "56912345678"
        remote_jid = key.get('remoteJid', '')
        from_ = remote_jid.split('@')[0]

        # Ignorar grupos
        if '@g.us' in remote_jid or not from_:
            return jsonify({'status': 'group_ignored'}), 200

        # Extraer texto (distintos tipos de mensaje)
        message_obj = msg_data.get('message', {})
        text = (
            message_obj.get('conversation') or
            message_obj.get('extendedTextMessage', {}).get('text') or
            message_obj.get('buttonsResponseMessage', {}).get('selectedButtonId') or
            message_obj.get('listResponseMessage', {}).get('title') or
            ''
        ).strip()

        if not text:
            logger.info(f"[evolution] Mensaje sin texto de {from_} (tipo: {msg_data.get('messageType', '?')})")
            return jsonify({'status': 'no_text'}), 200

        logger.info(f"[evolution] Mensaje de {from_}: {text[:80]}")

        # Enrutar: dueño → orquestador, clientes → sophie
        if from_ == config.OWNER_PHONE:
            respuesta = ask_orquestador(from_, text)
        else:
            respuesta = ask_sophie(from_, text, canal='whatsapp')

        send_whatsapp_safe(from_, respuesta)
        return jsonify({'status': 'ok'}), 200

    except Exception as e:
        logger.error(f"[evolution] Error en webhook: {e}")
        return jsonify({'error': str(e)}), 500


# ── Estado conexión WhatsApp ──────────────────────────────────────────────────

@app.route('/whatsapp/status')
def whatsapp_status():
    """Estado de la conexión WhatsApp y QR si está desconectado."""
    from tools.whatsapp import get_connection_status, get_qr_code
    estado = get_connection_status()
    resultado = {'estado': estado}
    if estado != 'open':
        resultado['qr'] = get_qr_code()
    return jsonify(resultado)


# ── Webhook Meta (legacy, mantenido para compatibilidad) ──────────────────────

@app.route('/webhook/meta', methods=['GET'])
def webhook_meta_verify():
    """Verificación de webhook Meta (legacy)."""
    mode      = request.args.get('hub.mode')
    token     = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')
    if mode == 'subscribe' and token == config.META_VERIFY_TOKEN:
        return Response(challenge, status=200)
    return Response('Forbidden', status=403)


# ── Webhook Twilio (llamadas) ─────────────────────────────────────────────────

@app.route('/webhook/call', methods=['POST'])
def webhook_call():
    # Mantener compatibilidad — respuesta TwiML básica
    return Response(
        '<?xml version="1.0"?><Response><Say language="es-MX">Aurora Bakers. Por favor envíanos un mensaje de WhatsApp.</Say></Response>',
        content_type='text/xml',
    )


# ── Producción ────────────────────────────────────────────────────────────────

@app.route('/produccion/preview')
def produccion_preview():
    _check_token()
    fecha = request.args.get('fecha') or (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    mensaje = generar_mensaje_produccion(fecha)
    return jsonify({'fecha': fecha, 'plan': mensaje})


@app.route('/produccion/enviar')
def produccion_enviar():
    _check_token()
    fecha = request.args.get('fecha')
    ok = enviar_plan_produccion(fecha)
    return jsonify({'enviado': ok})


# ── Finanzas ──────────────────────────────────────────────────────────────────

@app.route('/finanzas/reporte')
def finanzas_reporte():
    _check_token()
    reporte = generar_reporte_financiero()
    return jsonify({'reporte': reporte})


@app.route('/finanzas/gasto', methods=['POST'])
def finanzas_gasto():
    _check_token()
    body = request.get_json(silent=True) or {}
    ok = registrar_gasto(
        descripcion=body.get('descripcion', ''),
        monto=float(body.get('monto', 0)),
        categoria=body.get('categoria', 'General'),
        fecha=body.get('fecha'),
    )
    return jsonify({'guardado': ok})


# ── Agenda ────────────────────────────────────────────────────────────────────

@app.route('/agenda/hoy')
def agenda_hoy():
    _check_token()
    resumen = get_agenda_resumen()
    return jsonify({'agenda': resumen})


@app.route('/agenda/enviar-diaria')
def agenda_enviar_diaria():
    _check_token()
    resumen = get_agenda_resumen()
    ok = send_whatsapp_safe(config.OWNER_PHONE, resumen)
    return jsonify({'enviado': ok})


# ── Memoria ───────────────────────────────────────────────────────────────────

@app.route('/memoria/episodios')
def memoria_episodios():
    _check_token()
    from memoria.episodica import get_episodios_agente
    agente = request.args.get('agente', 'sophie')
    limit  = int(request.args.get('limit', 10))
    episodios = get_episodios_agente(agente, limit)
    return jsonify({'agente': agente, 'episodios': episodios})


@app.route('/memoria/conocimiento')
def memoria_conocimiento():
    _check_token()
    from memoria.semantica import get_conocimiento
    categoria = request.args.get('categoria')
    hechos = get_conocimiento(categoria)
    return jsonify({'categoria': categoria, 'hechos': hechos})


@app.route('/memoria/actualizar', methods=['POST'])
def memoria_actualizar():
    _check_token()
    from memoria.semantica import actualizar_conocimiento
    body = request.get_json(silent=True) or {}
    ok = actualizar_conocimiento(
        categoria=body.get('categoria', ''),
        concepto=body.get('concepto', ''),
        valor=body.get('valor', ''),
        fuente=body.get('fuente', 'api'),
        confianza=float(body.get('confianza', 0.8)),
    )
    return jsonify({'guardado': ok})


# ── Autónomo ──────────────────────────────────────────────────────────────────

@app.route('/autonomo/reporte-semanal')
def autonomo_reporte_semanal():
    _check_token()
    reporte = generar_reporte_financiero()
    agenda  = get_agenda_resumen()
    mensaje = f"{reporte}\n\n{agenda}"
    ok_wa   = send_whatsapp_safe(config.OWNER_PHONE, mensaje)
    ok_mail = send_email_safe(config.OWNER_EMAIL, 'Reporte semanal Aurora Bakers', mensaje)
    return jsonify({'whatsapp': ok_wa, 'email': ok_mail})


@app.route('/autonomo/plan-produccion-auto')
def autonomo_plan_produccion():
    _check_token()
    fecha = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    ok = enviar_plan_produccion(fecha)
    return jsonify({'enviado': ok, 'fecha': fecha})


@app.route('/autonomo/reactivacion-inteligente')
def autonomo_reactivacion():
    _check_token()
    inactivos_min = clientes_inactivos_minoristas(dias=21)
    inactivos_may = clientes_inactivos_mayoristas(dias=14)
    enviados = 0

    for c in inactivos_min[:5]:
        msg = (
            f"Hola {c['nombre'].split()[0]}! 👋 Es Aurora Bakers. "
            f"Hace {c['dias_inactivo']} días que no te vemos. "
            f"¿Te tienta un pan de masa madre esta semana? 🍞"
        )
        if send_whatsapp_safe(c['telefono'], msg):
            enviados += 1

    for c in inactivos_may[:3]:
        msg = (
            f"Hola {c['nombre']}! 👋 Aurora Bakers acá. "
            f"¿Cómo va el stock? Tenemos disponibilidad para esta semana si necesitan. "
            f"¿Coordinamos un pedido?"
        )
        if send_whatsapp_safe(c['telefono'], msg):
            enviados += 1

    return jsonify({
        'minoristas_inactivos': len(inactivos_min),
        'mayoristas_inactivos': len(inactivos_may),
        'mensajes_enviados':    enviados,
    })


@app.route('/autonomo/sincronizar-gastos')
def autonomo_sincronizar_gastos():
    """Placeholder para sincronización de gastos desde fuentes externas."""
    _check_token()
    return jsonify({'status': 'ok', 'message': 'Sin fuentes externas configuradas aún'})


# ── Debug ─────────────────────────────────────────────────────────────────────

@app.route('/debug/ultima-conversacion')
def debug_ultima_conversacion():
    _check_token()
    user_id = request.args.get('user_id', '')
    estado  = conversaciones.get(user_id)
    if not estado:
        return jsonify({'error': 'user_id no encontrado'}), 404
    return jsonify({
        'user_id':         user_id,
        'tipo':            estado.tipo,
        'pedido_guardado': estado.pedido_guardado,
        'num_mensajes':    len(estado.mensajes),
        'ultimos_3':       estado.mensajes[-3:],
    })


@app.route('/debug/contexto-agente')
def debug_contexto_agente():
    _check_token()
    from memoria.episodica import get_contexto_memoria
    agente  = request.args.get('agente', 'sophie')
    contexto = get_contexto_memoria(agente)
    return jsonify({'agente': agente, 'contexto': contexto})


# ── Cron ──────────────────────────────────────────────────────────────────────

@app.route('/cron/reactivacion')
def cron_reactivacion():
    _check_token()
    # Reusar endpoint autónomo
    return autonomo_reactivacion()


@app.route('/preview/reactivacion')
def preview_reactivacion():
    _check_token()
    inactivos_min = clientes_inactivos_minoristas(dias=21)
    inactivos_may = clientes_inactivos_mayoristas(dias=14)
    return jsonify({
        'minoristas': inactivos_min[:10],
        'mayoristas': inactivos_may[:10],
    })


# ── Test ──────────────────────────────────────────────────────────────────────

@app.route('/test')
def test():
    return jsonify({'status': 'ok', 'agente': 'sophie', 'ts': datetime.now().isoformat()})


@app.route('/test-mayorista')
def test_mayorista():
    respuesta = ask_sophie('test_mayorista', 'Hola, somos Café El Origen, RUT 76.123.456-7. Queremos hacer un pedido mayorista.')
    return jsonify({'respuesta': respuesta})


@app.route('/test-orquestador')
def test_orquestador():
    respuesta = ask_orquestador(config.OWNER_PHONE, '¿Cómo va el negocio esta semana?')
    return jsonify({'respuesta': respuesta})


@app.route('/test-sheets')
def test_sheets():
    try:
        from tools.sheets import get_sheet_client
        sp = get_sheet_client()
        hojas = [w.title for w in sp.worksheets()]
        return jsonify({'status': 'ok', 'hojas': hojas})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/test-dias')
def test_dias():
    return jsonify({'dias_despacho': config.DIAS_DESPACHO})


@app.route('/catalogo')
def catalogo():
    from tools.jumpseller import get_catalogo_texto
    return jsonify({'catalogo': get_catalogo_texto()})


@app.route('/aprendizaje/procesar')
def aprendizaje_procesar():
    """Trigger manual para procesar y guardar aprendizajes del día."""
    _check_token()
    # Placeholder para fase 3: procesamiento nocturno de conversaciones
    return jsonify({'status': 'ok', 'message': 'Aprendizaje automático en Fase 3'})


# ── Helper ────────────────────────────────────────────────────────────────────

def _check_token():
    """Valida el token de cron/admin en query params."""
    token = request.args.get('token', '')
    if token and token != config.CRON_SECRET:
        from flask import abort
        abort(403)


# ════════════════════════════════════════════════════════════════════════════
# CRM Dashboard Routes
# ════════════════════════════════════════════════════════════════════════════

def _actividad_semanal() -> tuple[list, list]:
    """Calcula interacciones por día de la última semana para el gráfico."""
    try:
        interacciones = get_records_cached(config.SHEET_LEAD_INTERACCIONES)
        hoy = datetime.now()
        dias_labels = []
        dias_values = []
        for i in range(6, -1, -1):
            dia = hoy - timedelta(days=i)
            label = dia.strftime('%d/%m')
            dias_labels.append(label)
            count = sum(
                1 for inter in interacciones
                if str(inter.get('Fecha', '')).startswith(dia.strftime('%Y-%m-%d'))
            )
            dias_values.append(count)
        return dias_labels, dias_values
    except Exception:
        return [str(i) for i in range(7)], [0] * 7


@app.route('/crm/')
@app.route('/crm')
def crm_dashboard():
    metricas  = get_metricas_crm()
    pipeline  = get_pipeline()
    followup  = get_leads_para_seguimiento()
    labels, values = _actividad_semanal()
    return render_template(
        'crm/dashboard.html',
        metricas=metricas,
        pipeline=pipeline,
        followup=followup,
        estados=config.PIPELINE_ESTADOS,
        actividad_labels=labels,
        actividad_values=values,
    )


@app.route('/crm/leads')
def crm_leads():
    estado = request.args.get('estado')
    if estado:
        leads = get_leads_por_estado(estado)
    else:
        leads = get_todos_leads()
    return render_template(
        'crm/leads.html',
        leads=leads,
        estados=config.PIPELINE_ESTADOS,
    )


@app.route('/crm/leads/<lead_id>')
def crm_lead_detail(lead_id):
    leads = get_todos_leads()
    lead  = next((l for l in leads if l.get('ID') == lead_id), None)
    if not lead:
        return "Lead no encontrado", 404
    interacciones = get_interacciones_lead(lead_id)
    flash_msg  = request.args.get('msg')
    flash_type = request.args.get('type', 'ok')
    return render_template(
        'crm/lead_detail.html',
        lead=lead,
        interacciones=interacciones,
        pipeline_estados=config.PIPELINE_ESTADOS,
        flash=flash_msg,
        flash_type=flash_type,
    )


@app.route('/crm/leads/<lead_id>/contactar', methods=['POST'])
def crm_contactar_lead(lead_id):
    canal = request.form.get('canal', 'email')
    tipo  = request.form.get('tipo', 'primer_contacto')
    resultado = contactar_lead(lead_id, canal, tipo)
    msg  = f"✓ Mensaje enviado por {canal}" if resultado.get('enviado') else f"✗ Error: {resultado.get('error','envío fallido')}"
    tipo_flash = 'ok' if resultado.get('enviado') else 'error'
    return redirect(f"/crm/leads/{lead_id}?msg={msg}&type={tipo_flash}")


@app.route('/crm/leads/<lead_id>/mover', methods=['POST'])
def crm_mover_lead(lead_id):
    nuevo_estado = request.form.get('estado', '')
    nota = request.form.get('nota', '')
    ok = mover_lead(lead_id, nuevo_estado, nota)
    msg = f"✓ Lead movido a {nuevo_estado}" if ok else "✗ Error moviendo lead"
    return redirect(f"/crm/leads/{lead_id}?msg={msg}")


@app.route('/crm/leads/<lead_id>/nota', methods=['POST'])
def crm_agregar_nota(lead_id):
    nota = request.form.get('nota', '').strip()
    if nota:
        mover_lead(lead_id, '', nota)  # solo actualiza la nota
    return redirect(f"/crm/leads/{lead_id}?msg=✓ Nota guardada")


@app.route('/crm/prospecting', methods=['GET'])
def crm_prospecting():
    resultado = None
    if request.args.get('resultado'):
        import json as _json
        try:
            resultado = _json.loads(request.args.get('resultado', '{}'))
        except Exception:
            pass
    return render_template(
        'crm/prospecting.html',
        resultado=resultado,
        tipos_negocio=config.TIPOS_NEGOCIO_OBJETIVO,
        comunas=config.COMUNAS_PROSPECTING,
        tiene_api=bool(config.GOOGLE_PLACES_API_KEY or config.SERPAPI_KEY),
    )


@app.route('/crm/prospecting/buscar', methods=['POST'])
def crm_prospecting_buscar():
    tipo   = request.form.get('tipo', 'restaurante')
    comuna = request.form.get('comuna', 'Providencia')
    limit  = int(request.form.get('limit', 15))
    resultado = buscar_y_guardar_leads(tipo, comuna, limit)
    import json as _json
    resultado_str = _json.dumps(resultado, ensure_ascii=False)
    return redirect(f"/crm/prospecting?resultado={resultado_str}")


@app.route('/crm/prospecting/masiva', methods=['POST'])
def crm_prospecting_masiva():
    tipo   = request.form.get('tipo', 'restaurante')
    total_nuevos = 0
    for comuna in config.COMUNAS_PROSPECTING:
        r = buscar_y_guardar_leads(tipo, comuna, 10)
        total_nuevos += r.get('nuevos', 0)
    return redirect(f"/crm/prospecting?resultado=" + json.dumps({'buscados': len(config.COMUNAS_PROSPECTING) * 10, 'nuevos': total_nuevos, 'duplicados': 0, 'leads': []}, ensure_ascii=False))


@app.route('/crm/prospecting/importar', methods=['POST'])
def crm_prospecting_importar():
    json_data = request.form.get('json_data', '').strip()
    if json_data:
        try:
            datos = json.loads(json_data)
            resultado = importar_leads_manual(datos if isinstance(datos, list) else [datos])
            return redirect(f"/crm/leads?msg=✓ {resultado['importados']} leads importados")
        except Exception as e:
            return redirect(f"/crm/prospecting?error={e}")
    return redirect('/crm/prospecting')


@app.route('/crm/seguimientos')
def crm_seguimientos():
    followup = get_leads_para_seguimiento()
    return render_template(
        'crm/leads.html',
        leads=followup,
        estados=config.PIPELINE_ESTADOS,
        titulo='Follow-up pendiente',
    )


@app.route('/crm/seguimientos/ejecutar', methods=['POST'])
def crm_ejecutar_seguimientos():
    _check_token()
    limit    = int(request.args.get('limit', 10))
    resultado = ejecutar_seguimientos_automaticos(limit)
    return jsonify(resultado)


@app.route('/crm/reporte')
def crm_reporte():
    metricas = get_metricas_crm()
    reporte  = generar_reporte_semanal()
    hoy      = datetime.now()
    return render_template(
        'crm/reporte.html',
        metricas=metricas,
        reporte=reporte,
        estados=config.PIPELINE_ESTADOS,
        fecha_inicio=(hoy - timedelta(days=7)).strftime('%d/%m/%Y'),
        fecha_fin=hoy.strftime('%d/%m/%Y'),
    )


@app.route('/crm/reporte/enviar', methods=['POST'])
def crm_enviar_reporte():
    ok = enviar_reporte_semanal()
    return redirect(f"/crm/reporte?msg={'✓ Reporte enviado' if ok else '✗ Error enviando'}")


# API JSON para integraciones externas
@app.route('/crm/api/metricas')
def crm_api_metricas():
    _check_token()
    return jsonify(get_metricas_crm())


@app.route('/crm/api/pipeline')
def crm_api_pipeline():
    _check_token()
    pipeline = get_pipeline()
    return jsonify({estado: len(leads) for estado, leads in pipeline.items()})


@app.route('/crm/api/leads', methods=['GET'])
def crm_api_leads():
    _check_token()
    estado = request.args.get('estado')
    leads  = get_leads_por_estado(estado) if estado else get_todos_leads()
    return jsonify(leads)


@app.route('/crm/api/leads/<lead_id>/respuesta', methods=['POST'])
def crm_api_respuesta(lead_id):
    """Endpoint para registrar que un lead respondió (ej: desde webhook de email)."""
    body     = request.get_json(silent=True) or {}
    contenido = body.get('contenido', 'Respondió')
    canal     = body.get('canal', 'email')
    ok = registrar_respuesta(lead_id, contenido, canal)
    return jsonify({'ok': ok})


# Cron semanal: enviar reporte y ejecutar seguimientos
@app.route('/cron/crm-semanal')
def cron_crm_semanal():
    _check_token()
    reporte_ok  = enviar_reporte_semanal()
    followup    = ejecutar_seguimientos_automaticos(limit=20)
    return jsonify({'reporte_enviado': reporte_ok, 'seguimientos': followup})


# ── Cron endpoint manual (trigger desde Railway o admin) ─────────────────────

@app.route('/cron/plan-produccion')
def cron_plan_produccion():
    _check_token()
    from cron import _tarea_plan_produccion
    _tarea_plan_produccion()
    return jsonify({'status': 'ok'})


@app.route('/cron/agenda-diaria')
def cron_agenda_diaria():
    _check_token()
    from cron import _tarea_agenda_diaria
    _tarea_agenda_diaria()
    return jsonify({'status': 'ok'})


@app.route('/cron/reporte-financiero')
def cron_reporte_financiero():
    _check_token()
    from cron import _tarea_reporte_financiero
    _tarea_reporte_financiero()
    return jsonify({'status': 'ok'})


# ── Entry point ───────────────────────────────────────────────────────────────

# Iniciar scheduler al cargar el módulo (compatible con gunicorn --workers 1)
iniciar_scheduler()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
