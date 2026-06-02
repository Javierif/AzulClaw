from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class SemanticGuardrailTest(unittest.TestCase):
    def test_core_semantic_layers_do_not_use_lexical_marker_heuristics(self) -> None:
        targets = [
            REPO_ROOT / "azul_backend" / "azul_brain" / "conversation.py",
            REPO_ROOT / "azul_backend" / "azul_brain" / "runtime",
            REPO_ROOT / "azul_backend" / "azul_brain" / "cortex" / "fast",
            REPO_ROOT / "azul_backend" / "azul_brain" / "channels" / "servicebus_worker.py",
            REPO_ROOT / "skills" / "official" / "telegram" / "src" / "relay_function" / "function_app.py",
        ]
        forbidden = [
            "_CONFIRMATION_MARKERS",
            "_FUTURE_CONFIRMATION_MARKERS",
            "SIMPLE_EXACT",
            "COMPLEX_MARKERS",
            "_TRIVIAL_QUERY",
            "_local_pending_route",
            "_CONTEXT_OVERFLOW_HINTS",
            "_looks_like_incomplete_promise",
            "_looks_like_tool_failure",
            "for marker in (",
            "for token in (",
            "confirm = {",
            "cancel = {",
            "visual_hints = (",
        ]

        offenders: list[str] = []
        for target in targets:
            files = [target] if target.is_file() else sorted(target.rglob("*.py"))
            for path in files:
                text = path.read_text(encoding="utf-8")
                for pattern in forbidden:
                    if pattern in text:
                        offenders.append(f"{path.relative_to(REPO_ROOT)} -> {pattern}")

        self.assertEqual(offenders, [], "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
