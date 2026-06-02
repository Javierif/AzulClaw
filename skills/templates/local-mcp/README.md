# Local MCP Skill Template

Use this template for skills that add local tools through an MCP server.

The MCP process must run outside the AzulClaw backend process and declare the
permissions it needs in `azul.skill.json`.

The default template expects a Python stdio MCP runtime at `mcp/server.py`.
