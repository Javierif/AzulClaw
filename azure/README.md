# Recursos Cloud de Azure para AzulClaw

Esta carpeta contiene la infraestructura en la nube requerida para exponer AzulClaw de forma segura a canales públicos (como Alexa, Microsoft Teams, etc.) usando arquitectura *Dark IT / Cero Puertos*.

## Función Relay (`functions/bot_relay`)

La Azure Function actúa como la única fachada pública (el webhook HTTPS) que interactúa con el Bot Framework de Microsoft. Valida el tráfico, asegura la conexión y delega asincrónicamente el procesamiento a la máquina local mediante el **Azure Service Bus**.

### Instrucciones de Despliegue Manual

1. **Service Bus Namespace:** Crea un namespace de Azure Service Bus en el portal de Azure.
   * **Tier:** Standard (recomendado para usar *sessions*) o Basic.
2. **Colas:** Crea las siguientes colas dentro del namespace:
   * `bot-inbound`
   * `bot-outbound` (¡marca la casilla "Habilitar soporte de sesiones" / "Enable session support" si usas el tier Standard!)
3. **Azure Function:** Despliega esta función en Azure:
   * Puedes usar Visual Studio Code con la extensión de Azure Functions.
   * O mediante el interfaz de línea de comando: `func azure functionapp publish <NombreDeTuFunctionApp>`
   * Ejecuta el despliegue desde la carpeta `azure/functions/bot_relay`, que es donde están `host.json`, `requirements.txt` y `function_app.py`.
4. **Environment Variables en Azure:** Copia en la Function App desplegada las siguientes variables (Configuration):
   * `SERVICE_BUS_CONNECTION_STRING` (La cadena de conexión Root del Service Bus)
   * `SERVICE_BUS_INBOUND_QUEUE` (bot-inbound)
   * `SERVICE_BUS_OUTBOUND_QUEUE` (bot-outbound)
   * `SERVICE_BUS_USE_SESSIONS` (`auto`, `true`, o `false`)
   * `MicrosoftAppId` (el App ID del Azure Bot)
   * `MicrosoftAppPassword` (el secreto del Azure Bot)
   * `MicrosoftAppTenantId` (opcional, si usas tenant específico)

### Configuración del Azure Bot Service
Vaya a la página de **Configuración** de su Azure Bot. En el campo "Messaging Endpoint", introduzca el URL de su nueva Azure Function añadiendo `/api/messages`.

*Ejemplo:* `https://tulambda.azurewebsites.net/api/messages`

### Validación rápida del despliegue
- Compruebe `https://tulambda.azurewebsites.net/api/health`.
- Si esa URL devuelve `404`, la Function no se ha desplegado o indexado correctamente.
- Si `/api/health` funciona pero el bot falla, revise que el `Messaging Endpoint` sea exactamente `/api/messages`.
