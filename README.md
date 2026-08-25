# Desktop Agent

Agente de escritorio desarrollado de forma incremental para convertir instrucciones
en lenguaje natural en acciones explícitas, controladas y auditables sobre una
computadora.

La versión ejecutable actual es **v0.3 — Natural Language**. Incorpora un intérprete
híbrido, validación y construcción local de acciones, configuración segura y un
adaptador opt-in para OpenAI. El proveedor permanece deshabilitado por defecto.
También persiste el consumo mensual y aplica un presupuesto local de USD 1,00 por
defecto antes de cada solicitud externa. La aceptación de la versión fue simulada:
no se realizaron llamadas reales ni se abrieron aplicaciones durante esa prueba.
La rama de trabajo de v0.4 completó únicamente la selección e instalación de
Playwright; todavía no implementa ni expone automatización de navegador.

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

## Arquitectura resumida

```text
Usuario
  -> CLI
  -> HybridInterpreter
       ├── parser determinista
       └── presupuesto mensual -> ProposalProvider opcional
  -> Action (Intent + RiskLevel + RequiresConfirmation)
  -> ActionExecutor
  -> herramienta registrada
       ├── open_url
       └── open_application
  -> sistema operativo
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
- Playwright para la futura automatización DOM acotada de v0.4.

El camino determinista continúa usando solo la biblioteca estándar. v0.3 declara
`openai==3.3.1` para su adaptador. El cliente externo se crea recién al necesitar el
fallback, por lo que los comandos deterministas no inicializan el SDK.
WEB-01 de v0.4 declara `playwright==1.62.0` y selecciona únicamente Chromium con
contextos temporales aislados. Todavía no existe un adaptador que lo lance.

## Requisitos

- Windows 10 u 11 para `open_application`.
- Windows 11 o posterior para la versión seleccionada de Playwright.
- Python 3.10 o posterior.
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

Iniciá el modo interactivo desde la raíz del proyecto:

```powershell
python -m desktop_agent
```

Ejemplo:

```text
Desktop Agent v0.3 — escribí 'salir' para terminar.
> abrir calculadora
Entendiendo comando...
Ejecutando open_application...
Aplicación abierta correctamente: Calculadora.
```

También se puede ejecutar una única instrucción:

```powershell
python -m desktop_agent "abrir youtube"
python -m desktop_agent "abrir vscode"
```

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

## Pruebas

Ejecutá la suite completa con:

```powershell
python -m unittest discover -s tests -v
```

Las pruebas usan navegadores, buscadores de ejecutables e iniciadores de procesos
falsos. Por eso pueden verificar las herramientas sin abrir ventanas reales.
El cierre de v0.3 contiene 80 pruebas locales aprobadas.

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
  fuera del catálogo, proveedor deshabilitado, fallo y límite mensual.

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
- El ejecutor rechaza cualquier acción distinta de `SAFE`.
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
├── budgeted_provider.py
├── catalog.py
├── cli.py
├── executor.py
├── interpretation.py
├── logging_config.py
├── models.py
├── parser.py
├── usage_budget.py
└── tools/
    ├── applications.py
    └── browser.py
docs/
├── IMPLEMENTATION_ROADMAP.md
├── PROJECT_DOCUMENTATION.md
├── V0.3_ARCHITECTURE_PROPOSAL.md
└── V0.4_ARCHITECTURE_PROPOSAL.md
tests/
```

## Versiones

- **v0.1 — Command Executor:** comandos deterministas y apertura de URLs.
- **v0.2 — Application Launcher:** apertura segura de Chrome, VS Code y Calculadora.
- **v0.3 — Natural Language:** [arquitectura y cierre](docs/V0.3_ARCHITECTURE_PROPOSAL.md);
  contrato, fallback, configuración, adaptador, integración, observabilidad,
  presupuesto mensual y aceptación simulada completados, sin llamadas reales.
- **v0.4 — Browser Automation:** [propuesta técnica](docs/V0.4_ARCHITECTURE_PROPOSAL.md);
  WEB-01 completado; dependencia y Chromium preparados, sin adaptador ni control real.

## Autoría y componentes externos

Construido en el proyecto:

- modelo de acciones y riesgo;
- parser determinista;
- catálogo de destinos permitidos;
- registro y ejecución de herramientas;
- herramientas de navegador y aplicaciones;
- adaptador seguro de propuestas para OpenAI;
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
