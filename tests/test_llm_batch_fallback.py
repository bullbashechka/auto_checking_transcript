"""Тесты fallback-логики check_batch без обращения к сети."""
from __future__ import annotations

import asyncio
import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

HAS_OPENAI = importlib.util.find_spec("openai") is not None

if HAS_OPENAI:
    import httpx
    from openai import BadRequestError

    from src.config import Settings
    from src.llm import BatchItem, LLMClient


def _stub_settings() -> "Settings":
    return Settings(
        telegram_token="x",
        openai_api_key="x",
        allowed_user_ids=frozenset(),
        allow_any=True,
        openai_model="gpt-5.6-luna",
        openai_reasoning_effort="low",
        llm_concurrency=1,
    )


def _make_items(n: int) -> list["BatchItem"]:
    return [
        BatchItem(id=i, contractor=f"c{i}", date="01.01.2026", time="09:00", content=f"text{i}")
        for i in range(n)
    ]


@unittest.skipUnless(HAS_OPENAI, "openai not installed — run `pip install -r requirements.txt`")
class TestCheckBatchFallback(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch("src.llm.AsyncOpenAI")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = LLMClient(_stub_settings())

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_happy_path_one_call(self) -> None:
        items = _make_items(3)
        mock = AsyncMock(return_value='[{"id":0},{"id":1},{"id":2}]')
        with patch.object(self.client, "_call_with_retries", mock):
            results = self._run(self.client.check_batch(items))
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertTrue(r.is_empty)
        self.assertEqual(mock.await_count, 1)

    def test_falls_back_on_invalid_response(self) -> None:
        items = _make_items(3)
        # batch вернул мусор → fallback на 3 single-вызова, каждый ок
        responses = iter([
            "garbage",
            '[{"id":0,"Исправленное_Содержание":"a"}]',
            '[{"id":0,"Предупреждение":"w"}]',
            '[{"id":0}]',
        ])
        mock = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        with patch.object(self.client, "_call_with_retries", mock):
            results = self._run(self.client.check_batch(items))
        self.assertEqual(mock.await_count, 4)  # 1 batch + 3 singles
        self.assertEqual(results[0].corrected, "a")
        self.assertEqual(results[1].warning, "w")
        self.assertTrue(results[2].is_empty)

    def test_falls_back_on_exception(self) -> None:
        items = _make_items(2)
        responses = iter([
            '[{"id":0,"Исправленное_Содержание":"a"}]',
            '[{"id":0}]',
        ])

        def side_effect(*args, **kwargs):
            if mock.await_count == 1:
                raise RuntimeError("boom")
            return next(responses)

        mock = AsyncMock(side_effect=side_effect)
        with patch.object(self.client, "_call_with_retries", mock):
            results = self._run(self.client.check_batch(items))
        self.assertEqual(mock.await_count, 3)  # 1 batch (raised) + 2 singles
        self.assertEqual(results[0].corrected, "a")
        self.assertTrue(results[1].is_empty)

    def test_single_fallback_empty_on_invalid_no_infinite_recursion(self) -> None:
        items = _make_items(1)
        mock = AsyncMock(return_value="garbage")
        with patch.object(self.client, "_call_with_retries", mock):
            results = self._run(self.client.check_batch(items))
        self.assertEqual(mock.await_count, 1)  # без рекурсии
        self.assertTrue(results[0].is_empty)

    def test_empty_items_no_calls(self) -> None:
        mock = AsyncMock()
        with patch.object(self.client, "_call_with_retries", mock):
            results = self._run(self.client.check_batch([]))
        self.assertEqual(results, [])
        self.assertEqual(mock.await_count, 0)

    def test_openai_request_uses_luna_and_strict_json(self) -> None:
        create = AsyncMock(return_value=SimpleNamespace(output_text='{"results": []}'))
        self.client._client.responses.create = create

        raw = self._run(self.client._call_with_retries("[]"))

        self.assertEqual(raw, '{"results": []}')
        kwargs = create.await_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-5.6-luna")
        self.assertEqual(kwargs["reasoning"], {"effort": "low"})
        self.assertFalse(kwargs["store"])
        self.assertEqual(kwargs["text"]["format"]["type"], "json_schema")
        self.assertTrue(kwargs["text"]["format"]["strict"])
        schema = kwargs["text"]["format"]["schema"]
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["properties"]["results"]["type"], "array")

    def test_bad_request_is_not_retried_or_split_into_singles(self) -> None:
        response = httpx.Response(
            400,
            request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
        )
        error = BadRequestError("bad schema", response=response, body={})
        create = AsyncMock(side_effect=error)
        self.client._client.responses.create = create

        with self.assertRaises(BadRequestError):
            self._run(self.client._call_with_retries("[]"))

        self.assertEqual(create.await_count, 1)

        mock = AsyncMock(side_effect=error)

        with patch.object(self.client, "_call_with_retries", mock):
            with self.assertRaises(BadRequestError):
                self._run(self.client.check_batch(_make_items(3)))

        self.assertEqual(mock.await_count, 1)


if __name__ == "__main__":
    unittest.main()
