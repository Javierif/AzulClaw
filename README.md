# AzulClaw

AzulClaw es un asistente personal local con backend cognitivo en Python y una futura app desktop dedicada.

## Estructura principal

- `azul_backend/`: backend Python del agente, memoria, integraciones y MCP.
- `azul_desktop/`: shell de la aplicacion de escritorio.
- `docs/`: documentacion tecnica, UX y arquitectura.
- `scripts/`: utilidades de setup y desarrollo.
- `memory/`: almacenamiento local de desarrollo si aplica.

## Arranque rapido del backend

1. Crear y activar el entorno virtual.
2. Instalar dependencias con `pip install -r requirements.txt`.
3. Crear `azul_backend/azul_brain/.env.local` si necesitas configuracion local.
4. Arrancar desde la raiz del repo:

```bash
python -m azul_backend.azul_brain.main_launcher
```

## Carpetas importantes

- Backend principal: `azul_backend/azul_brain/`
- MCP sandbox: `azul_backend/azul_hands_mcp/`
- Documentacion: `docs/`
- Desktop scaffold: `azul_desktop/`

## Seguridad

- No subir `.env.local` ni credenciales.
- El acceso a ficheros debe pasar por el MCP sandbox y su validador de rutas.
- El workspace de AzulClaw debe permanecer aislado del resto del sistema.

## Documentacion clave

- Arquitectura: `docs/01_arquitectura.md`
- Setup y desarrollo: `docs/02_setup_y_desarrollo.md`
- Modelo de seguridad: `docs/03_modelo_seguridad.md`
- Diseño desktop: `docs/08_diseno_interfaz_desktop.md`
- Wireframes: `docs/09_wireframes_baja_fidelidad_desktop.md`
- Estructura del repo: `docs/10_arquitectura_desktop_y_estructura_repo.md`
