# Desktop Agent

Agente de escritorio desarrollado de forma incremental para convertir instrucciones
en lenguaje natural en acciones explícitas, controladas y auditables sobre una
computadora.

La versión ejecutable actual es **v0.12 — Aplicación y servicio local**. La consola
Tkinter usa una fachada de servicio dentro del proceso, sin abrir puertos. Muestra
historial y estados por tarea, herramienta activa, resultado, evidencia, uso y
latencia; incorpora confirmaciones exactas, cancelación, stop, emergencia y suspensión
segura al bloquearse o volverse incierta la sesión de Windows.

## Funcionalidades

Abrir sitios web conocidos:

```text
abrir youtube
abrir google
abrir github
```

Abrir aplicaciones permitidas en Windows:

```text
abrir chrome
abrir vscode
abrir calculadora
```

También se aceptan variantes deterministas como `abrí VS Code` y
`abrir Visual Studio Code`. Una instrucción desconocida solo puede usar el fallback
externo si se habilitó expresamente; en otro caso se rechaza sin ejecutar acciones.

Reproducir y detener YouTube:

```text
poné en youtube qué tan malo puedo ser
pone lofi hip hop en youtube
detener youtube
```

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
       ├── open_url
       ├── open_application
       ├── play_youtube
       └── stop_youtube
  -> sistema operativo o Chromium aislado
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

El proveedor no puede elegir herramientas, URLs, ejecutables, argumentos, riesgo ni
confirmaciones. Esos valores siguen bajo control del código local. La configuración
externa también permanece deshabilitada por defecto y no conserva una credencial
cuando está apagada. El adaptador usa una única salida con JSON Schema estricto y
devuelve datos que el dominio vuelve a validar.

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
v0.9 a v0.12 usan solamente la biblioteca estándar. El bucle, las recetas, la
política, el servicio local y Tkinter no agregan servicios externos ni paquetes.

## Requisitos

- Windows 10 u 11 para `open_application`.
- Windows 11 o posterior para la versión seleccionada de Playwright.
- Python 3.10 o posterior.
- Una distribución completa de Python con Tcl/Tk para usar `--gui`. El paquete
  embebido de Windows no incluye Tkinter.
- Un navegador predeterminado configurado.
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

Instalá el proyecto y su dependencia declarada con:

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

El servicio arranca manualmente junto con `--gui`; no se instala en el inicio de
Windows, no escucha sockets y no persiste el historial. Si la sesión interactiva se
bloquea o no puede comprobarse, suspende entradas y solicita una detención de
emergencia. El desbloqueo no reanuda automáticamente: requiere el botón `Reanudar`.

Iniciá el modo interactivo desde la raíz del proyecto:

```powershell
python -m desktop_agent
```

Ejemplo:

```text
Desktop Agent v0.12.0 — escribí 'salir' para terminar.
> abrir calculadora
Entendiendo comando...
Ejecutando open_application...
Aplicación abierta correctamente: Calculadora.
```

También se puede ejecutar una única instrucción:

```powershell
python -m desktop_agent "abrir youtube"
python -m desktop_agent "abrir vscode"
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

Antes de llamar al proveedor se reserva conservadoramente USD 0,01. Al recibir la
telemetría, esa reserva se reemplaza por el costo estimado de la solicitud. Si queda
menos de esa reserva, el archivo es inválido o no se puede actualizar, la llamada se
bloquea y no se ejecuta ninguna acción. Cambiar el límite no borra el historial.

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
| `abrir chrome` | `OPEN_APPLICATION` | `open_application` | `SAFE` |
| `abrir vscode` | `OPEN_APPLICATION` | `open_application` | `SAFE` |
| `abrir calculadora` | `OPEN_APPLICATION` | `open_application` | `SAFE` |
| `poné en youtube <consulta>` | `BROWSER_NAVIGATION` | `play_youtube` | `SAFE` |
| `detener youtube` | `BROWSER_NAVIGATION` | `stop_youtube` | `SAFE` |

## Pruebas

Ejecutá la suite completa con:

```powershell
python -m unittest discover -s tests -v
```

La suite automatizada usa navegadores, buscadores de ejecutables e iniciadores de
procesos falsos. Por eso puede verificar las herramientas sin abrir ventanas reales.
La suite actual contiene 290 pruebas locales. Además se comprobó con Tcl/Tk real que
la ventana puede construirse, actualizar su layout y cerrar su worker sin iniciar
Chromium ni ejecutar una orden.

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

La aplicación crea `logs/agent.log` al ejecutarse:

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

## Seguridad actual

- Catálogo cerrado de sitios y aplicaciones.
- No se ejecuta texto arbitrario del usuario.
- No se usa una shell para iniciar aplicaciones.
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
├── browser_contract.py
├── catalog.py
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
├── tk_app.py
├── usage_budget.py
├── vision.py
├── vision_config.py
├── vision_runtime.py
├── windows_capture.py
├── windows_input.py
├── windows_session.py
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
└── V0.12_ARCHITECTURE.md
scripts/
├── policy11_qa_check.py
├── recipe10_qa_check.py
├── ui12_smoke_check.py
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
- CLI, logging, manejo de errores y pruebas.

Tecnología externa:

- Python y su biblioteca estándar;
- SDK oficial `openai==3.3.1`, licencia Apache-2.0, declarado para v0.3;
- OpenAI Responses API y `gpt-5.6-luna` como servicio y modelo seleccionados, aún
  sin llamadas reales;
- Playwright `1.62.0`, licencia Apache-2.0, y sus binarios administrados de Chromium,
  declarados para v0.4.

La integración depende de un servicio externo y su uso futuro tendrá costo y políticas
propias. El modelo y el SDK no son capacidades desarrolladas por el proyecto.
