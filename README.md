# Desktop Agent

Agente de escritorio desarrollado de forma incremental para convertir instrucciones
en lenguaje natural en acciones explícitas, controladas y auditables sobre una
computadora.

La versión actual es **v0.2 — Application Launcher**. Todavía no utiliza un modelo
de IA: los comandos se interpretan de forma determinista para comprender y validar
primero el núcleo de ejecución.

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

También se aceptan variantes como `abrí VS Code` y `abrir Visual Studio Code`.
Cualquier instrucción desconocida se rechaza sin ejecutar acciones.

## Arquitectura resumida

```text
Usuario
  -> CLI
  -> parser determinista
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

La documentación técnica completa está en
[docs/PROJECT_DOCUMENTATION.md](docs/PROJECT_DOCUMENTATION.md).

## Tecnologías

- Python 3.10 o posterior.
- Biblioteca estándar de Python.
- `webbrowser` para solicitar la apertura de URLs.
- `subprocess` sin `shell=True` para iniciar aplicaciones permitidas.
- `logging` para el registro persistente.
- `unittest` para las pruebas automatizadas.

No hay dependencias de terceros ni es necesario ejecutar `pip install`.

## Requisitos

- Windows 10 u 11 para `open_application`.
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

No se requieren pasos adicionales de instalación para v0.2.

## Uso

Iniciá el modo interactivo desde la raíz del proyecto:

```powershell
python -m desktop_agent
```

Ejemplo:

```text
Desktop Agent v0.2 — escribí 'salir' para terminar.
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

Cobertura funcional actual:

- parsing de sitios y aplicaciones;
- alias, mayúsculas, acentos y espacios;
- URLs HTTP(S) válidas e inválidas;
- resolución de aplicaciones por ruta conocida y por `PATH`;
- aplicaciones ausentes o no permitidas;
- fallos al iniciar un proceso;
- herramientas no registradas;
- bloqueo de acciones no seguras;
- coordinación de la CLI.

## Logging

La aplicación crea `logs/agent.log` al ejecutarse:

```text
[15:32:01] User: abrir calculadora
[15:32:01] Intent: OPEN_APPLICATION
[15:32:01] Application: calculator
[15:32:01] Tool: open_application
[15:32:01] Status: SUCCESS
```

Los logs locales están excluidos de Git.

## Seguridad actual

- Catálogo cerrado de sitios y aplicaciones.
- No se ejecuta texto arbitrario del usuario.
- No se usa una shell para iniciar aplicaciones.
- Toda acción declara `RiskLevel` y `requires_confirmation`.
- v0.2 rechaza cualquier acción distinta de `SAFE`.
- No hay acceso a archivos, mouse, teclado, screenshots ni APIs externas.

## Estructura principal

```text
desktop_agent/
├── __main__.py
├── catalog.py
├── cli.py
├── executor.py
├── logging_config.py
├── models.py
├── parser.py
└── tools/
    ├── applications.py
    └── browser.py
docs/
├── PROJECT_DOCUMENTATION.md
└── V0.3_ARCHITECTURE_PROPOSAL.md
tests/
```

## Versiones

- **v0.1 — Command Executor:** comandos deterministas y apertura de URLs.
- **v0.2 — Application Launcher:** apertura segura de Chrome, VS Code y Calculadora.
- **v0.3 — Natural Language:** [arquitectura propuesta](docs/V0.3_ARCHITECTURE_PROPOSAL.md);
  todavía no implementada.

## Autoría y componentes externos

Construido en el proyecto:

- modelo de acciones y riesgo;
- parser determinista;
- catálogo de destinos permitidos;
- registro y ejecución de herramientas;
- herramientas de navegador y aplicaciones;
- CLI, logging, manejo de errores y pruebas.

Tecnología externa:

- Python y su biblioteca estándar.

No se incorporó código de terceros ni una API de IA en v0.2.
