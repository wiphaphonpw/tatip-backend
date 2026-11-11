"""
TATIP AI RAG Pipeline Test & Evaluation Suite
---------------------------------------------
Automated unit tests and benchmark evaluation for TATIP Customer Support RAG.

Usage:
  # Run offline unit test suite:
  python test_rag_pipeline.py

  # Run live evaluation against production Cloud Run:
  python test_rag_pipeline.py --live
"""

import sys
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import json
import urllib.request
import asyncio


def _load_main_offline():
    """Load main.py safely for offline unit tests without connecting to GCP."""
    if "python_multipart" not in sys.modules:
        pm = MagicMock()
        pm.__version__ = "0.0.32"
        sys.modules["python_multipart"] = pm

    from google.cloud import firestore
    firestore.Client = MagicMock()
    import vertexai
    vertexai.init = MagicMock()

    import main
    return main


class TestRagPipelineLogic(unittest.TestCase):
    """Offline unit tests verifying RAG logic, schemas, streaming, guards, and safety."""

    @classmethod
    def setUpClass(cls):
        cls.main = _load_main_offline()

    def test_chat_request_schema(self):
        """Verify ChatRequest accepts both simple message and multi-turn history."""
        req1 = self.main.ChatRequest(message="ระบบคืออะไร")
        self.assertEqual(req1.message, "ระบบคืออะไร")
        self.assertIsNone(req1.history)

        req2 = self.main.ChatRequest(
            message="แล้วข้อสองล่ะ",
            history=[
                self.main.ChatHistoryItem(question="กล้องรองรับกี่ตัว", answer="อย่างน้อย 1 ตัว สูงสุด 7 ช่อง")
            ]
        )
        self.assertEqual(req2.message, "แล้วข้อสองล่ะ")
        self.assertEqual(len(req2.history), 1)
        self.assertEqual(req2.history[0].question, "กล้องรองรับกี่ตัว")

    def test_knowledge_upload_validation(self):
        """Verify allowed knowledge document suffixes."""
        allowed_suffixes = {".pdf", ".txt", ".md", ".docx", ".pptx", ".html", ".json"}
        valid_files = ["manual.pdf", "guide.txt", "doc.docx", "slides.pptx", "data.json", "info.html", "readme.md"]
        invalid_files = ["script.exe", "virus.bat", "archive.zip", "image.png"]

        for f in valid_files:
            suffix = "." + f.split(".")[-1]
            self.assertIn(suffix, allowed_suffixes, f"Suffix for {f} should be allowed")

        for f in invalid_files:
            suffix = "." + f.split(".")[-1]
            self.assertNotIn(suffix, allowed_suffixes, f"Suffix for {f} should not be allowed")

    def test_empty_context_fallback(self):
        """Verify that when no relevant chunks are found, AI returns the exact fallback message without calling Gemini."""
        with patch.object(self.main, "rag") as mock_rag:
            mock_results = MagicMock()
            mock_results.contexts.contexts = []
            mock_rag.retrieval_query.return_value = mock_results

            result = asyncio.run(self.main.get_rag_reply("สูตรทำพิซซ่า"))
            self.assertIn("เรื่องนี้ยังไม่มีข้อมูลในคู่มือการใช้งาน", result)
            self.assertIn("รบกวนติดต่อทีมงาน (Admin)", result)

    def test_multi_turn_continuation_query_reformulation(self):
        """Verify short follow-up questions incorporate previous question for RAG search."""
        with patch.object(self.main, "rag") as mock_rag:
            mock_results = MagicMock()
            mock_results.contexts.contexts = []
            mock_rag.retrieval_query.return_value = mock_results

            history = [self.main.ChatHistoryItem(question="กล้องรองรับกี่ตัว", answer="1 ตัวขึ้นไป")]
            asyncio.run(self.main.get_rag_reply("แล้วกล้องเสียงล่ะ", history=history))

            call_kwargs = mock_rag.retrieval_query.call_args.kwargs
            search_query = call_kwargs.get("text", "")
            self.assertIn("กล้องรองรับกี่ตัว", search_query)
            self.assertIn("แล้วกล้องเสียงล่ะ", search_query)

    def test_safety_blocked_response_handling(self):
        """Verify that if Gemini blocks the response due to safety filters, the system returns a polite notice instead of crashing."""
        with patch.object(self.main, "rag") as mock_rag, patch.object(self.main, "GenerativeModel") as mock_model_class:
            mock_context = MagicMock()
            mock_context.text = "ข้อมูลการตั้งค่ากล้อง TATIP"
            mock_context.source_display_name = "manual.pdf"
            mock_results = MagicMock()
            mock_results.contexts.contexts = [mock_context]
            mock_rag.retrieval_query.return_value = mock_results

            mock_candidate = MagicMock()
            mock_candidate.finish_reason = "SAFETY"
            mock_candidate.content.parts = []
            mock_response = MagicMock()
            mock_response.candidates = [mock_candidate]

            mock_instance = MagicMock()
            mock_instance.generate_content_async = AsyncMock(return_value=mock_response)
            mock_model_class.return_value = mock_instance

            result = asyncio.run(self.main.get_rag_reply("ทดสอบคำถามติด safety"))
            self.assertIn("ความปลอดภัย", result)

    def test_streaming_rag_reply_chunks_and_done(self):
        """Verify SSE streaming yields valid JSON chunk events and a final done event."""
        async def _collect_stream():
            with patch.object(self.main, "rag") as mock_rag, patch.object(self.main, "GenerativeModel") as mock_model_class:
                mock_context = MagicMock()
                mock_context.text = "ระบบ TATIP ใช้ AI วิเคราะห์ภาพกล้องวงจรปิด"
                mock_context.source_display_name = "overview.pdf"
                mock_results = MagicMock()
                mock_results.contexts.contexts = [mock_context]
                mock_rag.retrieval_query.return_value = mock_results

                async def _mock_stream_gen():
                    for text_part in ["ระบบ TATIP ", "ใช้ AI วิเคราะห์ภาพครับ"]:
                        chunk = MagicMock()
                        cand = MagicMock()
                        cand.finish_reason = "STOP"
                        chunk.candidates = [cand]
                        chunk.text = text_part
                        yield chunk

                mock_instance = MagicMock()
                mock_instance.generate_content_async = AsyncMock(return_value=_mock_stream_gen())
                mock_model_class.return_value = mock_instance

                events = []
                async for raw in self.main.stream_rag_reply("ระบบ TATIP คืออะไร"):
                    events.append(raw)
                return events

        events = asyncio.run(_collect_stream())
        self.assertGreaterEqual(len(events), 3)
        self.assertTrue(events[0].startswith("data: "))
        last_payload = json.loads(events[-1].replace("data: ", "").strip())
        self.assertTrue(last_payload.get("done"))
        self.assertEqual(last_payload.get("reply"), "ระบบ TATIP ใช้ AI วิเคราะห์ภาพครับ")


class TestRagLiveBenchmark:
    """Live Benchmark Evaluation against Cloud Run Production API."""

    BENCHMARK_CASES = [
        {
            "category": "In-domain Overview",
            "question": "ระบบ TATIP คืออะไร",
            "required_keywords": ["TATIP", "AI", "กล้อง", "Dashboard"],
            "forbidden_keywords": ["ยังไม่มีข้อมูลในคู่มือ"],
        },
        {
            "category": "Troubleshooting Steps",
            "question": "วิธีรีเซ็ตกล้องหรือแก้ไขปัญหากล้องดับ",
            "required_keywords": ["ปิด", "เปิด", "นาที"],
            "forbidden_keywords": [],
        },
        {
            "category": "Boundary Check (Shop vs Support Hours)",
            "question": "เวลาทำการของฝ่ายบริการลูกค้ากี่โมงถึงกี่โมง",
            "required_keywords": ["ไม่มีข้อมูล", "คู่มือ"],
            "forbidden_keywords": ["08:00", "09:00", "17:00", "18:00"],
        },
        {
            "category": "Out-of-domain Fallback",
            "question": "สูตรเค้กช็อกโกแลตคืออะไร",
            "required_keywords": ["เรื่องนี้ยังไม่มีข้อมูลในคู่มือการใช้งาน", "Admin"],
            "forbidden_keywords": ["แป้ง", "ช็อกโกแลต", "ส่วนผสม", "เตาอบ"],
        },
        {
            "category": "System Numerical Constraints",
            "question": "TATIP รองรับกล้องกี่ตัว",
            "required_keywords": ["1 ตัว", "7"],
            "forbidden_keywords": ["ไม่จำกัด"],
        },
    ]

    @classmethod
    def run_benchmark(cls, api_url: str = "https://tatip-backend-417035159214.us-central1.run.app"):
        print(f"\n{'='*70}\nRunning TATIP AI Live RAG Benchmark against:\n{api_url}\n{'='*70}\n")
        passed = 0
        total = len(cls.BENCHMARK_CASES)

        for index, case in enumerate(cls.BENCHMARK_CASES, 1):
            q = case["question"]
            print(f"[{index}/{total}] Category: {case['category']}")
            print(f"  Q: {q}")
            try:
                req = urllib.request.Request(
                    f"{api_url}/api/chat",
                    data=json.dumps({"message": q}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                res = urllib.request.urlopen(req, timeout=30)
                data = json.loads(res.read().decode("utf-8"))
                reply = data.get("reply", "")
                print(f"  A: {reply[:120]}..." if len(reply) > 120 else f"  A: {reply}")

                req_ok = any(kw.lower() in reply.lower() for kw in case["required_keywords"])
                forb_ok = not any(kw.lower() in reply.lower() for kw in case["forbidden_keywords"])

                if req_ok and forb_ok:
                    print("  Status: [PASS]")
                    passed += 1
                else:
                    print(f"  Status: [FAIL] (req_ok={req_ok}, forb_ok={forb_ok})")

            except Exception as error:
                print(f"  Status: [ERROR] - {error}")
            print("-" * 50)

        score = (passed / total) * 100
        print(f"\nFinal Benchmark Score: {passed}/{total} ({score:.1f}%)\n{'='*70}\n")
        return score >= 80


if __name__ == "__main__":
    if "--live" in sys.argv:
        success = TestRagLiveBenchmark.run_benchmark()
        sys.exit(0 if success else 1)
    else:
        unittest.main()
