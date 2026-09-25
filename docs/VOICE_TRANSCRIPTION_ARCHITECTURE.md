# Transcripción general de voz

## Motivo de la corrección

La auditoría real del 2026-08-26 reprodujo el defecto: `System.Speech` convirtió
`abrí calculadora` en texto sin relación con la frase. Agregar una gramática con
comandos conocidos habría mejorado solamente esos ejemplos y degradado el objetivo del
producto: poder dictar texto nuevo sin ampliar manualmente una lista de frases.

La GUI usa ahora `gpt-transcribe` sin `prompt`, keywords ni vocabulario privilegiado.
El backend anterior queda como implementación histórica testeada, pero no se selecciona
en el arranque normal.

## Flujo vigente

```text
botón o atajo explícito opt-in
  -> SoundDeviceWavRecorder
  -> detector local de voz y silencio
  -> WAV mono de 16 bits solamente en memoria
  -> reserva exacta en MonthlyUsageLedger
  -> OpenAITranscriptionProvider
  -> texto editable
  -> revisión manual o envío automático opt-in al pipeline común
```

`SoundDeviceWavRecorder` usa `sounddevice.RawInputStream`, por lo que no requiere NumPy.
La captura dura como máximo veinte segundos, espera como máximo cinco segundos para que
comience la frase y corta tras 1,2 s de silencio final o al pulsar Terminé de hablar.
Cancelar descarta la frase. El WAV queda por debajo de 4 MiB, no se
escribe en disco y se descarta al finalizar la llamada.

El proveedor envía sólo el archivo WAV, `language="es"` y el modelo fijo
`gpt-transcribe`. La transcripción no contiene un puntaje de confianza comparable con
el backend de Windows; por defecto se presenta para revisión humana. El envío automático
es una elección explícita y no elimina las validaciones del ejecutor. No se registra
el audio, el texto, la API key ni el detalle privado de errores. Los fallos externos se
reducen a categorías seguras: configuración, autenticación, permisos, modelo, cuota o
límite, solicitud rechazada, timeout, red, servicio, respuesta inválida o desconocido.
La GUI muestra esa categoría con una corrección posible y el log conserva sólo su código.
El parser admite mayúsculas, acentos, espacios y signos de puntuación que el transcriptor
agregue únicamente en los límites de la frase; la puntuación interior no se elimina.

## Configuración y consentimiento

La voz externa está deshabilitada por defecto y requiere simultáneamente:

- `DESKTOP_AGENT_VOICE_TRANSCRIPTION_ENABLED=true`;
- una API key disponible para la sesión;
- un presupuesto mensual válido en `DESKTOP_AGENT_AI_MONTHLY_BUDGET_USD`.

El opt-in de voz es independiente de `DESKTOP_AGENT_AI_ENABLED`. Esa separación permite
transcribir una frase para el parser determinista sin habilitar el fallback de texto.
Habilitar ambos sigue sin permitir que el modelo ejecute directamente: el texto enviado,
manualmente o de forma automática opt-in, atraviesa `CommandProcessor`, catálogo,
política y `ActionExecutor`.

`python -m scripts.start_voice_gui` y `python -m desktop_agent --gui` abren la misma GUI.
Configuración permite pegar la clave enmascarada y guardarla opcionalmente con DPAPI,
fuera del repositorio. Ningún secreto se recibe como argumento. La selección del
micrófono conserva nombre y API de audio, no el índice volátil de PortAudio, y vuelve
a resolverse antes de capturar. Un dispositivo ausente o duplicado falla sin cambiar
silenciosamente a otro. El medidor sólo observa bloques durante una captura explícita.

## Presupuesto

La tarifa local usada es USD 0,0045 por minuto. Después de capturar y antes de subir el
audio se reserva el costo exacto estimado por duración. Si el saldo mensual no alcanza,
no hay llamada. Una cancelación comprobada antes del envío libera la reserva. También
la liberan los rechazos explícitos de configuración, autenticación, permisos, modelo,
cuota o solicitud. Timeout, red y errores inciertos conservan el costo estimado, ya que
no prueban que el proveedor no haya procesado audio. Los errores miden tiempo de
transcripción, en lugar de indicar siempre cero.

Voz, texto y visión comparten el mismo `MonthlyUsageLedger` y el límite predeterminado
de USD 1 al mes. Las transcripciones se cobran por duración y no exponen tokens de uso;
por eso incrementan solicitudes y dólares, pero no inventan tokens. El archivo local
conserva solamente agregados mensuales y reservas, nunca contenido.

## Fallo seguro y límites

- silencio, cancelación, micrófono inaccesible, configuración inválida y presupuesto
  agotado no producen una orden;
- el timeout externo es configurable entre más de cero y sesenta segundos;
- no hay escucha permanente, palabra de activación ni streaming; el envío automático
  requiere una captura iniciada con botón/atajo y opt-in previo;
- el detector RMS es deliberadamente simple y puede requerir ajuste para micrófonos o
  ambientes muy silenciosos o ruidosos;
- la precisión y disponibilidad dependen de un servicio externo y una conexión activa;
- transcribir bien no crea una herramienta nueva: una orden desconocida aún requiere
  el fallback de interpretación habilitado o una capacidad local implementada.

El atajo usa `RegisterHotKey`, no un hook de todas las teclas. Ambos registros se
liberan al cerrar; si hay un conflicto no queda uno de ellos activo por accidente.
Una transcripción duplicada, cancelada o llegada después del cierre no se ejecuta.

Los detalles de v0.17 y sus límites de aceptación están en
[V0.17_ARCHITECTURE.md](V0.17_ARCHITECTURE.md).

## Validación

Las pruebas cubren WAV y límites, silencio, cancelación, clasificación y redacción de
errores, opt-in, credencial, request sin hints, respuesta inválida, presupuesto previo,
costo por duración, libro compartido, liberación de reservas y privacidad. Se ejecutan
con dobles de micrófono y API. La llamada real sigue requiriendo una API key configurada
localmente y una prueba explícita del usuario.
