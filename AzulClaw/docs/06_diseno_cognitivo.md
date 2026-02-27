# AzulClaw: Documentacion del Diseño Cognitivo Completo

**Fecha de ultima revision:** 23 de Febrero de 2026.
**Origen:** Deliberaciones de BlueClaw (`001_arquitectura_hibrida.md`, `002_filosofia_cognitiva.md`, `003_estructura_proyecto.md`).
**Objetivo:** Documentar TODOS los conceptos cognitivos del diseño original que un desarrollador debe implementar.

---

## 1. Arquitectura Dual: System 1 (Local) + System 2 (Cloud)

AzulClaw no usa un unico modelo de IA. Se divide en dos "cerebros" que trabajan **en paralelo**:

### System 1: El Cerebro Rapido (Local)
| Campo | Detalle |
|---|---|
| **Rol** | Narrador, Portero, Triaje |
| **Modelo** | Pequeño y eficiente: Phi-4, Llama-3 8B, o Mistral |
| **Runtime** | Ollama o LM Studio (local, gratuito) |
| **Latencia** | < 200ms |
| **Responsabilidades** | Chatter (respuestas inmediatas), Triage (clasificar complejidad), Narracion (explicar que hace S2 en tiempo real), Privacidad (datos sensibles nunca salen del PC) |

### System 2: El Cerebro Potente (Cloud)
| Campo | Detalle |
|---|---|
| **Rol** | Experto, Razonador |
| **Modelo** | SOTA: GPT-4o, DeepSeek-R1 via Azure OpenAI |
| **Latencia** | 2-10 segundos |
| **Responsabilidades** | Razonamiento complejo, Generacion de codigo, Ingesta masiva de datos (PDFs, codigos) |

### Patron "Comentarista Deportivo"
Para evitar que el usuario espere en silencio mientras la nube piensa:

1. **Input:** "Refactoriza main.py"
2. **S1 (Triage):** Detecta complejidad -> Activa S2
3. **S1 (Chatter):** "Entendido, voy a avisar al equipo de refactorizacion. Dame un segundo..." 
4. **S2 (Reasoning):** Decide llamar a `ReadFile("main.py")`
5. **SK Filter (Hook):** Intercepta la intencion de ReadFile
6. **S1 (Narrador):** "Vale, primero voy a leer main.py para ver que tenemos..."
7. **S2 (Action):** Ejecuta la lectura real

**Implementacion en Microsoft Agent Framework:** Usar `Imiddleware de tool-calling` para interceptar llamadas a tools y alimentar al modelo local para que narre.

---

## 2. Modulos Cognitivos (Del Sistema Limbico)

### 2.1 Triage Router (`cortex/fast/triage.py`)
Clasifica cada mensaje entrante antes de enviarlo a Azure:
- **Simple** (S1 local): "Hola", "¿Que hora es?", "Gracias"
- **Complejo** (S2 cloud): "Refactoriza este codigo", "Resume este PDF"

**Beneficio:** Ahorro masivo de costes. Las preguntas triviales no consumen tokens de GPT-4o.

### 2.2 Empathy Module (`limbic/theory_of_mind.py`)
Antes de responder, el agente debe simular el estado emocional del usuario:
- Input: "Javier dice: 'Otra vez fallo esto...'"
- Simulacion: "Javier esta frustrado y pierde confianza en mi"
- Output Modulado: Tono humilde, directo al grano, sin adornos

### 2.3 Confidence Score (`limbic/confidence.py`)
El agente evalua su propia certeza (Metacognicion):
- Confianza >= 70%: Respuesta afirmativa normal
- Confianza 30-70%: Lenguaje dubitativo ("Creo que...", "Podria ser...")
- Confianza < 30%: Admitir ignorancia, pedir busqueda externa. Evita alucinaciones.

### 2.4 Inner Voice (`limbic/inner_voice.py`)
Forzar al modelo a generar un bloque de "pensamiento oculto" (`<think>` tags) antes de hablar al usuario:
```
<think>
El usuario quiere que refactorice main.py. Primero necesito leerlo.
¿Hay riesgos? El archivo podria ser grande. Voy a verificar el tamaño primero.
Plan: 1. Leer archivo, 2. Analizar estructura, 3. Proponer cambios.
</think>
```
Basado en la teoria de Vygotsky sobre el habla interna como regulador del pensamiento.

---

## 3. Sistema de Memoria Completo

BlueClaw propone 4 tipos de memoria, inspirados en la neurociencia de Tulving:

### 3.1 Memoria Episodica (El Diario) - `memory/episodic/`
- **Backend:** SQLite local
- **Contenido:** Logs de sesiones, errores pasados, decisiones tomadas
- **Proposito:** "¿Que hicimos ayer? ¿Que error nos dio?" -> Clave para no repetir errores
- **Archivo:** `manager.py` (CRUD de recuerdos autobiograficos)

### 3.2 Memoria Semantica (El Conocimiento) - `memory/semantic/`
- **Backend:** Azure AI Search
- **Contenido:** Documentacion indexada, PDFs, bases de codigo
- **Archivo:** `ingest.py` (indexador), `search.py` (RAG)

### 3.3 Memoria Vectorial Local (Sin Coste Cloud) - `memory/vector/`
- **Backend:** `sqlite-vec`
- **Contenido:** Embeddings locales para busqueda semantica rapida
- **Archivos:** `store.py` (almacenamiento), `query.py` (busqueda)
- **Beneficio:** Permite busqueda semantica sin pagar Azure AI Search

### 3.4 Working Memory + Compactor - `memory/working/`
- **Backend:** `state.json` en RAM
- **Contenido:** Contexto volatil de la conversacion actual
- **Archivo critico:** `compactor.py` — Resumidor de historial
- **Proposito:** Cuando la conversacion supera la ventana de contexto del LLM, el compactor resume los mensajes antiguos en un resumen ejecutivo y descarta los originales. Sin esto, las conversaciones largas fallan.

---

## 4. El Alma (Soul) - Personalidad e Identidad

### 4.1 Identity (`soul/identity.json`)
Responde a "¿Quien soy?": Nombre base, Rol, Mision.

### 4.2 Masks (Personalidades Dinamicas)
El agente alterna entre personalidades segun el contexto:
- `soul/masks/commentator.md` — Personalidad System 1 (casual, rapido, empatico)
- `soul/masks/expert.md` — Personalidad System 2 (tecnico, preciso, formal)

### 4.3 Ethics (`soul/ethics.md`)
Imperativo Categorico de Kant aplicado: "Actua solo segun aquella maxima que puedas querer que se convierta en ley universal."
- No borres archivos sin permiso
- No mientas
- Protege la privacidad del usuario

### 4.4 Bootstrap (`soul/bootstrap.py`)
Rutina de despertar: Al arrancar, carga memoria, chequea entorno, verifica salud.

---

## 5. Infraestructura: Sistema Nervioso y Resiliencia

### 5.1 Event Bus (`nervous/bus.py`)
Pub/Sub interno (Event Emitter) para comunicacion asincrona entre modulos.
- Tipos de eventos: `Thought`, `Action`, `Error`, `ToolCall`
- Permite que S1 y S2 trabajen en paralelo

### 5.2 Scheduler / Heartbeat (`nervous/scheduler.py`)
- **Tecnologia:** APScheduler
- **Proposito:** El agente tiene "iniciativa". Cada X minutos verifica email, calendario, estado del sistema
- **BlueClaw lo llama:** "Pulsion de Vida" / "Ritmos Circadianos"

### 5.3 Circuit Breaker (`resilience/circuit_breaker.py`)
Si Azure OpenAI devuelve muchos errores consecutivos, el circuit breaker:
1. Corta las llamadas a la nube temporalmente
2. Informa al usuario: "La nube no responde, estoy en modo local"
3. Reintenta tras X minutos

### 5.4 Watchdog (`resilience/watchdog.py`)
Monitor de salud. Si el agente se cuelga o un proceso hijo muere, lo reinicia automaticamente.

### 5.5 Auth Manager (`resilience/auth_manager.py`)
Rotacion de claves API y gestion de cuotas (rate limits por proveedor).

---

## 6. Sentidos (Input)

### 6.1 File Watcher (`senses/file_watcher.py`)
- **Tecnologia:** `watchdog` (Python)
- **Proposito:** Vigilar cambios en el workspace y reaccionar (ej. "He detectado que has añadido un archivo nuevo")

### 6.2 Eyes (`senses/eyes.py`)
- **Tecnologia:** `playwright` (navegacion web dinamica)
- **Proposito:** Leer paginas web, extraer informacion

### 6.3 Safety Guard (`senses/safety_guard.py`)
Truncador de inputs gigantes. Si un archivo tiene 100.000 lineas, no enviarlo entero al LLM.

---

## 7. Tecnologias Clave del Stack Completo

| Componente | Tecnologia | Libreria Python |
|---|---|---|
| Framework IA | Microsoft Microsoft Agent Framework | `agent-framework` |
| System 1 Local | Ollama API | `ollama` o `httpx` |
| System 2 Cloud | Azure OpenAI | `azure-identity` |
| Canales | Azure Bot Service | `botbuilder-core` |
| Memoria Vectorial | sqlite-vec | `sqlite-vec` |
| Memoria Episodica | SQLite | `sqlite3` (stdlib) |
| Cron/Heartbeat | APScheduler | `apscheduler` |
| File Watching | Watchdog | `watchdog` |
| Navegacion Web | Playwright | `playwright` |
| Terminal Interactiva | pywinpty | `pywinpty` |
| Sandbox Local | MCP Protocol | `mcp` |

