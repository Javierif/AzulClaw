# AzulClaw: Documentación Arquitectónica

**Fecha de última revisión:** 12 de Abril de 2026.
**Objetivo del Documento:** Servir de base para cualquier desarrollador que entre al proyecto `AzulClaw`, explicando el "Por qué" y el "Cómo" de las decisiones de diseño.

## 1. Visión General: De OpenClaw a AzulClaw

`AzulClaw` nace como una evolución de `openclaw` (un orquestador de IA multicanal). El principal problema de `openclaw` era su **falta de seguridad en entornos locales (Desktop)** y su dependencia de contenedores (Docker) para aislar la ejecución de código. 

Para crear un producto instalable (`.exe` para Windows vía PyInstaller) que fuera seguro para el usuario final **sin depender de Docker**, hemos diseñado una **Arquitectura Híbrida Zero-Trust** basada en tres pilares:

1. **El Cerebro (System 2):** Reside en la Nube (Azure OpenAI, Azure Bot Service).
2. **El Agente Local (AzulBrain):** El proceso principal de la app de escritorio. Gestiona la memoria semántica, conversa con el usuario, y decide cuándo actuar.
3. **Las Manos (AzulHands - Servidor MCP):** Un proceso secundario (hijo) súper restringido. Es el *único* que tiene permisos para leer o escribir en el disco del usuario.

---

## 2. El Patrón "MCP Sandboxing" (El reemplazo de Docker)

Como la IA se ejecuta físicamente en el ordenador del usuario, si la IA "alucina" (o sufre una inyección de prompt) e intenta borrar el disco duro (`rm -rf /` o `Remove-Item C:\`), no hay un Docker que la detenga.

Lo hemos solucionado implementando el **Model Context Protocol (MCP)** desarrollado por Anthropic:

*   **Proceso `azul_brain` (El Cliente):** La lógica del Bot Framework importa el SDK de MCP Client. La IA **no tiene acceso a la librería `os` o `subprocess`**. Es completamente "manca" y "ciega".
*   **Proceso `azul_hands_mcp` (El Servidor):** Es un script independiente que se lanza vía *Standard Input/Output (stdio)*. Expone herramientas atómicas (Tools) en formato JSON-RPC 2.0 (ej. `read_safe_file`, `list_workspace_files`).
*   **El Validador (`path_validator.py`):** El corazón de la seguridad. Cada vez que el Cerebro pide usar una Herramienta, la ruta del archivo se pasa por el validador, que impide ataques de *Path Traversal* (Ej. intentar acceder a `../../Windows/System32`). Toda operación se restringe a una jaula: `C:\Users\{User}\Desktop\AzulWorkspace`.

### Flujo de Datos Seguro:
1.  Telegram -> `Azure Bot Service` -> HTTP POST a `localhost:3978/api/messages` (`main_launcher.py`).
2.  `AzulBot` analiza el mensaje usando Microsoft Agent Framework (Cerebro S2).
3.  Agent Framework decide que necesita buscar un Excel. Agent Framework pide al `MCP Client` ejecutar la herramienta `list_workspace_files`.
4.  La petición viaja por *stdio* (IPC) hasta `mcp_server.py`.
5.  `mcp_server.py` pasa la petición por `path_validator.py`.
6.  Si la ruta es segura, se lee el disco y se devuelve el JSON a SK. 
7.  SK redacta la respuesta en lenguaje natural y la envía de vuelta por Azure Bot Service.

---

## 3. Estructura de Directorios del Código Base

```text
repo-root/
├── docs/                        # Documentación técnica
│   ├── 01_arquitectura.md
│   └── 02_setup_y_desarrollo.md
│
├── azul_backend/
│   ├── azul_brain/              # [EL CEREBRO S1/S2] Lógica Cognitiva
│   │   ├── bot/                 # Integración con Azure Bot Framework
│   │   │   └── azul_bot.py      # Clase principal del Bot (ActivityHandler)
│   │   ├── cortex/              # Microsoft Agent Framework / GPT-4 Planner
│   │   ├── memory/              # Memoria híbrida SQLite + embeddings + FTS5
│   │   ├── main_launcher.py     # Punto de entrada de la APP (Escucha en 3978)
│   │   └── mcp_client.py        # Adaptador de MCP en Python (Cliente STDIO)
│   │
│   └── azul_hands_mcp/          # [LAS MANOS] Servidor Local MCP (Jaula)
│       ├── mcp_server.py        # Expone las "Tools" físicas (Solo Lectura/Escritura validada)
│       └── path_validator.py    # Algoritmo de prevención de Path Traversal
```

## 4. Memoria local híbrida (SQLite)

El agente local mantiene memoria **persistente** entre reinicios:

- **Historial reciente:** `SafeMemory` escribe un subconjunto de turnos en SQLite (`conversation_history`) cuando `AZUL_MEMORY_DB_PATH` está configurado.
- **Memoria semántica + texto:** `VectorMemoryStore` guarda contenido, metadatos y embeddings en la misma base; **FTS5** permite búsqueda léxica (BM25) y la búsqueda vectorial usa similitud coseno en Python sobre vectores almacenados como BLOB.
- **Recuperación híbrida:** las listas vectorial y de texto se fusionan con **RRF ponderado** (por defecto 70 % / 30 %), inspirado en el enfoque de OpenClaw.
- **Aprendizaje ligero:** un extractor en segundo plano usa el carril **rápido** de Azure (`AZURE_OPENAI_FAST_DEPLOYMENT`, p. ej. `gpt-5.4-nano`) para inferir preferencias y hechos atemporales sin bloquear la respuesta al usuario.

La guía de variables está en `docs/02_setup_and_development.md` (ancla `#hybrid-memory-env`).

## 5. Próxima Fase (Pendiente de Desarrollo): Fase 4

La arquitectura de red (`main_launcher.py`) y la tubería de seguridad local (`mcp_server.py`) **ya están montadas y probadas**. 

La siguiente tarea para el desarrollador que asuma el proyecto es conectar el Cerebro:

1.  Integrar `microsoft/agent-framework` dentro del `ActivityHandler` de `azul_bot.py`.
2.  Registrar dinámicamente las herramientas que expone `mcp_client.py` (`list_available_tools()`) como *Plugins* dentro del Microsoft Agent Framework.
3.  Manejar la inyección de prompts seguros ("Threat Modeling") para evitar ataques indirectos.

