# AzulClaw Desktop

Frontend de la app desktop de AzulClaw.

Esta app esta construida con **Tauri, React, TypeScript y Vite** para ofrecer una shell nativa ligera con una UI moderna y un enlace directo con el backend local en Python.

## Caracteristicas principales

### 1. Hatching
- Wizard inicial para definir identidad, tono, autonomia, workspace y capacidades base del agente.
- Dashboard posterior para editar esa configuracion sin repetir el onboarding completo.

### 2. Chat y control operativo
- **Composer inteligente:** campo multilinea con `Enter` para enviar y `Shift+Enter` para nueva linea.
- **Acciones rapidas:** accesos a Archivo, Memoria y Workspace desde el propio composer.
- **Contexto vivo:** panel lateral con lane activa, motivo de triage, modelo y proceso asociado al turno.
- **Streaming cognitivo dual:** el chat principal usa `POST /api/desktop/chat/stream`. La primera burbuja visible llega desde el cerebro `fast`, la ruta `slow` puede mostrar una tarjeta de progreso resumida y la respuesta final entra por `delta`.
- **Estado de envio real:** el boton de enviar usa un loader persistente; ya no muestra `"..."`.

### 3. Integracion segura
- La UI no decide la logica cognitiva critica.
- El backend Python concentra triage, memoria, runtime y streaming.
- El workspace del agente sigue actuando como sandbox visible y comprensible para el usuario.

## Tecnologias

- **Core:** Tauri 2.x
- **Frontend:** React 19 + TypeScript + Vite
- **Estilos:** CSS plano con variables, layout propio y animaciones ligeras

## Guia de desarrollo

Para iterar la UI en modo web:

```bash
npm install
npm run dev
```

Notas:
- El frontend de desarrollo vive en `http://localhost:1420`.
- `Vite` proxifica `/api` al backend local `http://localhost:3978`.
- El flujo de chat principal depende del endpoint incremental `/api/desktop/chat/stream`.

Para abrir la app desktop nativa:

```bash
npm run tauri:dev
```

Para compilar el bundle desktop:

```bash
npm run tauri:build
```

## Estructura

```text
azul_desktop/
|-- src/
|   |-- app/          # Shell principal
|   |-- components/   # Componentes compartidos
|   |-- features/     # Modulos de producto
|   |-- lib/          # Contratos, mocks y cliente HTTP
|   `-- styles/       # Estilos globales
|-- src-tauri/        # Capa nativa Tauri
`-- package.json
```
