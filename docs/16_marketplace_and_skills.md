# Marketplace and Skills

Last reviewed: 2026-06-02

## Purpose

This document explains how the AzulClaw Marketplace works, how installable
skills are structured, and where marketplace responsibilities live across the
repository.

Use this document when you need to:

- understand the difference between Marketplace and Registry Admin
- add or modify a skill
- package and publish a `.azulskill` bundle
- understand what the core runtime expects from a skill manifest

## Product surfaces

AzulClaw separates skill distribution into two product surfaces:

1. `Marketplace`
   Users browse approved skills, install them locally, configure them, and
   enable or disable their runtime.
2. `Registry Admin`
   Administrators inspect bundles, publish draft versions, approve or revoke
   versions, and control what the Marketplace catalog exposes.

This separation matters because local execution happens on the user's machine,
while publication and approval happen in the private company registry.

## Ownership model

Marketplace support is intentionally split across three repository areas:

- `skills/`
  Owns skill manifests, templates, prompts, schemas, tests, docs, MCP servers,
  workflow code, and skill-specific infrastructure.
- `azure/marketplace/`
  Owns the private Skill Registry API and its Azure infrastructure.
- `azul_backend/azul_brain/api/skill_services.py`
  Owns local install, local skill state, catalog refresh, and runtime
  resolution on the desktop side.

Core AzulClaw code should consume skills through their manifest and installed
state. Skill-specific behavior should stay inside the owning skill folder
instead of being embedded directly in `azul_backend` or `azul_desktop`.

## End-to-end lifecycle

The current skill lifecycle is:

1. Author a skill under `skills/official/<skill>/` or start from a template in
   `skills/templates/`.
2. Define the contract in `azul.skill.json`.
3. Validate manifests with `npm run skills:validate`.
4. Package artifacts with `npm run skills:package`.
5. Publish the resulting `.azulskill` bundle to the Skill Registry as a draft.
6. Approve the version in `Registry Admin`.
7. Refresh the desktop Marketplace catalog from the configured registry.
8. Install the approved bundle locally.
9. Configure local settings and secrets for the installed skill.
10. Enable the skill so AzulClaw can resolve and run its declared runtime.

At install time, AzulClaw extracts the bundle under
`memory/skills/packages/<skill_id>/<version>/`, validates the bundle hash when
available, rejects unsafe archive paths, and requires `azul.skill.json` at the
bundle root.

## Manifest contract

All installable skills declare their contract in `azul.skill.json`, validated
by `skills/schema/azul.skill.schema.json`.

The required top-level fields are:

- `schema_version`
- `id`
- `name`
- `version`
- `publisher`
- `description`
- `kind`
- `runtime`
- `compatibility`
- `permissions`
- `capabilities`

### Skill kinds

The current manifest schema supports these kinds:

- `local_mcp`: starts a local MCP server as a separate process
- `remote_agent`: calls a remote HTTP service or corporate agent
- `knowledge`: ships installable knowledge packs
- `workflow`: declares reusable workflow behavior using the core runtime
- `channel_connector`: connects AzulClaw to inbound or outbound channels

### Runtime block

The `runtime` block declares how AzulClaw should execute the skill.

- `kind = "mcp"` is used for local MCP processes
- `kind = "remote_agent"` is used for HTTP-backed skills
- `kind = "none"` is valid for skills that only contribute assets or knowledge
- `transport` is currently `stdio` or `http`
- `command` and `args` are resolved relative to the extracted skill folder when
  the referenced paths exist locally
- `endpoint` and `auth` describe remote runtime behavior

### Permissions and configuration

Skills must explicitly declare the permissions they need, including filesystem,
process, network, memory, channel, and sensitive action boundaries. Optional
`config_schema` and `secrets` blocks define what the Marketplace UI collects
from the user.

Configured skill values are stored locally in
`memory/skills/installed_skills.json`. Secret values are stored privately and
returned to the frontend only as configured placeholders.

### Workflow and activation blocks

The optional `workflow` block is how a skill declares an installable workflow.
It currently supports:

- `mode`: `built_in_template` or `isolated_process`
- `entrypoint`: process command and arguments for isolated workflows
- `tools`: named tool bindings
- `tool_policies`: approval requirements and sensitive action mapping
- `input_defaults`: default execution inputs
- `capability_prompt`: prompt contribution path inside the skill
- `schemas`: workflow-local schema paths
- `checkpoint_policy`: `none`, `optional`, or `required`

The optional `activation` block contributes semantic routing hints such as
`workflow_intents` and `workflow_examples`. AzulClaw uses those hints to route
relevant user requests into the installed skill workflow before the worker
starts.

### Presentation block

The optional `presentation` block controls Marketplace UI rendering:

- `icon_text` for compact tiles
- `banner.variant` for built-in generated banners
- `banner.image` for bundle-provided assets
- `banner.title` and `banner.accent` for display tuning

## Local state and runtime integration

The desktop app keeps marketplace state in three main files:

- `memory/skills/settings.json`
  Registry URL and optional function-key authentication settings.
- `memory/skills/registry_catalog.json`
  Cached approved catalog fetched from the registry.
- `memory/skills/installed_skills.json`
  Installed versions, local config, secret placeholders, and enablement state.

The desktop backend exposes runtime status through
`GET /api/desktop/skills/runtime`.

Local MCP skills receive a minimal runtime environment contract:

- `AZUL_SKILL_ID`
- `AZUL_SKILL_NAME`
- `AZUL_SKILL_ROOT`
- `AZUL_SKILL_CONFIG_<FIELD>` for non-secret config
- `AZUL_SKILL_SECRET_<NAME>` for resolved secrets
- `AZUL_SKILL_SECRET_<NAME>_CONFIGURED=true` when a secret exists

Enabled `remote_agent` skills are called through HTTPS with the configured
endpoint and auth policy. Enabled `channel_connector` skills may also feed
runtime behavior back into the local backend.

## Registry architecture

The private Skill Registry lives under `azure/marketplace/` and currently
exposes:

- `GET /api/health`
- `GET /api/catalog`
- `GET /api/admin/overview`
- `GET /api/skills`
- `GET /api/skills/{skill_id}/versions`
- `POST /api/skills/publish`
- `POST /api/skills/{skill_id}/versions/{version}/approve`
- `POST /api/skills/{skill_id}/versions/{version}/revoke`
- `GET /api/artifacts/{filename}`

The registry currently supports two storage modes:

1. `local`
   Metadata in JSON and artifacts on the local filesystem.
2. `azure`
   Metadata in Azure Table Storage and artifacts in Azure Blob Storage.

Desktop clients discover the registry from `Settings -> Marketplace`. When the
registry uses Azure Function key auth, the desktop app stores the consumer and
admin key state locally and sends `x-functions-key` on catalog, artifact, and
admin requests.

## Bundle shape

Marketplace bundles are `.azulskill` archives with `azul.skill.json` at the
root. A typical bundle shape is:

```text
azul.skill.json
prompts/
schemas/
assets/
docs/
```

Skills may also include `mcp/`, `knowledge/`, `workflow/`, `src/`, and
`infra/terraform/` depending on the skill kind.

## Example skill folder

`skills/official/desktop-organizer/` is the current reference skill because it
combines Marketplace packaging, a local MCP runtime, and a workflow owned by
the skill.

```text
skills/official/desktop-organizer/
|- azul.skill.json
|- README.md
|- mcp/
|  |- server.py
|  `- README.md
|- workflow/
|  |- main.py
|  `- README.md
|- prompts/
|  |- capability.md
|  |- planning.md
|  `- preview-context.md
|- schemas/
|  |- approval-request.schema.json
|  |- config.schema.json
|  |- execution-plan.schema.json
|  |- intent.schema.json
|  `- preview-context.schema.json
|- infra/
|  `- terraform/
`- tests/
   `- README.md
```

Responsibilities inside that folder are:

- `azul.skill.json`
  The installable manifest and runtime contract.
- `mcp/`
  The local MCP server used by the skill.
- `workflow/`
  The skill-owned workflow entrypoint and behavior documentation.
- `prompts/`
  Prompt fragments loaded through the skill contract.
- `schemas/`
  Workflow-local schemas used for validation and checkpoints.
- `infra/terraform/`
  Skill-owned cloud deployment scaffolding.
- `tests/`
  Skill-specific tests and docs.

This is the folder layout to copy when a new skill owns executable runtime,
workflow behavior, and its own deployment surface.

## Current templates

`skills/templates/` contains starter layouts for the supported patterns:

- `local-mcp/`
  For local stdio MCP runtimes.
- `remote-agent/`
  For skills backed by a remote corporate service.
- `workflow/`
  For skills that register repeatable workflows using core capabilities.
- `channel-connector/`
  For inbound or outbound channel integrations.
- `knowledge/`
  For installable knowledge packs.

## Contributor rules

- Keep skill-specific runtime and deployment code inside the owning skill
  folder.
- Use the manifest as the integration contract instead of hard-coding special
  cases into core runtime code.
- Treat `azure/marketplace/` as registry infrastructure, not as a home for
  skill runtime code.
- Prefer updating this document, `skills/README.md`, and
  `azure/marketplace/README.md` together when marketplace behavior changes.

## Related documents

- [Architecture Overview](01_architecture.md)
- [Component Reference](04_component_reference.md)
- [Skills README](../skills/README.md)
- [Azure Marketplace Platform](../azure/marketplace/README.md)