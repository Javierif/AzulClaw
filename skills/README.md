# AzulClaw Skills

This directory contains first-party skills, reusable templates, and the
manifest schema used by the AzulClaw marketplace.

For the full Marketplace lifecycle, skill ownership model, registry flow, and
a reference skill folder example, see
[Marketplace and Skills](../docs/16_marketplace_and_skills.md).

## Layout

```text
skills/
|- schema/                 Contract for azul.skill.json
|- templates/              Starter layouts for new skill authors
`- official/               First-party AzulClaw skills published by this repo
```

## Skill ownership

Each skill owns its runtime, prompts, schemas, tests, docs, and cloud
infrastructure. A skill that needs Azure resources keeps its Terraform under
`infra/terraform/` inside the skill folder.

Core AzulClaw code should consume skills through their manifests and installed
state. Avoid adding skill-specific behavior to `azul_backend` or
`azul_desktop` unless it is a temporary migration adapter.

Validate all skill manifests with:

```powershell
npm run skills:validate
```

Package first-party skills into `.azulskill` bundles with:

```powershell
npm run skills:package
```

The desktop backend can install a local bundle through:

```http
POST /api/desktop/skills/install-bundle
Content-Type: application/json

{
  "bundle_path": "dist/skills/dev.azulclaw.gemini-0.1.0.azulskill",
  "sha256": "optional expected artifact hash"
}
```

Installed bundles are extracted under
`memory/skills/packages/<skill_id>/<version>/`. The installer validates the
bundle hash when supplied, rejects unsafe zip paths, and requires
`azul.skill.json` at the bundle root.

Enterprise registries are configured from the desktop app under
`Settings -> Marketplace`. The URL is user-editable in the frontend, and v1 also
supports Azure Function key auth without requiring environment variables.
Refreshing the marketplace downloads `/api/catalog` into
`memory/skills/registry_catalog.json`.
Installing a registry skill downloads its `.azulskill`, verifies the artifact
SHA-256 from the catalog, and then uses the same local bundle installer.

## Bundle shape

Marketplace downloads are expected to be packaged as `.azulskill` bundles with
this shape:

```text
azul.skill.json
prompts/
schemas/
assets/
docs/
```

Skills may also include `mcp/`, `knowledge/`, `workflow/`, `src/`, and
`infra/terraform/` depending on their kind.

## Local MCP runtime convention

When an installed `local_mcp` skill is enabled, AzulClaw resolves its
`runtime.command` and `runtime.args` relative to the extracted skill folder when
those paths exist locally. Enabled MCP skills are then started as separate
stdio MCP runtimes alongside the built-in AzulHands MCP server.

Each runtime receives a minimal environment contract:

- `AZUL_SKILL_ID`
- `AZUL_SKILL_NAME`
- `AZUL_SKILL_ROOT`
- `AZUL_SKILL_CONFIG_<FIELD>` for non-secret configured fields
- `AZUL_SKILL_SECRET_<NAME>` for stored secret values
- `AZUL_SKILL_SECRET_<NAME>_CONFIGURED=true` when that secret exists

The desktop backend exposes local MCP runtime connection state at
`GET /api/desktop/skills/runtime`.

## Secret and remote-agent convention

Skill configuration entered from the desktop UI is stored locally under
`memory/skills/installed_skills.json`. Secret values are stored privately and
returned to the frontend only as `<configured>`.

Use `secrets[].config_field` when the UI field name should differ from the
runtime secret name. This is useful for skills like Telegram where the user
edits `botToken`, but the runtime contract expects `TELEGRAM_BOT_TOKEN`.

Enabled `remote_agent` skills are called through HTTPS with this request shape:

```json
{
  "skill_id": "dev.azulclaw.gemini",
  "skill_name": "Gemini",
  "prompt": "User instruction for the remote agent",
  "context": {}
}
```

For `runtime.auth.type = "api_key"`, AzulClaw reads the configured secret,
injects it into the declared header, and exposes the skill as a dynamic agent
tool once the skill is enabled.

## Channel connector convention

Enabled `channel_connector` skills can also feed runtime behavior back into the
local backend. The first-party Telegram connector now overrides the legacy
`TELEGRAM_ALLOWED_USER_IDS` and `TELEGRAM_ALLOWED_CHAT_IDS` environment
variables from its installed skill config when the skill is enabled.

Marketplace runtime status also includes enabled channel connectors so the UI
can show whether a connector is configured and whether extra Azure relay
deployment is expected.

## Marketplace presentation

Each skill can control how it appears in the Marketplace through the optional
`presentation` block in `azul.skill.json`:

```json
{
  "presentation": {
    "icon_text": "TG",
    "banner": {
      "variant": "telegram",
      "title": "Telegram",
      "image": "assets/banner.png",
      "accent": "#38bdf8"
    }
  }
}
```

Use `banner.variant` for built-in generated banners (`desktop`, `gemini`,
`telegram`, `blueprint`, `agent`, `channel`, `default`). Use `banner.image`
when a skill ships a custom image asset in its bundle. If `banner.image` is not
provided, AzulClaw renders the built-in generated banner and uses
`banner.title`, falling back to the skill `name`.
