# Desktop Agent

Agente de escritorio desarrollado de forma incremental para convertir instrucciones
en lenguaje natural en acciones explícitas, controladas y auditables sobre una
computadora.

La versión ejecutable actual es **v0.20 — Volumen por dispositivo de salida**. Agrega
resolución inequívoca de salidas activas de Windows, cambio de volumen entre 0 y 80 %
y lectura posterior mediante Core Audio. Conserva los proyectos/documentos aprobados
de v0.19, el control oficial de Spotify de v0.18 y el tope predeterminado de **USD 1
al mes** para llamadas de IA; las órdenes deterministas de audio no consumen ese
presupuesto.

No es todavía un agente universal: abre el catálogo de aplicaciones, automatiza
YouTube, controla Spotify mediante su API y acepta interpretación libre hacia esas
herramientas y los destinos aprobados. No explora el disco ni controla el interior de
juegos. El volumen de salida no cambia el dispositivo predeterminado ni el estado de
silencio. Los módulos
de visión, recetas y control remoto siguen teniendo demostraciones acotadas, no un
asistente autónomo general. No incluye app móvil ni relay desplegado.

## Inicio rápido

1. Abrí `Iniciar Desktop Agent.pyw` con Python, o ejecutá `python -m desktop_agent --gui`.
2. Entrá en **Configuración**, pegá la API key en el campo enmascarado y elegí los
   opt-ins de IA y voz. **Recordar la clave cifrada** evita repetirla al abrir la app.
3. Elegí tu micrófono real, especialmente si usás VoiceMeeter. El medidor se mueve
   durante una captura, no escucha de forma permanente.
4. Probá primero **Revisar y enviar**. Si querés operación sin clics, activá el envío
   automático y los atajos: **Ctrl+Alt+Espacio** inicia/termina una frase;
   **Ctrl+Alt+Esc** solicita emergencia. La app debe permanecer abierta.
5. Para YouTube en tu sesión, elegí Chrome u Opera GX y marcá la pestaña gestionada.
   La elección se guarda inmediatamente. Si estaba cerrado, el agente intenta abrir
   sólo ese navegador y espera hasta 5 s por la extensión. No cambia a Chromium si falla.
6. Para Spotify, creá una aplicación en el
   [Spotify Developer Dashboard](https://developer.spotify.com/dashboard), agregá
   exactamente `http://127.0.0.1:43817/callback` como Redirect URI y copiá el Client ID
   en **Configuración**. Activá Spotify. La primera orden abre el consentimiento; las
   siguientes renuevan la sesión automáticamente. Los controles oficiales requieren
   una cuenta Spotify Premium. En Development Mode, el propietario debe ser Premium;
   cualquier otra cuenta de prueba debe estar en **Settings → Users Management** de
   esa aplicación.
7. Para abrir tus proyectos o documentación, pulsá **Mis proyectos**, asigná un nombre
   y elegí explícitamente una carpeta o un archivo de texto. Después pedí, por
   ejemplo, «abrí el proyecto Proyecto IA» o «abrí la documentación de Guía IA».
   Cada documento se registra por separado; no se indexa el disco.
8. Para ajustar una salida de Windows, usá una parte inequívoca de su nombre: «poné el
   volumen de HyperX al 35 %». Consultá los nombres activos sin identificadores con
   `python -m desktop_agent --audio-outputs`. v0.20 admite de 0 a 80 % y verifica el
   valor leído; no desmutea ni cambia la salida predeterminada.

Después de actualizar el código de una extensión ya instalada, recargá **Desktop Agent
Browser Bridge** en `chrome://extensions` y comprobá “Sesión web: conectada”. El host
local se inspecciona sin modificarlo con `python -m scripts.browser15_bridge_setup`.
La extensión controla su propia pestaña, no cualquier pestaña que estés usando.

La guía de cambios y límites de esta capacidad está en
[docs/V0.20_ARCHITECTURE.md](docs/V0.20_ARCHITECTURE.md). El diagnóstico y las
pruebas personales pendientes de v0.17 siguen en [docs/ERROR_HISTORY.md](docs/ERROR_HISTORY.md).

El mantenimiento de v0.17 agrega **Historial de errores** persistente en la GUI y
consulta local con `python -m desktop_agent --errors`. Conserva hasta 500 incidentes
sin órdenes, audio ni claves. Incluye correcciones de preferencias y recuperación
tras una parada fallida. La pausa vuelve a consultar la pestaña gestionada si el
usuario reanuda el video manualmente. La reproducción, pausa y reanudación reales de
YouTube quedaron aceptadas el 2026-09-25; [estado y checklist](docs/ERROR_HISTORY.md)
conservan la evidencia histórica y los checkpoints de voz y atajos.

Para continuar el desarrollo con Sol u otro asistente, usar la
[guía maestra de continuidad](docs/MASTER_GUIDE.md), enlazada desde `AGENTS.md`.
Reúne el punto de reanudación, criterios de diagnóstico, pruebas y alcance; no
reemplaza las comprobaciones del repositorio ni autoriza nuevas versiones.

## Funcionalidades

Abrir sitios web conocidos:

```text
abrir youtube
abrir google
abrir github
abrir spotify web
```

Abrir aplicaciones permitidas en Windows:

```text
abrir chrome
abrir vscode
abrir calculadora
abrir spotify
abrir steam
abrir voicemeeter banana
abrir league of legends
abrir god of war ragnarok
```

`abrir spotify` prioriza la aplicación instalada. Para abrir el sitio se debe pedir
explícitamente `abrir spotify web` o `abrir spotify en el navegador`.

También se aceptan variantes deterministas como `abrí VS Code` y
`abrir Visual Studio Code`. Una instrucción desconocida solo puede usar el fallback
externo si se habilitó expresamente; en otro caso se rechaza sin ejecutar acciones.

Buscar, pausar y reanudar YouTube:

```text
poné en youtube qué tan malo puedo ser
pone lofi hip hop en youtube
detener youtube
pausá lo que estaba sonando en youtube
poné pausa al video que estoy mirando en youtube
reproducí lo que estaba mirando en youtube
reanudá el video de youtube
```

Buscar y controlar Spotify:

```text
poné la canción As It Was en spotify
poné mi playlist 7W7 en spotify
buscá la canción As It Was en spotify
pausá spotify
seguí reproduciendo spotify
siguiente canción en spotify
volvé a la canción anterior en spotify
poné el volumen de spotify al 35 %
```

`buscá` abre una búsqueda visible en Spotify sin reproducirla e informa el primer
resultado de catálogo. Primero intenta el protocolo de la app y, si Windows no lo
ofrece, abre Spotify Web. `poné` reproduce el resultado.
Si hay varias computadoras Spotify disponibles, configurá el nombre exacto del
dispositivo; el agente no elige silenciosamente un teléfono ni una computadora ambigua.

Ejecutar entre dos y cinco acciones conocidas como un único plan:

```powershell
python -m desktop_agent --plan "abrir youtube luego abrir github"
python -m desktop_agent --plan "poné lofi en youtube y después detener youtube"
```

## Arquitectura resumida

```text
Usuario
  -> CLI, modo --plan o ventana Tkinter/ttk
  -> controlador local persistente y cola serial
  -> HybridInterpreter o TaskPlanner
       ├── parser determinista
       └── presupuesto mensual -> ProposalProvider opcional
  -> Action o TaskPlan validado por completo
  -> ActionExecutor
  -> herramienta registrada
       ├── open_url con preferencia local
       ├── open_application
       ├── play_youtube
       ├── stop_youtube / resume_youtube
       └── herramientas Spotify -> OAuth PKCE -> Spotify Web API
  -> sistema operativo, Chromium aislado o pestaña gestionada por extensión local
```

El texto del usuario nunca se ejecuta como código o como comando de shell. El
parser solo genera acciones incluidas en un catálogo y el ejecutor únicamente
invoca herramientas registradas.

La integración de v0.3 agrega este fallback a la CLI:

```text
parser determinista
  -> si no reconoce el comando: ProposalProvider opcional
  -> datos no confiables
  -> validación estricta de ActionProposal
  -> Action construida con catálogo y política locales
```

El proveedor propone un intent y un destino de catálogo, o una consulta de YouTube
tratada sólo como texto. No puede elegir nombres de herramientas, URLs ejecutables,
código, rutas, riesgo ni confirmaciones. El adaptador usa JSON Schema estricto y el
dominio vuelve a validar la salida. La IA está apagada por defecto; la GUI permite
recordar una credencial sólo mediante una opción explícita y cifrado DPAPI por usuario.

La documentación técnica completa está en
[docs/PROJECT_DOCUMENTATION.md](docs/PROJECT_DOCUMENTATION.md).

La secuencia operativa para continuar el desarrollo con instrucciones breves como
`seguí al siguiente paso`, incluidos checkpoints de decisión y pruebas manuales, está
en [docs/IMPLEMENTATION_ROADMAP.md](docs/IMPLEMENTATION_ROADMAP.md).

## Tecnologías

- Python 3.10 o posterior.
- Biblioteca estándar de Python.
- `webbrowser` para solicitar la apertura de URLs.
- `subprocess` sin `shell=True` para iniciar aplicaciones permitidas.
- `logging` para el registro persistente.
- `unittest` para las pruebas automatizadas.
- Playwright para la automatización DOM acotada de v0.4.
- Tkinter/ttk para la consola nativa local de v0.4.1.
- APIs nativas de Windows (`user32` y `gdi32`) para la captura acotada de v0.6.

El camino determinista continúa usando solo la biblioteca estándar. v0.3 declara
`openai==3.3.1` para su adaptador. El cliente externo se crea recién al necesitar el
fallback, por lo que los comandos deterministas no inicializan el SDK.
WEB-01 de v0.4 declara `playwright==1.62.0` y selecciona únicamente Chromium con
contextos temporales aislados. WEB-02 a WEB-04 agregan el contrato, la política, el
adaptador y un backend Playwright de YouTube. WEB-08 registra el controlador en la CLI
sin iniciar Chromium hasta recibir una orden de reproducción.
v0.4.1 no agrega una dependencia de paquete: Tkinter pertenece a una instalación
completa de CPython. El controlador y la UI comparten el mismo núcleo que la CLI.
v0.5 tampoco agrega dependencias: el planificador reutiliza el catálogo, el ejecutor,
el presupuesto y el adaptador de Responses existente.
v0.6 usa `ctypes` y APIs incluidas en Windows; no agrega paquetes ni guarda capturas
durante la operación normal.
v0.7 reutiliza el SDK y el modelo `gpt-5.6-luna`: admite entrada de imagen y salidas
estructuradas a bajo costo. Solo se habilita con dos opt-ins y sigue sujeto al límite
mensual existente. Las pruebas y la evaluación de cierre no hicieron llamadas reales.
v0.8 usa controles Win32 y mensajes dirigidos antes que mouse global. No agrega una
dependencia y mantiene `Ctrl+Alt+Esc` como atajo de emergencia mientras el backend de
input está activo.
v0.9 a v0.13 se implementaron originalmente con la biblioteca estándar. El backend
histórico de voz usa `System.Speech`; la auditoría de precisión del 2026-08-26 demostró
que no sirve como dictado universal y la GUI dejó de seleccionarlo. La corrección
actual agrega `sounddevice==0.5.6` para capturar PCM sin NumPy y usa `gpt-transcribe`
como servicio externo opt-in.
v0.14 fija `cryptography==50.0.0` para AES-GCM y HKDF. Los secretos de dispositivo se
protegen con DPAPI para el usuario actual de Windows; TLS protege el salto al relay y
el cifrado de aplicación evita que ese relay lea órdenes o respuestas.
La parte de navegador de v0.15 no agrega dependencias Python: integra una extensión
Manifest V3 propia y Native Messaging para Chrome/Opera GX. La extensión sólo recibe
operaciones fijas, inspecciona YouTube y no solicita cookies, historial, descargas,
archivos ni acceso a todos los sitios.
v0.16 tampoco agrega paquetes: inicia sólo ejecutables del catálogo y usa la API
Tool Help de Windows para comprobar que apareció un proceso esperado. Las frases
deterministas siguen sin usar API; una formulación libre puede usar el fallback Luna
existente bajo el mismo presupuesto mensual.

## Requisitos

- Windows 10 u 11 para `open_application`.
- Windows 11 o posterior para la versión seleccionada de Playwright.
- Python 3.10 o posterior.
- Una distribución completa de Python con Tcl/Tk para usar `--gui`. El paquete
  embebido de Windows no incluye Tkinter.
- Un micrófono accesible y conexión a Internet para la transcripción de voz opcional.
- Una API key de OpenAI con facturación disponible si se habilita esa transcripción.
- Un navegador predeterminado configurado.
- Chrome u Opera GX sólo si se habilita el modo opcional de sesión actual.
- Las aplicaciones que se quieran abrir deben estar instaladas.

Comprobá Python con:

```powershell
python --version
```

En algunas instalaciones de Windows se utiliza `py` en lugar de `python`.

## Instalación

```powershell
git clone https://github.com/FS315874/Proyecto-IA.git
cd Proyecto-IA
```

Instalá el proyecto y sus dependencias declaradas con:

```powershell
python -m pip install .
```

Para preparar el navegador de v0.4 se descarga únicamente la familia Chromium que
corresponde exactamente a la versión fijada de Playwright:

```powershell
python -m playwright install chromium
```

En la instalación comprobada, Chrome for Testing, su shell headless, FFmpeg y el
verificador de dependencias ocuparon aproximadamente 701 MiB una vez extraídos. No se
instalaron Firefox ni WebKit. Actualizar Playwright puede requerir repetir este paso.

### Extensión opcional de v0.15

La apertura directa funciona sin extensión. Para controlar la sesión habitual hay
que instalar el proyecto en editable, registrar el host nativo para el usuario actual
y cargar manualmente la extensión desempaquetada:

```powershell
python -m pip install -e .
python -m scripts.browser15_bridge_setup --install-host
```

Después abrí `opera://extensions` o `chrome://extensions`, activá el modo de
desarrollador, elegí **Cargar extensión desempaquetada** y seleccioná la carpeta
`browser_extension` del repositorio. El script muestra la ruta absoluta y el ID fijo
que se debe comprobar. Para retirar el host:

```powershell
python -m scripts.browser15_bridge_setup --uninstall-host
```

El diseño y sus límites están en
[docs/V0.15_ARCHITECTURE.md](docs/V0.15_ARCHITECTURE.md).

## Uso

Iniciá la consola gráfica local:

```powershell
python -m desktop_agent --gui
```

La ventana mantiene un único controlador durante toda la sesión. Incluye entrada de
órdenes, historial acotado en memoria, estado por tarea, herramienta activa, resultado
y evidencia, latencia total/cola/ejecución, tokens, costo, presupuesto mensual,
confirmaciones de un uso, cancelación, detención normal, emergencia y cierre seguro.
Una segunda instancia se rechaza mediante un lock por usuario. La emergencia cancela
órdenes y confirmaciones pendientes y detiene la reproducción al alcanzar el siguiente
límite seguro; no interrumpe a mitad de una llamada bloqueante de Playwright.

El panel **Navegador web** permite elegir el predeterminado de Windows, Chrome u
Opera GX. Sin marcar la sesión actual, las aperturas simples usan esa elección y
YouTube conserva el Chromium aislado. Al marcar **Usar una pestaña gestionada de mi
sesión actual**, la interfaz exige que la extensión del navegador elegido aparezca
como conectada; ante desconexión falla de forma visible y no abre otro navegador.

El servicio arranca manualmente junto con `--gui`; no se instala en el inicio de
Windows, no escucha sockets y no persiste el historial. Si la sesión interactiva se
bloquea o no puede comprobarse, suspende entradas y solicita una detención de
emergencia. El desbloqueo no reanuda automáticamente: requiere el botón `Reanudar`.

`Hablar una frase` activa el micrófono sólo para una captura acotada de hasta diez
segundos. Un detector local corta el silencio y crea un WAV únicamente en memoria; si
la voz externa está habilitada, ese WAV se envía a `gpt-transcribe` en español y se
descarta al terminar. No existe escucha permanente, archivo de audio, prompt con frases
prioritarias ni log de audio o transcripción.

La transcripción aparece en un campo editable y sólo `Enviar transcripción` la
convierte en una orden normal. Silencio, timeout, falta de micrófono, presupuesto
agotado o cancelación no ejecutan nada. Las frases exactas `cancelar agente`, `detener
agente` y `parar agente` son el único canal verbal inmediato y sólo solicitan la
detención de emergencia. La voz nunca aprueba el panel de confirmación. El diseño y
sus límites se detallan en
[docs/VOICE_TRANSCRIPTION_ARCHITECTURE.md](docs/VOICE_TRANSCRIPTION_ARCHITECTURE.md).

El canal remoto de v0.14 es una infraestructura de núcleo, no una opción nueva de la
CLI o la ventana. Su aceptación reproducible se ejecuta sin red:

```powershell
python -m scripts.remote14_qa_check
```

La prueba vincula un teléfono ficticio, cifra una orden, recibe estado, bloquea un
replay, confirma una modificación de datos de prueba y revoca el dispositivo. Para
uso real todavía se necesitan una aplicación móvil y un relay desplegado; esa etapa
requerirá una decisión aparte sobre proveedor, credenciales, privacidad y operación.

Iniciá el modo interactivo desde la raíz del proyecto:

```powershell
python -m desktop_agent
```

Ejemplo:

```text
Desktop Agent v0.20.0 — escribí 'salir' para terminar.
> abrir calculadora
Entendiendo comando...
Ejecutando open_application...
Aplicación abierta y verificada: Calculadora.
```

También se puede ejecutar una única instrucción:

```powershell
python -m desktop_agent "abrir youtube"
python -m desktop_agent "abrir vscode"
python -m desktop_agent "abrir spotify"
python -m desktop_agent "abrir spotify web"
python -m desktop_agent "abrir steam"
python -m desktop_agent "abrir voicemeeter banana"
python -m desktop_agent "poné en youtube qué tan malo puedo ser"
```

El modo de planes se habilita explícitamente con `--plan`. Primero intenta separar
órdenes deterministas mediante `;`, `luego` o `y después`. Si eso no alcanza y la IA
está habilitada, solicita un JSON estricto de dos a cinco pasos. Cada paso se vuelve a
construir con datos locales y el plan completo se valida antes de ejecutar nada:

```powershell
python -m desktop_agent --plan "abrir youtube luego abrir github"
```

En modo interactivo, la reproducción continúa mientras se ingresan nuevas órdenes;
`detener youtube` o `salir` cierran la sesión. En modo de una sola instrucción, la CLI
espera Enter antes de detenerla para no dejar un proceso sin dueño.

El fallback opt-in de v0.3 requiere que la dependencia declarada esté
instalada, que `OPENAI_API_KEY` ya exista de forma segura en el entorno y un opt-in
explícito. La clave no debe pasarse como argumento ni guardarse en el repositorio:

```powershell
$env:DESKTOP_AGENT_AI_ENABLED = "true"
$env:DESKTOP_AGENT_AI_TIMEOUT_SECONDS = "5"
$env:DESKTOP_AGENT_AI_MONTHLY_BUDGET_USD = "1.00"
python -m desktop_agent "quiero usar la calculadora"
```

Si el opt-in está ausente, la configuración es inválida o el proveedor falla, no se
construye una acción externa. Una llamada real usaría un servicio con costo y todavía
no forma parte de las validaciones automatizadas realizadas en el proyecto.

La GUI configura voz e interpretación desde **Configuración**. Este lanzador histórico
ahora abre esa misma GUI y carga las opciones guardadas, sin volver a pedir una clave:

```powershell
python -m scripts.start_voice_gui
```

Cuando aún no existe una configuración guardada, se pueden tomar los opt-ins iniciales
del entorno (la API key debe estar cargada de forma segura, no en argumentos):

```powershell
$env:DESKTOP_AGENT_VOICE_TRANSCRIPTION_ENABLED = "true"
$env:DESKTOP_AGENT_AI_ENABLED = "true"
$env:DESKTOP_AGENT_VOICE_TIMEOUT_SECONDS = "20"
$env:DESKTOP_AGENT_AI_MONTHLY_BUDGET_USD = "1.00"
python -m desktop_agent --gui
```

Las preferencias guardadas de la GUI prevalecen sobre esos valores. La CLI sigue usando
su configuración de entorno. Guardar en la GUI reinicia el asistente cuando está libre;
mantiene el consumo y la configuración, pero el historial visible de tareas es temporal.
La reproducción se detiene durante ese reinicio. Si no marcás Recordar, la clave sólo
vive en esa sesión (incluido su reinicio interno) y se vuelve a pedir al abrir otro proceso.

Sin el opt-in o la credencial, la GUI funciona pero deshabilita los controles de voz.
Cada captura exitosa suma al mismo presupuesto mensual el costo estimado por duración;
el audio no aporta un conteo de tokens local, así que los tokens visibles corresponden
a las llamadas de texto o visión y el dinero visible suma todas las integraciones.

Compartir una región visual exige un segundo opt-in independiente. Habilitar IA para
órdenes de texto no habilita imágenes automáticamente:

```powershell
$env:DESKTOP_AGENT_AI_ENABLED = "true"
$env:DESKTOP_AGENT_VISION_ENABLED = "true"
$env:OPENAI_API_KEY = "..."  # solo en el entorno; nunca en archivos o logs
```

La integración visual envía únicamente el PNG en memoria de la región ya acotada y
redactada, con detalle bajo y `store=False`. Los textos de accesibilidad se fusionan
localmente y no se adjuntan a la llamada. v0.7 todavía no expone un comando de captura
en la CLI ni convierte un elemento observado en una acción.

`DESKTOP_AGENT_AI_MONTHLY_BUDGET_USD` es opcional: vale `1.00` si está ausente y
acepta importes entre `0.01` y `1000`. El registro usa el mes calendario local y se
guarda fuera del repositorio en `%LOCALAPPDATA%\DesktopAgent\ai_usage.json`; si
`LOCALAPPDATA` no está disponible, usa `~/.desktop_agent/ai_usage.json`. Conserva por
mes cantidad de solicitudes, tokens de entrada, salida, caché y total, costo estimado,
solicitudes sin medición y reservas pendientes. Nunca guarda la orden, la respuesta
ni la API key.

Después de cada fallback externo, la CLI muestra el acumulado, por ejemplo:

```text
Uso IA 2026-08: 30 tokens, USD 0.000011 de USD 1.00.
```

Antes de una llamada de texto se reserva conservadoramente USD 0,01; la voz reserva el
costo de su duración ya capturada. Al recibir telemetría, la reserva se reemplaza por
el costo estimado. Si no alcanza el saldo, el archivo es inválido o no se puede
actualizar, la llamada se bloquea y no se ejecuta ninguna acción. Cancelar antes del
envío libera la reserva. Un rechazo explícito de autenticación, permisos, modelo,
cuota o solicitud de voz libera la reserva y muestra USD 0 para ese intento. Timeout,
red o resultado incierto mantienen una estimación conservadora. Cambiar el límite no
borra el consumo. El contador es una estimación local, no una factura del proveedor;
no modifica cargos ni corrige automáticamente estimaciones de intentos históricos.

Para terminar el modo interactivo:

```text
> salir
```

## Comandos soportados

| Comando | Intent | Herramienta | Riesgo |
| --- | --- | --- | --- |
| `abrir youtube` | `OPEN_URL` | `open_url` | `SAFE` |
| `abrir google` | `OPEN_URL` | `open_url` | `SAFE` |
| `abrir github` | `OPEN_URL` | `open_url` | `SAFE` |
| `abrir spotify web` | `OPEN_URL` | `open_url` | `SAFE` |
| `abrir chrome` | `OPEN_APPLICATION` | `open_application` | `SAFE` |
| `abrir vscode` | `OPEN_APPLICATION` | `open_application` | `SAFE` |
| `abrir calculadora` | `OPEN_APPLICATION` | `open_application` | `SAFE` |
| `abrir spotify` | `OPEN_APPLICATION` | `open_application` | `SAFE` |
| `abrir steam` | `OPEN_APPLICATION` | `open_application` | `SAFE` |
| `abrir voicemeeter banana` | `OPEN_APPLICATION` | `open_application` | `SAFE` |
| `abrir league of legends` | `OPEN_APPLICATION` | `open_application` | `SAFE` |
| `abrir god of war ragnarok` | `OPEN_APPLICATION` | `open_application` | `SAFE` |
| `poné en youtube <consulta>` | `BROWSER_NAVIGATION` | `play_youtube` | `SAFE` |
| `detener youtube` | `BROWSER_NAVIGATION` | `stop_youtube` | `SAFE` |
| `reanudá el video de youtube` | `BROWSER_NAVIGATION` | `resume_youtube` | `SAFE` |
| `poné <canción> en spotify` | `MEDIA_PLAYBACK` | `play_spotify_track` | `SAFE` |
| `poné mi playlist <nombre> en spotify` | `MEDIA_PLAYBACK` | `play_spotify_playlist` | `SAFE` |
| `buscá <canción> en spotify` | `MEDIA_PLAYBACK` | `search_spotify_track` | `SAFE` |
| `pausá spotify` | `MEDIA_PLAYBACK` | `pause_spotify` | `SAFE` |
| `seguí reproduciendo spotify` | `MEDIA_PLAYBACK` | `resume_spotify` | `SAFE` |
| `siguiente canción en spotify` | `MEDIA_PLAYBACK` | `next_spotify` | `SAFE` |
| `canción anterior en spotify` | `MEDIA_PLAYBACK` | `previous_spotify` | `SAFE` |
| `poné el volumen de spotify al 35 %` | `MEDIA_PLAYBACK` | `set_spotify_volume` | `SAFE` |

## Pruebas

Ejecutá la suite completa con:

```powershell
python -m unittest discover -s tests -v
```

La suite automatizada usa navegadores, buscadores de ejecutables, iniciadores y
observadores de procesos falsos. Por eso puede verificar las herramientas sin abrir
ventanas de aplicaciones reales. La suite incluye también recorridos de Tk real
(ventanas ocultas y servicios ficticios) y pruebas de DPAPI con una clave sintética.
Para la aceptación de v0.17 y los tests del worker real de la extensión:

```powershell
python -m scripts.polish17_qa_check
python -m scripts.spotify18_qa_check
python -m scripts.targets19_qa_check
python -m scripts.audio20_qa_check
node --test tests/browser_extension.test.cjs
```

Node sólo se usa para esos tests sin red, no es una dependencia de producción.
Estado de v0.20: **119 pruebas específicas, 547 Python y 13 JS aprobadas**; el cambio
real de HyperX quedó aceptado por el usuario el 2026-09-24. v0.19 quedó aceptada y su
catálogo puede consultarse sin mostrar rutas con `python -m desktop_agent --targets`. La
aceptación real de v0.18: **127 pruebas específicas, 524 Python y 13 JS aprobadas**. La
aceptación real de autorización persistente, playlist, canción, pausa, reanudación,
siguiente, anterior y volumen se completó el 2026-09-16; la audibilidad y la vista
abierta siguen dependiendo de observación humana.
Auditoría inicial: [v0.17](docs/V0.17_ARCHITECTURE.md). Mantenimiento del 2026-09-14:
**489 Python + 13 JS aprobados**; [resultados y límites](docs/ERROR_HISTORY.md).

La aceptación simulada de v0.15 no abre navegador ni modifica el registro:

```powershell
python -m scripts.browser15_qa_check
```

La aceptación simulada de v0.16 recorre cuatro aperturas y un rechazo sin iniciar
programas reales ni usar OpenAI:

```powershell
python -m scripts.application16_qa_check
```

`python -m scripts.voice13_qa_check` renderiza la UI con un backend de voz falso,
permite corregir una frase y comprueba la cancelación verbal exacta. No abre el
micrófono ni usa red. La auditoría real del 2026-08-26 detectó el bloqueo de PowerShell,
lo corrigió y comprobó luego que `System.Speech` transcribía mal dictado libre. La GUI
actual usa captura WAV en memoria y `gpt-transcribe` sin frases privilegiadas; sus
pruebas automatizadas reemplazan micrófono y API por dobles.

WEB-07 dispone además de un runner manual separado. Estos comandos abren Chromium
visible y usan red; no forman parte de la suite normal:

```powershell
python -m scripts.web07_manual_check valid
python -m scripts.web07_manual_check no-results
python -m scripts.web07_manual_check custom
python -m scripts.web07_manual_check cancel
```

El escenario `custom` solicita una consulta interactiva, la valida como texto y no la
registra. Los casos `valid`, `custom` y `cancel` esperan Enter para detener y cerrar la
sesión sin enviar `Ctrl+C` al runtime. El checkpoint del 2026-08-25 comprobó el caso
válido en 9,01 s. YouTube devolvió un resultado para la consulta aleatoria del caso
`no-results`, y el primer intento de cancelación con `Ctrl+C` terminó con
`backend_failure`. El contenido no disponible no puede elegirse mediante la interfaz
pública externa actual. El backend la reproduce localmente mediante el error seguro
`content_unavailable`. WEB-07 está completado.

La corrección posterior conserva una reproducción exitosa mediante
`YouTubePlaybackTool` hasta llamar a `stop()`. Tres pruebas nuevas comprueban sesión
activa, detención idempotente, preservación ante una consulta inválida y fallo de
cierre. Una repetición real quedó activa durante 8 min 43 s y cerró limpiamente en
0,29 s tras la señal explícita del usuario.

Cobertura funcional actual:

- parsing de sitios y aplicaciones;
- alias, mayúsculas, acentos y espacios;
- URLs HTTP(S) válidas e inválidas;
- resolución de aplicaciones por ruta conocida y por `PATH`;
- verificación acotada del proceso iniciado y fallo seguro sin evidencia;
- aplicaciones ausentes o no permitidas;
- fallos al iniciar un proceso;
- herramientas no registradas;
- bloqueo de acciones no seguras;
- coordinación determinista e híbrida de la CLI;
- contrato y validación estricta de propuestas;
- construcción local de acciones desde destinos canónicos;
- prioridad del parser determinista y fallback con proveedor falso;
- rechazo seguro de respuestas inválidas, no soportadas o fuera del catálogo;
- configuración inválida y fallos externos sin ejecución de herramientas;
- camino, estado y duración de interpretación sin registrar la orden completa;
- uso numérico y costo aproximado cuando el proveedor los informa;
- acumulación y persistencia mensual, cambio de mes, reserva previa y bloqueo por
  presupuesto o registro inválido;
- aceptación simulada del comando exacto, tres frases naturales acordadas, destino
  fuera del catálogo, proveedor deshabilitado, fallo y límite mensual;
- contrato semántico de navegador, destinos canónicos, consultas acotadas,
  observaciones estructuradas e inyección de efectos sin lanzar Playwright.
- política, estados, backend DOM, verificación temporal e integración local completa
  del flujo web con dobles.

## Logging

La aplicación crea `%LOCALAPPDATA%\DesktopAgent\agent.log` al ejecutarse (fallback
`~/.desktop_agent/agent.log`). Rota a los 2 MB y conserva dos respaldos. Los logs
antiguos en `logs/` no se borran ni se importan automáticamente. Ejemplo:

```text
[15:32:01] Command received
[15:32:01] Interpretation: path=external status=success duration_ms=125.000 provider_configured=true provider=openai model=gpt-5.6-luna input_tokens=25 output_tokens=5 total_tokens=30 estimated_cost_usd=0.0000110000 monthly=2026-08 monthly_requests=1 monthly_input_tokens=25 monthly_output_tokens=5 monthly_total_tokens=30 monthly_estimated_cost_usd=0.0000110000 monthly_budget_usd=1.00 monthly_remaining_usd=0.9999890000 monthly_unmetered_requests=0 monthly_pending_reservations=0
[15:32:01] Intent: OPEN_APPLICATION
[15:32:01] Application: calculator
[15:32:01] Tool: open_application
[15:32:01] Status: SUCCESS
```

Los logs locales están excluidos de Git. La observabilidad de interpretación no
registra el texto de la orden, la respuesta completa, la credencial ni contenido del
escritorio. Los nombres de aplicación y URLs que aparecen después provienen del
catálogo local permitido.

### Historial de errores

El botón **Historial de errores** muestra incidentes persistentes con código, etapa,
fecha y una comprobación sugerida. La clasificación es preliminar, no un diagnóstico
automático. Marcar como revisado no borra ni prueba que se haya corregido el fallo.

```powershell
python -m desktop_agent --errors        # pendientes, sólo lectura
python -m desktop_agent --errors --all  # también revisados
```

Se conserva separado en `error_history.sqlite3`, junto al log, con los últimos 500
incidentes. No contiene órdenes, URLs, transcripciones, audio, claves ni excepciones
crudas. No usa IA ni envía datos. La GUI avisa si un incidente quedó sin guardar.
Consultar [privacidad, cobertura y recuperación](docs/ERROR_HISTORY.md).

## Seguridad actual

- Catálogo cerrado de sitios y aplicaciones.
- No se ejecuta texto arbitrario del usuario.
- No se usa una shell para iniciar aplicaciones.
- El éxito de una apertura exige observar un nombre de proceso fijado en el catálogo.
- Toda acción declara `RiskLevel` y `requires_confirmation`.
- El ejecutor exige una autorización exacta y de un uso para `CAUTION` y `DANGEROUS`.
- Los efectos siempre bloqueados no pueden producir una solicitud de confirmación.
- Las propuestas externas se consideran datos no confiables y nunca llegan
  directamente al ejecutor.
- Los tests del adaptador usan un cliente falso y la comprobación con el SDK real
  reemplazó la operación de red por un mock.
- Toda solicitud externa de la CLI pasa por una reserva persistente y un límite
  mensual local antes de llegar al adaptador.
- Un registro de consumo ausente empieza vacío; uno corrupto o inaccesible bloquea el
  proveedor en lugar de continuar sin conteo.
- La CLI no accede a archivos, mouse, teclado ni screenshots. Solo puede usar la
  Responses API después del opt-in explícito.

## Estructura principal

```text
desktop_agent/
├── __main__.py
├── agent_loop.py
├── budgeted_provider.py
├── budgeted_vision_provider.py
├── browser_adapter.py
├── browser_bridge.py
├── browser_bridge_install.py
├── browser_contract.py
├── browser_preferences.py
├── browser_runtime.py
├── catalog.py
├── chrome_extension_adapter.py
├── chrome_native_host.py
├── cli.py
├── executor.py
├── input_control.py
├── interpretation.py
├── local_controller.py
├── local_service.py
├── logging_config.py
├── models.py
├── observation.py
├── openai_plan_provider.py
├── openai_vision_provider.py
├── parser.py
├── plans.py
├── playwright_backend.py
├── permissions.py
├── recipes.py
├── remote_gateway.py
├── remote_protocol.py
├── device_registry.py
├── outbound_relay.py
├── tk_app.py
├── usage_budget.py
├── voice.py
├── vision.py
├── vision_config.py
├── vision_runtime.py
├── windows_capture.py
├── windows_input.py
├── windows_session.py
├── windows_secrets.py
├── windows_speech.py
└── tools/
    ├── applications.py
    ├── browser.py
    └── browser_automation.py
docs/
├── IMPLEMENTATION_ROADMAP.md
├── PROJECT_DOCUMENTATION.md
├── V0.3_ARCHITECTURE_PROPOSAL.md
├── V0.4_ARCHITECTURE_PROPOSAL.md
├── V0.4.1_ARCHITECTURE.md
├── V0.5_ARCHITECTURE.md
├── V0.6_ARCHITECTURE.md
├── V0.7_ARCHITECTURE.md
├── V0.8_ARCHITECTURE.md
├── V0.9_ARCHITECTURE.md
├── V0.10_ARCHITECTURE.md
├── V0.11_ARCHITECTURE.md
├── V0.12_ARCHITECTURE.md
├── V0.13_ARCHITECTURE.md
├── V0.14_ARCHITECTURE.md
├── V0.15_ARCHITECTURE.md
└── V0.16_ARCHITECTURE.md
scripts/
├── application16_qa_check.py
├── browser15_qa_check.py
├── policy11_qa_check.py
├── recipe10_qa_check.py
├── ui12_smoke_check.py
├── voice13_qa_check.py
├── remote14_qa_check.py
└── web07_manual_check.py
tests/
```

## Versiones

- **v0.1 — Command Executor:** comandos deterministas y apertura de URLs.
- **v0.2 — Application Launcher:** apertura segura de Chrome, VS Code y Calculadora.
- **v0.3 — Natural Language:** [arquitectura y cierre](docs/V0.3_ARCHITECTURE_PROPOSAL.md);
  contrato, fallback, configuración, adaptador, integración, observabilidad,
  presupuesto mensual y aceptación simulada completados, sin llamadas reales.
- **v0.4 — Browser Automation:** [propuesta técnica](docs/V0.4_ARCHITECTURE_PROPOSAL.md);
  completada; la CLI reproduce y detiene YouTube mediante Chromium aislado, con
  verificación DOM, sesión persistente y fallos estructurados.
- **v0.4.1 — Local Control Console:** completada; interfaz mínima, proceso local
  persistente, estado, métricas y detención de emergencia.
- **v0.5 — Tareas de varios pasos:** completada; planes de dos a cinco acciones,
  validación total previa, límites de tiempo, cancelación y resultados por paso.
- **v0.6 — Observación visual local:** completada; captura exacta de ventana,
  regiones acotadas, redacción, retención breve e invalidación por cambio de estado.
- **v0.7 — Interpretación visual segura:** completada; proveedor opt-in, esquema
  estricto, confianza, fusión accesible local y contenido visual tratado como datos.
- **v0.8 — Input de escritorio con límites:** completada; clic y texto confirmados,
  ventana/foco exactos, fallback ligado a observación, bloqueos y emergencia.
- **v0.9 — Bucle agente acotado:** completada; máquina de estados, evidencia,
  cancelación, límites, reintentos transitorios y abandono ante ambigüedad.
- **v0.10 — Memoria procedural segura:** completada; recetas aprobadas, huella de
  aplicación, precondiciones, verificaciones, recuperación declarada e invalidación.
- **v0.11 — Política de permisos completa:** completada; riesgo por efecto,
  confirmaciones exactas de un uso y efectos que permanecen siempre bloqueados.
- **v0.12 — Aplicación y servicio local:** completada; historial y evidencia visibles,
  confirmaciones, cancelación y suspensión segura de sesión, sin abrir puertos.
- **v0.13 — Entrada por voz local:** completada; captura explícita, transcripción
  editable, métricas, cancelación verbal segura y cero envío externo de audio.
- **v0.14 — Control remoto propio:** completada en el núcleo; pairing aprobado
  localmente, cifrado E2E, replay protection, estado acotado, cancelación,
  confirmaciones remotas, revocación y transporte HTTPS saliente. Falta desplegar un
  relay y construir la aplicación móvil para uso cotidiano.
- **v0.15 — Navegador habitual y Spotify:** implementada; agrega Spotify web/app,
  selección de navegador, reutilización de sesión, extensión local de mínimo permiso
  y una pestaña gestionada en Chrome/Opera GX. La aceptación simulada está aprobada y
  la extensión quedó instalada y conectada en Chrome; falta repetir el recorrido manual
  completo de Spotify, reproducción, reutilización y pausa.
- **v0.16 — Aplicaciones locales habituales:** implementada; agrega Steam, VoiceMeeter
  Banana, League of Legends y God of War Ragnarök al catálogo cerrado, habilita Luna
  en el lanzador efímero para formulaciones naturales y exige evidencia del proceso.
  La aceptación simulada está aprobada; la apertura visible queda como prueba manual.
- **v0.17 — Uso cotidiano y auditoría:** configuración protegida, micrófono elegible,
  atajos opt-in, envío automático opcional y correcciones del recorrido de YouTube,
  cancelación y Riot Client. El recorrido real con la extensión quedó aprobado el
  2026-09-25; voz, cancelación y atajos siguen como checkpoint del usuario.
- **v0.18 — Control oficial de Spotify:** implementación local completada con OAuth
  PKCE, token renovable cifrado, dispositivo explícito, búsqueda/reproducción y
  controles verificados. La aceptación real quedó aprobada con la cuenta y el
  dispositivo del usuario; las pruebas automatizadas continúan sin realizar red ni
  abrir Spotify.
- **v0.19 — Proyectos y documentos aprobados:** registro local explícito desde la
  GUI y apertura acotada en VS Code. La apertura visible de proyecto y documentación
  quedó aprobada por el usuario el 2026-09-19.
- **v0.20 — Volumen por dispositivo de salida:** completada y aceptada el 2026-09-24
  con Core Audio, coincidencia inequívoca, límite conservador y lectura posterior.

## Autoría y componentes externos

Construido en el proyecto:

- modelo de acciones y riesgo;
- parser determinista;
- catálogo de destinos permitidos;
- registro y ejecución de herramientas;
- herramientas de navegador y aplicaciones;
- contrato, política y adaptador semántico de navegación segura;
- backend DOM acotado y flujo vertical de YouTube;
- adaptador seguro de propuestas para OpenAI;
- contrato, validación y ejecución acotada de planes de varios pasos;
- contrato y backend nativo para observaciones visuales locales minimizadas;
- validación, evaluación y adaptador opt-in para interpretación visual;
- controlador y backend Win32 para input confirmado y acotado;
- orquestador observe-decide-act-evaluate finito y auditable;
- catálogo, persistencia y runner de recetas semánticas aprobadas;
- política por capacidades y broker de confirmaciones locales o remotas;
- fachada de servicio local, historial de tareas y monitor de sesión Windows;
- contrato y controlador de voz, captura WAV temporal y adaptador externo presupuestado;
- protocolo remoto cifrado, registro de dispositivos y gateway al servicio local;
- preferencias de navegador, puente local autenticado, host nativo y extensión
  Manifest V3 acotada;
- catálogo ampliado y verificación local acotada de procesos iniciados;
- cliente acotado de Spotify Web API, OAuth PKCE y controles con verificación;
- configuración persistente y coordinación de atajos globales opt-in;
- cliente de relay HTTPS exclusivamente saliente;
- CLI, logging, manejo de errores y pruebas.

Tecnología externa:

- Python y su biblioteca estándar;
- `System.Speech` y Windows PowerShell, conservados como backend histórico y cubiertos
  por tests, pero ya no seleccionados por la GUI;
- `sounddevice==0.5.6`, licencia MIT, para captura PCM del micrófono mediante PortAudio;
- `gpt-transcribe` como servicio externo opt-in para transcripción general de audio;
- SDK oficial `openai==3.3.1`, licencia Apache-2.0, declarado para v0.3;
- OpenAI Responses API y `gpt-5.6-luna` como servicio y modelo seleccionados, aún
  sin llamadas reales;
- Playwright `1.62.0`, licencia Apache-2.0, y sus binarios administrados de Chromium,
  declarados para v0.4.
- APIs de extensiones Chromium y Native Messaging provistas por Chrome y Opera GX;
  el manifiesto, host, protocolo y adaptador son código propio del proyecto.

La integración depende de un servicio externo y su uso futuro tendrá costo y políticas
propias. El modelo y el SDK no son capacidades desarrolladas por el proyecto.
