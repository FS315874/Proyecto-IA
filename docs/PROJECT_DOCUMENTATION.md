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

La suite usa `unittest` y no necesita red. Los tests del adaptador inyectan un cliente
falso; la compatibilidad con el SDK se comprobó por separado en un directorio temporal
con su método de red reemplazado por un mock.

Las dependencias que producen efectos se pueden inyectar:

- `open_url` recibe un navegador falso;
- `open_application` recibe un verificador de rutas, buscador y starter falsos;
- la CLI recibe una función de salida reemplazable;
- el ejecutor recibe un registro de herramientas de prueba.
- el registro de consumo recibe archivo, mes e identificadores de reserva inyectables.

Esto permite verificar cada herramienta de manera independiente del parser y de un
futuro LLM.

Comando:

```powershell
python -m unittest discover -s tests -v
```

Estado actual: 80 pruebas unitarias, todas sin red ni efectos reales.

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
- No hay Playwright, screenshots, visión, mouse, teclado ni memoria.
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
| v0.4 | Automatización de navegador con Playwright | Pendiente |
| v0.5 | Tareas de varios pasos | Pendiente |
| v0.6 | Screenshots | Pendiente |
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
- CLI, logging, errores y tests.

Tecnología externa:

- Python;
- módulos de su biblioteca estándar;
- SDK oficial `openai==3.3.1`, licencia Apache-2.0;
- OpenAI Responses API y `gpt-5.6-luna`, todavía sin uso real desde el proyecto.

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
