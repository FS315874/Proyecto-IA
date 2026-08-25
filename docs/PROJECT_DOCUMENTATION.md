# Documentación del proyecto Desktop Agent

## 1. Propósito

Desktop Agent es un proyecto personal y educativo orientado a construir un agente
capaz de recibir instrucciones, transformarlas en acciones explícitas y controlar
progresivamente una computadora de manera segura.

El objetivo no es crear una demostración donde un modelo tenga control irrestricto
del mouse. El proyecto busca demostrar arquitectura, automatización, integración
con IA, seguridad, testing, documentación y evolución mediante versiones pequeñas.

## 2. Principios de desarrollo

Cada versión debe:

- agregar una capacidad principal;
- funcionar de forma independiente;
- tener una demostración clara;
- incluir pruebas proporcionales al riesgo;
- mantener responsabilidades separadas;
- actualizar esta documentación y el README;
- poder publicarse como una versión estable.

Se evita implementar módulos futuros antes de que exista una necesidad concreta.

## 3. Estrategia tecnológica

### 3.1 Python como núcleo

Python fue elegido porque permite comenzar sin dependencias externas y ofrece un
ecosistema maduro para las capacidades previstas: Playwright, automatización de
Windows, computer vision, APIs de modelos y testing.

Node.js también era una alternativa razonable por la experiencia previa del autor.
Para el núcleo de automatización, Python ofrece una ruta más directa hacia las
bibliotecas que probablemente se evaluarán en versiones futuras.

### 3.2 CLI antes que interfaz gráfica

La CLI mantiene visible el flujo entrada -> acción -> herramienta -> resultado. Una
GUI en v0.1 o v0.2 agregaría complejidad sin mejorar el núcleo que se está validando.
La UI podrá cambiar más adelante sin reemplazar parser, modelos o herramientas.

### 3.3 Acciones directas antes que control visual

La prioridad técnica del proyecto es:

1. API o integración directa.
2. Comandos y capacidades controladas del sistema.
3. DOM y Playwright.
4. APIs de accesibilidad.
5. Visión, mouse y teclado.

Por ese motivo, abrir una URL usa `webbrowser` y abrir una aplicación usa un
ejecutable resuelto desde un catálogo. No se simulan clics en iconos del escritorio.

### 3.4 Fallback externo conectado y deshabilitado por defecto

Los comandos actuales son suficientemente simples para resolverse de manera
determinista. v0.3 define el límite que permite incorporar lenguaje natural sin
entregar control al proveedor. NLI-06 conectó ese núcleo a la CLI, manteniéndolo
deshabilitado por defecto; no se realizaron llamadas reales.

NLI-03 ya seleccionó OpenAI Responses API, `gpt-5.6-luna`, Structured Outputs y el
SDK oficial de Python bajo una política de minimización de datos y habilitación
explícita. NLI-04 agregó la configuración local validada y deshabilitada por defecto,
y NLI-05 declaró `openai==3.3.1` e implementó el adaptador real con Structured Outputs.
NLI-06 carga esa configuración una vez por proceso, crea el fallback solo al existir
un opt-in válido y conserva el modo determinista ante errores de configuración.

## 4. Arquitectura de ejecución actual

```text
User
  |
  v
CLI
  |
  v
Hybrid Interpreter
  |-- Deterministic Parser
  `-- Optional Proposal Provider
  |
  v
Action
  |-- Intent
  |-- Arguments
  |-- RiskLevel
  `-- RequiresConfirmation
  |
  v
ActionExecutor
  |
  +-- open_url
  `-- open_application
  |
  v
Operating System
```

### 4.1 Flujo de ejecución

1. La CLI recibe el texto.
2. El intérprete consulta primero al parser determinista, que normaliza mayúsculas,
   acentos y espacios y busca el destino en un catálogo cerrado.
3. Si el parser reconoce el comando, crea una `Action` inmutable sin invocar al
   proveedor.
4. Si no lo reconoce y existe configuración externa válida, el proveedor devuelve una
   propuesta que se valida y traduce con catálogo y política locales.
5. El ejecutor verifica riesgo, confirmación y nombre de herramienta.
6. La herramienta valida sus argumentos y solicita la acción al sistema.
7. El resultado se muestra al usuario y se registra en el log.
8. Si alguna etapa falla, se devuelve un error controlado.

Un comando desconocido, una configuración inválida o un fallo externo no alcanza
ninguna herramienta salvo que exista una propuesta válida y soportada.

### 4.2 Interpretación híbrida de v0.3

NLI-06 conecta la capa de interpretación a la CLI:

```text
Comando
  |
  +-- parser determinista -> Action, si reconoce el comando
  |
  `-- ProposalProvider opcional -> object no confiable
                                  |
                                  v
                          validate_proposal
                                  |
                                  v
                           ActionProposal
                                  |
                                  v
                    build_action_from_proposal
                                  |
                                  v
                    Action con política y catálogo locales
```

La interfaz `ProposalProvider` es independiente de cualquier SDK. El intérprete
híbrido prioriza el parser de v0.2, no llama al proveedor cuando el comando ya es
conocido y devuelve `None` ante propuestas inválidas, no soportadas o fuera del
catálogo. La construcción del cliente SDK también queda diferida hasta el primer
fallback, de modo que un comando determinista no importa ni inicializa el cliente.

## 5. Responsabilidades por módulo

| Módulo | Responsabilidad |
| --- | --- |
| `desktop_agent/__main__.py` | Permitir `python -m desktop_agent`. |
| `desktop_agent/budgeted_provider.py` | Envolver el proveedor con reserva, liquidación y fallo seguro del presupuesto. |
| `desktop_agent/catalog.py` | Definir sitios, aplicaciones, alias y ubicaciones permitidas. |
| `desktop_agent/cli.py` | Entrada/salida, carga segura de configuración y coordinación del intérprete híbrido. |
| `desktop_agent/models.py` | Definir acciones, intents, riesgo y resultados. |
| `desktop_agent/parser.py` | Transformar comandos conocidos en acciones estructuradas. |
| `desktop_agent/executor.py` | Aplicar la frontera de seguridad e invocar herramientas registradas. |
| `desktop_agent/interpretation.py` | Validar propuestas no confiables, construir acciones locales y orquestar el fallback opcional. |
| `desktop_agent/provider_config.py` | Cargar y validar opt-in, timeout, presupuesto y credencial del proveedor externo sin depender de su SDK. |
| `desktop_agent/openai_provider.py` | Solicitar una propuesta estructurada a OpenAI sin construir ni ejecutar acciones. |
| `desktop_agent/usage_budget.py` | Persistir consumo mensual, reservar costo antes de una llamada y bloquear al alcanzar el límite local. |
| `desktop_agent/tools/browser.py` | Validar y abrir URLs HTTP(S). |
| `desktop_agent/tools/applications.py` | Resolver e iniciar aplicaciones permitidas. |
| `desktop_agent/logging_config.py` | Crear y configurar el log persistente. |
| `tests/` | Verificar módulos y flujo sin efectos reales sobre el escritorio. |

## 6. Modelo de acciones

Una acción es el contrato entre interpretación y ejecución:

```python
Action(
    intent=Intent.OPEN_APPLICATION,
    tool_name="open_application",
    arguments={"name": "calculator"},
    risk_level=RiskLevel.SAFE,
    requires_confirmation=False,
)
```

Los argumentos se vuelven de solo lectura al construir la acción. El ejecutor no
acepta nombres de herramienta ausentes de su registro.

Los niveles definidos son:

- `SAFE`: puede ejecutarse automáticamente en la versión actual.
- `CAUTION`: requerirá confirmación en una versión futura.
- `DANGEROUS`: requerirá una política más estricta y confirmación explícita.

Aunque una acción se construya incorrectamente con riesgo no seguro y
`requires_confirmation=False`, el ejecutor de v0.2 la rechaza.

## 7. Catálogo y lista blanca

El catálogo es la fuente única para destinos soportados.

Sitios actuales:

- YouTube;
- Google;
- GitHub.

Aplicaciones actuales:

- Google Chrome;
- Visual Studio Code;
- Calculadora de Windows.

Los alias solo se usan para convertir texto conocido al identificador canónico. Por
ejemplo, `vscode`, `vs code` y `visual studio code` producen el identificador
`vscode`.

El usuario no puede proporcionar un ejecutable, una ruta o argumentos arbitrarios.

## 8. Herramientas

### 8.1 `open_url(url)`

Valida que la URL tenga esquema HTTP o HTTPS y un dominio. Luego solicita al
navegador predeterminado que abra una pestaña.

La respuesta de `webbrowser` permite saber si el sistema aceptó la solicitud, pero
no confirma que la página terminó de cargar. Esa validación necesitará Playwright.

### 8.2 `open_application(name)`

Recibe un identificador canónico del catálogo. La resolución sigue este orden:

1. rutas de instalación conocidas expandidas desde variables de Windows;
2. nombres de ejecutable disponibles en `PATH`;
3. error controlado si no se encuentra la aplicación.

El proceso se inicia con una lista de argumentos de `subprocess.Popen`. No se usa
`shell=True`, no se concatena un comando y no se interpreta texto del usuario.

Una creación exitosa del proceso confirma que Windows aceptó iniciarlo. v0.2 no
inspecciona todavía la ventana ni valida su contenido.

## 9. Seguridad

### 9.1 Controles implementados

- Parser determinista.
- Catálogo cerrado de destinos.
- Acciones inmutables.
- Herramientas registradas explícitamente.
- Validación de riesgo en el ejecutor.
- Rechazo de esquemas de URL distintos de HTTP(S).
- Resolución de ejecutables sin aceptar rutas del usuario.
- Ejecución de procesos sin shell.
- Errores controlados y logs auditables.
- Tests sin efectos sobre el escritorio.

### 9.2 Controles futuros

- Confirmación interactiva para `CAUTION` y `DANGEROUS`.
- Presupuestos de acciones y tiempo.
- Botón o atajo de emergencia.
- Cancelación de acciones pendientes.
- Protección de campos sensibles.
- Restricciones para archivos, comandos y comunicaciones externas.
- Filtrado de screenshots antes de enviarlos a servicios externos.

## 10. Manejo de errores

Las herramientas devuelven `ToolResult(success, message)` para fallos esperables,
como una URL inválida o una aplicación ausente.

El ejecutor transforma errores inesperados en `ActionExecutionError`, registra el
estado y evita mostrar un traceback técnico como respuesta normal de la CLI.

El modo interactivo continúa disponible después de un comando inválido. El modo de
una sola instrucción devuelve código `0` en éxito y `1` en error o comando no
soportado.

## 11. Logging

El archivo `logs/agent.log` registra:

- recepción de una instrucción, sin guardar su texto;
- camino determinista o externo, estado y duración de interpretación;
- proveedor y modelo configurados, sin credenciales;
- tokens de entrada, salida y total y costo aproximado, solo si existen;
- mes, solicitudes, tokens, costo estimado, presupuesto, saldo, solicitudes sin
  medición y reservas pendientes cuando se usa el fallback;
- intent seleccionado;
- URL o identificador de aplicación;
- herramienta ejecutada;
- estado final;
- traceback para excepciones inesperadas.

La metadata de interpretación no conserva el prompt, la respuesta completa, la clave
ni contexto del escritorio. Los contadores de uso se validan como enteros no
negativos; si están ausentes o son incoherentes, se omiten. Las URLs e identificadores
registrados después de construir la acción provienen del catálogo local. Los logs se
excluyen del repositorio.

El historial mensual se guarda separado del log en
`%LOCALAPPDATA%\DesktopAgent\ai_usage.json`, con fallback a
`~/.desktop_agent/ai_usage.json`. Su esquema versionado contiene solo acumulados
numéricos por mes calendario local. `DESKTOP_AGENT_AI_MONTHLY_BUDGET_USD` controla el
límite actual, con USD 1,00 por defecto. Antes de cada llamada se persiste una reserva
de USD 0,01; una respuesta medida la reemplaza por el costo estimado y una respuesta
sin telemetría conserva ese centavo como estimación conservadora.

## 12. Testing

La suite usa `unittest` y no necesita red. Los tests de adaptadores inyectan clientes,
navegador, contexto, página y runtime falsos. La compatibilidad con el SDK se comprobó
por separado con su método de red reemplazado por un mock.

Las dependencias que producen efectos se pueden inyectar:

- `open_url` recibe un navegador falso;
- `open_application` recibe un verificador de rutas, buscador y starter falsos;
- la CLI recibe una función de salida reemplazable;
- el ejecutor recibe un registro de herramientas de prueba;
- el registro de consumo recibe archivo, mes e identificadores de reserva inyectables.
- la automatización web recibe navegador, contexto, página, runtime, reloj y espera
  inyectables.
- los planes reciben proveedor, ejecutor, reloj, cancelación y herramientas falsas.

Esto permite verificar cada herramienta de manera independiente del parser y de un
futuro LLM.

Comando:

```powershell
python -m unittest discover -s tests -v
```

Estado actual: 183 pruebas automatizadas, todas sin red ni efectos reales. El runner
manual de WEB-07 es independiente y requiere autorización porque abre Chromium visible
y usa red.

## 13. Evolución por versiones

### v0.1 — Command Executor

Capacidad agregada: abrir tres sitios mediante comandos deterministas.

Incluyó:

- CLI mínima;
- parser normalizado;
- `Action`, `Intent` y `RiskLevel`;
- registro explícito de herramientas;
- `open_url`;
- logs y manejo de errores;
- primeras pruebas unitarias.

### v0.2 — Application Launcher

Capacidad agregada: abrir aplicaciones permitidas de Windows.

Incluye:

- intent `OPEN_APPLICATION`;
- catálogo central de sitios y aplicaciones;
- alias deterministas para VS Code y Calculadora;
- búsqueda por rutas conocidas y `PATH`;
- `open_application` sin shell;
- nuevos logs, mensajes de error y pruebas;
- separación entre README y documentación técnica acumulativa.

### v0.3 — Natural Language

Implementado y aceptado mediante simulación hasta NLI-08:

- contrato versionado `ActionProposal`;
- intents de propuesta `OPEN_URL`, `OPEN_APPLICATION` y `UNSUPPORTED`;
- validación estricta de campos y coherencia;
- construcción de `Action` solo desde el catálogo y la política locales;
- intérprete híbrido con prioridad para el parser determinista;
- proveedor inyectable y pruebas con un fake;
- fallo seguro ante respuestas inválidas o errores declarados del proveedor;
- configuración local validada, con opt-in explícito, timeout acotado y credencial
  redactada en su representación;
- adaptador OpenAI con una única solicitud, JSON Schema estricto, `store=false`, cero
  reintentos automáticos y errores externos convertidos en fallo seguro;
- integración opt-in con la CLI, configuración inválida degradada al modo local y
  creación diferida del cliente SDK;
- `InterpretationResult` con camino, estado, duración inyectable, proveedor
  configurado y telemetría numérica opcional;
- logs sin orden ni respuesta completa y costo estimado para `gpt-5.6-luna` cuando
  existe uso válido;
- presupuesto mensual configurable, USD 1,00 por defecto, con archivo persistente,
  historial por mes, reserva previa, acumulación de tokens y bloqueo de fallo seguro;
- aceptación reproducible con un comando exacto de v0.2, tres formulaciones
  naturales, destino fuera del catálogo, proveedor deshabilitado, fallo simulado,
  acumulado visible y bloqueo por presupuesto.

El proyecto no incluye credenciales guardadas ni realizó llamadas reales en su
validación. Las herramientas del sistema también fueron reemplazadas por dobles.

## 14. Limitaciones conocidas

- Solo se entienden comandos incluidos en el catálogo.
- El lanzador de aplicaciones está orientado a Windows.
- Las rutas conocidas pueden necesitar ampliarse para instalaciones no estándar.
- No se valida visualmente que una ventana esté lista.
- No hay argumentos para aplicaciones.
- No existe planificación de varios pasos.
- El adaptador de OpenAI no fue probado contra la API real.
- El costo es aproximado, depende de las tarifas consultadas el 2026-08-24 y debe
  revalidarse antes de una prueba real.
- El presupuesto es una barrera local, no reemplaza la facturación de OpenAI. El
  archivo no coordina varias instancias ejecutándose en paralelo; la v0.3 presupone
  una única instancia activa.
- Playwright y Chromium están instalados como base de v0.4 y existe un backend DOM
  acotado probado con dobles. La CLI expone reproducción y detención deterministas en
  un contexto temporal. Todavía no hay screenshots, visión, mouse, teclado ni memoria.
- La política de confirmación todavía no tiene interfaz; por eso todo riesgo no
  seguro se bloquea.

## 15. Estrategia Git y publicación

Ramas:

```text
main
  ^
develop
  ^
feature/* o fix/*
```

- `main` representa la última versión estable publicada.
- `develop` integra el trabajo de la próxima versión.
- cada capacidad se desarrolla en `feature/*`.
- cada corrección aislada se desarrolla en `fix/*`.
- una versión se publica después de actualizar código, tests, README y esta
  documentación.

Commits usados hasta v0.2:

```text
feat: implement desktop agent MVP v0.1
feat: add safe application launcher
docs: document v0.2 architecture and usage
```

## 16. Roadmap orientativo

La ejecución detallada de cada incremento, sus dependencias, checkpoints y criterios
de avance se mantiene en [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md). Esa
guía permite continuar con instrucciones breves sin convertir el roadmap en permiso
para saltar versiones o decisiones del usuario.

| Versión | Capacidad principal | Estado |
| --- | --- | --- |
| v0.1 | Command Executor y URLs | Completada |
| v0.2 | Application Launcher | Completada |
| v0.3 | Lenguaje natural estructurado con LLM | Completada; aceptación simulada |
| v0.4 | Automatización de navegador con Playwright | Completada |
| v0.4.1 | Consola local mínima y persistente | Completada |
| v0.5 | Tareas de varios pasos | Completada |
| v0.6 | Screenshots | Completada |
| v0.7 | Visión | Pendiente |
| v0.8 | Mouse y teclado con límites | Pendiente |
| v0.9 | Bucle observe-plan-act-evaluate | Pendiente |
| v0.10 | Recuperación y estrategias alternativas | Pendiente |
| v0.11 | Confirmaciones y permisos completos | Pendiente |
| v0.12 | Interfaz de escritorio | Pendiente |
| v0.13 | Entrada por voz | Pendiente |
| v0.14 | Control remoto propio | Pendiente |

El roadmap es una orientación, no un compromiso de implementar módulos antes de
que la versión anterior sea estable.

## 17. Autoría y dependencias externas

Construido en el proyecto:

- arquitectura incremental;
- modelos de acciones y riesgo;
- parser y catálogo;
- registro y ejecutor;
- herramientas de navegador y aplicaciones;
- contrato, política y adaptador semántico de navegación segura;
- backend DOM acotado y flujo vertical de YouTube;
- CLI, logging, errores y tests.

Tecnología externa:

- Python;
- módulos de su biblioteca estándar;
- SDK oficial `openai==3.3.1`, licencia Apache-2.0;
- OpenAI Responses API y `gpt-5.6-luna`, todavía sin uso real desde el proyecto;
- Playwright `1.62.0`, licencia Apache-2.0, con Chromium administrado para v0.4.

El adaptador y sus límites fueron construidos en el proyecto. El SDK, el transporte,
la API y el modelo son componentes externos y no se presentan como desarrollo propio.

## 18. Estado de implementación de v0.3

La [propuesta de arquitectura de v0.3](V0.3_ARCHITECTURE_PROPOSAL.md) documenta el
uso opt-in de un LLM como fallback del parser determinista. El primer incremento ya
implementa el contrato, su validación, la construcción local de `Action` y el
orquestador híbrido. NLI-04 agregó configuración segura y NLI-05 el adaptador real,
probado con un cliente falso y con el SDK temporal sin red. NLI-06 conectó ambos a la
CLI y agregó pruebas de integración locales. NLI-07 incorporó resultados observables,
duración inyectable, uso/costo opcional y logs sin el texto de la orden. NLI-07A
agregó el historial mensual persistente y el límite local configurable solicitado por
el usuario, inicialmente fijado en USD 1,00.

La versión v0.3 quedó cerrada mediante la aceptación simulada NLI-08. La decisión
NLI-03 seleccionó OpenAI, `gpt-5.6-luna`, Responses API, Structured Outputs y el SDK
oficial de Python; NLI-04 completó la configuración segura, NLI-05 declaró
`openai==3.3.1` e implementó el adaptador y NLI-06 habilitó su uso opt-in desde la
CLI. NLI-08 comprobó localmente las frases acordadas, los rechazos, los fallos y el
presupuesto sin red ni efectos reales. El adaptador sigue sin validación contra la
API real; cualquier prueba externa requiere autorización expresa.

## 19. Estado de implementación de v0.4

La [propuesta técnica de v0.4](V0.4_ARCHITECTURE_PROPOSAL.md) registra la decisión
WEB-01 aprobada el 2026-08-25. Se eligió Playwright `1.62.0`, su API síncrona y el
Chromium administrado por la misma versión, siempre con contexto temporal aislado y
sin reutilizar el perfil personal de Chrome.

La dependencia quedó declarada y se verificó localmente con Python 3.13. Playwright
descargó Chrome for Testing 151.0.7922.34, el shell headless correspondiente, FFmpeg y
el verificador de dependencias; no instaló Firefox ni WebKit. La caché observada ocupó
aproximadamente 701 MiB.

WEB-02 agregó `desktop_agent/browser_contract.py`. El contrato define operaciones
semánticas, límites locales, resolución de sitios desde catálogo, normalización de
consultas tratadas como datos, observaciones de página/búsqueda/reproducción y errores
estructurados. También exige navegador, contexto, página, reloj y espera inyectables.
Su interfaz pública no acepta URLs, selectores ni scripts. Las 17 pruebas nuevas usan
dobles y no importan, lanzan ni controlan Playwright.

WEB-03 agregó `desktop_agent/browser_adapter.py` y
`desktop_agent/tools/browser_automation.py`. La política local permite únicamente
YouTube, una sola página y un contexto no persistente; bloquea descargas, extensiones
y acceso a archivos. El adaptador valida el orden de cada operación, convierte
timeouts y fallos del backend en errores seguros, y registra operación, destino
canónico, duración, estado y código sin consultas, títulos ni detalles externos.

La herramienta es registrable en `ActionExecutor`, recibe solo una clave de catálogo
y siempre intenta cerrar contexto y navegador. Las 16 pruebas nuevas usan un backend
falso y cubren allowlist, estado, errores, redacción, cierre y registro; la suite
completa suma 113 pruebas. La CLI no registra todavía la herramienta y el proyecto no
inició Playwright ni realizó navegación real.

WEB-04 agregó `desktop_agent/playwright_backend.py` y `YouTubePlaybackTool`. El
backend conserva localmente los selectores, crea bajo demanda un Chromium administrado
y un contexto temporal sin descargas, permisos ni service workers. La consulta se
normaliza antes de cualquier inicio y se introduce mediante `fill`; nunca se usa como
URL, selector o script.

El primer resultado debe pertenecer a `/watch` y contener un identificador de video
válido. El backend reconstruye la URL canónica y descarta parámetros adicionales. El
flujo fijo abre YouTube, busca, selecciona, inicia y quita el silencio, deteniéndose
ante el primer fallo y cerrando contexto, navegador y runtime.

Las 14 pruebas nuevas cubren el camino exitoso, consulta inválida, consentimiento,
cero resultados, cambios de DOM, timeout, enlace externo, reproducción pausada,
aislamiento y limpieza parcial. La suite completa suma 127 pruebas y no inició un
navegador real.

WEB-05 extendió `BrowserAdapter` con `verify_playback`. El flujo toma dos observaciones
separadas por la ventana local inyectable y exige que ambas pertenezcan al mismo video
de YouTube, no estén pausadas ni silenciadas y tengan volumen positivo cuando el DOM
lo expone. `currentTime` debe avanzar al menos 0,1 segundos durante la ventana de un
segundo; ambos valores están validados y no pueden ser definidos por el modelo.

La herramienta sólo informa éxito después de `VERIFY_PLAYBACK`. Los fallos usan el
código seguro `PLAYBACK_NOT_CONFIRMED`, no incluyen URL, título ni contenido, y no
impiden el cierre. Cuatro pruebas nuevas cubren verificación positiva, volumen no
observable, estados no demostrados y rechazo final desde la herramienta. La suite
completa sumaba 131 pruebas sin esperas reales, red ni ventanas al cerrar WEB-05.

WEB-06 agregó una integración local reproducible que compone `ActionExecutor`,
`YouTubePlaybackTool`, la fábrica, `SafeBrowserAdapter` y `YouTubePlaywrightPage`. El
límite externo se reemplaza por navegador, contexto, página y runtime falsos. Un caso
recorre el flujo exitoso hasta `VERIFY_PLAYBACK`; otro simula consentimiento y exige
fallo seguro, ausencia de espera y cierre completo.

La fábrica permite inyectar reloj y espera y los valida antes de iniciar el runtime.
Las dos pruebas nuevas elevan la suite a 133 casos. El E2E real fue omitido de forma
intencional: es opcional, depende de red y del DOM externo, y requerirá autorización
expresa en el checkpoint WEB-07.

WEB-07 agregó `scripts/web07_manual_check.py`, un runner separado de la CLI con casos
fijos y una consulta pública interactiva normalizada, cierre en `finally`, resultados
JSON y tiempos de arranque, navegación, cierre y total. No registra la consulta ni
permite recibir URLs, selectores, scripts o perfiles externos.

El 2026-08-25 se ejecutaron tres observaciones con Chromium visible y red autorizada:

- `valid` completó apertura, búsqueda, selección, inicio y verificación; demoró
  1,47 s en iniciar el navegador, 7,15 s en navegar y verificar, y 9,01 s en total;
- una segunda consulta pública interactiva elegida por el usuario también completó el
  flujo: arranque 3,83 s, navegación y verificación 7,10 s, total 11,38 s;
- `no-results` no reprodujo la condición: YouTube devolvió y permitió seleccionar un
  resultado incluso para la consulta aleatoria; cerró correctamente en 5,46 s;
- `cancel` recibió `Ctrl+C` después de abrir YouTube, pero el cierre informó
  `backend_failure`; no existe todavía cancelación estructurada en el contrato.

El caso de contenido no disponible tampoco es alcanzable de manera reproducible: la
interfaz pública recibe una consulta y selecciona el primer resultado permitido, pero
no admite elegir un identificador de video. Alterar la página o pasar una URL para
forzar el fallo eludiría el contrato que se intenta validar. La interpretación no fue
medida en ese checkpoint porque el flujo todavía no estaba registrado en la CLI. El
DOM confirmó reproducción, ausencia de mute y progreso, no la salida física de audio.
La primera implementación cerraba inmediatamente después de verificar. La corrección
de WEB-07 transfirió la sesión exitosa a `YouTubePlaybackTool`: permanece activa hasta
`stop()`, mientras los fallos conservan el cierre inmediato. La detención es
idempotente, una consulta inválida no interrumpe la sesión existente y un fallo de
cierre se informa sin conservar un handle que parezca activo.

El runner sustituyó `Ctrl+C` por una espera de Enter. Así mantiene el navegador y la
reproducción activos sin enviar una interrupción al proceso de Playwright, y después
ejecuta el cierre normal en `finally`. Una repetición real con la consulta elegida por
el usuario permaneció activa 8 min 43 s y cerró contexto, navegador y runtime en
0,29 s después de la señal explícita.

Los estados negativos no se fuerzan contra el servicio externo. Cero resultados se
comprueba mediante el marcador DOM esperado y `NO_RESULTS`; contenido no disponible
se detecta mediante marcadores fijos del reproductor y produce
`CONTENT_UNAVAILABLE`. Ambos se validan de forma determinista sin aceptar URLs,
selectores ni scripts del usuario. Cuatro pruebas nuevas llevan la suite a 137 casos.

WEB-07 queda completado.

WEB-08 integró la capacidad con la CLI mediante dos herramientas registradas:
`play_youtube` conserva la sesión verificada y `stop_youtube` la detiene. El parser
reconoce formas deterministas acotadas, preserva la consulta normalizada como datos y
rechaza consultas vacías o mayores a 200 caracteres antes de abrir Chromium. Estas
órdenes no invocan el proveedor de IA ni se registran textualmente.

El modo interactivo conserva el mismo controlador entre órdenes y lo detiene al
recibir `detener youtube`, `salir`, EOF o una interrupción manejable. El modo de una
sola instrucción espera Enter antes del cierre. La fábrica permanece diferida: iniciar
la CLI o usar otras herramientas no lanza Playwright.

`pyproject.toml`, el paquete y el banner declaran `0.4.0`. Siete pruebas nuevas cubren
parsing, registro, bypass del proveedor, ciclo de vida de una orden y limpieza al salir;
la suite completa suma 144 casos. WEB-08 y v0.4 quedan completados.

Con WEB-08, v0.4 queda cerrada. El incremento siguiente, v0.4.1, se documenta a
continuación.

## 20. Estado de implementación de v0.4.1

El usuario aprobó una interfaz nativa Tkinter/ttk. Se descartó la interfaz web local
para este incremento porque abriría un puerto y exigiría controles HTTP adicionales
sin ser necesarios para una consola que solo opera en el equipo. La decisión completa
está en [V0.4.1_ARCHITECTURE.md](V0.4.1_ARCHITECTURE.md).

`CommandProcessor` extrae de la CLI el flujo interpretar-validar-ejecutar y devuelve
un resultado estructurado sin cambiar los mensajes ni los logs existentes.
`LocalAgentController` mantiene ese procesador, el ejecutor y el controlador de
reproducción durante toda la sesión. Un único worker serializa órdenes y conserva la
afinidad de hilo requerida por Playwright; la ventana solo envía solicitudes y consume
eventos.

La consola se abre con `python -m desktop_agent --gui`. Incluye entrada, ejecución,
estado, resultados, detención, emergencia, cierre, etapa activa, tokens, costo,
presupuesto mensual y tiempos de cola, ejecución y total. La emergencia cancela lo
pendiente inmediatamente y solicita la detención del efecto activo en el siguiente
límite seguro. No se mata un hilo ni se usa Playwright desde un hilo ajeno.

La instancia única usa un lock por usuario mantenido por Windows. El cierre cancela
pendientes, detiene una reproducción activa, espera el worker y libera el lock. La UI
no abre sockets, no registra órdenes y no agrega una dependencia de paquete.

La distribución portable usada hasta v0.4 era la edición embebida de CPython y no
incluía Tcl/Tk. Se verificó el instalador oficial de Python 3.13.15 mediante su SHA-256
publicado, se instaló para el usuario sin modificar PATH y se comprobó Tk 8.6. La
suite completa suma 154 pruebas. Un smoke real creó la ventana, actualizó el layout y
cerró el worker; el control visible automatizado no recibió a tiempo permiso de
Windows y fue omitido de forma segura.

## 21. Estado de implementación de v0.5

v0.5 agrega planes explícitos de entre dos y cinco pasos sin cambiar el contrato de
seguridad del ejecutor. `TaskPlanner` intenta primero una composición determinista de
órdenes conocidas separadas por `;`, `luego` o `y después`. El fallback de IA sigue
siendo opcional, comparte el límite mensual de USD 1,00 y solicita un único JSON con
esquema estricto; no se realizaron llamadas reales.

La propuesta externa solo expresa intención y destino. `validate_plan_proposal`
reconstruye cada `Action` desde catálogos locales, y `TaskPlanValidator` comprueba el
plan completo —cantidad, identificadores, intención, herramienta, argumentos, riesgo,
confirmación y registro— antes del primer efecto. Un paso inválido rechaza todo el
plan.

`TaskPlanExecutor` ejecuta secuencialmente, sin reintentos ni bucles. Tiene un límite
predeterminado de treinta segundos, cancelación entre pasos y estados explícitos para
éxito, fallo, cancelación, timeout y pasos omitidos. Los logs guardan identificador,
herramienta, estado y duración, pero no la orden ni sus argumentos.

La CLI expone esta capacidad mediante `--plan`. La demostración local abrió dos URLs
permitidas mediante herramientas falsas y el entrypoint real ejecutó dos detenciones
idempotentes sin iniciar Chromium. La suite completa suma 172 pruebas locales. La
decisión y el contrato detallados están en [V0.5_ARCHITECTURE.md](V0.5_ARCHITECTURE.md).

## 22. Estado de implementación de v0.6

v0.6 introduce observaciones visuales locales como datos temporales, no como acciones.
`ObservationService` requiere un `WindowTarget` concreto y una región dentro de su
área cliente. Aplica límites de 1280 × 720, 921.600 píxeles, una captura cada 250 ms,
ocho capturas por sesión y treinta segundos de retención. Los valores son configurables
en tests, pero siempre se validan localmente.

Cada observación contiene un identificador opaco, ventana, revisión, región,
dimensiones, formato y vencimiento; los píxeles se conservan aparte. `read_frame`
exige la misma ventana y revisión. `mark_state_changed` elimina las capturas asociadas
y devuelve una referencia de ventana con revisión nueva, de modo que un identificador
o coordenada derivado del estado anterior no puede reutilizarse.

Las regiones sensibles se validan respecto de la captura y se reemplazan por píxeles
negros antes de que el frame quede disponible. `close` descarta todos los frames. Los
logs no incluyen títulos, píxeles ni texto visible.

`WindowsWindowCaptureBackend` enumera títulos exactos, rechaza coincidencias ambiguas,
ventanas ocultas o minimizadas y cambios de proceso o tamaño. Solo captura objetivos
emitidos por la misma instancia. Usa `user32` y `gdi32` mediante `ctypes`, por lo que
no fue necesario agregar una dependencia.

Once pruebas unitarias usan frames ficticios. La demo autorizada de OBS-08 abrió una
ventana Tk propia con datos explícitamente ficticios, capturó y redactó una región,
codificó el frame en memoria, invalidó la referencia y cerró sin guardar ni enviar la
imagen. La suite pasa a 183 pruebas.
