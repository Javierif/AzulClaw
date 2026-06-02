# Folder Organizer Skill Tests

Self-contained tests for the skill's own MCP server contract (`mcp/server.py`).
They do **not** import the AzulClaw backend, so the bundle stays portable.

Run them with:

```
python -m unittest discover -s skills/official/desktop-organizer/tests -p "test_*.py" -v
```

Brain-side integration tests (router selection, semantic grouping wiring, HITL
approval, end-to-end with the real MCP runtime) live in the repository-level
`tests/` package instead, because they exercise `azul_backend`.
