# Refactor Folder Organizer Como Skill Ejemplar Con Agent Framework HITL

## Summary

El objetivo es convertir Folder Organizer en la skill oficial de referencia de AzulClaw. Su logica de intent, preview, plan, aprobacion y ejecucion debe salir del core nativo y vivir en la propia skill, usando Microsoft Agent Framework como motor de workflow y human-in-the-loop.

El core de AzulClaw debe aportar un runtime generico de workflows, permisos, aprobaciones, persistencia, UI y auditoria. Folder Organizer debe demostrar como una skill del marketplace declara y ejecuta un flow completo sin tener comportamiento especifico incrustado en `conversation.py`.

## Key Interfaces

Extender `azul.skill.json` con una seccion `workflow`:

```json
{
  "workflow": {
    "mode": "isolated_process",
    "protocol_version": "1.0",
    "entrypoint": {
      "command": "python",
      "args": ["workflow/main.py"]
    },
    "tools": {
      "preview": "preview_folder_organization",
      "execute": "organize_target_folder"
    },
    "tool_policies": {
      "preview": {
        "requires_approval": false
      },
      "execute": {
        "requires_approval": true,
        "sensitive_action": "move_files"
      }
    },
    "input_defaults": {
      "recursive": true,
      "preview_arguments": {
        "recursive": true,
        "include_moves": true
      }
    },
    "sensitive_actions": ["move_files"],
    "capability_prompt": "prompts/capability.md",
    "schemas": {
      "intent": "schemas/intent.schema.json",
      "preview_context": "schemas/preview-context.schema.json",
      "execution_plan": "schemas/execution-plan.schema.json"
    },
    "checkpoint_policy": "required"
  }
}
```

Declarar activacion semantica fuera del core:

```json
{
  "activation": {
    "restart_required": false,
    "workflow_intents": [
      "Create a reviewed organization plan for the configured target folder.",
      "Preview proposed file moves before execution.",
      "Apply a previously reviewed Folder Organizer plan only after human approval."
    ],
    "workflow_examples": [
      "Dame un plan de organizacion utilizando la skill de folder organizer.",
      "Use Folder Organizer to preview how the target folder should be organized."
    ]
  }
}
```

Anadir tipos core para workflows:

- `SkillWorkflowRun`: `run_id`, `skill_id`, `conversation_id`, `status`, `checkpoint_id`.
- `HumanApprovalRequest`: `request_id`, `run_id`, `action_kind`, `title`, `summary`, `payload`, `risk`, `labels`.
- `HumanApprovalResponse`: `approved`, `reason`, `user_id`.
- `SkillWorkflowEvent`: `delta`, `status`, `request_info`, `completed`, `failed`.

Anadir API nueva para HITL:

- `POST /api/desktop/workflows/{run_id}/requests/{request_id}/decision`
- El streaming de chat debe emitir eventos HITL estructurados.
- El frontend debe dejar de depender de parsear `[PENDING_ACTION:approval]` para nuevos approvals.
- Mantener compatibilidad de solo lectura para approvals legacy ya persistidos.

## Implementation Changes

### Core Runtime

Crear `runtime/skill_workflow_runtime.py` con estas responsabilidades:

- Cargar el manifest de la skill y validar `workflow`, permisos y sensitive actions.
- Iniciar workflows de Microsoft Agent Framework.
- Persistir checkpoints y asociarlos a `run_id`, `conversation_id` y `skill_id`.
- Reanudar workflows con respuestas humanas.
- Traducir `WorkflowContext.request_info(...)` a eventos de aprobacion para la UI.

Usar primitives de Microsoft Agent Framework:

- `Workflow`
- `WorkflowBuilder`
- `FunctionExecutor`
- `AgentExecutor`
- `WorkflowContext.request_info`
- `WorkflowCheckpoint`
- `CheckpointStorage`
- `response_handler`

El mecanismo nuevo de aprobacion debe ser:

```python
await ctx.request_info(
    HumanApprovalRequest(...),
    HumanApprovalResponse,
)
```

`ApprovalService` debe mantenerse, pero como indice/lifecycle de requests HITL, no como motor paralelo de ejecucion.

### Skill Isolation

Permitir workflows propios de marketplace solo en proceso aislado.

No permitir import directo de codigo Python de una skill dentro del proceso principal de AzulClaw.

El worker aislado debe comunicarse con el core mediante un contrato RPC/eventos:

- El core entrega manifest, config, contexto conversacional y permisos.
- El worker emite eventos de workflow.
- El worker solicita tools al core.
- El core aplica permisos, `tool_policies` y aprobaciones.
- El worker nunca ejecuta tools no declaradas ni sensitive actions sin aprobacion.

### Folder Organizer Skill

Anadir a `skills/official/desktop-organizer/`:

```text
workflow/
  main.py
  README.md
schemas/
  intent.schema.json
  preview-context.schema.json
  execution-plan.schema.json
  approval-request.schema.json
prompts/
  capability.md
  planning.md
  preview-context.md
```

El workflow de Folder Organizer debe ser:

```text
marketplace_router_selects_workflow
  -> preview_folder_organization
  -> build_organization_plan
  -> complete_with_plan, si no hay movimientos ejecutables
  -> request_approval, solo si organization_plan.executable=true
  -> execute, solo desde organization_plan aprobado
  -> summarize_result
```

Si no hay movimientos ejecutables:

- Debe explicar el estado real.
- Debe devolver `organization_plan`.
- No debe pedir aprobacion.

Si hay movimientos ejecutables:

- Debe crear `organization_plan` antes de cualquier approval.
- Debe emitir `HumanApprovalRequest`.
- Debe ejecutar solo tras `HumanApprovalResponse(approved=True)` y solo si la request contiene el `organization_plan` ejecutable previo.

La taxonomia, nombres de carpetas y reglas especificas deben vivir en prompts/schemas/flow de la skill, no en AzulClaw core. No debe haber deteccion por regex ni listas ad hoc en el worker.

### Conversation Core Cleanup

Objetivo final: eliminar de `conversation.py` el comportamiento especifico de Folder Organizer:

- preflight hardcodeado de `preview_folder_organization`
- recovery especial de Folder Organizer
- fallback safe especifico
- guards especificos del contrato de Folder Organizer
- ejecucion directa de `organize_target_folder`
- constantes especificas como skill id/tool name en el flujo conversacional

`conversation.py` debe delegar en `SkillWorkflowRuntime` cuando una skill active un workflow.

Estado implementado:

- Hay un router semantico generico para workflows de marketplace basado en `activation.workflow_intents`, `workflow_examples`, descripcion y capabilities.
- El core puede arrancar cualquier workflow seleccionado por manifest sin conocer su logica interna.
- `input_defaults` permite que cada skill defina el payload inicial del workflow sin hardcodearlo en AzulClaw.
- Cuando un workflow de marketplace esta habilitado pero falla o falta el runtime, AzulClaw no cae al camino embebido legacy.
- Folder Organizer ya no tiene fallback workflow especifico en el flujo activo de `process_user_message` ni streaming.
- Si Folder Organizer tiene workflow habilitado y el router generico no lo selecciona, el core no ejecuta el preflight legacy de `preview_folder_organization`.
- La capa legacy de ejecucion/recuperacion de approvals de Folder Organizer fue eliminada: ya no existen el staging de `[PENDING_ACTION:folder_organizer]` por texto, la ejecucion directa de pending actions contra `organize_target_folder`, ni la recuperacion de approvals desde previews antiguas. Las approvals de Folder Organizer van exclusivamente por el workflow HITL.
- Se conserva la infraestructura compartida: el preview store (`FolderOrganizerPreviewStore` + `maybe_record_folder_organizer_preview`), el safe-reply de fallback, el plan-context (`_maybe_prepare_folder_organizer_plan_context`) y el capability guard.
- El modulo `runtime/folder_organizer_legacy_approvals.py` fue eliminado; las constantes y la validacion de argumentos compartidas viven ahora en `runtime/pending_action_intent.py`.
- Las reglas de capability de Folder Organizer fueron retiradas del system prompt global; viven en `skills/official/desktop-organizer/prompts/capability.md` y se cargan desde el manifest de la skill.
- `conversation.py` ya no usa regex para parsing/checks legacy de Folder Organizer; los helpers legacy usan parsing estructurado o lectura manual del payload/resumen.

### Localized Workflow Rendering

El worker aislado no tiene contexto de idioma ni acceso al LLM: produce resumenes de plan, estado y resultado en ingles (source summary). La localizacion y el formateo legible viven en el core, no en la skill.

- `SemanticJudgeService.render_skill_workflow_message(...)` (fast lane, sin regex/listas) recibe el source summary en ingles, el mensaje del usuario y la fase, y devuelve JSON `{language, plan_markdown, plan_short, next_step}`.
- El idioma de salida se decide por como escribe el usuario, nunca por el source summary (que siempre es ingles). El source summary solo aporta datos (numeros, nombres de grupos), nunca influye en el idioma.
- `ConversationOrchestrator.localize_workflow_message(...)` es el punto de entrada para las rutas; reune un `language_sample` con los ultimos mensajes del usuario para que un disparador corto (p. ej. "Ok, implementalo") salga en el idioma correcto, y cae al texto original ingles si el LLM falla.
- Fases soportadas: `plan_ready` (invita a confirmar la aplicacion, no a "revisar"), `plan_only` (explica que no hay nada que mover), `awaiting_approval` (apunta a los botones Apply/Cancel, sin repetir plan ni ids) y `executed` (confirma lo realizado, tambien localizado).
- El texto del ACTION de la card se limpia de markdown antes de mostrarse, porque la card lo renderiza como texto plano. La respuesta del turno ya no incluye la linea cruda con el `request_id`; en su lugar persiste un CTA corto y localizado.
- Las salidas no contienen ids, tokens ni volcados largos de rutas; conservan numeros y nombres de grupos.

### Frontend

Renderizar approvals nuevos desde `HumanApprovalRequest` estructurado.

Enviar decisiones al endpoint HITL nuevo.

Mostrar estados desde lifecycle HITL:

- `pending`
- `approved`
- `rejected`
- `running`
- `completed`
- `failed`
- `expired`

Mantener renderer legacy para mensajes antiguos con `[PENDING_ACTION:approval]`, solo para historial y compatibilidad.

El refresh automatico de chat (poll de fondo cada 5s) no debe destruir la approval card: `mergeMessageMetadata` preserva `workflow_events` y los campos de approval (`approval_action_id`, `approval_status`, `approval_status_label`) cuando el mensaje entrante no los trae, en lugar de sobrescribirlos con vacio.

## Migration Plan

### Phase 1: Runtime HITL Base

- Introducir `SkillWorkflowRuntime`.
- Introducir modelos core HITL.
- Introducir endpoint de decision HITL.
- Conectar `ApprovalService` al lifecycle nuevo.
- Mantener approvals legacy funcionando.

### Phase 2: Folder Organizer Workflow

- Anadir workflow aislado a la skill.
- Ampliar manifest de Folder Organizer.
- Mover prompts/schemas especificos a la skill.
- Ejecutar Folder Organizer por `SkillWorkflowRuntime`.

### Phase 3: Remove Core Hardcoding

- Introducir router semantico generico de workflows desde manifest.
- Mover defaults de entrada al manifest con `workflow.input_defaults`.
- Impedir fallback embebido si el workflow instalado falla o no tiene runtime disponible.
- Convertir tests principales a workflow y mantener tests legacy solo con workflows deshabilitados explicitamente.
- Hecho: retirar el fallback workflow especifico de Folder Organizer del flujo activo.
- Hecho: dejar approvals/previews legacy como compatibilidad activa solo cuando no hay workflow habilitado.
- Hecho: separar la logica legacy de Folder Organizer del servicio principal de pending actions.
- Hecho: retirar instrucciones especificas de Folder Organizer del prompt global de AzulClaw.
- Hecho: quitar regex activo de Folder Organizer en `conversation.py`.
- Pendiente: retirar el codigo legacy completo cuando el historial antiguo pueda tratarse como solo lectura/renderizado.

### Phase 4: Marketplace Generalization

- Validar workflows aislados en install/enable.
- Documentar Folder Organizer como ejemplo canonico.
- Permitir que nuevas skills declaren workflows propios con el mismo contrato.
- Probar plug-and-play con una skill ficticia sin rutas especificas en `conversation.py`.

## Implemented Checkpoints

- `SkillWorkflowRuntime` soporta proceso aislado, tool mediation, checkpoints, resume e HITL.
- El worker de Folder Organizer usa Microsoft Agent Framework (`WorkflowBuilder`, `Executor`, `request_info`, `response_handler`, `FileCheckpointStorage`).
- `workflow.tool_policies` protege tools sensibles y bloquea ejecucion sin HITL aprobado.
- `activation.workflow_intents` y `activation.workflow_examples` alimentan routing semantico sin regex.
- `workflow.input_defaults` mueve defaults de arranque al manifest.
- `ConversationOrchestrator` usa routing generico de marketplace para workflows; Folder Organizer entra por el mismo mecanismo que cualquier skill plug-and-play.
- El prompt global de AzulClaw no contiene contratos de Folder Organizer; el contrato se declara en la propia skill.
- El preflight legacy de Folder Organizer se bloquea cuando la skill declara workflow habilitado.
- Las approval cards legacy de Folder Organizer no se stagean ni ejecutan cuando el workflow marketplace esta habilitado; el usuario debe obtener una approval HITL emitida por el workflow.
- Las rutas desktop exponen decision HITL para `run_id/request_id`.
- El frontend renderiza `workflow_events` y decisiones de approval estructuradas.
- Los mensajes del workflow (plan, espera de aprobacion y resultado tras aplicar) se localizan al idioma del usuario via fast lane, usando `language_sample` para disparadores cortos; el source summary del worker (ingles) solo aporta datos.
- El texto del ACTION de la card se limpia de markdown y la respuesta del turno ya no expone el `request_id` crudo.
- El poll de fondo de 5s ya no borra la approval card: `mergeMessageMetadata` preserva `workflow_events` y los campos de approval.

## Test Plan

### Manifest And Registry

- Rechaza workflow sin permisos requeridos.
- Rechaza sensitive actions no declaradas en `permissions.sensitive_actions`.
- Rechaza entrypoints invalidos o fuera de la skill.
- Acepta Folder Organizer como skill ejemplar.

### SkillWorkflowRuntime

- Inicia workflow y emite eventos.
- Guarda checkpoint al emitir `request_info`.
- Reanuda desde checkpoint con approve.
- Reanuda desde checkpoint con reject.
- Marca lifecycle correctamente en `ApprovalService`.
- Falla de forma segura si el checkpoint no existe o no coincide con el workflow.

### Security

- Un worker aislado no puede invocar tools no declaradas.
- Un worker aislado no puede ejecutar sensitive actions sin HITL.
- Una `tool_policy` con `requires_approval=true` debe estar vinculada a una `sensitive_action` declarada.
- Una aprobacion obsoleta no ejecuta nada.
- Una decision duplicada es idempotente.

### Folder Organizer

- Pide plan y ejecuta preview automaticamente.
- Si hay movimientos, emite approval HITL.
- Si no hay movimientos, entrega plan conceptual sin approval.
- Approve ejecuta MCP tool desde checkpoint.
- Reject cancela sin ejecutar.
- Conflictos o preview obsoleto terminan en `failed` seguro.
- Batching con `plan_token` sigue funcionando.

### Frontend/API

- Renderiza card desde `HumanApprovalRequest`.
- Envia decision HITL al endpoint nuevo.
- Muestra estados lifecycle.
- Historial legacy con `[PENDING_ACTION:approval]` sigue visible.
- Streaming emite `request_info`, `status`, `delta` y `completed/failed`.

## Assumptions

- Alcance inicial: Folder Organizer + runtime generico. No migrar todas las skills en el primer cambio.
- Las skills marketplace con codigo propio se ejecutan en proceso aislado.
- No se importa codigo arbitrario de skills dentro del backend principal.
- Priorizamos API nueva HITL por robustez.
- La compatibilidad con approvals antiguos sera de lectura/migracion, no el mecanismo principal.
- Microsoft Agent Framework gestiona workflow, checkpoints y request/response HITL.
- AzulClaw sigue siendo responsable de permisos, lifecycle, UI, auditoria y politica de seguridad.
