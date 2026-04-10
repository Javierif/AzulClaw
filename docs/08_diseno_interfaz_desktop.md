# AzulClaw: Diseno de Interfaz Desktop y Wireframes

**Fecha de ultima revision:** 7 de Abril de 2026.  
**Objetivo:** Consolidar la vision de producto para la aplicacion de escritorio de AzulClaw, definir sus wireframes principales y dejar fijada la idea de un escritorio-sandbox propio para el agente.

---

## 1. Vision de producto

AzulClaw no debe sentirse como "otro chat con ajustes", sino como un agente personal local con identidad, memoria, habilidades y un espacio de trabajo propio.

La experiencia se divide en dos grandes momentos:

1. **Hatching**
   El usuario "cria" a su AzulClaw, define su identidad, personalidad, habilidades y permisos.
2. **Life with AzulClaw**
   El usuario conversa con el agente en una interfaz principal, puede anadir nuevas skills, ver procesos en segundo plano y gestionar la memoria.

La idea central es que AzulClaw sea una criatura/agente con comportamiento propio, pero dentro de limites claros y seguros.

---

## 2. Principios de UX

### 2.1 Identidad fuerte
La app debe transmitir que AzulClaw "vive" dentro del sistema:
- Tiene nombre
- Tiene personalidad
- Tiene estado
- Tiene habilidades
- Tiene memoria
- Tiene un espacio propio de trabajo

### 2.2 Seguridad entendible
La seguridad no debe presentarse como una limitacion tecnica abstracta, sino como una caracteristica de confianza:
- AzulClaw puede operar en su propio espacio
- No puede romper nada fuera de ese espacio
- Las acciones sensibles requieren confirmacion
- Los permisos se entienden a simple vista

### 2.3 Chat como centro, no como unica pantalla
La ventana principal debe estar centrada en el chat, pero el producto necesita superficies dedicadas para:
- Skills
- Procesos en segundo plano
- Memoria
- Configuracion del alma y del sistema

### 2.4 Visual vivo y no-SaaS
Evitar una interfaz generica de paneles grises. El estilo debe sentirse:
- Moderno
- Orgánico pero tecnico
- Cuidado y con personalidad
- Mas cercano a una criatura digital que a un software corporativo

---

## 3. Direccion visual propuesta

### 3.1 Personalidad visual
- Azul profundo como color principal
- Cian o turquesa como acento tecnico
- Marfil o arena suave para dar calidez
- Acentos coral o naranja suave para avisos y estados vivos

### 3.2 Estilo
- Fondos con atmosfera, gradientes o texturas suaves
- Tarjetas redondeadas con caracter
- Tipografia con personalidad, evitando el look estandar de SaaS
- Presencia constante del personaje de AzulClaw en puntos clave

### 3.3 Uso del personaje
El monigote de AzulClaw debe aparecer en momentos importantes:
- Pantalla de bienvenida
- Hatching
- Estados de espera
- Confirmaciones importantes
- Cambios de estado del agente

En la fase de Hatching, la variante "AzulClaw con chupete" debe ser el elemento protagonista.

---

## 4. Arquitectura de pantallas

### 4.1 Pantallas principales
1. Splash / Wake Up
2. Hatching Intro
3. Hatching: Identidad
4. Hatching: Personalidad
5. Hatching: Skills
6. Hatching: Permisos y conexiones
7. Hatching: Resumen y nacimiento
8. Home / Chat principal
9. Skills Manager
10. Background Processes
11. Memory Manager
12. Settings / Soul / System

### 4.2 Estructura narrativa del producto
1. El usuario abre la app
2. Ve a AzulClaw "despertar"
3. Si no existe configuracion previa, entra al flujo de Hatching
4. Tras el nacimiento, aterriza en la vista principal de chat
5. Desde el chat puede ampliar skills, ver procesos y gestionar memoria sin perder el foco

---

## 5. Wireframes funcionales

## 5.1 Splash / Wake Up

**Objetivo:** transmitir vida, estado y continuidad.

**Elementos:**
- Fondo atmosferico
- AzulClaw en estado idle o durmiendo
- Indicadores de arranque:
  - Loading memory
  - Checking tools
  - Waking up
- Boton principal: `Continuar`

**Notas de UX:**
- No debe parecer una pantalla tecnica de carga
- Debe sentirse como el "despertar" del agente

---

## 5.2 Hatching Intro

**Objetivo:** presentar el concepto de criar/configurar un AzulClaw propio.

**Elementos:**
- Imagen grande de AzulClaw bebe con chupete
- Titulo principal
- Texto corto explicando el proceso de Hatching
- CTA principal: `Empezar hatching`
- CTA secundaria: `Restaurar uno existente`

**Idea de copy:**
Vamos a criar tu AzulClaw: su personalidad, sus habilidades y su forma de ayudarte.

---

## 5.3 Hatching: Identidad

**Objetivo:** definir quien es AzulClaw.

**Campos propuestos:**
- Nombre
- Rol principal
- Mision
- Idioma principal
- Tipo de relacion con el usuario

**Layout:**
- Columna izquierda: preview visual del personaje
- Columna derecha: formulario
- Parte inferior: navegacion tipo wizard
- Panel pequeño de preview de identidad en tiempo real

---

## 5.4 Hatching: Personalidad

**Objetivo:** definir como se comporta el agente.

**Ejes propuestos:**
- Calido <-> Directo
- Creativo <-> Preciso
- Silencioso <-> Explicativo
- Autonomo <-> Confirmador
- Serio <-> Jugueton

**Controles adicionales:**
- Arquetipo base:
  - Companero
  - Ingeniero
  - Explorador
  - Mayordomo
  - Guardian
- Campo: comportamientos deseados
- Campo: cosas que nunca debe hacer

**Comportamiento UI recomendado:**
- Mostrar ejemplos de respuesta en tiempo real
- Reflejar visualmente el tono elegido

---

## 5.5 Hatching: Skills

**Objetivo:** configurar las habilidades iniciales.

**Categorias sugeridas:**
- Comunicacion
- Productividad
- Sistema
- Memoria
- Automatizacion

**Skills iniciales candidatas:**
- Email
- Telegram
- Calendario
- Archivos
- Navegacion web
- Terminal segura
- Notificaciones
- Busqueda documental
- Memoria semantica
- Automatizaciones programadas

**Cada tarjeta de skill deberia mostrar:**
- Nombre
- Descripcion breve
- Estado: no activada, configurar, activa
- Nivel de riesgo
- Boton `Anadir`

**Principio UX:**
Activar una skill debe sentirse como "darle una nueva capacidad" al agente.

---

## 5.6 Hatching: Permisos y conexiones

**Objetivo:** presentar permisos con claridad y confianza.

**Bloques:**
- Cuentas conectadas
- Permisos del sistema
- Workspace del agente
- Politica de aprobacion humana
- Modelo local / modelo cloud

**Toggles recomendados:**
- Pedir confirmacion antes de acciones sensibles
- Permitir acceso a email
- Permitir envios automaticos
- Permitir tareas en segundo plano
- Permitir lectura del workspace de AzulClaw

**Criterio de diseño:**
- Distinguir visualmente permisos seguros, moderados y sensibles
- Usar lenguaje claro, no terminologia excesivamente tecnica

---

## 5.7 Hatching: Resumen y nacimiento

**Objetivo:** cerrar el onboarding de forma memorable.

**Contenido:**
- Nombre
- Resumen de personalidad
- Skills activadas
- Configuracion base de memoria y permisos
- Boton principal: `Nacer`

**Momento visual:**
- Animacion breve de "hatching"
- AzulClaw pasa de estado bebe a companion activo
- Primer mensaje del agente en su tono personalizado

---

## 5.8 Home / Chat principal

**Objetivo:** ser el centro de la experiencia diaria.

**Layout recomendado:**
- Sidebar izquierda estrecha
- Zona central dominante para el chat
- Panel derecho contextual y colapsable

**Sidebar izquierda:**
- Chat
- Skills
- Processes
- Memory
- Settings

**Zona central:**
- Header con nombre de AzulClaw
- Estado del agente
- Modelo o modo activo
- Conversacion principal
- Composer avanzado en la parte inferior
- Acciones rapidas:
  - Adjuntar
  - Usar skill
  - Crear tarea
  - Recordar esto

**Panel derecho contextual:**
- Estado general
- Pasos en tiempo real
- Herramientas usadas
- Aprobaciones pendientes
- Resumen de actividad reciente

**Principio clave:**
El usuario nunca debe perder visibilidad de lo que AzulClaw esta haciendo.

---

## 5.9 Skills Manager

**Objetivo:** administrar capacidades de AzulClaw mas alla del onboarding.

**Secciones:**
- Skills instaladas
- Catalogo / marketplace
- Conexiones activas
- Permisos por skill
- Ultima actividad por skill

**Detalle por skill:**
- Que hace
- Que puede leer
- Que puede escribir
- Permisos requeridos
- Historial reciente
- Boton `Desactivar`

---

## 5.10 Background Processes

**Objetivo:** hacer visible la "vida interna" del agente.

**Tipos de procesos a mostrar:**
- Tareas activas
- Tareas programadas
- Esperando aprobacion
- Completadas
- Fallidas

**Datos utiles por proceso:**
- Nombre
- Estado
- Hora de inicio
- Skill o modulo implicado
- Progreso o ultimo evento
- Acciones disponibles

**Regla de UX importante:**
Por defecto debe hablar en lenguaje humano.  
Debe existir un toggle para ver detalle tecnico.

---

## 5.11 Memory Manager

**Objetivo:** permitir inspeccionar y gestionar lo que AzulClaw recuerda.

**Pestanas sugeridas:**
- Recuerdos
- Conocimiento
- Preferencias sobre mi
- Resumen de sesiones

**Funciones:**
- Buscar memoria
- Fijar recuerdos importantes
- Editar o borrar recuerdos
- Ver origen de una memoria
- Distinguir memoria episodica, semantica y working memory resumida

**Visual recomendado:**
- Tarjetas, timeline o vistas mixtas
- Evitar una tabla fria como interfaz principal

---

## 5.12 Settings / Soul / System

**Objetivo:** exponer la configuracion profunda del agente.

**Bloques:**
- Identidad y personalidad
- Modelos S1 / S2
- Privacidad y seguridad
- Workspace
- Integraciones
- Apariencia
- Backup / restore

**Nombre sugerido de seccion:**
- Soul & System
- O simplemente Settings

---

## 6. AzulWorkspace: escritorio-sandbox propio de AzulClaw

### 6.1 Objetivo
AzulClaw debe tener su propia carpeta en el escritorio del usuario para operar con ficheros de forma segura. Esa carpeta funcionara como su "escritorio privado" o sandbox operativo.

### 6.2 Nombre conceptual
- `AzulWorkspace`
- Alternativa visual/UI: `Escritorio de AzulClaw`

### 6.3 Principio de seguridad
AzulClaw puede:
- Leer dentro de su workspace
- Escribir dentro de su workspace
- Crear subcarpetas
- Reorganizar archivos
- Mantener documentos de trabajo, borradores y salidas generadas

AzulClaw no puede:
- Escapar fuera del workspace
- Leer rutas arbitrarias del sistema sin autorizacion explicita
- Mover o borrar archivos fuera del sandbox
- Romper el escritorio real ni otras carpetas del usuario

Esto encaja con la arquitectura Zero-Trust ya definida en el proyecto y con el uso de `path_validator.py`.

### 6.4 Ruta propuesta
Ruta por defecto:

```text
C:\Users\{User}\Desktop\AzulWorkspace
```

Opcionalmente, mas adelante:
- permitir una ruta configurable
- mantener siempre una validacion estricta de la raiz permitida

### 6.5 Funcion del workspace dentro del producto
El `AzulWorkspace` no es solo una carpeta tecnica. Debe ser un concepto visible en la interfaz:
- Lugar donde AzulClaw guarda sus archivos
- Superficie segura para automatizaciones
- Zona de intercambio con el usuario
- Espacio donde descargar adjuntos, generar informes o reorganizar materiales

### 6.6 Reflejo UI del workspace

**En Hatching:**
- Mostrar la ruta del workspace
- Explicar que es el escritorio seguro de AzulClaw
- Permitir confirmar o cambiar ubicacion si en el futuro se habilita

**En la app principal:**
- Widget o tarjeta de `Workspace`
- Acceso rapido a archivos recientes del sandbox
- Posibilidad de arrastrar archivos al chat para copiarlos dentro del workspace

**En Settings / Security:**
- Mostrar claramente la carpeta raiz permitida
- Explicar que todas las operaciones de archivos pasan por esa jaula

### 6.7 Funciones futuras asociadas
- Explorador de archivos integrado del workspace
- Plantillas de carpetas iniciales:
  - `Inbox`
  - `Projects`
  - `Generated`
  - `MemoryExports`
  - `Logs`
- Politicas por carpeta o por skill
- Confirmacion reforzada para borrados dentro del sandbox

### 6.8 Relacion con la arquitectura existente
Esta idea debe mantenerse alineada con la documentacion actual:
- `docs/01_arquitectura.md`
- `docs/03_modelo_seguridad.md`
- `docs/04_referencia_componentes.md`
- `azul_backend/azul_hands_mcp/path_validator.py`

El `AzulWorkspace` es la traduccion de producto/UI del perimetro tecnico de seguridad ya definido.

---

## 7. Wireframes maestros prioritarios

Para avanzar de forma ordenada, los 5 wireframes prioritarios son:

1. Hatching Intro
2. Hatching Personalidad + Skills
3. Hatching Resumen
4. Chat principal
5. Processes + Memory

Estos cinco wireframes permiten validar:
- El tono del producto
- La narrativa de onboarding
- La utilidad diaria
- La visibilidad operativa
- La propuesta diferencial de identidad + memoria + procesos

---

## 8. Decisiones fijadas por ahora

1. AzulClaw sera una app de escritorio con foco en una experiencia moderna y usable.
2. El onboarding principal se estructurara como un flujo de `Hatching`.
3. El personaje de AzulClaw tendra un rol visual importante, especialmente en su version bebe con chupete.
4. La ventana principal estara centrada en el chat, pero con secciones dedicadas a skills, procesos y memoria.
5. AzulClaw tendra un workspace propio en el escritorio del usuario que funcionara como sandbox de archivos.
6. Ese workspace debe ser visible tanto a nivel tecnico como a nivel de producto.
7. La seguridad del sandbox debe apoyarse en la arquitectura Zero-Trust y en la validacion estricta de rutas.

---

## 9. Siguientes pasos recomendados

1. Convertir estos puntos en wireframes de baja fidelidad pantalla por pantalla.
2. Definir la navegacion exacta de la app desktop.
3. Elegir stack de UI desktop:
   - Electron
   - Tauri
   - Otra opcion
4. Definir sistema visual:
   - tipografia
   - colores
   - componentes base
5. Diseñar especificamente la experiencia del `AzulWorkspace` dentro de la UI.
