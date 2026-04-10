import os
from pathlib import Path

class SecurityError(Exception):
    """Excepción lanzada cuando una operación viola las reglas de seguridad de AzulHands."""
    pass

class PathValidator:
    """
    Motor de validación de rutas (Path Traversal Guard).
    Asegura que cualquier ruta solicitada por el Cerebro de IA esté estrictamente
    dentro de un directorio base permitido (el 'Workspace').
    """

    def __init__(self, allowed_base_dir: str):
        self.allowed_base = Path(allowed_base_dir).resolve()
        
        # Crear el directorio base si no existe
        if not self.allowed_base.exists():
            self.allowed_base.mkdir(parents=True, exist_ok=True)
            
        print(f"[Security] PathValidator inicializado. Workspace restringido a: {self.allowed_base}")

    def safe_resolve(self, requested_path: str) -> Path:
        """
        Resuelve una ruta solicitada y verifica mediante resolución canónica 
        que no escape del directorio base permitido.
        
        Args:
            requested_path: La ruta relativa o absoluta que la IA quiere tocar.
            
        Returns:
            Path obj resuelto y validado.
            
        Raises:
            SecurityError: Si la ruta intenta hacer Path Traversal (ej. '../../Windows')
        """
        try:
            # 1. Expandir variables de entorno y '~' si existieran
            expanded_path = os.path.expanduser(os.path.expandvars(requested_path))
            
            # 2. Si no es absoluta, interpretarla como relativa al workspace permitido
            if not os.path.isabs(expanded_path):
                target_path = self.allowed_base / expanded_path
            else:
                target_path = Path(expanded_path)
                
            # 3. Resolver la ruta final (elimina los ../ y symlinks)
            resolved_target = target_path.resolve()
            
            # 4. Chequeo Crítico de Seguridad: ¿El directorio resultante empieza por nuestro allowed_base?
            if not str(resolved_target).startswith(str(self.allowed_base)):
                raise SecurityError(
                    f"Violación de Seguridad: La ruta solicitada '{requested_path}' "
                    f"resuelve fuera del Workspace permitido ({self.allowed_base})"
                )
                
            return resolved_target
            
        except Exception as e:
            if isinstance(e, SecurityError):
                raise
            # Cualquier otro error de parsing de rutas se bloquea por defecto
            raise SecurityError(f"Error al analizar la ruta solicitada: {str(e)}")
