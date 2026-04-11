# AzulClaw: Arquitectura Desktop y Estructura del Repositorio

**Fecha de ultima revision:** 11 de Abril de 2026.  
**Objetivo:** Definir una estructura simple y clara del repositorio para facilitar onboarding, mantenimiento y evolucion del producto desktop de AzulClaw siguiendo KISS.

---

## 1. Principios

- KISS: estructura simple, explicita y facil de explicar.
- Separacion clara entre backend, desktop, documentacion y scripts.
- Onboarding rapido: una persona nueva debe ubicar cada capa en minutos.

---

## 2. Estructura actual recomendada

```text
repo-root/
├── azul_backend/           # Backend Python
├── azul_desktop/           # App desktop
├── docs/                   # Documentacion
├── scripts/                # Setup y utilidades
├── memory/                 # Datos locales de desarrollo
├── README.md
└── requirements.txt
```

### Responsabilidades

**`azul_backend/`**
- cerebro cognitivo
- MCP client y sandbox server
- memoria
- integraciones
- runtime local

**`azul_desktop/`**
- shell desktop
- frontend
- navegacion
- vistas de producto

**`docs/`**
- arquitectura
- seguridad
- UX
- wireframes
- decisiones tecnicas

**`scripts/`**
- setup
- comandos de soporte
- utilidades de desarrollo

---

## 3. Arquitectura de ejecucion

```text
[Usuario]
   |
   v
[Azul Desktop App]
   |
   v
[Azul Backend Local en Python]
   |
   +--> memoria
   +--> skills
   +--> MCP
   +--> workspace sandbox
```

### Regla principal
La UI no contiene la logica cognitiva critica.  
La inteligencia y el control viven en el backend.

### Flujo real del chat desktop

```text
[Usuario]
   |
   v
[React / Tauri]
   |
   v
POST /api/desktop/chat/stream
   |
   v
[aiohttp + ConversationOrchestrator]
   |
   +--> commentary inicial del fast
   +--> progress resumido (solo ruta slow)
   +--> delta de la respuesta final
   +--> done con runtime metadata
```

### Contrato de streaming actual

- `start`: confirma apertura del stream.
- `commentary`: primera burbuja visible y mensajes ligeros de narracion.
- `progress`: estado resumido de fases para la ruta `slow`.
- `delta`: tokens o fragmentos de la respuesta final.
- `done`: cierre del turno con `reply`, `history` y metadata de runtime.
- `error`: error serializado sin romper el contrato del frontend.

### Notas operativas de desarrollo

- En desarrollo web, `Vite` proxifica `/api` hacia `http://localhost:3978`, evitando depender de CORS para el flujo normal de UI.
- El backend mantiene CORS explicito para `OPTIONS` y para `StreamResponse` usando `on_response_prepare`, porque el streaming no hereda bien las cabeceras si se aplican demasiado tarde.
- El frontend cae a `POST /api/desktop/chat` solo como fallback si el stream no puede abrirse o se corta antes de devolver contenido util.

---

## 4. Decision de stack

- Backend local: Python
- App desktop: Tauri
- UI: frontend web moderno

### Motivos

- El backend actual ya concentra bien la logica del producto.
- La interfaz necesita chat, hatching, procesos, memoria y workspace con una UX moderna.
- Tauri da una base desktop ligera y suficientemente seria para crecer.

---

## 5. Estructura inicial de `azul_desktop/`

```text
azul_desktop/
├── src/
│   ├── app/
│   ├── features/
│   │   ├── hatching/
│   │   ├── chat/
│   │   ├── skills/
│   │   ├── processes/
│   │   ├── memory/
│   │   └── workspace/
│   ├── components/
│   ├── layouts/
│   ├── lib/
│   ├── styles/
│   └── main.tsx
├── src-tauri/
├── package.json
└── README.md
```

### Criterio
Agrupar por funcionalidad de producto, no solo por tipo tecnico.

---

## 6. Estructura de `azul_backend/`

```text
azul_backend/
├── azul_brain/
│   ├── bot/
│   ├── cortex/
│   ├── memory/
│   ├── soul/
│   ├── config.py
│   └── main_launcher.py
└── azul_hands_mcp/
```

### Nota
Conviene introducir mas adelante una capa `api/` en `azul_backend/azul_brain/` para desacoplar la desktop app del bot framework interno.

---

## 7. Convenciones KISS

1. Una carpeta, una responsabilidad principal.
2. El backend no conoce detalles de rendering.
3. La UI no toma decisiones cognitivas.
4. La documentacion vive en `docs/`.
5. El sandbox debe verse como concepto de producto y como limite tecnico.

---

## 8. Siguientes pasos

1. Definir el contrato inicial desktop <-> backend:
   - chat
   - procesos
   - memoria
   - workspace
2. Bootstrap real de `azul_desktop/` con Tauri.
3. Añadir un README corto de onboarding para nuevos desarrolladores.
