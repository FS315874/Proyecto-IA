# Historial de errores y mantenimiento de v0.17

Revisión: 2026-09-14. Implementado con asistencia de IA dentro de v0.17.0, sin
agregar dependencias, capacidades de control ni permisos. No cierra POLISH-08.

## Para usarlo

Abrir **Historial de errores** en la cabecera de la GUI. Se muestran primero los
incidentes sin revisar, con fecha UTC, clasificación preliminar, etapa y código.
Seleccionar uno muestra un resumen y una comprobación sugerida, escritos por el
proyecto; ninguna IA analiza ni envía automáticamente estos datos.

- **Actualizar** vuelve a leer el archivo local.
- **Marcar seleccionado como revisado** sólo cambia una marca. No borra el incidente
  ni significa que esté corregido. **Incluir revisados** permite verlo de nuevo.
- Un fallo posterior del mismo tipo crea otro incidente sin revisar.
- Se conservan los últimos **500 incidentes**, incluidos revisados y no revisados.
  Al superar ese límite salen los más antiguos; no es un archivo histórico ilimitado.
- Si no se puede guardar un incidente, la cabecera avisa cuántos quedaron sin guardar
  durante esa sesión. Una recuperación posterior no elimina ese aviso. No hay cola
  de reintentos ni recuperación retroactiva de esos registros.

Para retomar el desarrollo, desde la raíz del repositorio:

```powershell
python -m desktop_agent --errors
python -m desktop_agent --errors --all
```

El primero devuelve JSON con pendientes; el segundo incluye revisados. Son consultas
de sólo lectura: no crean el historial si falta, no preparan el navegador, no cargan
configuración de proveedores ni usan la API. Salida `0`: consulta correcta (puede estar
vacía); `1`: historial inaccesible/dañado; `2`: argumentos incorrectos.

La clasificación no prueba la causa: `entorno` puede esconder un defecto de selección
de dispositivo; `contenido` puede resultar de un DOM que cambió. Antes de descartar
un fallo, reproducirlo y reunir evidencia. Tampoco inferir que la aplicación funciona
porque el historial esté vacío: podría no haberse probado o haber fallado el guardado.

## Datos, privacidad y límites

SQLite de la biblioteca estándar guarda
`%LOCALAPPDATA%\DesktopAgent\error_history.sqlite3`. Si no existe una base absoluta
válida de LocalAppData, se usa `~/.desktop_agent/error_history.sqlite3`.

Cada incidente contiene únicamente:

| Campo | Contenido |
| --- | --- |
| `id`, `occurred_at` | Identificador local y fecha UTC |
| `version`, `session` | Versión de la app e identificador aleatorio de ejecución |
| `code`, `source`, `stage`, `tool` | Valores de catálogos cerrados, nunca texto libre |
| `duration_ms` | Duración finita y acotada, cuando se midió |
| `reviewed` | Marca de revisión humana |

No guarda órdenes, consultas musicales, transcripciones, audio, screenshots, rutas
personales, URLs, credenciales, respuestas de modelos, mensajes de excepción ni
tracebacks. Los títulos y sugerencias del informe se derivan del código estático.
Se validan también las filas leídas: una fila desconocida no se exporta como texto
libre. El archivo es local, no cifrado; aplican los permisos de la cuenta de Windows.
No subirlo a Git ni enviarlo a terceros por defecto.

La captura cubre interpretación fallida, validación/ejecución de herramientas,
recorridos de YouTube, parada del controlador, voz fallida, errores de preferencia de
navegador y ciertos fallos de inicio/eventos de Tk. Éxitos, cancelaciones normales y
transcripciones que sólo requieren revisión no se consideran errores. No pretende
capturar toda excepción de cada demo experimental, fallos previos a configurar el
logger, cierres abruptos del proceso ni problemas que el usuario perciba pero la
aplicación no pueda observar (por ejemplo parlantes físicamente inaudibles).

El log operativo pasa a `%LOCALAPPDATA%\DesktopAgent\agent.log` (mismo fallback), con
rotación de 2 MB y dos respaldos. Conserva eventos de éxito/cancelación/confirmación y
metadata segura; no se importa como historial. Los `logs/agent.log` antiguos del
repositorio **no se borran ni se migran**. Un `configure_logging(log_file=...)`
explícito coloca la base de errores junto a ese archivo para pruebas/integraciones;
la CLI consulta siempre la ubicación predeterminada.

## Decisiones de implementación

- `diagnostics.py` define códigos y sanitiza valores; `emit_failure` anota un evento
  de logging. `ErrorHistoryHandler` sólo persiste esa anotación, nunca el mensaje
  completo del logger. El núcleo no depende de Tk ni de proveedores.
- El ejecutor registra el fallo de la herramienta una vez; el procesador no lo
  vuelve a registrar. Una operación fallida y su limpieza fallida son dos incidentes
  distintos porque requieren comprobaciones distintas.
- Cada escritura usa una transacción y una conexión breve, cerrada explícitamente.
  La espera por bloqueo de SQLite se limita a 200 ms. La retención es parte de la
  misma transacción. Un fallo del historial no convierte una acción fallida en éxito.
- Esquema `user_version=1`; una base dañada o de versión futura se rechaza sin
  borrarla o recrearla. La GUI informa el problema; no repara datos por su cuenta.
- El diálogo es único por ventana principal y refresca al abrirlo. Las lecturas son
  locales; no hay monitor remoto, telemetría ni correcciones autónomas.

## Defectos corregidos con regresiones

1. Un fallo al guardar la preferencia de navegador se perdía en el siguiente refresco
   y los controles podían mostrar una opción no aplicada. Ahora se conserva el error
   y se restaura la última elección válida; un guardado exitoso retira el aviso.
2. Un `stop()` fallido descartaba la referencia a la sesión. El segundo intento podía
   decir que no había reproducción y aparentar éxito. Ahora se retienen los recursos,
   se bloquea otra reproducción y un intento explícito sólo reintenta los cierres
   fallidos. El adaptador no permite reutilizar recursos parcialmente cerrados.
3. La extensión marcaba su recurso como cerrado antes de confirmar la pausa. Ahora
   exige `paused is True` en la respuesta y sólo entonces lo marca cerrado.
4. Al agotarse la espera de búsqueda, la falta del DOM esperado se devolvía como cero
   resultados. Ahora es `search_dom_unavailable`, distinto de un marcador explícito
   de búsqueda vacía. **No se cambiaron selectores ni se ampliaron timeouts.**
5. Se perdían categorías del error al cruzar el puente. Se preservan códigos conocidos
   (timeout, consentimiento, contenido, reproducción); los desconocidos se redactan.
   El worker usa una lista cerrada en lugar de aceptar cualquier texto en minúsculas.
6. «Pausá lo que estaba sonando en YouTube» dependía del proveedor externo y podía
   fallar antes de ejecutar. Ahora las formas acotadas de pausa de YouTube tienen un
   camino local; las demás variantes naturales conservan el intérprete general.
7. Después de detener o fallar un recorrido, el controlador olvidaba el adaptador y
   no podía pausar un video reanudado manualmente en su pestaña gestionada. Una
   detención explícita ahora consulta esa pestaña si la preferencia sigue activa. No
   abre un navegador cerrado ni usa una pestaña arbitraria.
8. Una búsqueda fallida intentaba detener una página sin elemento de video; el worker
   lo trataba como fallo y bloqueaba reproducciones posteriores. En una pestaña de
   YouTube gestionada, ausencia de reproductor ahora significa que no queda medio que
   pausar. La pestaña se conserva y la limpieza es idempotente.
9. «Poné pausa al video que estoy mirando» y «reproducí lo que estaba mirando»
   podían entrar como consultas de búsqueda. Ahora buscar contenido,
   `STOP_YOUTUBE` y `RESUME_YOUTUBE` son acciones estructuradas diferentes. Las
   frases inequívocas comunes tienen un camino local rápido y el proveedor conserva
   el mismo contrato para otras formulaciones. Reanudar exige comprobar el mismo
   `/watch`, audio habilitado y avance temporal; no basta con pulsar play.

Si se perdió la pestaña gestionada o el puente, una pausa sigue sin confirmarse. No se
informa éxito ni se toma otra pestaña. No repetir en bucle: comprobar la conexión y
el estado antes de volver a detener o reiniciar la app. La recuperación automática de
una pestaña desaparecida queda fuera de este mantenimiento.

## Validación registrada

El 2026-09-14, Python 3.13 en Windows y Node 18.18.0:

- **489 tests Python aprobados**, sin omitidos, en la suite completa.
- **13 tests JS aprobados** sobre el worker real con un navegador ficticio.
- Tk real con ventanas ocultas: apertura/revisión del historial, corrupción, aviso de
  incidentes perdidos y preferencia restaurada. No es una aprobación visual personal.
- Persistencia tras reinicio, retención, escritores concurrentes, esquema futuro,
  privacidad frente a entradas desconocidas, CLI de sólo lectura y rotación de logs.
- Recuperación tras parada fallida, cierre parcial, pausa de una pestaña gestionada
  reanudada manualmente, limpieza sin reproductor y ausencia de pausa observable.
- Separación entre búsqueda, pausa y reanudación; reanudación del video gestionado
  sin consulta nueva y verificación de identidad, audio y progreso.
- No hubo llamadas reales a modelos, captura de micrófono ni cambios de presupuesto.

Los tests se ejecutan con:

```powershell
python -m unittest discover -s tests -q
node --test tests/browser_extension.test.cjs
git diff --check
```

El runner `polish17_qa_check` selecciona un subconjunto que crece con los módulos; no
sumar sus tests otra vez al total. La auditoría inicial de v0.17 (439 Python/9 JS) se
conserva como evidencia histórica en `V0.17_ARCHITECTURE.md`.

### Prueba real: corrección pendiente de retest

El 2026-09-12 una prueba con el puente conectado falló con `browser_no_results` en
`select_first_result`, aproximadamente 4 s. El 2026-09-13 se reprodujo con el nuevo
Python en unos 3,59 s; el runner **no llegó a la segunda canción**, y la detención
tampoco fue confirmada por el puente. Estos incidentes permanecen sin revisar.

La observación posterior de la pestaña de prueba mostró **19 enlaces compatibles con
el selector actual**, sin marcador de búsqueda vacía, y un elemento de video pausado
sin fuente cargada. Esto no identifica la causa durante la búsqueda: quedan por
distinguir carga transitoria, copia cargada del worker y sesión realmente controlada.
No atribuirlo a micrófono, cuota, contenido inexistente o cambio de selector sin prueba.

El 2026-09-14 el usuario confirmó transcripción y aperturas habituales, pero informó
que YouTube volvió a fallar y que una orden de pausa no detuvo un video reanudado a
mano. El historial mostró `browser_timeout`, `browser_stop_failed` y un
`provider_error`. Los defectos de limpieza y pausa descritos arriba quedaron
corregidos en código y simulación; la reproducción real aún requiere recargar la
extensión y repetir una prueba corta. No se declara resuelta antes de esa observación.

En la prueba siguiente, el recorrido base funcionó, pero «poné pausa al video que
estoy mirando en YouTube» abrió una búsqueda sobre pausas y «reproducí lo que estaba
mirando en YouTube» buscó ese texto. La transcripción no era la causa: faltaba separar
el control de reanudación de `PLAY_YOUTUBE`. La corrección está automatizada, pero su
aceptación real permanece pendiente.

El control de navegador de Codex bloqueó la página de administración de extensiones.
No se usaron rutas alternativas para eludirlo. Falta que el usuario recargue **Desktop
Agent Browser Bridge** en Chrome y verifique la copia instalada; editar el repositorio
no garantiza que Chrome esté ejecutando ese worker.

Hay un runner opt-in, sin cargar claves/settings de IA ni modificar preferencias:

```powershell
python -m scripts.browser17_manual_check
# Sólo después de autorizar las dos reproducciones y la pausa final:
python -m scripts.browser17_manual_check --run
```

Sin `--run` no abre nada. Con `--run` usa la integración nativa del proyecto, exige
Chrome conectado, recorre dos búsquedas fijas y verifica pausa final. Una consulta
que falle detiene la prueba; no cambia de navegador ni reproduce alternativas.
Preservación de la misma pestaña y sonido por los auriculares requieren observación
humana. El acceso de Codex a Chrome no es la integración nativa de Desktop Agent.

## Checklist corto para la próxima prueba personal

1. Recargar la extensión instalada y abrir de nuevo la app con el código actualizado.
2. Abrir **Historial de errores**, comprobar que persiste entre aperturas. No marcar
   como resuelto un incidente sólo para dejar la lista vacía.
3. Comprobar elección de Chrome + pestaña gestionada; probar una canción por texto.
   Si falla, conservar el estado, anotar código/etapa y examinar la página antes de
   volver a ejecutar. No probar muchas variantes a ciegas.
4. Sólo cuando pase: decir «Poné pausa al video que estoy mirando en YouTube» y
   luego «Reproducí lo que estaba mirando en YouTube». Debe pausar y reanudar el
   mismo video sin buscar esas frases ni cerrar la pestaña. Después probar una segunda
   canción, misma pestaña y sonido.
5. Con el usuario presente, retomar voz, cancelación/atajos y aperturas de POLISH-08.
   Las llamadas pagas y el micrófono mantienen sus permisos específicos.
