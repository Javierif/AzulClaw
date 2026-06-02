# Live semantic tests

These tests validate real Azure OpenAI semantic behavior without starting the
desktop frontend or the backend HTTP server.

They are opt-in because they call a live model endpoint.

## Configure

Create `azul_backend/.env.local`, `azul_backend/azul_brain/.env.local`, or a
repo-root `.env.local` with:

```env
AZUL_RUN_LIVE_SEMANTIC_TESTS=1
AZUL_AZURE_OPENAI_AUTH_MODE=api_key

AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_API_KEY=your-key
AZURE_OPENAI_DEPLOYMENT=your-main-deployment
AZURE_OPENAI_FAST_DEPLOYMENT=your-fast-deployment
```

`AZURE_OPENAI_FAST_DEPLOYMENT` is used by the semantic router. If omitted, the
tests fall back to `AZURE_OPENAI_DEPLOYMENT`.

`AZURE_OPENAI_API_VERSION` is optional. The backend already defaults to
`2024-10-21` for Azure OpenAI endpoints, and Foundry-compatible endpoints do
not use it.

## Run

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_live_folder_organizer_skill -v
```

Without `AZUL_RUN_LIVE_SEMANTIC_TESTS=1`, the tests are skipped.
