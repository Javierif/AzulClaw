import os
from pathlib import Path

class SecurityError(Exception):
    """Exception raised when an operation violates AzulHands security rules."""
    pass

class PathValidator:
    """
    Path Traversal Guard.
    Ensures that any path requested by the AI brain stays strictly
    within an allowed base directory (the 'Workspace').
    """

    def __init__(self, allowed_base_dir: str):
        self.allowed_base = Path(allowed_base_dir).resolve()

        # Create the base directory if it does not exist
        if not self.allowed_base.exists():
            self.allowed_base.mkdir(parents=True, exist_ok=True)

        print(f"[Security] PathValidator initialized. Workspace restricted to: {self.allowed_base}")

    def safe_resolve(self, requested_path: str) -> Path:
        """
        Resolves a requested path and verifies via canonical resolution
        that it does not escape the allowed base directory.

        Args:
            requested_path: The relative or absolute path the AI wants to access.

        Returns:
            Resolved and validated Path object.

        Raises:
            SecurityError: If the path attempts path traversal (e.g. '../../Windows')
        """
        try:
            # 1. Expand environment variables and '~' if present
            expanded_path = os.path.expanduser(os.path.expandvars(requested_path))

            # 2. If not absolute, treat as relative to the allowed workspace
            if not os.path.isabs(expanded_path):
                target_path = self.allowed_base / expanded_path
            else:
                target_path = Path(expanded_path)

            # 3. Resolve the final path (strips ../ and symlinks)
            resolved_target = target_path.resolve()

            # 4. Critical Security Check: does the resolved path start with our allowed_base?
            if not str(resolved_target).startswith(str(self.allowed_base)):
                raise SecurityError(
                    f"Security Violation: requested path '{requested_path}' "
                    f"resolves outside the allowed Workspace ({self.allowed_base})"
                )

            return resolved_target

        except Exception as e:
            if isinstance(e, SecurityError):
                raise
            # Any other path parsing error is blocked by default
            raise SecurityError(f"Error parsing the requested path: {str(e)}")
