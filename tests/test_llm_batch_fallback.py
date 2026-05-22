"""Тесты fallback-логики check_batch без обращения к сети."""
from __future__ import annotations

import asyncio
import importlib.util
import unittest
from unittest.mock import AsyncMock, patch

HAS_GENAI = importlib.util.find_spec("google.genai") is not None

if HAS_GENAI:
    from src.config import Settings
    from src.llm import BatchItem, LLMClient


def _stub_settings() -> "Settings":
    return Settings(
        telegram_token="x",
        gemini_api_key="x",
        allowed_user_ids=frozenset(),
        allow_any=True,
        gemini_model="gemini-2.5-flash",
        llm_concurrency=1,
    )


def _make_items(n: int) -> list["BatchItem"]:
    return [
        BatchItem(id=i, contractor=f"c{i}", date="01.01.2026", time="09:00", content=f"text{i}")
        for i in range(n)
    ]


@unittest.skipUnless(HAS_GENAI, "google-genai not installed — run `pip install -r requirements.txt`")
class TestCheckBatchFallback(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch("src.llm.genai.Client")
        patcher.start()
        self.addCleanup(patcher.stop)
        # Отключаем cache-логику — в этих тестах нас интересует только batch/fallback
        cache_patcher = patch.object(LLMClient, "_try_create_cache_sync")
        cache_patcher.start()
        self.addCleanup(cache_patcher.stop)
        ensure_patcher = patch.object(LLMClient, "_ensure_cache", new=AsyncMock(return_value=None))
        ensure_patcher.start()
        self.addCleanup(ensure_patcher.stop)
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


if __name__ == "__main__":
    unittest.main()
