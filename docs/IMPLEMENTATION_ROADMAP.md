# Roadmap operativo de implementación

> Estado del documento: guía de ejecución aprobada para trabajo incremental.
> Estado comprobado del producto: v0.3 completada y v0.4 en desarrollo.
> Última revisión: 2026-08-25.

## 1. Propósito

Este documento convierte la visión de Desktop Agent en una secuencia operativa de
trabajo. Su objetivo es que una persona o un asistente de IA pueda continuar el
proyecto con instrucciones breves como `seguí al siguiente paso`, sin depender de
copiar un prompt nuevo para cada incremento.

No reemplaza el estado real del repositorio. Antes de implementar un paso se deben
comprobar el código, los tests, `pyproject.toml`, el README, la documentación y Git.
Si este documento contradice la implementación, prevalece la evidencia del
repositorio y se corrige la guía antes de continuar.

Este roadmap no autoriza por sí mismo:

- avanzar más de una capacidad principal por versión;
- instalar dependencias o adoptar servicios externos;
- solicitar, guardar o utilizar credenciales;
- ejecutar pruebas con efectos reales;
- crear commits, hacer push o abrir Pull Requests;
- realizar acciones sensibles sobre la computadora;
- omitir una pausa marcada como decisión o prueba del usuario.

## 2. Visión del producto

Desktop Agent busca convertirse progresivamente en un agente personal de escritorio
que reciba objetivos por texto o voz, comprenda el contexto, proponga un plan,
ejecute herramientas controladas, verifique resultados y reutilice procedimientos
aprobados.

La visión puede resumirse así:

```text
Texto / voz / escritorio / celular
                 |
                 v
       Intérprete y orquestador
                 |
                 v
        Plan estructurado local
                 |
                 v
     Política, permisos y límites
                 |
        +--------+--------+---------+----------+
        |        |        |         |          |
        v        v        v         v          v
       API    navegador  sistema  accesibilidad  visión
        |        |        |         |          |
        +--------+--------+---------+----------+
                 |
                 v
        Verificación observable
                 |
                 v
       Resultado, log y memoria
```

La IA aporta interpretación, planificación, selección de estrategias y recuperación.
El código local conserva herramientas, argumentos permitidos, riesgo, confirmaciones,
presupuestos, ejecución y verificación. El texto generado por un modelo nunca se
ejecuta directamente como shell, ruta, selector, código o acción de escritorio.

## 3. Resultado deseado a largo plazo

La visión se considera materializada cuando el sistema pueda, dentro de límites
explícitos:

- entender distintas formulaciones de un mismo objetivo;
- ejecutar comandos conocidos por un camino rápido y determinista;
- descomponer objetivos nuevos en pasos estructurados;
- abrir y controlar aplicaciones permitidas;
- automatizar flujos web con DOM antes de recurrir a visión;
- utilizar accesibilidad antes de coordenadas de mouse;
- comprobar el resultado observable de cada acción relevante;
- detenerse ante ambigüedad, pérdida de foco o estado no verificable;
- pedir confirmación para acciones sensibles;
- cancelar tareas y respetar límites de tiempo, acciones y reintentos;
- reutilizar recorridos aprobados sin guardar secretos ni depender de coordenadas;
- recibir órdenes desde una interfaz de escritorio, voz y una futura interfaz móvil;
- delegar desarrollo de software a herramientas de código en vez de escribir
  simulando teclas dentro de un editor.

No se promete control universal ni éxito perfecto en cualquier aplicación. Cada
capacidad debe ser demostrable en un alcance conocido antes de generalizarse.

## 4. Principios que no se deben perder

### 4.1 IA como orquestador, no como ejecutor irrestricto

El agente no será una lista cerrada de frases, pero tampoco permitirá ejecución libre.
Un modelo podrá combinar primitivas seguras y proponer planes. El núcleo local deberá
validar cada propuesta y traducirla a herramientas registradas.

### 4.2 Camino rápido para tareas conocidas

El parser determinista, los adaptadores específicos y las recetas verificadas evitan
usar un modelo en cada clic. La IA se utiliza cuando aporta interpretación,
planificación o recuperación, no como requisito artificial para cada paso.

### 4.3 Automatización híbrida

Orden de preferencia:

1. API o integración directa;
2. comandos seguros y controlados del sistema;
3. DOM y Playwright;
4. APIs de accesibilidad;
5. visión, mouse y teclado.

### 4.4 Verificación antes de declarar éxito

Abrir un proceso, enviar una tecla o no recibir una excepción no prueba que el objetivo
se haya cumplido. Cada herramienta debe definir el resultado observable que puede
comprobar y declarar lo que no puede confirmar.

### 4.5 Incrementos verticales

Cada versión agrega una capacidad principal y termina con una demostración útil. El
roadmap completo describe la dirección; no autoriza implementar módulos futuros antes
de cerrar la versión activa.

### 4.6 Interfaces reemplazables

CLI, aplicación de escritorio, voz, ChatGPT Remote y una futura aplicación móvil son
formas de enviar un mismo `UserCommand`. Ninguna interfaz debe contener la lógica de
seguridad ni acoplar el núcleo a un proveedor concreto.

## 5. Forma de trabajo con instrucciones breves

### 5.1 Comandos de colaboración

El usuario podrá utilizar estas frases sin preparar otro prompt:

| Instrucción | Comportamiento esperado |
| --- | --- |
| `estado` | Inspeccionar y resumir el estado comprobable sin escribir archivos. |
| `seguí` | Ejecutar solo el próximo paso habilitado de este roadmap. |
| `seguí al siguiente paso` | Equivalente a `seguí`. |
| `probemos` | Ejecutar o guiar el checkpoint de prueba pendiente. |
| `repetí las validaciones` | Repetir las pruebas documentadas del paso actual. |
| `pausá` | Detener trabajo nuevo y entregar estado y siguiente paso. |
| `cerrá la versión` | Verificar Definition of Done y preparar el cierre; no hacer commit. |
| `mostrame el plan` | Resumir pasos completos, actual y siguientes. |

`seguí` no significa avanzar indefinidamente. Autoriza un solo paso operativo. Si el
paso termina en un checkpoint, el asistente debe detenerse y devolver el control.

### 5.2 Protocolo obligatorio al recibir `seguí`

1. Leer `AGENTS.md` y este documento.
2. Ejecutar inspecciones no destructivas de Git y de los archivos relevantes.
3. Identificar el primer paso `PENDIENTE` cuyas dependencias estén `COMPLETADAS`.
4. Comprobar que no exista una decisión, permiso o prueba manual pendiente.
5. Resumir objetivo, límite, archivos esperados, riesgos y supuestos.
6. Marcar solo el paso elegido como `EN_PROGRESO`.
7. Implementar exclusivamente ese paso.
8. Agregar o actualizar tests proporcionales al cambio.
9. Ejecutar las validaciones pertinentes.
10. Revisar el diff y preservar cambios ajenos.
11. Actualizar el estado y la evidencia del paso.
12. Detenerse con el resultado y el próximo checkpoint.

Si la evidencia muestra que el paso ya estaba implementado, no se repite: se valida,
se documenta la evidencia y se selecciona el siguiente paso en un turno posterior.

### 5.3 Estados permitidos

| Estado | Significado |
| --- | --- |
| `PENDIENTE` | No existe evidencia suficiente de implementación. |
| `EN_PROGRESO` | Es el único paso que se está trabajando. |
| `LISTO_PARA_PRUEBA` | Implementado y automatizado; falta una prueba manual acordada. |
| `ESPERA_DECISION` | Requiere una elección o autorización del usuario. |
| `BLOQUEADO` | Existe un impedimento concreto documentado. |
| `COMPLETADO` | Criterios y validaciones del paso están aprobados. |

Nunca puede haber más de un paso `EN_PROGRESO`.

### 5.4 Tipos de checkpoint

- `AUTOMATICO`: el asistente puede implementar y validar localmente.
- `DECISION_USUARIO`: se deben presentar alternativas y detenerse.
- `PRUEBA_USUARIO`: el usuario debe probar un comportamiento visible o sensible.
- `PERMISO_EXTERNO`: credenciales, dependencias, red, publicación o servicios.
- `CIERRE_VERSION`: revisión completa y formato de cierre de `AGENTS.md`.

## 6. Estado maestro

| Versión | Capacidad principal | Estado actual | Próximo hito |
| --- | --- | --- | --- |
| v0.1 | Ejecutor y apertura de URLs | `COMPLETADO` | Ninguno. |
| v0.2 | Lanzador seguro de aplicaciones | `COMPLETADO` | Ninguno. |
| v0.3 | Interpretación de lenguaje natural | `COMPLETADO` | Ninguno. |
| v0.4 | Automatización de navegador | `EN_PROGRESO` | Implementar WEB-02. |
| v0.5 | Tareas de varios pasos | `PENDIENTE` | Solo después de cerrar v0.4. |
| v0.6 | Observación mediante screenshots | `PENDIENTE` | Solo después de cerrar v0.5. |
| v0.7 | Interpretación visual | `PENDIENTE` | Solo después de cerrar v0.6. |
| v0.8 | Mouse y teclado con límites | `PENDIENTE` | Solo después de cerrar v0.7. |
| v0.9 | Bucle observar-planificar-actuar-evaluar | `PENDIENTE` | Solo después de cerrar v0.8. |
| v0.10 | Recuperación y memoria procedural | `PENDIENTE` | Solo después de cerrar v0.9. |
| v0.11 | Confirmaciones y permisos completos | `PENDIENTE` | Solo después de cerrar v0.10. |
| v0.12 | Aplicación de escritorio y servicio local | `PENDIENTE` | Solo después de cerrar v0.11. |
| v0.13 | Entrada por voz | `PENDIENTE` | Solo después de cerrar v0.12. |
| v0.14 | Control remoto propio | `PENDIENTE` | Solo después de cerrar v0.13. |

Las versiones posteriores a v0.12 extienden la visión original y deberán revisarse
cuando se acerquen. Su presencia no autoriza incorporarlas antes de tiempo.

## 7. v0.3 — Interpretación de lenguaje natural

### Objetivo de versión

Aceptar formulaciones variadas para capacidades ya existentes sin permitir que el
modelo invente herramientas, URLs, ejecutables, argumentos, permisos o riesgo.

### Fuera de alcance

- planes de varios pasos;
- navegación dentro de páginas;
- screenshots, visión, mouse o teclado;
- memoria de recorridos;
- control remoto propio;
- nuevas acciones sensibles.

### Pasos

#### NLI-01 — Contrato de propuestas

- Estado: `COMPLETADO`.
- Evidencia: `ActionProposal`, versión de esquema e intents de propuesta.
- Validación: tests de forma, tipos, versión y coherencia.

#### NLI-02 — Intérprete híbrido con proveedor falso

- Estado: `COMPLETADO`.
- Evidencia: prioridad del parser determinista, fallback opcional y fallo seguro.
- Validación: proveedor falso y rechazo de salida inválida o no soportada.

#### NLI-03 — Decisión de proveedor y privacidad

- Estado: `COMPLETADO`.
- Checkpoint resuelto: `DECISION_USUARIO` y `PERMISO_EXTERNO`, opción A
  aprobada por el usuario el 2026-08-24.
- Decisión adoptada:
  - proveedor OpenAI mediante Responses API;
  - modelo `gpt-5.6-luna` con razonamiento `none` para priorizar latencia y costo;
  - Structured Outputs con el esquema estricto y versionado de `ActionProposal`;
  - SDK oficial de Python `openai` frente a un cliente HTTP propio, manteniendo sus
    tipos fuera del dominio; licencia Apache-2.0 y dependencias transitivas a
    revalidar para la versión exacta antes de instalar;
  - integración deshabilitada por defecto y credencial solo desde
    `OPENAI_API_KEY`, sin registrarla ni guardarla en el repositorio;
  - el parser determinista conserva prioridad; se permite como máximo una llamada
    sin reintento automático por comando no reconocido;
  - timeout inicial objetivo de 5 segundos y salida limitada al objeto de propuesta;
  - `store=false` y solicitudes independientes, sin estado conversacional.
- Datos permitidos en una solicitud:
  - texto de la orden actual;
  - instrucción mínima de clasificación;
  - claves canónicas del catálogo permitido;
  - esquema JSON de la propuesta.
- Datos excluidos: screenshots, archivos, logs, historial, variables de entorno,
  rutas, contenido de ventanas y cualquier otro estado del escritorio.
- Política de fallo: timeout, red, límite, rechazo o salida inválida termina sin
  construir ni ejecutar una acción.
- Costo de referencia al 2026-08-24: USD 0,20 por millón de tokens de entrada y
  USD 1,20 por millón de tokens de salida; registrar consumo cuando el proveedor lo
  informe y mantener un presupuesto explícito antes de una prueba real.
- Privacidad conocida: OpenAI declara que los datos de API no se usan para entrenar
  modelos salvo participación voluntaria; los registros de monitoreo pueden conservar
  contenido hasta 30 días bajo la configuración predeterminada.
- Fuentes consultadas:
  - https://developers.openai.com/api/docs/models/gpt-5.6-luna
  - https://developers.openai.com/api/reference/cli/resources/responses/methods/create
  - https://developers.openai.com/api/docs/guides/structured-outputs
  - https://developers.openai.com/api/docs/guides/your-data
  - https://developers.openai.com/api/docs/libraries
- Resultado: decisión registrada sin instalar el SDK, solicitar o guardar una clave,
  implementar el adaptador ni realizar llamadas de red.

#### NLI-04 — Configuración segura del proveedor

- Estado: `COMPLETADO`.
- Evidencia:
  - `ProviderConfig` y `load_provider_config` independientes del SDK;
  - opt-in mediante `DESKTOP_AGENT_AI_ENABLED`, deshabilitado por defecto;
  - credencial solo desde `OPENAI_API_KEY`, no conservada al estar deshabilitado;
  - timeout obligatorio, con 5 segundos por defecto y máximo de 30;
  - errores tipados que identifican la variable sin repetir su valor;
  - `ProposalProvider` continúa siendo la interfaz entre dominio y adaptador.
- Validación: 11 tests unitarios de configuración válida e inválida, credencial
  ausente, integración deshabilitada, invariantes y redacción de secretos.
- Límite: no se instaló el SDK, no se implementó el adaptador y no hubo red.

#### NLI-05 — Adaptador real de propuestas

- Estado: `COMPLETADO`.
- Dependencia: NLI-04.
- Checkpoint resuelto: el usuario autorizó expresamente el 2026-08-24 agregar
  `openai==3.3.1` y descargarlo en un entorno temporal para pruebas sin llamadas
  reales.
- Evaluación realizada el 2026-08-24:
  - versión actual observada en el registro: `openai==3.3.1`;
  - metadata del wheel: Python `>=3.10` y licencia `Apache-2.0`;
  - dependencias directas: `anyio`, `httpx2`, `jiter`, `pydantic`, `sniffio` y
    `typing-extensions`;
  - resolución comprobada en Python 3.12/Windows: 13 paquetes transitivos y 14
    wheels en total, aproximadamente 4,95 MB descargados;
  - licencias declaradas del conjunto: Apache-2.0, MIT, BSD-3-Clause, PSF-2.0 y
    la expresión dual MIT o Apache-2.0;
  - compatibilidad: tanto el proyecto como el SDK requieren Python 3.10 o posterior;
  - mantenimiento: es el SDK oficial recomendado por la documentación de OpenAI y
    `3.3.1` era la versión más reciente informada por el registro;
  - alternativa descartada: un cliente HTTP propio reduciría dependencias, pero
    agregaría autenticación, transporte y decodificación que el SDK ya mantiene;
  - riesgo pendiente: no se ejecutó un escáner de vulnerabilidades dedicado; se
    deberán revisar la instalación resuelta y sus cambios antes del cierre de v0.3.
- Evidencia:
  - `openai==3.3.1` declarado como dependencia exacta en `pyproject.toml`;
  - `OpenAIProposalProvider` realiza como máximo una `responses.create`;
  - `store=false`, `max_retries=0`, timeout por solicitud, razonamiento `none` y
    límites de 1.000 caracteres de entrada y 128 tokens de salida;
  - instrucciones mínimas, claves canónicas y JSON Schema estricto sin datos del
    escritorio;
  - el adaptador devuelve JSON no confiable y nunca construye una `Action`;
  - timeout, red, inicialización, respuesta incompleta o vacía y JSON inválido
    terminan en `ProposalProviderError` sin detalles sensibles;
  - la versión desconocida y demás incoherencias se rechazan luego en el dominio.
- Validación:
  - 11 tests nuevos con cliente falso y sin red;
  - smoke test con el SDK temporal y `responses.create` reemplazado por un mock;
  - suite completa: 57 tests aprobados;
  - ninguna API key ni llamada real.
- Limitación del paso al completarse: no se ejecutó un escáner de vulnerabilidades
  dedicado y todavía no había integración con la CLI; NLI-06 resolvió luego la
  segunda limitación.

#### NLI-06 — Integración híbrida con la CLI

- Estado: `COMPLETADO`.
- Dependencia: NLI-05.
- Evidencia:
  - `desktop_agent.cli.build_interpreter` carga la configuración una vez y construye
    `HybridInterpreter` con el adaptador solo ante un opt-in válido;
  - `process_command` usa el intérprete inyectable y conserva sus valores de retorno,
    mensajes y ejecución mediante `ActionExecutor`;
  - el modo interactivo reutiliza una misma instancia de intérprete;
  - la configuración inválida emite un aviso genérico y conserva el modo
    determinista, sin repetir valores ni crear el proveedor;
  - `OpenAIProposalProvider` difiere la importación e inicialización del cliente SDK
    hasta el primer fallback real.
- Validación:
  - 4 tests nuevos de integración de CLI con dobles y sin red;
  - un comando exacto ejecuta el camino de v0.2 sin invocar al proveedor;
  - una propuesta válida sigue pasando por validación y ejecutor locales;
  - un fallo externo no ejecuta ninguna herramienta;
  - una configuración inválida no rompe un comando determinista;
  - suite completa: 61 tests aprobados;
  - ninguna API key real ni llamada externa.
- Limitación del paso al completarse: todavía no registraba origen, duración ni
  resultado estructurado; NLI-07 resolvió luego esa limitación.

#### NLI-07 — Observabilidad de interpretación

- Estado: `COMPLETADO`.
- Dependencia: NLI-06.
- Evidencia:
  - `InterpretationResult` distingue camino `deterministic` o `external`, estados
    `success`, `unsupported`, `provider_error` e `invalid_proposal`, duración y si el
    proveedor está configurado;
  - `interpret_detailed` devuelve metadata estructurada y `interpret` conserva la
    interfaz compatible `Action | None`;
  - el reloj es inyectable para medir duración sin esperas reales en tests;
  - `ProposalProviderResult` transporta únicamente payload no confiable, contadores
    numéricos validados y costo opcional;
  - el adaptador extrae `response.usage`, ignora telemetría incoherente y conserva
    solo los contadores numéricos incluso si la respuesta termina en error;
  - el costo se estima para `gpt-5.6-luna` con las tarifas oficiales consultadas el
    2026-08-24;
  - la CLI reemplaza `User: <orden>` por eventos que no incluyen prompt, respuesta,
    credencial ni contexto del escritorio.
- Validación:
  - caminos determinista y externo y todos los estados comprobados con dobles;
  - duración comprobada mediante reloj falso;
  - uso válido, uso inválido y costo aproximado comprobados sin SDK ni red;
  - logs comprobados sin orden completa, JSON de respuesta, error externo ni API key
    ficticia;
  - suite completa: 63 tests aprobados;
  - ninguna API key real ni llamada externa.
- Limitación del paso al completarse: el costo era solo por llamada y no persistía ni
  aplicaba un límite; NLI-07A resolvió esa parte. Las tarifas siguen requiriendo
  revalidación antes de una prueba real.

#### NLI-07A — Presupuesto y consumo mensual local

- Estado: `COMPLETADO`.
- Dependencia: NLI-07.
- Solicitud del usuario: conteo persistente de tokens y dinero con límite mensual
  modificable, inicialmente USD 1,00.
- Evidencia:
  - `DESKTOP_AGENT_AI_MONTHLY_BUDGET_USD` vale USD 1,00 por defecto y acepta valores
    entre USD 0,01 y USD 1.000;
  - `MonthlyUsageLedger` conserva historial por mes calendario local, solicitudes,
    tokens de entrada, salida, caché y total, costo estimado, solicitudes sin medición
    y reservas pendientes;
  - el archivo se ubica fuera del repositorio en
    `%LOCALAPPDATA%\DesktopAgent\ai_usage.json`, con fallback a
    `~/.desktop_agent/ai_usage.json`;
  - el registro no contiene orden, respuesta, credencial ni contexto del escritorio;
  - `BudgetedProposalProvider` persiste una reserva conservadora de USD 0,01 antes de
    cada llamada y la sustituye por el costo estimado informado al finalizar;
  - una solicitud sin telemetría conserva la reserva como costo conservador y una
    reserva pendiente sobrevive un cierre inesperado;
  - presupuesto agotado, archivo corrupto, lectura o escritura fallida bloquean el
    fallback sin ejecutar una acción;
  - la CLI muestra el acumulado después de cada fallback y lo registra sin contenido
    sensible.
- Validación:
  - acumulación de dos llamadas, persistencia entre instancias y cambio de mes;
  - conservación del historial y de reservas pendientes;
  - bloqueo antes de invocar al proveedor y fallo seguro ante archivo corrupto;
  - salida y logs mensuales comprobados con dobles y archivos temporales;
  - suite completa: 74 tests aprobados;
  - ninguna API key real ni llamada externa.
- Limitaciones:
  - el costo es una estimación local basada en `response.usage` y las tarifas
    documentadas, no el importe final de la factura;
  - el mecanismo presupone una única instancia activa; no coordina procesos paralelos;
  - la reserva de USD 0,01 puede dejar sin usar hasta un centavo del límite mensual.

#### NLI-08 — Aceptación y cierre de v0.3

- Estado: `COMPLETADO`.
- Checkpoints: `PRUEBA_USUARIO` y `CIERRE_VERSION`.
- Decisión del usuario: aceptación simulada solicitada el 2026-08-24; sin red, API
  key, costo ni apertura real de aplicaciones.
- Evidencia:
  - `abrir youtube` siguió el camino determinista sin invocar al proveedor;
  - `poneme YouTube`, `quiero hacer una cuenta en la calculadora` y
    `abrime el editor Visual Studio Code` produjeron únicamente acciones del catálogo;
  - `abrime Spotify` fue rechazado por estar fuera del catálogo;
  - proveedor deshabilitado y fallo simulado terminaron sin efectos;
  - el acumulado mensual fue visible y el presupuesto bloqueó antes del proveedor;
  - seis pruebas de aceptación nuevas y suite completa de 80 tests aprobadas;
  - README, documentación, banner y versión `0.3.0` sincronizados;
  - terceros y limitaciones declarados; no se creó commit.
- Limitación de aceptación: el adaptador no fue probado contra la API real ni se
  verificó la apertura observable de ventanas; ambos límites quedan explícitos.

## 8. v0.4 — Automatización de navegador

### Objetivo de versión

Controlar un flujo web acotado mediante DOM y una herramienta registrada, con
verificación observable. La demostración candidata es buscar y reproducir contenido
en YouTube sin depender de mouse o visión.

### Pasos

#### WEB-01 — Propuesta técnica y dependencia

- Estado: `COMPLETADO`.
- Checkpoint: `DECISION_USUARIO` y `PERMISO_EXTERNO`.
- Decisión aprobada por el usuario el 2026-08-25:
  - Playwright `1.62.0`, API síncrona y versión fijada;
  - únicamente Chromium administrado por Playwright;
  - contexto temporal no persistente, sin login ni perfil personal;
  - navegador visible para la demostración con audio y ejecución headless solo para
    pruebas que no necesiten reproducción audible;
  - Chrome estable con perfil separado queda como fallback sujeto a evidencia de un
    problema de codecs; conectar el perfil personal o usar CDP queda fuera.
- Evidencia:
  - licencia Apache-2.0 y Python >=3.10 confirmados en la metadata oficial;
  - compatibilidad comprobada con el Python 3.13 local;
  - dependencia declarada en `pyproject.toml`;
  - Chrome for Testing 151.0.7922.34, shell headless, FFmpeg y verificador instalados;
  - Firefox y WebKit ausentes; caché local observada de aproximadamente 701 MiB;
  - no se lanzó ni controló ningún navegador.
- Riesgos aceptados:
  - cada actualización de Playwright puede exigir descargar binarios compatibles;
  - el flujo real dependerá del DOM, consentimiento, anuncios y políticas de YouTube;
  - la reproducción audible debe verificarse y no puede inferirse de un clic.

#### WEB-02 — Contrato del adaptador de navegador

- Estado: `PENDIENTE`.
- Definir operaciones semánticas; no aceptar selectores arbitrarios del modelo.
- Inyectar navegador, página, reloj y esperas para tests.
- Definir timeout y cierre de recursos.

#### WEB-03 — Herramienta de navegación segura

- Estado: `PENDIENTE`.
- Permitir solo destinos definidos por catálogo o política local.
- Aislar entorno, extensiones y acceso a archivos cuando sea posible.
- Registrar intención, destino canónico, duración y resultado.

#### WEB-04 — Flujo vertical de YouTube

- Estado: `PENDIENTE`.
- Interpretar una consulta ya estructurada.
- Abrir búsqueda, esperar resultados, seleccionar el primer resultado que cumpla el
  criterio local e iniciar reproducción.
- Tratar consentimiento, ausencia de resultados y cambios de DOM como fallos
  controlados.

#### WEB-05 — Verificación de reproducción

- Estado: `PENDIENTE`.
- Comprobar título o URL del contenido, estado no pausado y progreso temporal cuando
  el navegador lo permita.
- No declarar éxito solo por haber hecho clic.

#### WEB-06 — Tests del adaptador

- Estado: `PENDIENTE`.
- Unitarios con página falsa.
- Integración local reproducible cuando sea posible.
- E2E real opcional y separado por dependencia de red y cambios externos.

#### WEB-07 — Prueba manual

- Estado: `PENDIENTE`.
- Checkpoint: `PRUEBA_USUARIO`.
- Probar consulta válida, sin resultados, contenido no disponible y cancelación.
- Medir tiempo total y separar interpretación, inicio del navegador y navegación.

#### WEB-08 — Cierre de v0.4

- Estado: `PENDIENTE`.
- Checkpoint: `CIERRE_VERSION`.

## 9. v0.5 — Tareas de varios pasos

### Objetivo de versión

Permitir que la IA proponga un plan estructurado y limitado compuesto únicamente por
acciones conocidas, manteniendo validación y ejecución separadas.

### Secuencia

1. `PLAN-01`: diseñar `TaskPlan`, `PlanStep` y estados de paso.
2. `PLAN-02`: definir cantidad máxima de acciones, duración y cero ejecución parcial
   cuando el plan sea inválido.
3. `PLAN-03`: validar todas las acciones contra catálogo y política local.
4. `PLAN-04`: implementar ejecución secuencial con resultados estructurados.
5. `PLAN-05`: agregar cancelación entre pasos.
6. `PLAN-06`: registrar paso, herramienta, duración, éxito, fallo o cancelación.
7. `PLAN-07`: probar planes válidos, inválidos, demasiado largos y fallos intermedios.
8. `PLAN-08`: demostrar una tarea web de varios pasos y cerrar la versión.

No se permiten todavía bucles libres, reintentos autónomos ni nuevas herramientas
inventadas por el modelo.

## 10. v0.6 — Observación mediante screenshots

### Objetivo de versión

Capturar estado visual acotado para verificar o preparar una acción sin enviar por
defecto el escritorio completo a un servicio externo.

### Secuencia

1. definir región, ventana objetivo y metadatos de captura;
2. capturar localmente mediante una interfaz reemplazable;
3. limitar resolución, frecuencia, cantidad y retención;
4. asociar cada captura a un identificador y ventana concretos;
5. impedir reutilizar coordenadas o identificadores después de cambiar el estado;
6. redactar o excluir regiones sensibles cuando corresponda;
7. probar con imágenes ficticias y sin capturar el escritorio real;
8. realizar una prueba manual autorizada y cerrar la versión.

## 11. v0.7 — Interpretación visual

### Objetivo de versión

Convertir una observación visual minimizada en una propuesta estructurada, sin
permitir que el modelo ejecute directamente acciones.

### Secuencia

1. decidir proveedor, modelo, privacidad, costo y habilitación explícita;
2. definir esquema de elementos, confianza operativa y resultado `UNSUPPORTED`;
3. enviar solo la región necesaria;
4. validar toda respuesta localmente;
5. combinar texto de accesibilidad cuando esté disponible;
6. rechazar instrucciones presentes dentro del contenido observado;
7. medir precisión con un conjunto de pantallas ficticias;
8. probar y cerrar la versión.

## 12. v0.8 — Mouse y teclado con límites

### Objetivo de versión

Actuar sobre una ventana objetivo verificada, con foco comprobado, límites estrictos
y capacidad de emergencia.

### Requisitos previos obligatorios

- ventana exacta y observable;
- acción estructurada validada;
- presupuesto de inputs;
- cancelación probada;
- atajo o botón de emergencia;
- prohibición de automatizar terminales, autenticación, seguridad y campos secretos;
- preferencia por elementos accesibles antes que coordenadas.

### Secuencia

1. diseñar interfaz de input inyectable;
2. implementar selección única de ventana;
3. implementar clic por elemento accesible;
4. implementar escritura con foco observado;
5. agregar coordenadas como fallback asociado a una captura vigente;
6. detenerse ante cambio de ventana, modal desconocido o pérdida de foco;
7. probar completamente con dobles antes de efectos reales;
8. realizar demo manual segura y cerrar la versión.

## 13. v0.9 — Bucle observar-planificar-actuar-evaluar

### Objetivo de versión

Permitir recuperación acotada entre pasos sin transformar el agente en un bucle
infinito o irrestricto.

### Límites mínimos

- tiempo total;
- cantidad de observaciones;
- cantidad de acciones;
- reintentos por causa;
- herramientas permitidas;
- condición de éxito;
- condición de abandono;
- cancelación en todo momento.

### Secuencia

1. definir máquina de estados del ciclo;
2. separar observación, decisión, acción y evaluación;
3. conservar evidencia estructurada de cada iteración;
4. reintentar solo errores clasificados como transitorios;
5. bloquear repetición de la misma acción sin estado nuevo;
6. terminar ante ambigüedad persistente;
7. probar límites y cancelación;
8. demostrar un flujo de QA controlado y cerrar la versión.

## 14. v0.10 — Recuperación y memoria procedural

### Objetivo de versión

Reutilizar procedimientos aprobados y estrategias de recuperación sin reentrenar el
modelo ni guardar información privada innecesaria.

### Una receta debe incluir

- aplicación y versión o huella pertinente;
- objetivo canónico;
- precondiciones;
- acciones semánticas;
- verificaciones por paso;
- estrategia alternativa permitida;
- fecha y evidencia de la última validación;
- estado: propuesta, aprobada, obsoleta o deshabilitada.

### Reglas

- no almacenar contraseñas, tokens, contenido privado ni screenshots completos;
- no aprender automáticamente una receta solo porque no hubo excepción;
- requerir éxito observable y aprobación antes de reutilizar;
- invalidar ante cambios de aplicación o selectores;
- no guardar coordenadas como mecanismo principal.

## 15. v0.11 — Confirmaciones y permisos completos

### Objetivo de versión

Permitir acciones distintas de `SAFE` mediante una política explícita, auditable y
coherente entre interfaz local y futura interfaz remota.

### Secuencia

1. definir taxonomía de riesgo por efecto, no por nombre del comando;
2. definir acciones siempre bloqueadas;
3. definir acciones que requieren confirmación inmediata;
4. asociar confirmación a acción, argumentos, destino y vencimiento;
5. impedir que una confirmación autorice acciones posteriores diferentes;
6. implementar rechazo, timeout y cancelación;
7. probar carrera, repetición y confirmación obsoleta;
8. auditar logs y cerrar la versión.

Hasta completar esta versión, las capacidades anteriores deben permanecer en
escenarios seguros, aislados o de prueba.

## 16. v0.12 — Aplicación de escritorio y servicio local

### Objetivo de versión

Ofrecer una interfaz visible y un proceso local controlado sin mover lógica de dominio
fuera del núcleo.

### Alcance inicial

- entrada de texto;
- historial de tareas y estados;
- confirmaciones;
- cancelar y detener;
- indicador de herramienta activa;
- resultado y evidencia;
- inicio manual del servicio;
- comportamiento seguro al cerrar sesión o bloquear el equipo.

La ejecución permanente al iniciar Windows requiere una decisión separada y no debe
habilitarse de forma silenciosa.

## 17. v0.13 — Entrada por voz

### Objetivo de versión

Transformar audio en el mismo `UserCommand` utilizado por CLI y GUI.

### Secuencia

1. decidir reconocimiento local o externo y política de privacidad;
2. comenzar con pulsar-para-hablar, no escucha permanente;
3. mostrar y permitir corregir la transcripción;
4. separar confirmación verbal de una orden original;
5. soportar cancelación por voz y por botón;
6. medir latencia de captura, transcripción, interpretación y ejecución;
7. probar ruido, frases incompletas y comandos ambiguos;
8. evaluar palabra de activación solo después de validar el flujo base.

## 18. v0.14 — Control remoto propio

### Objetivo de versión

Permitir que una futura interfaz móvil envíe órdenes autenticadas al servicio local,
reciba estado y confirme acciones sin exponer la computadora directamente a Internet.

### Requisitos

- vinculación explícita de dispositivo;
- autenticación y cifrado;
- identificadores únicos y protección contra repetición;
- conexión saliente o relay seguro;
- estado online, bloqueado o no disponible;
- confirmaciones sensibles desde el teléfono;
- logs y revocación de dispositivos;
- límites de comandos y cancelación remota;
- ausencia de secretos dentro de mensajes o URLs.

ChatGPT Remote y el chat `Desktop Agent Control` funcionan como interfaz provisional
de experimentación. No forman parte del núcleo ni reemplazan la autenticación de la
futura solución propia.

## 19. Demostraciones verticales previstas

Las demostraciones validan la capacidad de una versión; no agregan por sí mismas una
nueva arquitectura.

### 19.1 Reproducir contenido en YouTube

```text
Usuario: "Poné [consulta] en YouTube"
Resultado esperado:
  - consulta interpretada;
  - navegador controlado;
  - resultado seleccionado según criterio local;
  - reproducción comprobada;
  - título informado;
  - fallo seguro ante bloqueo o ambigüedad.
```

### 19.2 Probar alta de cliente

Debe ejecutarse solamente sobre una aplicación y datos de prueba:

- caso válido;
- campos obligatorios;
- formato inválido;
- duplicado;
- error esperado del backend;
- comprobación de mensaje y estado final;
- reporte con evidencia;
- limpieza mediante fixture o entorno descartable.

Nunca se crean clientes ficticios en producción por asumir que una pantalla es de
testing.

### 19.3 Delegar desarrollo de software

Una orden de desarrollo debe enrutarse a un agente de código con repositorio, alcance,
rama, tests y revisión. Desktop Agent no debe escribir código simulando teclas en VS
Code cuando existen herramientas directas y verificables para editar el repositorio.

## 20. Estrategia transversal de testing

Orden recomendado:

1. unitarios de dominio, validación y política;
2. unitarios de herramientas con dependencias falsas;
3. integración local sin red ni efectos reales;
4. E2E en entorno aislado;
5. prueba manual visible;
6. prueba externa opcional y autorizada.

Cada corrección debe reproducir el defecto con una prueba cuando sea razonable. No se
debilitan tests para obtener una suite verde.

Métricas útiles por escenario:

- resultado correcto;
- tiempo hasta acuse de recibo;
- tiempo total;
- cantidad de llamadas al modelo;
- observaciones y acciones;
- reintentos;
- confirmaciones;
- costo aproximado;
- recuperación o causa de abandono.

## 21. Presupuesto de latencia

El proyecto debe distinguir:

- tarea conocida por camino determinista;
- receta conocida;
- objetivo nuevo en una aplicación conocida;
- aplicación nueva que requiere exploración.

No se fija todavía una cifra universal. Cada demostración deberá medir por separado:

```text
entrada
  + interpretación
  + inicio o recuperación de sesión
  + ejecución
  + verificación
  = duración total
```

Optimizaciones permitidas después de medir:

- mantener el servicio local preparado;
- reutilizar sesiones seguras;
- parser determinista antes del modelo;
- recetas aprobadas;
- una llamada de IA para planificar y ejecución local del recorrido;
- esperas por condiciones en vez de pausas fijas;
- DOM o accesibilidad antes de imágenes;
- respuesta inmediata de recepción seguida del resultado final.

La velocidad nunca justifica omitir verificaciones o confirmaciones relevantes.

## 22. Privacidad y seguridad transversal

- El equipo se considera un entorno con información potencialmente sensible.
- Screenshots y accesibilidad se minimizan antes de enviarse externamente.
- El contenido observado es no confiable y no puede modificar la política.
- Credenciales y tokens permanecen fuera de código, tests, prompts y logs.
- La IA no puede decidir su propio nivel de riesgo ni concederse permisos.
- La selección de ventana debe ser inequívoca.
- La pérdida de foco o un modal desconocido detienen el flujo.
- Los datos de pruebas son ficticios y los entornos están identificados.
- Toda tarea remota tiene timeout, cancelación y registro.
- El usuario mantiene un mecanismo de emergencia probado.

## 23. Mantenimiento de este roadmap

Al completar un paso se debe actualizar:

- estado;
- evidencia concreta;
- validaciones ejecutadas;
- decisiones adoptadas;
- limitaciones descubiertas;
- próximo checkpoint.

No se agregan nuevas versiones por cada idea puntual. Una capacidad futura se registra
solo cuando cambia materialmente la arquitectura o el producto y el usuario aprueba
incorporarla al roadmap.

## 24. Formato de entrega de cada paso

```text
Paso completado:
<identificador y nombre>

Qué cambió:
...

Archivos:
...

Validaciones realizadas:
...

Prueba manual pendiente:
...

Limitaciones:
...

Próximo paso habilitado:
...

¿Se debe detener?:
Sí/No y motivo.
```

## 25. Próxima acción autorizable

El primer paso no completado es `WEB-02 — Contrato del adaptador de navegador` de
v0.4.

Cuando el usuario solicite continuar se deberá definir e implementar únicamente el
contrato semántico reemplazable, sus errores, límites y tests unitarios. WEB-02 no
autoriza todavía navegar por YouTube ni abrir un navegador real.
