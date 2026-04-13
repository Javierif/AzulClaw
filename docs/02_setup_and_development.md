# AzulClaw: Guía de Configuración y Desarrollo Local

**Fecha de ultima revision:** 12 de Abril de 2026.
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
repo-root/
├── .venv/                         # Entorno virtual (NO subir a Git)
├── docs/                          # Esta documentación
│
├── azul_backend/
│   ├── azul_brain/                # PROCESO PRINCIPAL (El Cerebro)
│   │   ├── main_launcher.py       # Entry Point de la App (aiohttp en :3978)
│   │   ├── mcp_client.py          # Cliente MCP (se conecta a AzulHands vía stdio)
│   │   ├── bot/
│   │   │   └── azul_bot.py        # Controlador de Bot Framework (ActivityHandler)
│   │   ├── cortex/                # Microsoft Agent Framework / IA
│   │   ├── memory/                # Memoria local
│   │   └── soul/                  # Personalidad / System Prompts
│   └── azul_hands_mcp/            # PROCESO HIJO (Las Manos - Sandbox MCP)
│       ├── mcp_server.py          # Servidor MCP que expone Tools al Cerebro
│       └── path_validator.py      # Seguridad contra Path Traversal
│
├── azul_desktop/                  # App de escritorio en construccion
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
.\.venv\Scripts\python.exe -m azul_backend.azul_brain.main_launcher
```

También sigue siendo compatible ejecutar el archivo directamente:

```powershell
.\.venv\Scripts\python.exe azul_backend\azul_brain\main_launcher.py
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

### 4.2 Arrancar la UI desktop en modo web

En otra terminal, desde `azul_desktop/`:

```powershell
npm install
npm run dev
```

Notas importantes:
- La UI de desarrollo vive en `http://localhost:1420`.
- `Vite` proxifica todas las rutas `/api` al backend local `http://localhost:3978`.
- El flujo de chat principal usa `POST /api/desktop/chat/stream` y espera NDJSON incremental.

### 4.3 Arrancar la app desktop nativa (Tauri)

Tambien puedes abrir la shell desktop real:

```powershell
cd azul_desktop
npm run tauri:dev
```

Notas importantes:
- Tauri sigue hablando con el backend Python local en `:3978`.
- El backend ya inyecta CORS tambien en respuestas de streaming (`StreamResponse`) y en `OPTIONS`, util cuando la UI no entra por el proxy de Vite.

### 4.4 Probar con Bot Framework Emulator

1. Abrir **Bot Framework Emulator**.
2. Crear una nueva conexión con la URL: `http://localhost:3978/api/messages`.
3. Dejar en blanco `Microsoft App ID` y `Microsoft App Password` (modo local).
4. Enviar un mensaje de texto. Deberías recibir una respuesta generada por el agente; si faltan credenciales `AZURE_OPENAI_*`, el bot devolverá un mensaje de error controlado.

### 4.5 Probar el Servidor MCP de forma independiente

Si necesitas depurar las "Manos" sin el Cerebro:

```powershell
.\.venv\Scripts\python.exe azul_backend\azul_brain\mcp_client.py
```

Este script ejecuta un *smoke test* que:
- Lista las herramientas disponibles.
- Lista archivos en el workspace seguro.
- Intenta un **ataque de Path Traversal** (`../../../../../Windows/System32`) y confirma que es bloqueado.

---

## 5. Variables de Entorno

El proceso principal carga **`azul_backend/azul_brain/.env.local`** al arrancar (no sobrescribe variables que ya existan en el entorno). Como plantilla puedes copiar el ejemplo versionado:

```powershell
copy azul_backend\azul_brain\.env.example azul_backend\azul_brain\.env.local
```

En macOS o Linux:

```bash
cp azul_backend/azul_brain/.env.example azul_backend/azul_brain/.env.local
```

### 5.1 Azure OpenAI y carriles *fast* / *slow*

| Variable | Descripción |
|---|---|
| `AZURE_OPENAI_ENDPOINT` | URL base del recurso Azure OpenAI. |
| `AZURE_OPENAI_API_KEY` | Clave del recurso. |
| `AZURE_OPENAI_API_VERSION` | Versión de API (por defecto `2024-10-21`). |
| `AZURE_OPENAI_DEPLOYMENT` | Despliegue por defecto del asistente (también usado como *slow* si no defines `AZURE_OPENAI_SLOW_DEPLOYMENT`). |
| `AZURE_OPENAI_FAST_DEPLOYMENT` | Despliegue del carril **rápido** (heartbeats, tareas baratas, **extracción en segundo plano de preferencias**). Valor típico en nuestro entorno: `gpt-5.4-nano`. Si está vacío, se usa `AZURE_OPENAI_FALLBACK_DEPLOYMENT` o `gpt-4o-mini`. |
| `AZURE_OPENAI_SLOW_DEPLOYMENT` | Despliegue del carril **lento** (respuestas principales más pesadas). Opcional si ya tienes `AZURE_OPENAI_DEPLOYMENT`. |
| `AZURE_OPENAI_FAST_ENDPOINT`, `AZURE_OPENAI_FAST_API_KEY`, `AZURE_OPENAI_FAST_API_VERSION` | Opcionales: otro recurso Azure solo para el carril rápido. Si faltan, se reutilizan `AZURE_OPENAI_ENDPOINT` y `AZURE_OPENAI_API_KEY`. |
| `AZURE_OPENAI_SLOW_*` | Igual que los anteriores, para el carril lento. |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | Nombre del despliegue de embeddings (por defecto `text-embedding-3-large`). |
| `MicrosoftAppId` / `MicrosoftAppPassword` | Bot Framework en producción; vacíos = emulador local. |
| `PORT` | Puerto HTTP del cerebro (por defecto `3978`). |

> **Importante:** NUNCA subir credenciales a Git. Solo `.env.example` (sin secretos) puede versionarse.

<a id="hybrid-memory-env"></a>

### 5.2 Memoria híbrida y variables relacionadas

La memoria a largo plazo vive en **SQLite** (ruta por defecto `<workspace_root>/.azul/azul_memory.db`; el `workspace_root` se lee del perfil de hatching en `memory/hatching_profile.json`). Incluye:

- Tabla de **memorias** con embedding en BLOB y búsqueda **FTS5** (BM25) sobre el texto.
- Fusión **híbrida** (RRF ponderado: por defecto 70 % vector / 30 % texto).
- Historial reciente de chat en la misma base (`SafeMemory` siempre activo usando esa ruta resuelta).

| Variable | Descripción | Por defecto |
|---|---|---|
| `AZUL_MEMORY_DB_PATH` | Ruta del fichero SQLite compartido (vector + FTS5 + historial). | `<workspace_root>/.azul/azul_memory.db` |
| `AZUL_EMBEDDING_DIM` | Dimensión del vector; debe coincidir con el modelo de embedding desplegado. | `3072` |
| `VECTOR_MEMORY_ENABLED` | `false` desactiva por completo el almacén vectorial. | `true` |
| `AZUL_HYBRID_VECTOR_WEIGHT` / `AZUL_HYBRID_TEXT_WEIGHT` | Pesos en la fusión RRF híbrida. | `0.7` / `0.3` |
| `AZUL_PREFERENCE_EXTRACTION_ENABLED` | `false` desactiva el extractor en segundo plano. | `true` |
| `MEMORY_MAX_MESSAGES` | Máximo de mensajes en el deque de corto plazo por usuario. | `50` |

**Extracción *fire-and-forget*:** tras cada turno, si el mensaje del usuario supera un filtro barato, se lanza una tarea asíncrona que invoca el modelo del carril **rápido** (`AZURE_OPENAI_FAST_DEPLOYMENT`, p. ej. `gpt-5.4-nano`) para obtener JSON con preferencias y hechos; se deduplican, se embeden y se guardan en SQLite. No retrasa la respuesta visible al usuario.

**Nota de implementación:** los vectores se calculan con similitud coseno en Python sobre BLOBs SQLite (sin exigir la extensión `sqlite-vec` en desarrollo). El paquete `sqlite-vec` figura en `requirements.txt` por si se quiere evolucionar a KNN nativo más adelante.

---

## 6. Comandos Útiles de Desarrollo

| Comando | Descripción |
|---|---|
| `.\.venv\Scripts\python.exe -m azul_backend.azul_brain.main_launcher` | Levantar el bot completo |
| `cd azul_desktop && npm run dev` | Levantar la UI desktop web en `:1420` |
| `cd azul_desktop && npm run tauri:dev` | Levantar la app desktop nativa |
| `.\.venv\Scripts\python.exe azul_backend\azul_brain\mcp_client.py` | Test aislado del MCP |
| `pip freeze > requirements.txt` | Exportar dependencias actuales |
| `pyinstaller --onefile azul_backend\azul_brain\main_launcher.py` | Compilar a `.exe` (Fase 5) |

---

## 7. Convenciones de Código

- **Idioma del código:** Inglés para nombres de variables, funciones y clases. Español para comentarios y documentación.
- **Serialización:** Usar únicamente `json` o `pydantic`. **Prohibido**: `pickle`, `eval()`, `exec()`, `yaml.unsafe_load()`.
- **Acceso al disco:** NUNCA importar `os`, `shutil` o `subprocess` en `azul_brain/`. Todo acceso al sistema de archivos debe pasar por `mcp_client.py` -> `mcp_server.py` -> `path_validator.py`.
- **Logging:** No usar emojis en `print()` (causa `UnicodeEncodeError` en terminales Windows cp1252). Usar prefijos como `[INFO]`, `[ERROR]`, `[REQ]`.

