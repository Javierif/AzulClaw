# AzulClaw: Arquitectura Desktop y Estructura del Repositorio

**Fecha de ultima revision:** 9 de Abril de 2026.  
**Objetivo:** Definir una estructura simple y clara del repositorio para facilitar onboarding, mantenimiento y evolucion del producto desktop de AzulClaw siguiendo KISS.

---

## 1. Principios

- KISS: estructura simple, explicita y facil de explicar.
- Separacion clara entre backend, desktop, documentacion y scripts.
- Onboarding rapido: una persona nueva debe ubicar cada capa en minutos.

---

## 2. Estructura actual recomendada

```text
AzulClaw/
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

1. Limpiar cualquier resto de la ruta antigua `AzulClaw/`.
2. Definir el contrato inicial desktop <-> backend:
   - chat
   - procesos
   - memoria
   - workspace
3. Bootstrap real de `azul_desktop/` con Tauri.
4. Añadir un README corto de onboarding para nuevos desarrolladores.
