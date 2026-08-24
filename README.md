# Desktop Agent

MVP de un agente de escritorio que transforma instrucciones conocidas en acciones
explícitas y controladas. La versión **v0.1** abre YouTube, Google o GitHub en el
navegador predeterminado y rechaza cualquier comando no soportado.

El objetivo del proyecto es aprender y construir progresivamente un agente útil,
auditable y seguro, sin delegar acciones arbitrarias a un modelo de lenguaje.

## Estado actual: v0.1 — Command Executor

Funciona:

- `abrir youtube`
- `abrir google`
- `abrir github`
- variantes con mayúsculas, espacios extra y `abrí`
- rechazo explícito de comandos desconocidos
- logging persistente en `logs/agent.log`
- validación básica de URLs y errores del navegador
- pruebas que no abren el navegador real

## Decisiones tecnológicas

**Python** es la recomendación para el núcleo. Es sencillo para este MVP, tiene
buen soporte futuro para Playwright, automatización, visión e IA, y permite probar
las herramientas sin acoplarlas a una interfaz. Frente a Node.js, ambos serían
válidos; Python tiene una ventaja práctica en el ecosistema de automatización e IA,
mientras que la experiencia previa con JavaScript haría Node.js inicialmente más
familiar.

Para v0.1 se usa solamente la biblioteca estándar de Python:

- `webbrowser` abre URLs con el navegador predeterminado;
- `logging` registra la actividad;
- `unittest` ejecuta pruebas;
- `dataclasses`, `enum` y tipos modelan acciones explícitas.

No se necesita `pip install`, Playwright, una API de IA ni una base de datos.
`webbrowser` es suficiente para solicitar al sistema que abra una URL; Playwright
aportará valor cuando haya que inspeccionar e interactuar con el DOM.

## Arquitectura mínima

```text
Usuario
  -> CLI
  -> parser determinista
  -> Action (Intent + RiskLevel + RequiresConfirmation)
  -> ActionExecutor
  -> herramienta registrada open_url
  -> navegador predeterminado
```

El texto del usuario nunca se ejecuta como código ni como comando del sistema. El
parser solo produce acciones incluidas en una lista blanca, y el ejecutor solo
acepta herramientas registradas.

## Estructura

```text
desktop_agent/
├── __main__.py          # Punto de entrada: python -m desktop_agent
├── cli.py               # Entrada/salida y coordinación del flujo v0.1
├── executor.py          # Registro, validación y ejecución de herramientas
├── logging_config.py    # Configuración del log persistente
├── models.py            # Action, Intent, RiskLevel y ToolResult
├── parser.py            # Comandos conocidos -> Action
└── tools/
    └── browser.py       # Herramienta open_url
tests/                   # Pruebas unitarias sin efectos reales
pyproject.toml           # Metadatos y versión del proyecto
.gitignore               # Archivos locales, logs y secretos ignorados
```

Las responsabilidades permanecen separadas para poder sustituir la CLI por una UI,
agregar parsers o incorporar herramientas sin mezclar esas decisiones con la
ejecución del sistema.

## Requisitos

- Windows 10/11 (el código también es portable a macOS y Linux)
- Python 3.10 o posterior
- un navegador predeterminado configurado

Comprobá la instalación:

```powershell
python --version
```

En algunas instalaciones de Windows el comando es `py` en lugar de `python`.

## Uso

Desde la raíz del proyecto, iniciá el modo interactivo:

```powershell
python -m desktop_agent
```

Ejemplo:

```text
Desktop Agent v0.1 — escribí 'salir' para terminar.
> abrir youtube
Entendiendo comando...
Ejecutando open_url...
URL abierta correctamente: https://www.youtube.com/
```

También se puede ejecutar una sola instrucción:

```powershell
python -m desktop_agent "abrir github"
```

Un comando desconocido no ejecuta ninguna herramienta:

```text
> preparame un café
Entendiendo comando...
Comando no soportado todavía.
```

## Pruebas

```powershell
python -m unittest discover -s tests -v
```

Las pruebas inyectan un navegador falso. Así validan el parser, la seguridad, el
ejecutor y `open_url` sin abrir ventanas durante el test.

## Logging

Cada ejecución agrega eventos a `logs/agent.log`:

```text
[15:32:01] User: abrir youtube
[15:32:01] Intent: OPEN_URL
[15:32:01] URL: https://www.youtube.com/
[15:32:01] Tool: open_url
[15:32:01] Status: SUCCESS
```

La carpeta `logs` se crea al iniciar la aplicación y sus archivos `.log` no se
versionan.

## Seguridad y privacidad

- Solo existen tres destinos permitidos en v0.1.
- `open_url` rechaza esquemas distintos de HTTP(S).
- Una `Action` declara `RiskLevel` y `requires_confirmation` desde el inicio.
- El ejecutor rechaza acciones que requieren confirmación porque ese flujo todavía
  no fue implementado.
- No hay shell, archivos, mouse, teclado, screenshots, secretos ni servicios
  externos.

## Limitaciones deliberadas

Quedan fuera de v0.1:

- interfaz gráfica;
- apertura de aplicaciones;
- lenguaje natural con LLM;
- Playwright y automatización del DOM;
- tareas de varios pasos y bucle de agente;
- screenshots, visión, mouse y teclado;
- persistencia estructurada y memoria;
- confirmación interactiva para acciones sensibles;
- validación de que la página terminó de cargar.

`webbrowser` informa si el sistema aceptó abrir el navegador, pero no puede probar
que el sitio cargó correctamente. Esa validación llegará con Playwright en una
versión posterior.

## Desarrollo y Git

Flujo previsto:

```text
main <- develop <- feature/* o fix/*
```

Cada versión debe mantenerse pequeña, demostrable y probada. Las features
importantes se integrarán mediante Pull Request. No deben versionarse `.env`, API
keys ni logs locales.

## Autoría

Construido en este proyecto:

- modelo de acciones y riesgo;
- parser determinista;
- registro y ejecutor de herramientas;
- coordinación CLI;
- manejo de errores, logging y pruebas.

Tecnología externa:

- Python y su biblioteca estándar.

No se incorporó código ni una API de terceros en v0.1.

## Próximo paso posible

La orientación del roadmap propone **v0.2 — Application Launcher**, con una
herramienta `open_application()` limitada por una lista blanca. No forma parte de
esta entrega y no se implementará hasta decidir explícitamente continuar.

