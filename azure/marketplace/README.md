# Azure Marketplace Platform

The Skill Registry API and its cloud infrastructure live here.

Skill-specific deployment code does not belong here. Put that under the skill
folder that owns it.

## Registry API

The current scaffold exposes:

- `GET /api/health`
- `GET /api/catalog`
- `GET /api/admin/overview`
- `GET /api/skills`
- `GET /api/skills/{skill_id}/versions`
- `POST /api/skills/publish`
- `POST /api/skills/{skill_id}/versions/{version}/approve`
- `POST /api/skills/{skill_id}/versions/{version}/revoke`
- `POST /api/skills/{skill_id}/versions/{version}/approval` (compat)
- `GET /api/artifacts/{filename}`

For local development, generate bundles and a catalog with:

```powershell
npm run skills:package
```

Then point `AZUL_SKILL_ARTIFACT_DIR` at `dist/skills` so the registry can serve
the generated `.azulskill` bundles.

For a writable local registry, also configure:

- `AZUL_SKILL_REGISTRY_NAME`
- `AZUL_SKILL_REGISTRY_METADATA_PATH`
- `AZUL_SKILL_REGISTRY_CONSUMER_KEY`
- `AZUL_SKILL_REGISTRY_ADMIN_KEY`

The registry persists publish and approval state in that metadata JSON file and
stores artifact binaries in the configured artifact directory.

## Storage backends

Registry metadata now supports two modes:

1. `local`
   - metadata in a JSON file
   - artifacts on the local filesystem
2. `azure`
   - metadata in Azure Table Storage
   - artifacts in Azure Blob Storage

Select the backend with:

- `AZUL_SKILL_REGISTRY_STORAGE_MODE=local`
- `AZUL_SKILL_REGISTRY_STORAGE_MODE=azure`

When using `azure`, configure either:

- `AZUL_SKILL_REGISTRY_AZURE_CONNECTION_STRING`, or
- `AzureWebJobsStorage`

Optional Azure storage settings:

- `AZUL_SKILL_REGISTRY_AZURE_TABLE_NAME`
- `AZUL_SKILL_REGISTRY_AZURE_BLOB_CONTAINER`

## Internal publish flow

Local or CI publishing can use any of these:

1. `POST /api/skills/publish` with multipart upload

The server reads the uploaded `.azulskill`, validates `azul.skill.json`, stores
the artifact, and creates a `draft` version.

2. `POST /api/skills/publish` with JSON body:

```json
{
  "bundle_path": "C:/repo/dist/skills/dev.azulclaw.gemini-0.1.0.azulskill",
  "status": "draft",
  "published_by": "ci"
}
```

3. `POST /api/skills/publish` with JSON body:

```json
{
  "filename": "dev.azulclaw.gemini-0.1.0.azulskill",
  "content_base64": "<base64 artifact bytes>",
  "status": "draft",
  "published_by": "ci"
}
```

Approval and revocation then happen through:

```text
POST /api/skills/{skill_id}/versions/{version}/approve
POST /api/skills/{skill_id}/versions/{version}/revoke
```

The public `GET /api/catalog` surface only returns the latest approved version
for each skill. Internal `GET /api/skills` and version endpoints return drafts
and historical versions as well.

The desktop installer consumes the downloaded artifact by verifying the
catalog-provided SHA-256 and extracting it into the local runtime package store.
That keeps the registry responsible for distribution while AzulClaw keeps local
execution and configuration state on the user's machine.

Desktop installations discover an enterprise registry from `Settings -> Marketplace`.
The saved URL and optional Azure Function key mode are stored locally under
`memory/skills/settings.json`. The desktop API redacts the function key when it
returns settings to the UI; it only reports whether consumer/admin keys are configured.

`Settings -> Marketplace` also exposes a `Test connection` action. That calls
`POST /api/desktop/skills/settings/test` with the draft URL/auth settings so the
desktop app can verify `/api/health` and `/api/catalog` before or after saving.

If an admin key is configured, AzulClaw also enables the in-product `Registry`
surface. That view can:

- inspect a local `.azulskill`
- publish it as draft
- review version history
- approve or revoke versions

`POST /api/desktop/skills/marketplace/refresh` downloads `/api/catalog` from the
configured registry URL, stores a cache under `memory/skills/registry_catalog.json`,
and the normal install endpoint downloads the selected `.azulskill` artifact
from `/api/artifacts`. When auth mode is `function_key`, AzulClaw sends the
consumer key as the `x-functions-key` header for catalog refresh and artifact
download requests, and the admin key for registry administration requests.
