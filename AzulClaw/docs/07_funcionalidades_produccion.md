# AzulClaw: Funcionalidades de Produccion (Inspiradas en OpenClaw)

**Fecha de ultima revision:** 23 de Febrero de 2026.
**Origen:** Analisis profundo de `openclaw/src/agents/` (387 archivos), `openclaw/src/gateway/` (169 archivos).
**Objetivo:** Documentar los subsistemas de robustez y resiliencia que hacen que una IA sea usable en entornos reales, no solo en demos.

---

## 1. Compactacion de Contexto (Context Compaction)

### Problema
Los LLMs tienen una ventana de contexto limitada (ej. GPT-4o: 128k tokens). En conversaciones largas, el historial supera este limite y la llamada falla.

### Solucion de OpenClaw
Un sistema de compactacion multi-etapa que resume automaticamente los mensajes antiguos:

1. **Estimacion de tokens:** Calcula cuantos tokens ocupa el historial actual.
2. **Chunking inteligente:** Divide el historial en trozos por peso de tokens (no por numero de mensajes).
3. **Summarizacion por fases:** Resume cada trozo con el propio LLM, luego fusiona los resumenes parciales.
4. **Fallback progresivo:** Si un mensaje individual es demasiado grande (>50% del contexto), lo excluye del resumen y lo trata por separado.
5. **Pruning por presupuesto:** Descarta los chunks mas antiguos hasta cumplir un presupuesto de tokens.

### Implementacion propuesta para AzulClaw

**Archivos a crear:**
- `azul_brain/memory/working/compactor.py`
- `azul_brain/memory/working/token_estimator.py`

**Pseudocodigo:**
```python
class ContextCompactor:
    MAX_HISTORY_SHARE = 0.5  # El historial ocupa max 50% del contexto
    
    async def compact(self, messages: list, context_window: int) -> list:
        total_tokens = self.estimate_tokens(messages)
        budget = int(context_window * self.MAX_HISTORY_SHARE)
        
        if total_tokens <= budget:
            return messages  # No hace falta compactar
        
        # Dividir en chunks y resumir los mas antiguos
        old_messages, recent_messages = self.split_by_budget(messages, budget)
        summary = await self.summarize_with_llm(old_messages)
        
        return [{"role": "system", "content": f"Resumen del historial previo:\n{summary}"}] + recent_messages
```

### Prioridad: CRITICA
Sin esto, cualquier conversacion con mas de ~50 intercambios fallara.

---

## 2. Deteccion de Tool Loops (Bucles Infinitos de Herramientas)

### Problema
La IA puede quedarse en un bucle: llama a `list_files(".")` una y otra vez sin avanzar, gastando tokens infinitamente.

### Solucion de OpenClaw
Tres detectores especializados con un circuit breaker global:

| Detector | Que detecta | Umbral Warning | Umbral Critico |
|---|---|---|---|
| **Generic Repeat** | Misma herramienta + mismos parametros repetidos | 10 llamadas | 20 llamadas |
| **Known Poll No-Progress** | Polling donde el resultado no cambia entre llamadas | 10 llamadas | 20 llamadas |
| **Ping-Pong** | Alternancia entre dos herramientas sin progreso | 10 pares | 20 pares |
| **Circuit Breaker Global** | Cualquier patron detectado | — | **30 llamadas → mata la sesion** |

### Implementacion propuesta para AzulClaw

**Archivo a crear:** `azul_brain/resilience/tool_loop_detector.py`

```python
import hashlib, json

class ToolLoopDetector:
    HISTORY_SIZE = 30
    WARNING_THRESHOLD = 10
    CRITICAL_THRESHOLD = 20
    CIRCUIT_BREAKER = 30
    
    def __init__(self):
        self.call_history: list[dict] = []
    
    def record_call(self, tool_name: str, params: dict, result: str):
        signature = self._hash(tool_name, params)
        self.call_history.append({
            "tool": tool_name,
            "sig": signature,
            "result_hash": hashlib.md5(result.encode()).hexdigest()
        })
        # Ventana deslizante
        if len(self.call_history) > self.HISTORY_SIZE:
            self.call_history.pop(0)
    
    def check_loop(self) -> dict:
        # Contar repeticiones de la misma signature
        if not self.call_history:
            return {"stuck": False}
        
        last = self.call_history[-1]
        count = sum(1 for c in self.call_history if c["sig"] == last["sig"])
        
        if count >= self.CIRCUIT_BREAKER:
            return {"stuck": True, "level": "circuit_breaker", "count": count}
        if count >= self.CRITICAL_THRESHOLD:
            return {"stuck": True, "level": "critical", "count": count}
        if count >= self.WARNING_THRESHOLD:
            return {"stuck": True, "level": "warning", "count": count}
        return {"stuck": False}
    
    def _hash(self, tool_name: str, params: dict) -> str:
        data = json.dumps({"t": tool_name, "p": params}, sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()[:16]
```

### Prioridad: CRITICA
Sin esto, un bucle de la IA puede gastar cientos de euros en tokens en minutos.

---

## 3. Context Window Guard (Guardia de Ventana de Contexto)

### Problema
Si alguien configura un modelo con ventana de contexto muy pequeña (ej. 4k tokens), el sistema fallara silenciosamente o dara respuestas sin sentido.

### Solucion de OpenClaw
Verificacion al inicio de cada sesion:
- **< 16.000 tokens:** **BLOQUEAR** — el modelo no puede funcionar con AzulClaw
- **< 32.000 tokens:** **AVISAR** — el modelo funcionara pero con limitaciones

### Implementacion propuesta
**Archivo a crear:** `azul_brain/cortex/context_guard.py`

```python
HARD_MIN_TOKENS = 16_000
WARN_BELOW_TOKENS = 32_000

def evaluate_context_window(model_context_tokens: int) -> dict:
    return {
        "tokens": model_context_tokens,
        "should_block": model_context_tokens < HARD_MIN_TOKENS,
        "should_warn": model_context_tokens < WARN_BELOW_TOKENS,
    }
```

### Prioridad: MEDIA

---

## 4. Model Fallback Chain (Cadena de Respaldo de Modelos)

### Problema
Si Azure OpenAI sufre un rate limit, timeout o error 500, AzulClaw se queda muerto hasta que Azure se recupere.

### Solucion de OpenClaw
Sistema automatico de failover con multiples candidatos:

1. **Candidato primario:** GPT-4o en Azure OpenAI
2. **Fallback 1:** GPT-4o-mini (mas barato, mas disponible)
3. **Fallback 2:** Modelo local via Ollama (sin coste, offline)

Con logica avanzada:
- **Cooldown por perfil de auth:** Si un provider falla, entra en cooldown (30s min entre reintentos).
- **Probe periodico:** Cada 2 minutos re-prueba el candidato primario para ver si se ha recuperado.
- **Auth profile rotation:** Si tienes multiples API keys, rota entre ellas.

### Implementacion propuesta
**Archivo a crear:** `azul_brain/resilience/model_fallback.py`

```python
class ModelFallbackChain:
    def __init__(self, candidates: list[dict]):
        self.candidates = candidates  # [{"provider": "azure", "model": "gpt-4o"}, ...]
        self.cooldowns: dict[str, float] = {}
    
    async def run_with_fallback(self, task_fn):
        attempts = []
        for candidate in self.candidates:
            key = f"{candidate['provider']}:{candidate['model']}"
            if self._in_cooldown(key):
                continue
            try:
                result = await task_fn(candidate["provider"], candidate["model"])
                return {"result": result, "candidate": candidate, "attempts": attempts}
            except Exception as e:
                attempts.append({"candidate": candidate, "error": str(e)})
                self._set_cooldown(key, seconds=30)
        
        raise Exception(f"Todos los modelos fallaron: {attempts}")
```

### Prioridad: CRITICA
Sin esto, AzulClaw es fragil: un solo error de Azure lo deja inoperativo.

---

## 5. Subagent/Spawn System (Delegacion a Sub-Agentes)

### Problema
Tareas complejas se benefician de dividirse en sub-tareas ejecutadas en paralelo por agentes especializados.

### Solucion de OpenClaw
- `sessions_spawn`: Crea un sub-agente con su propia sesion
- Limite de profundidad configurable (evita recursion infinita)
- El sub-agente informa al agente principal al terminar
- Registry con persistencia para tracking de todos los sub-agentes activos

### Implementacion propuesta
**Archivo a crear:** `azul_brain/cortex/subagent_manager.py`

Funcionalidad:
- Crear sub-tareas con Semantic Kernel invocando el kernel recursivamente
- Limite de profundidad: maximo 3 niveles de anidacion
- Timeout por sub-agente: maximo 5 minutos

### Prioridad: BAJA (funcionalidad avanzada post-MVP)

---

## 6. Tool Policy Pipeline (Politicas por Herramienta)

### Problema
No todas las herramientas tienen el mismo nivel de riesgo. `list_files` es inofensivo, pero `move_file` puede destruir datos.

### Solucion de OpenClaw
Un pipeline de politicas con capas:
1. **Capa Global:** Herramientas bloqueadas para todo el sistema
2. **Capa por Canal:** Herramientas bloqueadas segun el canal (ej. HTTP vs WebSocket)
3. **Capa por Agente:** Cada agente puede tener permisos diferentes
4. **Capa de Aprobacion:** Herramientas que requieren `human-in-the-loop`

Lista de herramientas peligrosas (siempre requieren aprobacion):
```
exec, spawn, shell, sessions_spawn, sessions_send,
fs_write, fs_delete, fs_move, apply_patch
```

### Implementacion propuesta
**Archivo a crear:** `azul_hands_mcp/tool_policy.py`

```python
from enum import Enum

class ToolRisk(Enum):
    SAFE = "safe"           # list_workspace_files
    MODERATE = "moderate"   # read_safe_file
    DANGEROUS = "dangerous" # move_safe_file

TOOL_POLICIES = {
    "list_workspace_files": ToolRisk.SAFE,
    "read_safe_file": ToolRisk.MODERATE,
    "move_safe_file": ToolRisk.DANGEROUS,
}

# Las herramientas DANGEROUS siempre piden confirmacion al usuario
# via Azure Bot Service antes de ejecutarse
```

### Prioridad: ALTA (necesario antes de distribuir el .exe)

---

## 7. Workspace Management (Gestion Robusta del Espacio de Trabajo)

### Problema
Escrituras concurrentes al mismo archivo pueden corromper datos. Cierres inesperados pueden dejar archivos a medio escribir.

### Solucion de OpenClaw
- **Write Locks:** Bloqueos de escritura por archivo para evitar condiciones de carrera
- **Session File Repair:** Al arrancar, detecta y repara transcripts corruptos
- **Transcript Repair:** Reconstruccion de historial de conversacion si el JSON esta malformado

### Implementacion propuesta
**Archivos a crear:**
- `azul_hands_mcp/workspace_lock.py` — File locking con `fcntl`/`msvcrt`
- `azul_brain/memory/repair.py` — Reparador de memoria corrupta al arrancar

### Prioridad: MEDIA (necesario cuando haya multiples sesiones concurrentes)
