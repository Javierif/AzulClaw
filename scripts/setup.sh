#!/usr/bin/env bash
# Script de instalación multiplataforma (POSIX). Para Windows nativo ver notas abajo.
# Uso:
#   chmod +x scripts/setup.sh
#   ./scripts/setup.sh

set -e

PYTHON=${PYTHON:-python3}
VENV_DIR=".venv"

echo "Usando intérprete: $PYTHON"

# Crear virtualenv si no existe
if [ ! -d "$VENV_DIR" ]; then
  echo "Creando virtualenv en $VENV_DIR..."
  $PYTHON -m venv "$VENV_DIR"
fi

# Activar virtualenv
if [ -f "$VENV_DIR/bin/activate" ]; then
  # POSIX
  # shellcheck source=/dev/null
  . "$VENV_DIR/bin/activate"
elif [ -f "$VENV_DIR/Scripts/activate" ]; then
  # Git Bash / MSYS on Windows
  . "$VENV_DIR/Scripts/activate"
fi

# Actualizar pip e instalar dependencias
pip install --upgrade pip
if [ -f "requirements.txt" ]; then
  pip install -r requirements.txt
else
  echo "requirements.txt no encontrado en la raíz del repo."
  exit 1
fi

echo "Instalación completada. Activa el entorno y ejecuta el launcher:"
echo "  POSIX: source .venv/bin/activate && python -m AzulClaw.azul_brain.main_launcher"
echo "  Windows CMD: .venv\\Scripts\\activate && python -m AzulClaw.azul_brain.main_launcher"
echo "  Windows PowerShell: . .venv\\Scripts\\Activate.ps1; python -m AzulClaw.azul_brain.main_launcher"

# Notas para Windows nativo (CMD / PowerShell):
: <<'WINDOWS_NOTES'
Windows CMD:
  python -m venv .venv
  .venv\Scripts\activate
  pip install -r requirements.txt

PowerShell (si aparece bloqueo de ejecución, use: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass):
  python -m venv .venv
  . .venv\Scripts\Activate.ps1
  pip install -r requirements.txt
WINDOWS_NOTES