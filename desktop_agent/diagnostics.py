"""Vocabulario local de diagnóstico: nunca recibe órdenes ni excepciones crudas."""
import logging
from dataclasses import dataclass


@dataclass(frozen=True)
class DiagnosticDefinition:
    category: str
    summary: str
    next_check: str


DEFINITIONS = {
    "unsupported": DiagnosticDefinition("capacidad", "Pedido sin herramienta disponible", "Revisar capacidad y opt-in de IA; no asumir un defecto de transcripción."),
    "provider_error": DiagnosticDefinition("por_investigar", "Falló la interpretación externa", "Comprobar conexión, credencial y cuota; el estado no identifica la causa."),
    "invalid_proposal": DiagnosticDefinition("integracion", "Propuesta externa rechazada por validación", "Reproducir el contrato con proveedor ficticio; no relajar validación."),
    "budget_exceeded": DiagnosticDefinition("presupuesto", "Tope local alcanzado", "Consultar el contador; no aumentar el tope ni borrar reservas automáticamente."),
    "usage_tracking_error": DiagnosticDefinition("entorno", "No se pudo verificar el consumo", "Revisar acceso e integridad del libro sin mostrar ni modificar datos privados."),
    "action_rejected": DiagnosticDefinition("seguridad", "Acción o autorización rechazada", "Comprobar contrato y permisos; no saltarse el ejecutor."),
    "tool_failed": DiagnosticDefinition("por_investigar", "La herramienta no confirmó el resultado", "Reproducir el destino acordado; no inferir éxito por ausencia de excepción."),
    "tool_exception": DiagnosticDefinition("revisar_producto", "Excepción al ejecutar una herramienta", "Crear una reproducción acotada; podría ser integración o entorno."),
    "invalid_tool_result": DiagnosticDefinition("revisar_producto", "La herramienta incumplió el contrato de resultado", "Revisar herramienta y test de regresión."),
    "controller_internal": DiagnosticDefinition("revisar_producto", "Error interno del controlador", "Reproducir con dependencias ficticias y comprobar cancelación."),
    "browser_disconnected": DiagnosticDefinition("entorno", "Extensión no conectada al navegador elegido", "Comprobar navegador, host nativo y extensión; no usar otro perfil como fallback."),
    "browser_failed": DiagnosticDefinition("por_investigar", "Recorrido de navegador incompleto", "Revisar la etapa registrada y reproducir sin cambiar permisos."),
    "browser_stop_failed": DiagnosticDefinition("integracion", "No se confirmó la detención del navegador", "Comprobar manualmente si sigue reproduciendo y revisar la sesión."),
    "browser_preferences": DiagnosticDefinition("entorno", "No se pudo leer o guardar la preferencia", "Comprobar archivo y permisos; conservar la última preferencia válida."),
    "browser_timeout": DiagnosticDefinition("integracion", "El navegador agotó el tiempo del paso", "Medir carga y conexión; no ampliar tiempos sin evidencia."),
    "browser_consent_required": DiagnosticDefinition("entorno", "El sitio requiere intervención del usuario", "Dejar al usuario resolver consentimiento; no automatizar permisos."),
    "browser_no_results": DiagnosticDefinition("contenido", "No se encontraron resultados", "Comprobar búsqueda y estructura visible; podría haber cambiado el DOM."),
    "browser_content_unavailable": DiagnosticDefinition("contenido", "Contenido no disponible", "Comprobar restricciones del video sin intentar eludirlas."),
    "browser_dom_unavailable": DiagnosticDefinition("integracion", "La página no cumple el DOM esperado", "Distinguir carga incompleta, intersticial y cambio de selectores."),
    "browser_playback_not_confirmed": DiagnosticDefinition("integracion", "Reproducción no confirmada", "Comprobar avance, pausa y silencio de video/pestaña; luego salida física."),
    "browser_backend_failure": DiagnosticDefinition("integracion", "Falló el backend del navegador", "Revisar extensión/Playwright y reproducir la etapa."),
    "browser_invalid_input": DiagnosticDefinition("seguridad", "Entrada del navegador rechazada", "Revisar el contrato sin aceptar URLs o selectores arbitrarios."),
    "browser_invalid_state": DiagnosticDefinition("revisar_producto", "Estado de navegador incompatible", "Revisar orden de pasos y ciclo de vida de sesión."),
    "spotify_configuration": DiagnosticDefinition("configuracion", "Spotify no está configurado", "Revisar Client ID, activación y Redirect URI sin copiar credenciales al historial."),
    "spotify_authorization": DiagnosticDefinition("configuracion", "Autorización de Spotify incompleta", "Completar o repetir el consentimiento desde una orden explícita."),
    "spotify_authentication": DiagnosticDefinition("configuracion", "Spotify rechazó la sesión guardada", "Desconectar y volver a autorizar la cuenta; no registrar tokens."),
    "spotify_network": DiagnosticDefinition("entorno", "No se pudo conectar con Spotify", "Comprobar la conexión antes de reintentar."),
    "spotify_permission": DiagnosticDefinition("proveedor", "Spotify rechazó la operación", "Comprobar Premium, permisos concedidos y restricciones del dispositivo."),
    "spotify_no_device": DiagnosticDefinition("entorno", "No hay un reproductor Spotify disponible", "Abrir Spotify en la computadora y comprobar el dispositivo configurado."),
    "spotify_rate_limit": DiagnosticDefinition("proveedor", "Spotify limitó temporalmente las órdenes", "Esperar antes de reintentar; no hacer reintentos automáticos."),
    "spotify_service": DiagnosticDefinition("proveedor", "Spotify no completó la operación", "Comprobar el servicio y repetir una sola orden acotada."),
    "spotify_invalid_response": DiagnosticDefinition("integracion", "Respuesta de Spotify inválida", "Reproducir el contrato con transporte ficticio antes de cambiar la validación."),
    "spotify_invalid_input": DiagnosticDefinition("seguridad", "Entrada de Spotify rechazada", "Corregir el intent o argumento estructurado sin enviar el valor rechazado."),
    "spotify_no_results": DiagnosticDefinition("contenido", "Spotify no encontró contenido", "Probar nombre y artista o el nombre completo de la playlist."),
    "spotify_ambiguous": DiagnosticDefinition("contenido", "Más de un destino Spotify coincide", "Configurar el dispositivo o decir el nombre completo de la playlist."),
    "spotify_not_confirmed": DiagnosticDefinition("integracion", "Spotify no confirmó el estado final", "Leer reproducción y dispositivo; no asumir éxito por una respuesta 204."),
    "app_missing": DiagnosticDefinition("entorno", "Aplicación no encontrada", "Comprobar instalación y ruta del catálogo; no ejecutar coincidencias desconocidas."),
    "app_launch_denied": DiagnosticDefinition("entorno", "Windows rechazó el lanzamiento", "Revisar instalación manualmente; no elevar permisos automáticamente."),
    "app_launch_failed": DiagnosticDefinition("por_investigar", "No se pudo iniciar la aplicación", "Comprobar ejecutable, dependencias y permisos."),
    "app_unverified": DiagnosticDefinition("integracion", "Proceso de aplicación no confirmado", "Distinguir launcher activo, login, actualización y proceso final."),
    "target_rejected": DiagnosticDefinition("seguridad", "Destino local rechazado", "Revisar el nombre y tipo sin abrir rutas no aprobadas."),
    "target_not_registered": DiagnosticDefinition("configuracion", "Proyecto o documento no registrado", "Abrir Mis proyectos, registrar el destino y repetir su nombre."),
    "target_ambiguous": DiagnosticDefinition("configuracion", "Más de un destino aprobado coincide", "Usar el nombre completo o quitar la coincidencia innecesaria."),
    "target_unavailable": DiagnosticDefinition("entorno", "El destino aprobado ya no está disponible", "Comprobar si fue movido o eliminado y registrarlo de nuevo."),
    "target_changed": DiagnosticDefinition("seguridad", "La ruta aprobada cambió", "Registrar nuevamente el destino antes de abrirlo."),
    "target_catalog_invalid": DiagnosticDefinition("revisar_producto", "El catálogo de proyectos no se pudo validar", "Revisar integridad y permisos sin reemplazar el archivo automáticamente."),
    "audio_unavailable": DiagnosticDefinition("entorno", "Control de salidas de audio no disponible", "Comprobar que la aplicación se ejecuta en Windows con Core Audio activo."),
    "audio_backend_failure": DiagnosticDefinition("integracion", "Windows Core Audio no completó la operación", "Enumerar salidas y repetir una lectura antes de cambiar el backend."),
    "audio_invalid_input": DiagnosticDefinition("seguridad", "Destino o porcentaje de audio rechazado", "Usar una salida activa y un entero de 0 a 80."),
    "audio_device_missing": DiagnosticDefinition("entorno", "No se encontró la salida de audio pedida", "Listar salidas activas y usar parte inequívoca de su nombre."),
    "audio_device_ambiguous": DiagnosticDefinition("configuracion", "Más de una salida de audio coincide", "Usar un nombre más completo; no elegir una salida al azar."),
    "audio_volume_not_confirmed": DiagnosticDefinition("integracion", "Windows no confirmó el volumen final", "Leer nuevamente el endpoint; no asumir éxito por la escritura."),
    "voice_no_speech": DiagnosticDefinition("entorno", "Captura sin voz detectada", "Comprobar medidor y micrófono físico, especialmente con mezcladores virtuales."),
    "voice_unavailable": DiagnosticDefinition("entorno", "Captura de voz no disponible", "Comprobar dispositivo y backend; no cambiar micrófono silenciosamente."),
    "voice_failed": DiagnosticDefinition("por_investigar", "Fallo de voz sin causa identificada", "Separar captura de transcripción usando las duraciones de la app."),
    "voice_provider_setup": DiagnosticDefinition("entorno", "No se pudo preparar la transcripción", "Comprobar instalación y configuración local."),
    "voice_authentication": DiagnosticDefinition("configuracion", "Credencial de transcripción rechazada", "Revisar la clave desde Configuración, sin copiarla a logs o al chat."),
    "voice_permission": DiagnosticDefinition("configuracion", "Permiso de transcripción denegado", "Revisar acceso al proyecto/modelo de API."),
    "voice_model_unavailable": DiagnosticDefinition("configuracion", "Modelo de transcripción no disponible", "Verificar acceso y contrato; no cambiar de modelo sin evaluar costo."),
    "voice_quota_or_rate_limit": DiagnosticDefinition("proveedor", "Cuota o límite externo de transcripción", "Distinguir créditos de API de cuota de Codex y tope local."),
    "voice_request_rejected": DiagnosticDefinition("integracion", "Solicitud de transcripción rechazada", "Revisar formato y límites con una prueba ficticia."),
    "voice_timeout": DiagnosticDefinition("proveedor", "Transcripción agotó el tiempo", "Comprobar red y duración; una petición enviada puede haber consumido."),
    "voice_network": DiagnosticDefinition("entorno", "Fallo de red al transcribir", "Comprobar conexión sin repetir automáticamente una llamada paga."),
    "voice_service_unavailable": DiagnosticDefinition("proveedor", "Servicio de transcripción no disponible", "Comprobar disponibilidad antes de un nuevo intento autorizado."),
    "voice_invalid_response": DiagnosticDefinition("integracion", "Respuesta de transcripción inválida", "Revisar contrato con proveedor ficticio."),
    "voice_unknown": DiagnosticDefinition("por_investigar", "Error externo de voz no clasificado", "Reproducir minimizando datos; no persistir la excepción original."),
    "gui_callback": DiagnosticDefinition("revisar_producto", "Falló un evento de interfaz", "Reproducir la interacción y agregar una regresión Tk."),
    "startup_failed": DiagnosticDefinition("por_investigar", "No se pudo iniciar la interfaz", "Comprobar runtime, configuración e instancia previa."),
}
SOURCES = frozenset({"interpretation", "executor", "controller", "voice", "gui", "startup"})
STAGES = frozenset({"interpreting", "validating", "executing", "capture", "transcription", "settings", "callback", "startup", "open_site", "search", "select_first_result", "start_playback", "read_playback", "verify_playback", "reset", "close", "stop", "spotify_authorization", "spotify_request", "spotify_search", "spotify_device", "spotify_playback", "spotify_verify", "target_resolution", "target_open", "audio_device", "audio_backend", "audio_verify"})
TOOLS = frozenset({
    "open_url",
    "open_application",
    "play_youtube",
    "stop_youtube",
    "resume_youtube",
    "play_spotify_track",
    "play_spotify_playlist",
    "search_spotify_track",
    "pause_spotify",
    "resume_spotify",
    "next_spotify",
    "previous_spotify",
    "set_spotify_volume",
    "open_approved_target",
    "set_output_volume",
    "navigate_browser",
})


def safe_duration(value: object) -> float | None:
    # Comparar antes de convertir evita overflow con enteros externos enormes.
    # El intervalo también descarta NaN e infinitos.
    if type(value) not in (int, float) or not 0 <= value <= 86_400_000:
        return None
    return round(float(value), 3)


def emit_failure(logger: logging.Logger, code: str, source: str, *,
                 stage: str | None = None, tool: str | None = None,
                 duration_ms: float | None = None) -> None:
    # Sólo estos campos cerrados llegan al historial, nunca message/args/traceback.
    fields = {
        "code": code if isinstance(code, str) and code in DEFINITIONS else "tool_failed",
        "source": source if isinstance(source, str) and source in SOURCES else "executor",
        "stage": stage if isinstance(stage, str) and stage in STAGES else None,
        "tool": tool if isinstance(tool, str) and tool in TOOLS else None,
        "duration_ms": safe_duration(duration_ms),
    }
    logger.warning("Diagnostic: code=%s source=%s stage=%s tool=%s",
                   fields["code"], fields["source"], fields["stage"], fields["tool"],
                   extra={"diagnostic": fields})
