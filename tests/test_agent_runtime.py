from __future__ import annotations

import unittest

from pydantic import BaseModel

from azul_backend.azul_brain.cortex.kernel_setup import _Result, _compose_instructions
from azul_backend.azul_brain.soul.system_prompt import AZULCLAW_SYSTEM_PROMPT
from azul_backend.azul_brain.runtime.agent_runtime import _serialize_runtime_text


class StructuredValue(BaseModel):
    route: str


class ResultLike:
    def __init__(self, value):
        self.value = value


class RuntimeSerializationTests(unittest.TestCase):
    def test_serialize_runtime_text_handles_structured_values(self) -> None:
        result = ResultLike(StructuredValue(route="none"))

        self.assertEqual(_serialize_runtime_text(result), '{"route":"none"}')

    def test_serialize_runtime_text_handles_dicts(self) -> None:
        result = ResultLike({"route": "none"})

        self.assertEqual(_serialize_runtime_text(result), '{"route": "none"}')

    def test_result_wrapper_stringifies_values(self) -> None:
        result = _Result({"route": "none"})

        self.assertEqual(result.text, '{"route": "none"}')
        self.assertEqual(str(result), '{"route": "none"}')

    def test_task_instructions_are_composed_with_base_prompt(self) -> None:
        instructions = _compose_instructions("Use no tools.")

        self.assertTrue(instructions.startswith(AZULCLAW_SYSTEM_PROMPT))
        self.assertIn("Task-specific instructions:\nUse no tools.", instructions)

    def test_empty_task_instructions_keep_base_prompt(self) -> None:
        self.assertEqual(_compose_instructions(""), AZULCLAW_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
