# Guía operativa para asistentes de IA

Estas reglas se aplican a todo asistente que analice, modifique o ejecute el
repositorio. El estado comprobable tiene prioridad sobre planes o supuestos. Si una
solicitud contradice las reglas o amplía materialmente el alcance, se debe pedir
dirección antes de actuar.

## 1. Propósito del proyecto

Desktop Agent es un agente de escritorio progresivo y seguro: transforma
instrucciones en acciones explícitas para interactuar con una computadora. También
es un proyecto de aprendizaje y portfolio sobre arquitectura,
automatización, IA, seguridad y testing. El control humano, la privacidad y la
seguridad deben prevalecer sobre la autonomía o la cantidad de funcionalidades.

## 2. Alcance actual y desarrollo incremental

- Cada versión debe agregar una capacidad principal y quedar funcional, demostrable,
  documentada y testeable. No se debe adelantar el roadmap ni avanzar de versión sin
  solicitud explícita.
- El alcance actual de la tarea tiene prioridad sobre mejoras posibles o futuras.
- Las abstracciones deben responder a una necesidad comprobada, no a especulación.
- La versión y las capacidades actuales deben obtenerse de `pyproject.toml`, el
  paquete, el README, los tests y la implementación; no deben inferirse del roadmap.
- Actualmente existe una CLI determinista con catálogo, acciones, ejecutor y
  herramientas explícitas. Solo el repositorio prueba que una capacidad existe.

Comandos actualmente confirmados:

```powershell
python -m desktop_agent
python -m desktop_agent "abrir youtube"
python -m unittest discover -s tests -v
```

No hay un comando de lint documentado; cuando exista debe registrarse.

## 3. Principios de arquitectura

- Se deben preferir soluciones simples y mantenibles, responsabilidades separadas sin
  capas innecesarias, archivos acotados y abstracciones justificadas.
- Los nombres deben expresar intención; los comentarios deben explicar decisiones no
  evidentes, no repetir el código.
- El núcleo debe permanecer desacoplado de interfaces y proveedores. Las dependencias
  con efectos deben poder reemplazarse en tests.
- Se debe respetar el contrato actual entre parser, `Action`, `ActionExecutor`,
  catálogo y herramientas mientras siga siendo adecuado. Ninguna integración debe
  saltarse el registro de herramientas o la validación del ejecutor.
- Frameworks, patrones o servicios requieren una necesidad comprobable. Un cambio
  estructural grande exige propuesta, alternativas, archivos y riesgos previos.

Prioridad para elegir un mecanismo de automatización:

1. API o integración directa.
2. Comandos seguros del sistema.
3. DOM o Playwright.
4. APIs de accesibilidad.
5. Visión, mouse y teclado como último recurso.

## 4. Uso responsable de inteligencia artificial

- La IA asiste; no es la autora ni la responsable final. El usuario decide producto,
  arquitectura, alcance y aceptación.
- El asistente puede analizar, proponer, implementar y verificar solo dentro del
  alcance autorizado.
- Las decisiones y resultados relevantes deben explicarse con evidencia.
- Debe distinguirse entre trabajo implementado por el proyecto, trabajo asistido por
  IA y capacidades provistas por Python, modelos, bibliotecas o servicios externos.
- No se debe copiar código externo sin comprender y verificar procedencia, licencia,
  seguridad y adecuación.

## 5. Protocolo de colaboración

Antes de un cambio importante, el asistente debe:

1. resumir lo entendido y el límite de la tarea;
2. inspeccionar el estado real del repositorio de forma no destructiva;
3. explicar brevemente la propuesta y su motivo;
4. indicar los archivos que espera modificar;
5. exponer decisiones, riesgos y supuestos relevantes.

Durante la implementación debe preservar cambios del usuario, evitar reescrituras y
mejoras ajenas al alcance, y comunicar bloqueos o fallos sin ocultarlos.

Al finalizar debe validar según el riesgo, informar cambios y forma de prueba,
mencionar validaciones omitidas y limitaciones, y detenerse. Puede resolver detalles
pequeños, reversibles y coherentes sin pedir confirmación por cada uno.

## 6. Autonomía y permisos

- Una solicitud de explicar, analizar, revisar o diagnosticar no autoriza escrituras.
- Una solicitud de implementar, corregir o refactorizar autoriza solo los cambios
  locales necesarios para ese objetivo.
- Acciones destructivas, externas, irreversibles, costosas o que amplíen materialmente
  el alcance requieren confirmación explícita y un objetivo exacto verificado.
- Una dependencia de producción exige explicar necesidad y alternativas; instalarla
  requiere autorización si afecta al equipo o entorno.
- Crear commits, hacer push, abrir o fusionar Pull Requests, publicar, enviar mensajes
  o modificar servicios externos requiere autorización explícita.
- El asistente no debe eliminar, sobrescribir ni revertir cambios del usuario para
  facilitar su tarea, ni usar comandos destructivos sobre destinos ambiguos.

## 7. Seguridad del agente de escritorio

- Se debe aplicar mínimo privilegio: herramientas limitadas, entradas validadas y
  resultados estructurados.
- Ningún texto generado por un usuario o modelo debe ejecutarse directamente como
  shell, código, ruta, selector o acción de escritorio.
- Planificación y ejecución deben permanecer separadas. Un modelo podrá proponer una
  acción estructurada; el núcleo deberá validarla antes de ejecutarla.
- Toda acción debe declarar `RiskLevel` y `requires_confirmation` (el concepto
  `RequiresConfirmation`). Las acciones sensibles requieren confirmación explícita.
- Una acción no es exitosa solo porque no lanzó una excepción. Debe validarse el
  resultado observable adecuado a su riesgo y declarar los límites de esa validación.
- Los futuros bucles deben limitar tiempo, acciones y reintentos, y admitir cancelación.
  El control libre exige antes un botón o atajo de emergencia probado.
- Ante estado ambiguo se debe fallar de forma segura. Se debe preferir simulación o
  `dry-run` cuando sea aplicable y registrar acciones sin secretos.

## 8. Privacidad y secretos

- No se deben incluir claves, tokens o contraseñas en código, tests, documentación,
  prompts ni logs. Cuando corresponda se debe usar `.env`, manteniéndolo fuera de Git.
- Los mensajes de error y logs deben redactar datos sensibles y evitar contenido
  privado innecesario.
- Screenshots y contenido visible del escritorio deben tratarse como potencialmente
  sensibles. No deben enviarse a modelos o servicios externos sin necesidad,
  minimización de datos y autorización.
- Los datos sensibles deben redactarse; ejemplos y tests deben usar datos ficticios.

## 9. Reglas de implementación

- El código debe ser legible, con responsabilidades y errores explícitos; se prefieren
  funciones pequeñas cuando mejoran claridad.
- Se deben minimizar dependencias y conservar comportamiento determinista cuando un
  LLM no aporte valor real.
- Las acciones de modelos requieren esquemas y validación de entrada y salida; el
  modelo no debe eludir al ejecutor.
- Toda integración debe definir timeouts, errores esperables y comportamiento ante
  fallos cuando esos conceptos apliquen.
- Se debe mantener compatibilidad con el sistema operativo y el stack confirmados en
  el repositorio. No se deben imponer tecnologías todavía no adoptadas.

## 10. Testing y criterios de finalización

- Una funcionalidad no está terminada hasta que pueda comprobarse.
- Una corrección debe reproducir el defecto con una prueba cuando sea razonable. No se
  deben borrar o debilitar tests para obtener una ejecución verde.
- Las herramientas deben poder probarse independientemente de un LLM y, siempre que
  sea posible, sin efectos reales mediante dependencias inyectables.
- Se priorizan tests unitarios, luego integración y end-to-end según el riesgo. Todo
  fallo debe informarse con su causa conocida o probable.
- No se debe afirmar que una prueba fue ejecutada si no lo fue; se debe aclarar toda
  limitación del entorno.

Definition of Done:

- alcance acordado implementado, sin adelantar el roadmap;
- entradas, errores y riesgos relevantes tratados;
- pruebas nuevas o actualizadas y suite pertinente aprobada;
- comportamiento comprobado al nivel permitido por el entorno;
- README y documentación sincronizados cuando cambie el uso o la arquitectura;
- cambios revisados, limitaciones comunicadas y workspace sin modificaciones ajenas.

## 11. Logging y observabilidad

Los flujos nuevos o modificados deben registrar intención, herramienta, resultado,
duración y error relevante cuando aplique. Los eventos deben distinguir éxito, fallo,
cancelación y confirmación pendiente, y permitir reconstruir la ejecución. Los logs no
deben capturar contraseñas, tokens, contenido privado ni datos innecesarios; deben ser
útiles para depurar, no una copia indiscriminada del contexto del usuario.

## 12. Dependencias y servicios externos

Antes de agregar una dependencia se deben evaluar necesidad, mantenimiento, licencia,
seguridad, tamaño, compatibilidad y alternativas existentes. Modelos, Playwright y
otros servicios deben identificarse como externos, no como desarrollo propio. El
roadmap por sí solo no justifica incorporarlos.

## 13. Git y trazabilidad

- Antes de actuar se debe verificar el flujo vigente. El repositorio actualmente
  confirma `main`, `develop`, `feature/*` y commits integrados mediante Pull Request.
- `main` debe representar una versión estable; `develop`, la integración; `feature/*`
  y `fix/*`, cambios acotados.
- Los commits deben ser pequeños, descriptivos y seguir Conventional Commits. No se
  deben mezclar cambios no relacionados ni reescribir historial compartido.
- No se debe crear un commit, push o Pull Request sin solicitud explícita.
- Los cambios de comportamiento deben mantener sincronizados README, documentación y
  pruebas. Las decisiones arquitectónicas relevantes deben registrarse brevemente.

## 14. Documentación y portfolio

La documentación debe explicar problema, decisiones, arquitectura, versiones,
validaciones, límites, seguridad, terceros y uso de IA. El README prioriza explicación
y uso; la documentación técnica conserva el detalle. Se debe reconocer la asistencia
de IA sin presentar el proyecto como automático ni atribuir al autor capacidades de
terceros.

## 15. Formato de cierre de cada versión

```text
Versión completada:
v0.X

Qué funciona:
...

Qué aprendimos:
...

Validaciones realizadas:
...

Limitaciones:
...

Próximo paso recomendado:
...

Commit sugerido:
feat: ...
```

El commit solo se sugiere; no debe ejecutarse salvo solicitud explícita del usuario.
El asistente debe detenerse después de cerrar la versión.

## 16. Mantenimiento de estas instrucciones

`AGENTS.md` debe mantenerse conciso, sin duplicar el README ni convertirse en un
roadmap detallado. Las reglas no deben repetirse. Solo debe actualizarse cuando cambie
una política estable del proyecto; una tarea puntual no debe convertirse
automáticamente en regla permanente. Toda modificación material de estas instrucciones
debe mencionarse al usuario.
