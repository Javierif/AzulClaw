# AzulClaw: Sistema unificado de Heartbeats (Scheduler)

**Fecha de última revisión:** Abril 2026

## 1. Visión General

El sistema de **Heartbeats** de AzulClaw es el mecanismo que le otorga "iniciativa propia" al agente. Históricamente existían dos conceptos paralelos que hacían lo mismo:
1. **System Heartbeat**: El "latido" base del sistema para revisar el contexto (leer archivos de estado y ejecutar un prompt de chequeo general).
2. **Scheduled Jobs**: Tareas recurrentes creadas por el usuario.

Para simplificar la arquitectura cognitiva, se ha realizado una **unificación arquitectónica**. Ahora, **todo son Heartbeats**. Todas las rutinas y automatizaciones (incluyendo el sistema base) comparten un único modelo de datos, la misma vista de interfaz de usuario y el mismo ciclo de evaluación.

## 2. El Heartbeat del Sistema (`system-heartbeat`)

El heartbeat principal de AzulClaw se trata ahora como un *Heartbeat nativo/integrado*. 
*   **Indestructible:** No se puede borrar desde la interfaz (aparece con un icono de candado 🔒).
*   **Inyección Automática de Contexto:** El motor de ejecución (Scheduler) intercepta la ejecución de este heartbeat específico (`system: true` con id `system-heartbeat`) y le inyecta automáticamente el contenido del archivo `HEARTBEAT.md` que exista en el Workspace, además del propio prompt configurado en la UI.
*   **Autocreación:** Al arrancar el cerebro en `store.py`, la función `ensure_system_heartbeat_job()` revisa si existe. De lo contrario, lo crea automáticamente con los valores por defecto asegurando que el agente nunca se quede sin "pulso".

Si el agente escanea `HEARTBEAT.md` y no encuentra nada accionable, el hilo se cierra silenciosamente devolviendo un `HEARTBEAT_SKIP` en lugar de invocar una inferencia costosa del System 2.

## 3. Custom Heartbeats (User Jobs)

Las automatizaciones de los usuarios se manejan de manera idéntica al latido principal, permitiendo crear recordatorios, validaciones periódicas o reportes con las siguientes propiedades:
*   Frecuencia (`interval_seconds`).
*   Prompt específico.
*   Pausa/Reanudación.

## 4. Triage y Selección de Cerebros (Lanes)

En la interfaz de usuario, al crear un Heartbeat, ya no se permite seleccionar manualmente el "Brain" (por ejemplo, forzar que use System 1 `fast` o System 2 `slow`). Mantenemos una postura opinada donde el _Lane_ es siempre **Auto**.
La carga de trabajo se enruta mediante nuestro sistema interno de **Triage**, donde el agente `fast` decide, en tiempo de ejecución, si la tarea del heartbeat es lo suficientemente trivial para resolverla localmente o si requiere delegarla al modelo `slow`. Esto abstrae al usuario de la carga cognitiva de coordinar LLMs.

## 5. Arquitectura Interna y Componentes Clave

### 5.1 Backend
*   **`azul_backend/azul_brain/runtime/store.py`**: Posee el modelo `ScheduledJob` unificado. Añadimos el campo `system: bool` y protegemos el borrado de tareas del propio sistema.
*   **`azul_backend/azul_brain/runtime/scheduler.py`**: Contiene un único método `_execute_job()` para procesar todos los heartbeats en su turno evitando redundancias.

### 5.2 Frontend
*   Todo el texto visible al usuario hace referencia a traducciones de **"Heartbeats / Automations"**. 
*   Se unieron las vistas antiguas de configuración del pulso del sistema con el listado de jobs, creando la interfaz unificada `HeartbeatsShell.tsx`.
