# 🧠 AzulClaw — Cerebro Cognitivo Local

<p align="center">
  <img width="600" height="400" alt="Gemini_Generated_Image_l284qhl284qhl284" src="https://github.com/user-attachments/assets/c73da31c-f0e1-416e-9da7-ee5e30650857" />
</p>

<p align="center">
  <strong>AzulClaw</strong> es el "cerebro" de un asistente personal local: procesa razonamiento, conecta con servicios de IA (Azure OpenAI) y orquesta las "manos" (MCP server) que actúan sobre un workspace seguro.
</p>

<p align="center">
  <a href="https://github.com/Javierif/AzulClaw/actions"><img src="https://img.shields.io/github/actions/workflow/status/Javierif/AzulClaw/ci.yml?branch=main&style=for-the-badge" alt="CI status"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-check-blue.svg?style=for-the-badge" alt="License"></a>
</p>

Descripción
-----------
AzulClaw agrupa la lógica cognitiva del agente: recibe mensajes vía Bot Framework, construye prompts para el motor de IA (Azure OpenAI), mantiene memoria conversacional local y se comunica con un MCP server que proporciona acceso controlado al sistema host (las "manos").

Estructura principal
--------------------
- azul_brain/: código del cerebro (bots, kernel, memoria, cliente MCP).
- azul_hands_mcp/: servidor MCP que ejecuta herramientas seguras sobre el workspace.
- docs/: documentación del proyecto y diseño arquitectural.

Requisitos
----------
- Python 3.10+
- Dependencias del proyecto (revisa requirements.txt o el docs/02_setup_y_desarrollo.md)
- Credenciales de Azure y Microsoft (ver sección Environment / Secrets)

Variables de entorno (ejemplo)
------------------------------
Crear `azul_brain/.env.local` (no subir al repo — ya está en .gitignore). Variables principales:

- MicrosoftAppId=
- MicrosoftAppPassword=
- PORT=3978
- AZURE_OPENAI_ENDPOINT=
- AZURE_OPENAI_API_KEY=
- AZURE_OPENAI_DEPLOYMENT=gpt-4o
- AZUL_MEMORY_DB_PATH=memory/azul_memory.db

Instalación y arranque rápido
-----------------------------
1. Crear virtualenv e instalar dependencias:
```bash
python -m venv .venv
.venv\\Scripts\\activate    # Windows
# o: source .venv/bin/activate   # macOS / Linux
pip install -r requirements.txt
```

2. Rellenar `azul_brain/.env.local` con tus secretos locales.

3. Lanzar el servidor (desde la carpeta raíz del repo):
```bash
python -m AzulClaw.azul_brain.main_launcher
```
Por defecto escucha en el puerto definido en `PORT` (3978 si no lo cambias).

Depuración local
----------------
- Si pruebas con curl o el Bot Framework Emulator, asegúrate de usar distintos `from.id` para no contaminar historial.  
- Para limpiar memoria de pruebas:
```bash
# Usar sqlite3 o un pequeño script Python para borrar rows de memory/azul_memory.db
```

Desarrollo
----------
- El código del núcleo está en `azul_brain/`.  
- `cortex/` contiene la configuración del Kernel y plugins MCP.  
- Sigue las guías en `docs/02_setup_y_desarrollo.md` para flujo de desarrollo y tests.

Seguridad y privacidad
----------------------
- Nunca subir `.env.local` ni credenciales. `.gitignore` ya incluye `AzulClaw/azul_brain/.env.local`.
- El MCP server se diseña para exponer herramientas limitadas; revisa `azul_hands_mcp/path_validator.py` y `docs/03_modelo_seguridad.md`.

Contribuir
----------
Lee `docs/02_setup_y_desarrollo.md` y `docs/05_roadmap_desarrollo.md`. Abre issues y PRs en GitHub siguiendo las convenciones del proyecto.

Recursos
--------
- Documentación del proyecto: `AzulClaw/docs/`
- Diseño arquitectónico: `AzulClaw/docs/arquitectura_azulclaw.drawio`
- MCP server: `azul_hands_mcp/`

Licencia
--------
Revisa el archivo `LICENSE` en el repo para los términos del proyecto.

Contacto
-------
Repositorio: https://github.com/Javierif/AzulClaw
