# Tauri

This folder contains the native wrapper for the desktop application.

- `src/` holds the Rust entry point
- `tauri.conf.json` defines bundling and application metadata
- `capabilities/` and generated files support the native shell configuration

The native layer should stay thin. Product logic belongs in the frontend and backend, not in Tauri-specific glue.
