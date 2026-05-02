from __future__ import annotations

import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from azul_backend.azul_brain.main_launcher import apply_cors_headers, cors_middleware


class DesktopCorsTests(unittest.IsolatedAsyncioTestCase):
    def test_allowed_origin_gets_cors_headers(self) -> None:
        request = make_mocked_request("GET", "/api/desktop/backend/status", headers={"Origin": "http://localhost:1420"})
        response = web.Response()

        apply_cors_headers(request, response)

        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "http://localhost:1420")

    def test_disallowed_origin_does_not_get_cors_headers(self) -> None:
        request = make_mocked_request("GET", "/api/desktop/backend/status", headers={"Origin": "https://example.com"})
        response = web.Response()

        apply_cors_headers(request, response)

        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    async def test_preflight_from_disallowed_origin_is_rejected(self) -> None:
        request = make_mocked_request("OPTIONS", "/api/desktop/azure/connect", headers={"Origin": "https://example.com"})

        response = await cors_middleware(request, lambda _: web.Response())

        self.assertEqual(response.status, 403)

    async def test_simple_request_from_disallowed_origin_is_rejected(self) -> None:
        request = make_mocked_request("POST", "/api/desktop/azure/connect", headers={"Origin": "https://example.com"})

        response = await cors_middleware(request, lambda _: web.Response(status=200))

        self.assertEqual(response.status, 403)

    def test_env_can_add_allowed_origin(self) -> None:
        request = make_mocked_request("GET", "/api/desktop/backend/status", headers={"Origin": "http://localhost:5173"})
        response = web.Response()

        with patch.dict("os.environ", {"AZUL_CORS_ALLOWED_ORIGINS": "http://localhost:5173"}, clear=False):
            apply_cors_headers(request, response)

        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "http://localhost:5173")


if __name__ == "__main__":
    unittest.main()
