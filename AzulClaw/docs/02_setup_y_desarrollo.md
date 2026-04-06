# AzulClaw: Guía de Configuración y Desarrollo Local

**Fecha de última revisión:** 6 de Abril de 2026.
**Objetivo:** Permitir a un nuevo desarrollador levantar el entorno de desarrollo de AzulClaw desde cero en Windows.

---

## 1. Requisitos Previos

| Requisito | Versión Mínima | Notas |
|---|---|---|
| **Python** | 3.11+ | Recomendado 3.13. Debe estar en PATH. |
| **Git** | 2.x | Para clonar el repositorio. |
| **Visual Studio Code** | Última | IDE recomendado (extensiones: Python, Pylance). |
| **Bot Framework Emulator** | 4.x | Para probar el bot localmente sin Azure. Descarga: [github.com/microsoft/BotFramework-Emulator](https://github.com/microsoft/BotFramework-Emulator/releases) |
| **Cuenta Azure** (Opcional Fase 4+) | N/A | Necesaria solo para conectar Azure OpenAI y Bot Service en producción. |

---

## 2. Instalación del Entorno

### 2.1 Clonar el repositorio
```powershell
git clone https://github.com/TU_USUARIO/AzulClaw.git
cd AzulClaw
```

### 2.2 Crear y activar el entorno virtual
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2.3 Instalar dependencias
```powershell
pip install -r requirements.txt
```

> **Nota:** El proyecto ya usa `agent-framework`. Si añades nuevas dependencias, actualiza también `requirements.txt`.

---

## 3. Estructura del Proyecto (Archivos Clave)

```
AzulClaw/
├── venv/                          # Entorno virtual (NO subir a Git)
├── docs/                          # Esta documentación
│
├── azul_brain/                    # PROCESO PRINCIPAL (El Cerebro)
│   ├── main_launcher.py           # Entry Point de la App (aiohttp en :3978)
│   ├── mcp_client.py              # Cliente MCP (se conecta a AzulHands vía stdio)
│   ├── bot/
│   │   └── azul_bot.py            # Controlador de Bot Framework (ActivityHandler)
│   ├── cortex/                    # [PENDIENTE] Microsoft Agent Framework / IA
│   ├── memory/                    # [PENDIENTE] Memoria segura (JSON only)
│   └── soul/                      # [PENDIENTE] Personalidad / System Prompts
│
├── azul_hands_mcp/                # PROCESO HIJO (Las Manos - Sandbox MCP)
│   ├── mcp_server.py              # Servidor MCP que expone Tools al Cerebro
│   └── path_validator.py          # Módulo de Seguridad contra Path Traversal
│
├── blueclaw/                      # Propuesta original de diseño cognitivo (referencia)
│   └── deliberaciones/            # Documentos de diseño System 1/System 2
│
└── openclaw/                      # Código base original (referencia, NO se modifica)
```

---

## 4. Levantar el Servidor Localmente

### 4.1 Arrancar AzulClaw (modo desarrollo)

Desde la raíz del proyecto, con el venv activado:

```powershell
.\.venv\Scripts\python.exe -m AzulClaw.azul_brain.main_launcher
```

También sigue siendo compatible ejecutar el archivo directamente:

```powershell
.\.venv\Scripts\python.exe AzulClaw\azul_brain\main_launcher.py
```

**Salida esperada aproximada:**
```
[INFO] Despertando el Cerebro de AzulClaw...
[INFO] Conectando el Cerebro con AzulHands (MCP Server)...
[INFO] Conexión MCP establecida. AzulClaw ahora tiene acceso a herramientas.
[INFO] Servidor local HTTP escuchando a Azure Bot en http://localhost:3978/api/messages
```

Esto hace dos cosas simultáneamente:
1. **Levanta el Servidor HTTP** en el puerto 3978 (escuchando mensajes de Azure Bot Service).
2. **Arranca el proceso hijo AzulHands** (MCP Server) para dar acceso seguro al disco.

### 4.2 Probar con Bot Framework Emulator

1. Abrir **Bot Framework Emulator**.
2. Crear una nueva conexión con la URL: `http://localhost:3978/api/messages`.
3. Dejar en blanco `Microsoft App ID` y `Microsoft App Password` (modo local).
4. Enviar un mensaje de texto. Deberías recibir una respuesta generada por el agente; si faltan credenciales `AZURE_OPENAI_*`, el bot devolverá un mensaje de error controlado.

### 4.3 Probar el Servidor MCP de forma independiente

Si necesitas depurar las "Manos" sin el Cerebro:

```powershell
.\.venv\Scripts\python.exe AzulClaw\azul_brain\mcp_client.py
```

Este script ejecuta un *smoke test* que:
- Lista las herramientas disponibles.
- Lista archivos en el workspace seguro.
- Intenta un **ataque de Path Traversal** (`../../../../../Windows/System32`) y confirma que es bloqueado.

---

## 5. Variables de Entorno

Actualmente el proyecto funciona en modo local sin credenciales. Para conectar con Azure Bot Service en producción, se necesitarán estas variables:

| Variable | Descripción | Valor por defecto |
|---|---|---|
| `MicrosoftAppId` | ID de la aplicación registrada en Azure Bot Service | `""` (vacío = modo local) |
| `MicrosoftAppPassword` | Contraseña/secret del Bot Registration | `""` |
| `AZURE_OPENAI_ENDPOINT` | URL del recurso Azure OpenAI (Fase 4) | No configurado aún |
| `AZURE_OPENAI_API_KEY` | Clave del recurso Azure OpenAI (Fase 4) | No configurado aún |

> **Importante:** NUNCA subir estas variables a Git. Usar un archivo `.env` local o Azure Key Vault en producción.

---

## 6. Comandos Útiles de Desarrollo

| Comando | Descripción |
|---|---|
| `.\.venv\Scripts\python.exe -m AzulClaw.azul_brain.main_launcher` | Levantar el bot completo |
| `.\.venv\Scripts\python.exe AzulClaw\azul_brain\mcp_client.py` | Test aislado del MCP |
| `pip freeze > requirements.txt` | Exportar dependencias actuales |
| `pyinstaller --onefile AzulClaw\azul_brain\main_launcher.py` | Compilar a `.exe` (Fase 5) |

---

## 7. Convenciones de Código

- **Idioma del código:** Inglés para nombres de variables, funciones y clases. Español para comentarios y documentación.
- **Serialización:** Usar únicamente `json` o `pydantic`. **Prohibido**: `pickle`, `eval()`, `exec()`, `yaml.unsafe_load()`.
- **Acceso al disco:** NUNCA importar `os`, `shutil` o `subprocess` en `azul_brain/`. Todo acceso al sistema de archivos debe pasar por `mcp_client.py` -> `mcp_server.py` -> `path_validator.py`.
- **Logging:** No usar emojis en `print()` (causa `UnicodeEncodeError` en terminales Windows cp1252). Usar prefijos como `[INFO]`, `[ERROR]`, `[REQ]`.

