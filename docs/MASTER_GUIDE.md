# Guía maestra de continuidad — Desktop Agent

Revisión: 2026-09-25. Preparada con asistencia de Astra para continuar con Sol u otro
asistente. Es contexto operativo del proyecto, no entrenamiento del modelo, una
garantía de resultados ni autorización para ejecutar todo el backlog.

## 1. Cómo usar esta guía

Al comenzar una tarea o recuperar contexto, leer `AGENTS.md` y las secciones 1–3 de
esta guía. Consultar el resto según el área afectada. No releer toda la historia en
cada turno. Las instrucciones de seguridad y colaboración de `AGENTS.md` siguen
vigentes; esta guía las complementa, no las reemplaza ni amplía permisos.

La solicitud actual delimita el trabajo. El código y las pruebas demuestran qué
existe; el README explica el uso; la arquitectura de cada versión explica decisiones;
el roadmap registra secuencia y checkpoints. Una casilla histórica `COMPLETADO` no
prueba que el recorrido siga funcionando hoy. Ante contradicción, comprobar y
corregir la documentación afectada, sin reescribir la historia.

### Producto que el usuario quiere

Un asistente de Windows que comprenda pedidos naturales y actúe por texto o voz,
sin obligar al usuario a recordar frases exactas ni hacer todos los clics. A futuro:
activación por tecla, música, aplicaciones/juegos, proyectos/documentos, audio de
Windows, respuestas habladas, pruebas de sus propias apps y acceso desde el celular.
El norte es un intermediario de IA útil, extensible y supervisable; no una colección
cerrada de frases ni un agente con control irrestricto.

La arquitectura híbrida conserva ese rumbo: un atajo determinista resuelve lo
conocido rápidamente; la IA interpreta variantes y propone acciones estructuradas;
el núcleo valida y ejecuta herramientas explícitas. Comprender un pedido nuevo no
crea por sí solo la herramienta necesaria. Explicar esa diferencia al usuario.

No confundir tres sistemas:

- Sol/Astra en Codex: asistentes que ayudan a desarrollar este repositorio.
- Desktop Agent: la aplicación Python que estamos construyendo.
- Proveedores de la aplicación: interpretación y transcripción externas habilitadas
  por opt-in. Cambiar el modelo del chat no cambia esos proveedores.

La aspiración de aprender recorridos exige recetas verificadas, precondiciones,
invalidación cuando cambia la interfaz y aprobación; no memorizar clics ciegamente.
Los módulos experimentales actuales no equivalen a resolver cualquier app desconocida.

## 2. Punto de reanudación

Este bloque es una fotografía, no un estado en vivo. Actualizarlo después de un hito
autorizado; comprobar primero `git status`, versión y archivos afectados.

- Versión local: **0.20.0**, coincidente en `pyproject.toml`, paquete y extensión.
- Rama observada al preparar la guía: `feature/mvp-0.15`; su nombre quedó histórico
  y no indica la versión ejecutable. Verificar rama y remoto antes de publicar.
- Estado: v0.19 quedó aceptada el 2026-09-19 con proyecto y documentación reales.
  v0.20 quedó aceptada el 2026-09-24 con el cambio real de HyperX al 35 %.
  `POLISH-08` de v0.17 conserva voz, cancelación y atajos reales pendientes.
- Validación vigente de v0.20: **119 pruebas específicas, 547 Python y 13 JS
  aprobadas**, enumeración y lectura reales y aceptación personal del cambio físico.
  v0.19 conserva su aceptación visible.
- Mantenimiento autorizado: historial de errores persistente, preferencia efectiva
  visible y recuperación honesta tras parada fallida. **489 tests Python y 13 JS
  aprobados** el 2026-09-14. La auditoría inicial (439/9) es histórica; los runners
  seleccionan subconjuntos, no tests adicionales.
- Al retomar desarrollo, ejecutar primero **`python -m desktop_agent --errors`**:
  consulta sólo lectura, sin navegador/API. Leer `--all` sólo si hace falta revisar
  incidentes anteriores. Categoría preliminar no prueba culpa de entorno/producto.
- La prueba personal del 2026-09-25 aprobó reproducción, pausa y reanudación de
  YouTube en la pestaña gestionada. Las frases de control ya no se convirtieron en
  búsquedas. `PLAY_YOUTUBE`, `STOP_YOUTUBE` y `RESUME_YOUTUBE` quedaron aceptadas
  como acciones separadas. Detalle en [ERROR_HISTORY.md](ERROR_HISTORY.md).
- Durante la prueba de voz del 2026-09-25, «abrí Spotify» resolvió el sitio web y
  produjo dos fallos `open_url`. El catálogo ahora prioriza la aplicación para el
  nombre desnudo y exige «web» o «navegador» para la URL. La corrección aprobó 34
  pruebas directas y 547 Python; falta reiniciar la app y repetir esa frase.
- La prueba real de Spotify reprodujo playlist y canción, pausó, reanudó, avanzó,
  retrocedió y ajustó volumen. Corrigió falsos fallos causados por cuerpos no JSON en
  respuestas exitosas. El usuario ya había confirmado audio audible; el contenido de
  una búsqueda abierta requiere observación visual. Esto no reemplaza los checkpoints
  reales pendientes de voz, cancelación o atajos.
- Datos/clave/consumo quedan fuera del repositorio. No leer sus valores para recuperar
  contexto. La GUI conserva preferencias; la clave OpenAI sólo persiste con Recordar
  opt-in y el refresh token de Spotify siempre queda cifrado con DPAPI.
- Proveedores configurados en código: `gpt-5.6-luna` para interpretar y `gpt-transcribe`
  para transcribir. Tope inicial compartido de la app: **USD 1/mes**, modificable por
  el usuario. No aumentar el tope ni migrar modelos como efecto de esta guía.

### Próximo paso concreto: cerrar POLISH-08

Spotify, proyectos, volumen y el recorrido de YouTube quedan aceptados. El historial
seguro se consultó el 2026-09-25: no mostró un fallo nuevo de navegador; conserva un
`unsupported` sin contenido por privacidad que no puede atribuirse a esa prueba.
Reiniciar y repetir «abrí Spotify» por voz para confirmar la aplicación; comprobar
también que cancelar impida cualquier apertura. No marcar `POLISH-08` completado por
sus pruebas simuladas. Detalles: [arquitectura v0.20](V0.20_ARCHITECTURE.md),
[arquitectura v0.19](V0.19_ARCHITECTURE.md),
[arquitectura v0.18](V0.18_ARCHITECTURE.md) e
[historial/checklist](ERROR_HISTORY.md).

## 3. Contrato de trabajo por pedido

1. Identificar resultado observable, alcance y permiso: explicar no es implementar;
   probar no autoriza llamadas pagas ilimitadas; implementar no incluye publicar.
2. Revisar cambios existentes sin pisarlos y localizar el recorrido afectado con `rg`.
   Anunciar brevemente propuesta, archivos y riesgo antes de un cambio importante.
3. Reproducir el fallo con una prueba cuando sea posible. Formular una hipótesis
   comprobable, hacer el cambio mínimo coherente y verificarla. No acumular parches
   de síntomas ni ensanchar timeouts sin medir dónde se consume el tiempo.
4. Mantener parser → Action/TaskPlan → ActionExecutor → herramienta registrada.
   Probar también fallo, cancelación y permiso cuando se toquen esas fronteras.
5. Actualizar sólo documentación relevante y este punto de reanudación si cambió.
   Cerrar con resultado, prueba ejecutada, límite y siguiente paso concreto.

Interpretar las expresiones habituales así:

| Pedido | Respuesta operativa |
| --- | --- |
| «Seguí» | Retomar el siguiente paso pendiente ya acordado. Si es prueba del usuario, guiarla; no marcarla completada ni saltarla. Si ya cerró la versión, proponer la siguiente capacidad y esperar aprobación. |
| «No anda» con captura | Localizar capa y error observable. No asumir micrófono roto, falta de cuota o parser defectuoso sólo por el síntoma. |
| «Abrila/probemos» | Verificar proceso/versión, abrir una instancia por el lanzador y probar el recorrido acordado; no lanzar todos los juegos ni repetir toda la suite. |
| «Mejorá todo» | Priorizar defectos reportados dentro de las capacidades existentes; no convertir una auditoría en reescritura o autonomía nueva. |
| «Commit/subilo» | Revisar diff, secretos, pruebas, rama y remoto; publicar sólo el destino autorizado, sin force push ni integración implícita a main. |

No hacer preguntas para cada detalle reversible. Preguntar cuando cambie producto,
privacidad, costo, destino o permisos. No tratar un permiso amplio de un turno antiguo
como autorización permanente. No inventar pruebas exitosas para poder seguir.

## 4. Mapa de navegación del código

Rutas relativas a la raíz; los nombres abreviados de una celda comparten directorio
con su primera ruta. Abrir implementación y tests vecinos, no todo el árbol.

| Área | Archivos de entrada | Pruebas de referencia |
| --- | --- | --- |
| Texto e IA | `desktop_agent/parser.py`, `interpretation.py`, `openai_provider.py`, `models.py`, `executor.py` | `tests/test_parser.py`, `test_interpretation.py`, `test_openai_provider.py`, `test_executor.py` |
| Voz/dispositivo | `desktop_agent/audio_capture.py`, `audio_devices.py`, `voice.py`, `openai_voice.py` | `tests/test_audio_capture.py`, `test_audio_devices.py`, `test_voice.py`, `test_openai_voice.py` |
| GUI/atajos/configuración | `desktop_agent/tk_app.py`, `settings_dialog.py`, `app_settings.py`, `hotkeys.py`, `launcher.py` | `tests/test_tk_workflows.py`, `test_tk_app.py`, `test_app_settings.py`, `test_hotkeys.py` |
| Cola/cancelación | `desktop_agent/local_controller.py`, `local_service.py`, `command_processor.py` | `tests/test_local_controller.py`, `test_local_service.py` |
| Navegador | `desktop_agent/browser_runtime.py`, `browser_preferences.py`, `preferred_browser.py`, `chrome_extension_adapter.py`, `browser_bridge.py`, `chrome_native_host.py` | `tests/test_browser_runtime.py`, `test_browser_preferences.py`, `test_chrome_extension_adapter.py`, `test_browser_bridge.py` |
| Extensión | `browser_extension/service_worker.js`, `manifest.json` | `tests/browser_extension.test.cjs`, `test_browser_extension_manifest.py` |
| Apps | `desktop_agent/catalog.py`, `tools/applications.py` | `tests/test_application_tool.py` |
| Proyectos aprobados | `desktop_agent/approved_targets.py`, `targets_dialog.py` | `tests/test_approved_targets.py`, `test_tk_workflows.py` |
| Salidas de audio | `desktop_agent/output_audio.py`, `parser.py`, `cli.py` | `tests/test_output_audio.py` |
| Consumo | `desktop_agent/usage_budget.py`, `budgeted_provider.py`, `provider_config.py`, `voice_transcription_config.py` | `tests/test_usage_budget.py`, `test_openai_voice.py`, `test_provider_config.py` |
| Incidentes locales | `desktop_agent/diagnostics.py`, `error_history.py`, `error_history_dialog.py`, `logging_config.py` | `tests/test_error_history.py`, `test_diagnostic_workflows.py`, `test_logging_config.py`, `test_tk_workflows.py` |

Visión, entradas nativas, bucles, recetas y remoto tienen documentación por versión
en `docs/V0.6_ARCHITECTURE.md` hasta `V0.14_ARCHITECTURE.md`. Consultarla sólo si esa
capa está en alcance. No usar los módulos de demostración para prometer control libre.

## 5. Diagnóstico: lecciones que no hay que perder

### Voz: captura, transcripción e interpretación son etapas distintas

- Falla al instante: comprobar dispositivo, permisos, backend y error local seguro.
  El mensaje debe orientar sin exponer excepciones con secretos.
- El medidor no responde: verificar entrada física, no salida/mezcla virtual de
  VoiceMeeter; resolver nombre/API al capturar. No cambiar a otro micrófono en silencio.
- Texto absurdo: comprobar captura/calidad/idioma antes de modificar el parser.
  El usuario rechazó sesgar la transcripción con frases privilegiadas: no agregar
  hints de «abrí calculadora» para esconder el problema.
- Texto correcto pero orden rechazada: inspeccionar normalización, opt-in del
  intérprete y capacidades disponibles. No alterar una transcripción correcta.
- Error externo: distinguir credencial, cuota/rate limit, modelo, red/timeout y
  presupuesto local. El contador de la app no demuestra crédito disponible en la API.
- Cancelación tardía: resultado descartado o duplicado nunca ejecuta. Una solicitud
  ya enviada puede consumir; cancelar no garantiza deshacer un efecto realizado.

### Navegador: abrir un sitio no equivale a controlarlo

La apertura directa usa la preferencia del usuario. Playwright aislado y extensión
de la sesión habitual son modos diferentes. La extensión controla su propia pestaña
de YouTube en el perfil donde está instalada, no cualquier pestaña activa de Chrome.

Comprobar preferencia efectiva → proceso elegido → host/puente → extensión conectada
→ pestaña/origen → DOM → reproductor. Si cambia la preferencia, no conservar el
adaptador anterior. Si la extensión falla, no abrir Chromium como fallback oculto.

Después de editar el worker hay que recargar la extensión instalada. La regresión
para Chrome sin ventana normal/selector de perfiles y el control del reproductor
quedaron aceptados en el recorrido real del 2026-09-25. No elegir otro perfil ni
cambiar configuraciones para sortear futuros fallos.

Éxito de música en YouTube exige URL adecuada, reproducción, avance de tiempo, reproductor y
pestaña no silenciados. Aun así, el volumen físico/salida de Windows necesita evidencia
adicional. Spotify web abierto no significa playlist reproducida.

### Spotify: API oficial; búsqueda con apertura visual acotada

Spotify usa OAuth PKCE y Web API. El Client ID es configuración; access/refresh tokens
son secretos y no deben copiarse al chat o logs. El access token queda en memoria y el
refresh token cifrado con DPAPI. Para iniciar contenido, elegir sólo el dispositivo
configurado o una única computadora; no usar un teléfono ni adivinar entre varios.
Verificar estado posterior: `is_playing`, canción/contexto, dispositivo o volumen. Los
comandos aceptan sólo `200`, `202` o `204` y descartan su cuerpo; ninguno prueba éxito
sin esa lectura posterior. Una búsqueda usa un URI `spotify:search:` construido y
codificado localmente, con Spotify Web como fallback; no controla el DOM del cliente.
Premium, permisos, rate limits y catálogo pertenecen a Spotify. Ante 401 se permite
una renovación y un reintento; no repetir otros fallos.

### Aplicaciones y latencia

Catálogo actual: Chrome, VS Code, calculadora, Spotify app, Steam, VoiceMeeter Banana,
League y God of War Ragnarök. Su presencia en catálogo no demuestra instalación.
Usar destinos y argumentos fijos. No ejecutar texto del modelo como shell/ruta.
Si falta un ejecutable, diagnosticar; no buscar y ejecutar cualquier coincidencia.

El lanzamiento comprueba procesos de forma acotada; no prueba ventana lista, juego
jugable ni contenido cargado. League usa Riot Client con argumentos fijos de catálogo;
un rechazo de Windows no se resuelve elevando privilegios automáticamente.

Medir por separado captura, red/transcripción, interpretación, cola, herramienta y
resultado visible. La latencia mostrada por la app no incluye necesariamente el tiempo
del chat de Codex intermediario. Informar arranque frío/caliente y extremos de la
medición. No prometer «11 s totales» midiendo sólo una parte.

## 6. Validación proporcional y control de gasto

Comandos desde la raíz con el Python del entorno verificado:

```powershell
python -m unittest tests.test_parser tests.test_interpretation -q
python -m scripts.polish17_qa_check
python -m scripts.spotify18_qa_check
python -m unittest discover -s tests -q
node --test tests/browser_extension.test.cjs
git diff --check
```

Elegir módulos según el cambio; el primer comando es un ejemplo, no cubre toda feature.
El runner v0.17 es un subconjunto. Los tests JS son necesarios al modificar extensión;
una suite Python verde no los sustituye. La suite completa corresponde al cierre de
una versión o cambio transversal y a las obligaciones específicas de la documentación.
Una edición sólo documental requiere comprobar enlaces, coherencia y diff; no simular
que se probó audio ni repetir E2E con efectos ajenos al cambio. No hay lint documentado.

En el equipo de la auditoría se usó Python 3.13 y Node 18.18.0. Si `python` no está en
PATH, localizar el intérprete instalado; no instalar otro como primera reacción.
Node/Tcl necesitaron permiso de ejecución fuera del sandbox por rutas del runtime.
Separar fallo de entorno de fallo del producto; no debilitar tests para hacerlo pasar.

Los tests usan proveedores/dispositivos ficticios; no necesitan una clave real. No
leer `.env`, settings, capturas privadas ni el libro de consumo completo para una
inspección rutinaria. Nunca imprimir claves, ni siquiera durante un diagnóstico.

Para pruebas pagas: confirmar propósito, cantidad acotada, datos que saldrán del
equipo y presupuesto. No asumir que la cuota de Codex financia la API de la app.
Conservar reservas para resultados inciertos y liberar rechazos explícitos según
el contrato existente; no borrar el historial ni subir el tope para destrabar tests.
La medición local es contabilidad estimada, no factura garantizada del proveedor.

## 7. Qué desarrollar después, si el usuario lo aprueba

Primero cerrar POLISH-08 y los defectos que exponga. Luego elegir
**una capacidad principal**, no anunciar nuevas versiones sólo porque aparecen en
esta lista. La primera capacidad ya se implementó en v0.19:

1. Proyectos/documentos seleccionados: registro, nombre e inicio de VS Code
   implementados y aceptados visualmente en v0.19.
2. Volumen por dispositivo: v0.20 completada y aceptada con HyperX al 35 % el
   2026-09-24.
3. Respuesta hablada y móvil: diseñar cada capacidad por separado con costo,
   privacidad, autenticación y cancelación. El remoto acotado existente no es una
   app móvil desplegada.
4. Pruebas de apps propias/control adaptativo: sólo entornos de prueba y datos
   ficticios, observación autorizada, herramientas limitadas y parada comprobada.

Orden orientativo por utilidad y riesgo, no compromiso ni autorización. Para cada
capacidad acordar: resultado observable, mecanismo preferido (API/sistema/DOM antes
de visión), contrato validado, permisos, fallos previsibles, regresiones, demostración
real y criterio para detenerse. No reestructurar todo el núcleo para una sola orden.

## 8. Trabajar con Sol sin desperdiciar contexto

Recomendación de trabajo, no benchmark: usar Sol para correcciones delimitadas,
tests, documentación e integraciones con contrato claro. Pedir revisión con Astra
para cambios de seguridad/arquitectura complejos o un bloqueo sin nueva evidencia
después de dos intentos bien investigados. Antes de cambiar de modelo, dejar síntoma,
reproducción, hipótesis descartadas y decisión pendiente; el usuario elige el cambio.

Una guía puede reducir reconstrucción de contexto y decisiones repetidas. No cambia
las capacidades internas de Sol, no elimina errores y también consume contexto al
leerse. Evaluar utilidad durante las próximas tres tareas acotadas: tiempo hasta
aceptación, retrabajo/regresiones e intervención necesaria. Registrar uso sólo cuando
la herramienta lo exponga; no inventar tokens ahorrados ni equivalencias con la cuota.

Prácticas de economía:

- Leer secciones y archivos dirigidos por un síntoma; no cargar roadmap y chat enteros.
- No repetir comandos verdes si ni código ni entorno relevante cambiaron, salvo
  validación de cierre requerida. No ahorrar omitiendo controles de seguridad.
- Evitar planes repetidos, volcados enormes y refactors preventivos. Resumir evidencia
  y decisiones, no un monólogo de razonamiento ni registros privados.
- Antes de dejar una tarea, actualizar sección 2 si corresponde: versión, paso,
  prueba realmente ejecutada, límite y siguiente acción. No crecer indefinidamente
  este archivo; guardar detalle en arquitectura/roadmap y enlazarlo.
- Una falta de permisos, micrófono mal elegido o credenciales ausentes se resuelve
  en esa capa; consumir más razonamiento no sustituye el dato o permiso faltante.

Para iniciar con Sol, basta una vez:

> Leé AGENTS.md y docs/MASTER_GUIDE.md. Retomá el punto de reanudación comprobando el
> repositorio. Conservá el alcance y las pruebas pendientes; no adelantes versiones.

En tareas posteriores, «seguí» remite al contrato de la sección 3. No se cambió el
modelo del chat ni se creó otra tarea al escribir esta guía.

### Fundamento externo y límites

OpenAI documenta `AGENTS.md` como mecanismo para dar contexto e instrucciones del
proyecto a Codex. Por eso se enlaza esta guía desde allí; un Markdown suelto no debe
suponerse leído automáticamente. En otro entorno, adjuntarlo o pedir su lectura y
comprobar qué instrucciones se cargaron.
[Documentación oficial de AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

La ficha oficial presenta Sol como modelo para trabajo profesional complejo; no
establece el ahorro ni la tasa de éxito en este repositorio. La distribución de
tareas anterior es una recomendación a validar, no una promesa de equivalencia con
Astra ni un cambio de proveedor dentro de Desktop Agent.
[Ficha oficial de GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol).
