from __future__ import annotations

import unittest

from pydantic import BaseModel

from azul_backend.azul_brain.cortex.kernel_setup import _Result
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


if __name__ == "__main__":
    unittest.main()
