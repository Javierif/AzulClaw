# AzulClaw: Referencia de Componentes (API Interna)

**Fecha de última revisión:** 22 de Febrero de 2026.
**Objetivo:** Documentar cada archivo del proyecto, su responsabilidad, sus dependencias y los puntos de extensión para futuros desarrolladores.

---

## 1. AzulHands (Servidor MCP Local)

### 1.1 `azul_hands_mcp/path_validator.py`

**Responsabilidad:** Módulo de seguridad nuclear. Toda operación de disco debe pasar por aquí.

| Elemento | Detalle |
|---|---|
| **Clase** | `PathValidator` |
| **Constructor** | `PathValidator(allowed_base_dir: str)` — Define la carpeta raíz de la "jaula". |
| **Método principal** | `safe_resolve(requested_path: str) -> Path` |
| **Excepción** | `SecurityError` — Se lanza cuando una ruta intenta escapar del workspace. |

**Reglas internas de `safe_resolve()`:**
1. Expande variables de entorno (`%USERPROFILE%`) y `~`.
2. Si la ruta no es absoluta, la interpreta como relativa al `allowed_base`.
3. Resuelve la ruta canónica (elimina `../`, sigue symlinks).
4. Comprueba que la ruta resultante empiece por `allowed_base`. Si no → `SecurityError`.

**Cómo extenderlo:**
- Si necesitas añadir restricciones adicionales (ej. bloquear extensiones `.exe`, `.bat`), modifica `safe_resolve()` añadiendo validaciones después del paso 4.
- Ejemplo:
  ```python
  BLOCKED_EXTENSIONS = {'.exe', '.bat', '.cmd', '.ps1', '.vbs'}
  if resolved_target.suffix.lower() in BLOCKED_EXTENSIONS:
      raise SecurityError(f"Extensión bloqueada: {resolved_target.suffix}")
  ```

---

### 1.2 `azul_hands_mcp/mcp_server.py`

**Responsabilidad:** Servidor MCP que se comunica vía stdio (JSON-RPC 2.0) con el proceso padre.

| Elemento | Detalle |
|---|---|
| **Framework** | `mcp.server.Server` + `mcp.server.stdio.stdio_server` |
| **Workspace** | `C:\Users\{user}\Desktop\AzulWorkspace` |
| **Protocolo** | stdio (Standard Input/Output) — perfecto para procesos padre-hijo |

**Herramientas expuestas actualmente:**

| Tool Name | Descripción | Parámetros | Pasa por PathValidator |
|---|---|---|---|
| `list_workspace_files` | Lista archivos en un directorio del workspace | `path: string` (relativo) | ✅ Sí |
| `read_safe_file` | Lee contenido de un archivo de texto | `path: string` | ✅ Sí |
| `move_safe_file` | Mueve/renombra un archivo dentro del workspace | `source: string`, `destination: string` | ✅ Sí (ambos paths) |

**Cómo añadir una nueva herramienta:**

1. Añadir la lógica en la función `call_tool()` dentro de `mcp_server.py`:
   ```python
   elif name == "mi_nueva_herramienta":
       param = arguments.get("param", "")
       try:
           safe_path = validator.safe_resolve(param)  # OBLIGATORIO
           # ... tu lógica aquí ...
           return [types.TextContent(type="text", text="Resultado")]
       except SecurityError as e:
           return [types.TextContent(type="text", text=f"PATH DENIED: {str(e)}")]
   ```

2. Registrar la herramienta en `list_tools()`:
   ```python
   types.Tool(
       name="mi_nueva_herramienta",
       description="Descripción clara para que la IA entienda cuándo usarla.",
       inputSchema={
           "type": "object",
           "properties": {
               "param": {"type": "string", "description": "..."}
           },
           "required": ["param"]
       }
   )
   ```

> **REGLA DE ORO:** Toda herramienta que toque el disco DEBE llamar a `validator.safe_resolve()`. Nunca usar `os.path` o `Path()` directamente con datos provenientes de la IA.

---

## 2. AzulBrain (El Cerebro / Proceso Principal)

### 2.1 `azul_brain/mcp_client.py`

**Responsabilidad:** Wrapper que conecta al Cerebro con el Servidor MCP vía stdio.

| Elemento | Detalle |
|---|---|
| **Clase** | `AzulHandsClient` |
| **Constructor** | `AzulHandsClient(server_script_path: str)` |
| **Dependencias** | `mcp.ClientSession`, `mcp.client.stdio.stdio_client`, `contextlib.AsyncExitStack` |

**Métodos públicos:**

| Método | Descripción | Retorno |
|---|---|---|
| `connect()` | Arranca el proceso hijo y establece la sesión MCP | `None` (async) |
| `list_available_tools()` | Obtiene las herramientas registradas en AzulHands | `list[Tool]` |
| `call_tool(name, arguments)` | Ejecuta una herramienta en el servidor MCP | `CallToolResult` |
| `cleanup()` | Cierra la conexión y mata el proceso hijo | `None` (async) |

**Ejemplo de uso desde Microsoft Agent Framework (Fase 4):**
```python
# Dentro del Plugin de SK
tools = await mcp_client.list_available_tools()
result = await mcp_client.call_tool("read_safe_file", {"path": "informe.txt"})
contenido = result.content[0].text
```

---

### 2.2 `azul_brain/bot/azul_bot.py`

**Responsabilidad:** Controlador central de Azure Bot Framework. Procesa los mensajes entrantes.

| Elemento | Detalle |
|---|---|
| **Clase** | `AzulBot(ActivityHandler)` |
| **Constructor** | `AzulBot(mcp_client: AzulHandsClient)` |
| **Hereda de** | `botbuilder.core.ActivityHandler` |

**Métodos clave:**

| Método | Cuándo se dispara | Estado |
|---|---|---|
| `on_message_activity()` | El usuario envía un mensaje de texto | ✅ Implementado (Echo) |
| `on_members_added_activity()` | Un usuario se une al chat del bot | ✅ Implementado (Bienvenida) |

**Punto de extensión principal (Fase 4):**
En `on_message_activity()`, donde actualmente hay un echo, se debe:
1. Instanciar el `Kernel` de Microsoft Agent Framework.
2. Pasar el mensaje del usuario como input.
3. Registrar las herramientas MCP como plugins del Kernel.
4. Ejecutar el planner y devolver la respuesta.

```python
# Pseudocódigo de la integración futura
async def on_message_activity(self, turn_context: TurnContext):
    user_message = turn_context.activity.text
    
    # 1. Crear kernel con Azure OpenAI
    kernel = sk.Kernel()
    kernel.add_service(AzureChatCompletion(...))
    
    # 2. Registrar herramientas MCP como plugins
    kernel.add_plugin(MCPToolsPlugin(self.mcp_client), "desktop_tools")
    
    # 3. Invocar al planner
    result = await kernel.invoke_prompt(user_message)
    
    # 4. Responder
    await turn_context.send_activity(MessageFactory.text(str(result)))
```

---

### 2.3 `azul_brain/main_launcher.py`

**Responsabilidad:** Punto de entrada de toda la aplicación. Orquesta el arranque.

| Elemento | Detalle |
|---|---|
| **Servidor HTTP** | `aiohttp.web` en `localhost:3978` |
| **Ruta del webhook** | `POST /api/messages` |
| **Adaptador** | `BotFrameworkAdapter` con `BotFrameworkAdapterSettings` |

**Secuencia de arranque:**
1. Crea instancia de `AzulHandsClient` apuntando a `../azul_hands_mcp/mcp_server.py`.
2. Llama a `mcp_client.connect()` (arranca el proceso hijo MCP).
3. Crea instancia de `AzulBot(mcp_client)`.
4. Monta la app `aiohttp` con la ruta `/api/messages`.
5. Inicia el servidor HTTP en el puerto 3978.

**Nota para PyInstaller (Fase 5):** Cuando se compile a `.exe`, el path relativo de `mcp_server.py` debe resolverse correctamente. Usar `sys._MEIPASS` si PyInstaller empaqueta en modo `--onefile`:
```python
if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS
else:
    base_path = os.path.dirname(os.path.abspath(__file__))
```

---

## 3. Carpetas Pendientes de Desarrollo

| Carpeta | Propósito | Fase |
|---|---|---|
| `azul_brain/cortex/` | Integración de Microsoft Agent Framework, Plugins, Planners | Fase 4 |
| `azul_brain/memory/` | Memoria conversacional segura (JSON/SQLite sin pickle) | Fase 4 |
| `azul_brain/soul/` | System Prompts, personalidad del bot, delimitadores anti-injection | Fase 4 |

