# AzulClaw: Wireframes de Baja Fidelidad para Desktop

**Fecha de ultima revision:** 9 de Abril de 2026.  
**Objetivo:** Definir los wireframes maestros de la app desktop de AzulClaw en baja fidelidad, con foco en layout, jerarquia, navegacion y comportamiento.

---

## 1. Alcance de este documento

Este documento aterriza la vision definida en:

- `docs/08_diseno_interfaz_desktop.md`

Se definen cinco wireframes prioritarios:

1. Hatching Intro
2. Hatching: Personalidad + Skills
3. Hatching: Resumen y Nacimiento
4. Chat principal
5. Processes + Memory

El objetivo en esta fase no es cerrar el estilo visual final, sino fijar:
- distribucion de zonas
- prioridad de contenidos
- comportamiento principal
- relacion entre paneles y acciones

---

## 2. Convenciones

### 2.1 Niveles de fidelidad
- Esto es baja fidelidad
- Los bloques representan zonas funcionales
- Los textos son orientativos
- Los componentes pueden cambiar de forma en diseño visual

### 2.2 Notacion
- `[Bloque]` representa una zona UI
- `(Accion)` representa un boton o accion
- `->` indica flujo o transicion

---

## 3. Wireframe 01: Hatching Intro

### 3.1 Objetivo
Introducir la narrativa de "crear y despertar" a tu AzulClaw.

### 3.2 Jerarquia
1. Imagen/identidad del personaje
2. Propuesta de valor del Hatching
3. CTA principal
4. Opcion de restaurar

### 3.3 Layout

```text
+--------------------------------------------------------------+
|                     [Background atmosferico]                 |
|                                                              |
|     [Ilustracion grande: AzulClaw bebe con chupete]          |
|                                                              |
|          Vamos a criar tu AzulClaw                           |
|          Personalidad, habilidades y forma de ayudarte       |
|                                                              |
|                (Empezar Hatching)                            |
|                (Restaurar uno existente)                     |
|                                                              |
|                [Hint visual: progreso 0/5]                   |
+--------------------------------------------------------------+
```

### 3.4 Comportamiento
- `Empezar Hatching` -> abre el wizard
- `Restaurar uno existente` -> flujo alternativo para cargar configuracion previa

### 3.5 Notas UX
- Muy poca densidad de informacion
- El peso visual debe recaer en el personaje
- La pantalla debe parecer mas un ritual de inicio que un formulario

---

## 4. Wireframe 02: Hatching Personalidad + Skills

### 4.1 Objetivo
Combinar dos decisiones importantes en una sola vista expandida:
- como es AzulClaw
- que sabe hacer al nacer

Esta pantalla puede resolverse como dos pasos del wizard con la misma estructura base.

### 4.2 Jerarquia
1. Preview del personaje
2. Ejes de personalidad
3. Arquetipo base
4. Skills iniciales recomendadas
5. Preview de comportamiento

### 4.3 Layout

```text
+----------------------------------------------------------------------------------+
| [Wizard Header: Identidad | Personalidad | Skills | Permisos | Nacimiento]      |
|----------------------------------------------------------------------------------|
| [Preview personaje]      | [Personalidad]                                        |
|                          | Calido <--------------------o----------------> Directo |
|  [AzulClaw reacts]       | Creativo <------------------o---------------> Preciso |
|                          | Silencioso <----------------o---------------> Explica  |
|  Nombre: AzulClaw        | Autonomo <------------------o-------------> Confirmar |
|  Rol: Companero tecnico  | Serio <---------------------o--------------> Jugueton |
|                          |                                                      |
|                          | Arquetipo: [Companero] [Ingeniero] [Guardian]        |
|----------------------------------------------------------------------------------|
| [Preview de respuestas]  | [Skills iniciales]                                    |
| "Te ayudo con..."        | [Email] [Telegram] [Calendario] [Archivos]           |
| "Antes de borrar..."     | [Navegacion web] [Memoria] [Automatizaciones]         |
|                          | [Terminal segura] [Notificaciones]                    |
|                          |                                                      |
|                          | Cada tarjeta: estado, riesgo, anadir/configurar       |
|----------------------------------------------------------------------------------|
| (Atras)                                     (Continuar)                          |
+----------------------------------------------------------------------------------+
```

### 4.4 Comportamiento
- Mover sliders actualiza el preview textual
- Cambiar arquetipo recoloca valores por defecto
- Activar una skill puede abrir configuracion rapida si necesita credenciales o permisos

### 4.5 Decisiones recomendadas
- No usar demasiados sliders si la app no los va a honrar en el comportamiento real
- Mejor pocos ejes, bien explicados
- Las skills "sensibles" deben quedar marcadas desde el primer momento

---

## 5. Wireframe 03: Hatching Resumen y Nacimiento

### 5.1 Objetivo
Cerrar el onboarding y generar una sensacion de "nacimiento".

### 5.2 Jerarquia
1. Nombre e identidad final
2. Resumen de personalidad
3. Skills elegidas
4. Sandbox/workspace asignado
5. CTA final

### 5.3 Layout

```text
+----------------------------------------------------------------------------------+
|                          [Tu AzulClaw esta listo]                                |
|----------------------------------------------------------------------------------|
| [Ilustracion personaje]     | Nombre: AzulClaw                                   |
|                             | Rol: Companero tecnico                             |
| [Estado visual: awakening]  | Tono: Directo, explicativo, autonomo moderado     |
|                             |                                                    |
|                             | Skills activadas:                                  |
|                             | - Email                                            |
|                             | - Telegram                                         |
|                             | - Archivos                                         |
|                             | - Memoria                                          |
|                             |                                                    |
|                             | Workspace:                                         |
|                             | C:\Users\{User}\Desktop\AzulWorkspace              |
|                             |                                                    |
|                             | Politica: confirmar acciones sensibles             |
|----------------------------------------------------------------------------------|
| (Volver a editar)                                 (Nacer)                        |
+----------------------------------------------------------------------------------+
```

### 5.4 Comportamiento
- `Volver a editar` permite regresar a pasos anteriores
- `Nacer` crea la configuracion inicial y entra a la app principal

### 5.5 Momento posterior recomendado
Tras pulsar `Nacer`, mostrar una microanimacion y aterrizar en el chat con un primer mensaje del agente.

---

## 6. Wireframe 04: Chat principal

### 6.1 Objetivo
Ser la superficie diaria de trabajo. Todo lo importante debe poder arrancar desde aqui.

### 6.2 Jerarquia
1. Conversacion
2. Estado actual del agente
3. Acciones rapidas
4. Procesos/contexto lateral

### 6.3 Layout

```text
+------------------------------------------------------------------------------------------------+
| [Sidebar]           | [Header Chat]                                   | [Panel Contextual]    |
|---------------------|--------------------------------------------------|-----------------------|
| AzulClaw            | AzulClaw                     Estado: Working      | Estado actual         |
|                     | Modo: Local + Cloud          Modelo: GPT-4o      | - Leyendo archivos    |
| > Chat              |--------------------------------------------------| - Preparando resumen  |
|   Skills            |                                                  |                       |
|   Processes         | [Mensaje usuario]                                | Herramientas usadas   |
|   Memory            | "Resume los archivos del proyecto X"             | - list_workspace      |
|   Settings          |                                                  | - read_safe_file      |
|                     | [Respuesta AzulClaw]                             |                       |
| [Mini widget]       | "Estoy revisando el workspace..."                | Aprobaciones          |
| Workspace           |                                                  | - Esperando confirmar |
| - recientes         | [Timeline de mensajes]                           |                       |
| - abrir carpeta     |                                                  | Actividad reciente    |
|                     |                                                  | - Memoria actualizada |
|---------------------|--------------------------------------------------|-----------------------|
|                     | (Adjuntar) (Usar skill) (Crear tarea)            |                       |
|                     | [Caja de texto grande..........................]  |                       |
|                     |                         (Enviar)                  |                       |
+------------------------------------------------------------------------------------------------+
```

### 6.4 Subzonas importantes

**Sidebar**
- Navegacion principal
- Miniestado del workspace
- Acceso rapido a secciones

**Header**
- Nombre de AzulClaw
- Estado
- Modo cognitivo o proveedor activo
- Posible selector de sesion en el futuro

**Chat**
- Mensajes del usuario
- Mensajes del agente
- Mensajes de sistema ligeros
- Posibilidad de mostrar "narracion" del agente mientras trabaja

**Panel contextual**
- Procesos de la sesion actual
- Tools usadas
- Aprobaciones
- Memoria reciente

### 6.5 Comportamiento
- El panel derecho cambia segun el contexto de la conversacion
- Si AzulClaw esta idle, muestra resumen y quick actions
- Si esta ejecutando acciones, muestra pasos y actividad en tiempo real
- Si hay una accion delicada, la aprobacion aparece visible y accionable

### 6.6 Decision importante
El panel contextual no debe convertirse en un muro de logs.  
Debe traducir actividad tecnica a lenguaje comprensible y permitir expandir detalle tecnico solo cuando haga falta.

---

## 7. Wireframe 05: Processes + Memory

### 7.1 Objetivo
Dar visibilidad a la "vida interna" de AzulClaw sin sobrecargar la pantalla principal.

### 7.2 Enfoque
Se propone una vista con dos paneles principales o dos tabs hermanas:
- Processes
- Memory

La estructura puede compartir una shell comun.

### 7.3 Layout general

```text
+------------------------------------------------------------------------------------------------+
| [Sidebar]           | [Header: Processes / Memory]                                              |
|---------------------|---------------------------------------------------------------------------|
| AzulClaw            | [Tabs: Processes] [Memory] [Workspace]                                   |
|   Chat              |---------------------------------------------------------------------------|
|   Skills            | [Panel principal]                           | [Panel detalle]              |
| > Processes         |                                           |                               |
|   Memory            |                                           |                               |
|   Settings          |                                           |                               |
+------------------------------------------------------------------------------------------------+
```

---

## 8. Vista detallada: Processes

### 8.1 Objetivo
Permitir inspeccionar tareas activas, programadas, recientes y fallidas.

### 8.2 Layout

```text
+------------------------------------------------------------------------------------------------+
| [Tabs: Processes] [Memory] [Workspace]                                                         |
|------------------------------------------------------------------------------------------------|
| Filtros: [Todos] [Running] [Scheduled] [Waiting] [Done] [Failed]                              |
|------------------------------------------------------------------------------------------------|
| [Lista de procesos]                                  | [Detalle del proceso seleccionado]      |
|                                                      |                                         |
| > Revisar email de soporte            Running        | Nombre: Revisar email de soporte       |
|   Skill: Email                        12:01          | Estado: Running                         |
|                                                      | Inicio: 12:01                           |
| > Generar resumen semanal             Done           | Skill: Email + Memory                   |
|   Skill: Memoria                      11:32          |                                         |
|                                                      | Timeline                                |
| > Mover archivos a Projects           Waiting        | - Se lanzo por regla automatica         |
|   Skill: Workspace                    11:20          | - Leyo carpeta Inbox                    |
|                                                      | - Espera aprobacion para mover 3 files  |
| > Sincronizar Telegram                Failed         |                                         |
|   Skill: Telegram                     10:58          | Acciones                                |
|                                                      | (Aprobar) (Pausar) (Cancelar)           |
+------------------------------------------------------------------------------------------------+
```

### 8.3 Comportamiento
- La lista prioriza estado y hora
- El detalle explica que paso y que hara despues
- Acciones disponibles dependen del riesgo y del tipo de proceso

### 8.4 Regla UX
Procesos programados y procesos interactivos deben distinguirse claramente.

---

## 9. Vista detallada: Memory

### 9.1 Objetivo
Mostrar que recuerda AzulClaw, por que lo recuerda y permitir controlarlo.

### 9.2 Layout

```text
+------------------------------------------------------------------------------------------------+
| [Tabs: Processes] [Memory] [Workspace]                                                         |
|------------------------------------------------------------------------------------------------|
| Subtabs: [Recuerdos] [Conocimiento] [Preferencias] [Sesiones]                                 |
|------------------------------------------------------------------------------------------------|
| [Buscador........................................]   [Filtro tipo] [Orden]                     |
|------------------------------------------------------------------------------------------------|
| [Lista de memorias]                                  | [Detalle de memoria]                    |
|                                                      |                                         |
| > "Javier prefiere respuestas directas"              | Tipo: Preferencia                       |
|   Preferencia                     fijada             | Fuente: Hatching / ajustado manualmente|
|                                                      | Ultimo uso: hace 2 horas                |
| > "Error recurrente con Azure auth"                  |                                         |
|   Episodica                       reciente           | Contenido completo                      |
|                                                      |                                         |
| > "Resumen de arquitectura de AzulClaw"              | Acciones                                |
|   Semantica                       documento          | (Editar) (Fijar) (Olvidar)             |
|                                                      |                                         |
| > "Sesion del 8 de abril"                            | Relacionado con                         |
|   Sesion                          resumen            | - Chat #14                              |
+------------------------------------------------------------------------------------------------+
```

### 9.3 Comportamiento
- El usuario puede editar, fijar o borrar ciertos recuerdos
- Algunas memorias pueden ser de solo lectura si se generan automaticamente
- Debe mostrarse de donde sale una memoria y cuando se uso por ultima vez

### 9.4 Decision importante
La memoria debe parecer util y gobernable, no misteriosa.  
Transparencia primero.

---

## 10. Vista detallada: Workspace

### 10.1 Objetivo
Hacer visible el sandbox de archivos de AzulClaw como una superficie operativa propia.

### 10.2 Layout

```text
+------------------------------------------------------------------------------------------------+
| [Tabs: Processes] [Memory] [Workspace]                                                         |
|------------------------------------------------------------------------------------------------|
| Workspace raiz: C:\Users\{User}\Desktop\AzulWorkspace                                          |
|------------------------------------------------------------------------------------------------|
| [Arbol de carpetas]                                 | [Contenido / Preview]                    |
|                                                      |                                         |
| > Inbox                                             | Carpeta actual: /Inbox                  |
| > Projects                                          |                                         |
| > Generated                                         | [archivo_1.md]                          |
| > MemoryExports                                     | [archivo_2.pdf]                         |
| > Logs                                              | [subcarpeta_a]                          |
|                                                      |                                         |
|                                                      | Acciones                                |
|                                                      | (Nuevo archivo) (Nueva carpeta)         |
|                                                      | (Mover) (Renombrar) (Eliminar)          |
|                                                      |                                         |
|                                                      | Nota de seguridad                       |
|                                                      | "AzulClaw solo puede operar aqui"       |
+------------------------------------------------------------------------------------------------+
```

### 10.3 Comportamiento
- El usuario puede inspeccionar que esta haciendo el agente con sus archivos
- Las acciones de borrar o mover deben tener proteccion
- Esta vista puede servir tambien como superficie de debugging funcional

### 10.4 Recomendacion
Aunque esta pantalla no estaba entre las cinco maestras originales, deberia entrar pronto en la iteracion porque el `AzulWorkspace` es parte del diferencial del producto.

---

## 11. Navegacion global propuesta

### 11.1 Navegacion principal
- Chat
- Skills
- Processes
- Memory
- Settings

### 11.2 Relaciones entre vistas
- Desde `Chat` se puede saltar a `Processes` al pulsar sobre una tarea activa
- Desde `Chat` se puede abrir `Memory` al pulsar sobre un recuerdo citado
- Desde `Chat` o `Settings` se puede abrir `Workspace`
- Desde `Skills` se puede abrir configuracion concreta de una integracion

### 11.3 Principio
La navegacion no debe esconder los sistemas clave.  
Skills, procesos, memoria y workspace son pilares del producto, no menus secundarios.

---

## 12. Riesgos de diseño a evitar

1. Convertir la app en un chat comun con paneles de administracion pegados.
2. Meter demasiada complejidad tecnica en la pantalla principal.
3. Hacer que procesos y memoria parezcan logs de desarrollador en vez de herramientas de control.
4. Ocultar la idea del sandbox como si fuera solo implementacion interna.
5. Diseñar el onboarding como un formulario corporativo sin identidad.

---

## 13. Decisiones abiertas para la siguiente iteracion

1. Si `Skills` merece pantalla propia completa o panel lateral avanzado.
2. Si `Processes`, `Memory` y `Workspace` viven en secciones separadas o en una shell comun.
3. Cuanto detalle tecnico mostrar por defecto en el panel contextual del chat.
4. Como representar visualmente el estado de AzulClaw:
   - avatar estatico
   - avatar animado
   - cambios por mood o actividad
5. Si el Hatching debe ser full-screen o modal dentro de la shell de la app.

---

## 14. Siguiente paso recomendado

La siguiente iteracion deberia cerrar tres cosas:

1. Arquitectura final de navegacion
2. Sistema visual base
3. Wireframes de media fidelidad del `Chat principal` y del `Workspace`

Esas dos pantallas son las que mas condicionan la personalidad real del producto.
