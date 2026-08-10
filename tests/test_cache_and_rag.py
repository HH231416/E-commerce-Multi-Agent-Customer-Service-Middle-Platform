import importlib
import os
import sys
import types
import unittest
from unittest.mock import patch


os.environ.setdefault("DASHSCOPE_API_KEY", "test-key")


class SemanticCacheTests(unittest.TestCase):
    def setUp(self):
        if "agents.cache_layer" in sys.modules:
            del sys.modules["agents.cache_layer"]
        self.cache_layer = importlib.import_module("agents.cache_layer")

    def _new_cache(self):
        with patch.object(
            self.cache_layer.SemanticCache,
            "_init_redis",
            lambda instance: setattr(instance, "redis", None),
        ):
            return self.cache_layer.SemanticCache()

    def test_store_skips_non_cacheable_replies(self):
        cache = self._new_cache()

        self.assertFalse(
            cache.should_cache_reply(
                query="我的订单到了吗",
                reply="订单当前配送中，请稍后查看最新物流状态。",
            )
        )
        self.assertFalse(
            cache.should_cache_reply(
                query="帮我查订单",
                reply="Error: Tool execution failed or was interrupted.",
            )
        )
        self.assertFalse(
            cache.should_cache_reply(
                query="我要人工",
                reply="正在为您转接人工客服，请稍候。",
                transferred=True,
            )
        )

    def test_stats_include_runtime_counters_without_redis(self):
        cache = self._new_cache()

        result = self._run_async(cache.stats())

        self.assertFalse(result["enabled"])
        self.assertEqual(result["hits"], 0)
        self.assertEqual(result["misses"], 0)
        self.assertEqual(result["stores"], 0)
        self.assertEqual(result["store_skips"], 0)
        self.assertEqual(result["lookup_errors"], 0)
        self.assertEqual(result["store_errors"], 0)
        self.assertEqual(result["threshold"], 0.92)
        self.assertEqual(result["total_entries"], 0)

    def _run_async(self, coroutine):
        import asyncio

        return asyncio.run(coroutine)


class RagFallbackTests(unittest.TestCase):
    def test_query_knowledge_base_returns_fallback_on_retriever_error(self):
        fake_module = types.ModuleType("agents.rag_hybrid")

        class BrokenRetriever:
            def retrieve(self, *_args, **_kwargs):
                raise RuntimeError("vector db unavailable")

        fake_module.get_hybrid_retriever = lambda: BrokenRetriever()

        module_name = "agents.rag_hybrid"
        previous_module = sys.modules.get(module_name)
        sys.modules[module_name] = fake_module
        try:
            if "agents.RAG_tool" in sys.modules:
                del sys.modules["agents.RAG_tool"]
            rag_tool = importlib.import_module("agents.RAG_tool")
            reply = rag_tool.query_knowledge_base("退货规则")
        finally:
            if previous_module is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous_module

        self.assertIn("知识库暂时不可用", reply)


class RagDeduplicationTests(unittest.TestCase):
    def test_deduplicate_uses_document_id_and_content_prefix(self):
        from langchain_core.documents import Document
        from agents.rag_hybrid import _deduplicate_documents

        prefix = "相同的前缀" * 20
        documents = [
            Document(page_content=prefix + "BM25 结果", metadata={"document_id": "doc-a"}),
            Document(page_content=prefix + "FAISS 结果", metadata={"document_id": "doc-a"}),
            Document(page_content=prefix + "另一份文档", metadata={"document_id": "doc-b"}),
        ]

        result = _deduplicate_documents(documents)

        self.assertEqual(2, len(result))
        self.assertEqual(prefix + "BM25 结果", result[0].page_content)
        self.assertEqual("doc-b", result[1].metadata["document_id"])


if __name__ == "__main__":
    unittest.main()
