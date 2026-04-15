# AzulClaw

<p align="center">
  <img width="600" height="400" alt="AzulClaw" src="https://github.com/user-attachments/assets/c73da31c-f0e1-416e-9da7-ee5e30650857" />
</p>

> A local-first, secure AI companion that connects a cognitive Azure brain to a sandboxed filesystem — all running on your machine.

## Architecture

AzulClaw is split into two layers that communicate over a local HTTP API:

```
azul_backend/
├── azul_brain/          # Cognitive layer: Azure OpenAI agent, memory, HTTP API, Bot Framework
│   ├── cortex/          # Agent setup and MCP tool adapter
│   ├── api/             # Desktop app endpoints (chat, memory, workspace, hatching)
│   ├── bot/             # Azure Bot Framework activity handler
│   ├── memory/          # Short-term and vector memory
│   └── soul/            # System prompt
└── azul_hands_mcp/      # Secure filesystem MCP server (path traversal guard)

azul_desktop/            # Tauri + React desktop shell
docs/                    # Technical documentation
scripts/                 # Setup and development utilities
```

## Quick Start

```bash
# 1. Create and activate a virtual environment
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment (copy and fill in your Azure credentials)
cp azul_backend/azul_brain/.env.example azul_backend/azul_brain/.env.local

# 4. Start the backend
python -m azul_backend.azul_brain.main_launcher
```

## Security

- Do not commit `.env.local` or any credentials.
- All file access must go through the MCP sandbox and its path validator — direct filesystem access is not allowed.
- The AzulClaw workspace must remain isolated from the rest of the system.

## Documentation

| Section | File |
|---------|------|
| Architecture | [docs/01_architecture.md](docs/01_architecture.md) |
| Setup & Development | [docs/02_setup_and_development.md](docs/02_setup_and_development.md) |
| Security Model | [docs/03_security_model.md](docs/03_security_model.md) |
| Channels, Transport & Delivery | [docs/14_channels_and_transport.md](docs/14_channels_and_transport.md) |
| Desktop Interface Design | [docs/08_desktop_interface_design.md](docs/08_desktop_interface_design.md) |
| Desktop Wireframes | [docs/09_desktop_low_fidelity_wireframes.md](docs/09_desktop_low_fidelity_wireframes.md) |
| Desktop Architecture & Repo Structure | [docs/10_desktop_architecture_and_repo_structure.md](docs/10_desktop_architecture_and_repo_structure.md) |
| Azure Bot Deployment Guide | [docs/13_azure_bot_deployment_guide.md](docs/13_azure_bot_deployment_guide.md) |
