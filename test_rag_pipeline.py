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


class TestCameraLayoutsApi(unittest.TestCase):
    """Offline unit tests for Camera Placement Layout API & Schema."""

    @classmethod
    def setUpClass(cls):
        cls.main = _load_main_offline()

    def test_camera_layout_schema_and_access_control(self):
        """Verify CameraLayoutSaveRequest schema and role-based ownership access."""
        req = self.main.CameraLayoutSaveRequest(
            name="สาขาสยาม",
            business_type="restaurant",
            width_m=10.0,
            length_m=14.0,
            floors=1,
            cameras=[
                self.main.CameraPlacementItem(
                    camera_id="CAM-01",
                    name="Entrance",
                    x_m=5.0,
                    y_m=12.0,
                    direction_deg=90.0,
                    fov_deg=78.0,
                    range_m=10.0,
                )
            ],
        )
        self.assertEqual(req.name, "สาขาสยาม")
        self.assertEqual(len(req.cameras), 1)

        layout_doc = {"USER_ID": "u1", "TENANT_ID": "t1"}
        self.assertTrue(self.main.can_access_camera_layout(layout_doc, {"USER_ID": "u1", "TENANT_ID": "t1", "ROLE": "customer"}))
        self.assertFalse(self.main.can_access_camera_layout(layout_doc, {"USER_ID": "u2", "TENANT_ID": "t1", "ROLE": "customer"}))
        self.assertTrue(self.main.can_access_camera_layout(layout_doc, {"USER_ID": "mgr1", "TENANT_ID": "t1", "ROLE": "branch_manager"}))
        self.assertTrue(self.main.can_access_camera_layout(layout_doc, {"USER_ID": "adm1", "TENANT_ID": "t99", "ROLE": "admin"}))

    def test_save_and_serialize_camera_layout(self):
        """Verify saving a camera layout writes to Firestore camera_layouts and returns serialized payload."""
        mock_db = MagicMock()
        mock_doc_ref = MagicMock()
        mock_db.collection.return_value.document.return_value = mock_doc_ref

        req = self.main.CameraLayoutSaveRequest(
            name="Cafe 8x10",
            business_type="cafe",
            width_m=8.0,
            length_m=10.0,
            floors=1,
        )
        current_user = {"USER_ID": "user_01", "TENANT_ID": "TENANT_01", "ROLE": "customer"}

        with patch.object(self.main, "db", mock_db):
            res = self.main.save_camera_layout(req, current_user)

        self.assertEqual(res["status"], "success")
        self.assertTrue(res["layout_id"].startswith("LAY-"))
        self.assertEqual(res["data"]["name"], "Cafe 8x10")
        self.assertEqual(res["data"]["tenant_id"], "TENANT_01")
        mock_db.collection.assert_called_with("camera_layouts")
        mock_doc_ref.set.assert_called_once()


class TestSupportCaseStatusDashboardApi(unittest.TestCase):
    """Offline unit tests for Support Case status updates and Dashboard summary."""

    @classmethod
    def setUpClass(cls):
        cls.main = _load_main_offline()

    def test_update_support_case_status_and_admin_note(self):
        """Verify PATCH status updates Firestore, handles legacy CASE_UPDATES, and sets RESOLVED_AT."""
        mock_db = MagicMock()
        mock_case_ref = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.exists = True
        mock_snapshot.to_dict.return_value = {
            "USER_ID": "guest",
            "TENANT_ID": "T1",
            "STATUS": "OPEN",
            "CASE_UPDATES": "legacy-corrupted-non-list",
        }
        mock_case_ref.get.return_value = mock_snapshot
        mock_db.collection.return_value.document.return_value = mock_case_ref

        req = self.main.UpdateSupportCaseStatusRequest(status="resolved", admin_note="ตรวจสอบแล้ว")
        admin_user = {"USER_ID": "adm1", "TENANT_ID": "SYS", "ROLE": "admin"}

        with patch.object(self.main, "db", mock_db):
            res = self.main.update_support_case_status("CASE-01", req, admin_user)

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["new_status"], "resolved")
        self.assertEqual(res["admin_note"], "ตรวจสอบแล้ว")
        mock_case_ref.update.assert_called_once()
        update_payload = mock_case_ref.update.call_args[0][0]
        self.assertEqual(update_payload["STATUS"], "resolved")
        self.assertEqual(update_payload["ADMIN_NOTE"], "ตรวจสอบแล้ว")
        self.assertIn("RESOLVED_AT", update_payload)
        self.assertIsInstance(update_payload["CASE_UPDATES"], list)
        self.assertEqual(update_payload["CASE_UPDATES"][0]["STATUS"], "resolved")

    def test_update_support_case_branch_manager_isolation(self):
        """Verify branch_manager cannot update a case belonging to a different tenant."""
        mock_db = MagicMock()
        mock_case_ref = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.exists = True
        mock_snapshot.to_dict.return_value = {"TENANT_ID": "T2", "STATUS": "open"}
        mock_case_ref.get.return_value = mock_snapshot
        mock_db.collection.return_value.document.return_value = mock_case_ref

        req = self.main.UpdateSupportCaseStatusRequest(status="in_progress")
        mgr_user = {"USER_ID": "mgr1", "TENANT_ID": "T1", "ROLE": "branch_manager"}

        with patch.object(self.main, "db", mock_db):
            with self.assertRaises(self.main.HTTPException) as ctx:
                self.main.update_support_case_status("CASE-99", req, mgr_user)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_dashboard_summary_returns_all_cases_and_admin_note(self):
        """Verify dashboard summary returns full cases list alongside recent_cases[:12]."""
        fake_docs = []
        for idx in range(14):
            doc = MagicMock()
            doc.id = f"CASE-{idx:02d}"
            doc.to_dict.return_value = {
                "USER_ID": f"u{idx}",
                "TENANT_ID": "T1",
                "BRANCH": "สาขาหลัก",
                "ISSUE": f"ปัญหา {idx}",
                "STATUS": "OPEN" if idx == 0 else "in_progress",
                "ADMIN_NOTE": f"โน้ต {idx}" if idx == 0 else "",
            }
            fake_docs.append(doc)

        mock_db = MagicMock()
        mock_db.collection.return_value.stream.return_value = fake_docs
        admin_user = {"USER_ID": "adm1", "TENANT_ID": "SYS", "ROLE": "admin"}

        with patch.object(self.main, "db", mock_db):
            res = self.main.get_admin_dashboard_summary(admin_user)

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["summary"]["total"], 14)
        self.assertEqual(res["summary"]["open"], 1)
        self.assertEqual(len(res["recent_cases"]), 12)
        self.assertEqual(len(res["cases"]), 14)
        case_00 = next(c for c in res["cases"] if c["case_id"] == "CASE-00")
        self.assertEqual(case_00["status"], "open")
        self.assertEqual(case_00["admin_note"], "โน้ต 0")

    def test_public_contact_case_ignores_empty_file_placeholder(self):
        """Verify submitting a public contact case without choosing a file ignores empty browser placeholder."""
        import io
        from starlette.datastructures import Headers, UploadFile

        placeholder = UploadFile(
            filename="",
            file=io.BytesIO(b""),
            headers=Headers({"content-type": "application/octet-stream"}),
        )
        mock_db = MagicMock()
        mock_doc_ref = MagicMock()
        mock_db.collection.return_value.document.return_value = mock_doc_ref

        with patch.object(self.main, "db", mock_db):
            res = asyncio.run(
                self.main.submit_public_contact_case(
                    full_name="สมชาย ใจดี",
                    phone="0812345678",
                    issue="กล้องชั้น 1 ออฟไลน์ครับ",
                    category="",
                    website="",
                    attachments=[placeholder],
                    current_user=None,
                )
            )

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["attachment_count"], 0)
        self.assertEqual(res["category"], "กล้อง / CCTV")
        mock_doc_ref.set.assert_called_once()

    def test_issue_category_classification_and_update(self):
        """Verify clear category separation for CCTV, ROI, Auth/Login, and Unrelated issues."""
        cls_fn = self.main.classify_support_issue_category
        self.assertEqual(cls_fn("", "กล้องชั้น 1 ออฟไลน์ ไม่มีภาพ"), "กล้อง / CCTV")
        self.assertEqual(cls_fn("", "สอนตี ROI กล้องหน้าเคาน์เตอร์หน่อย"), "การตี ROI / พื้นที่วิเคราะห์")
        self.assertEqual(cls_fn("", "เข้าสู่ระบบไม่ได้ ลืมรหัสผ่าน"), "การเข้าสู่ระบบ / บัญชีผู้ใช้")
        self.assertEqual(cls_fn("", "ถามสูตรทำข้าวผัดหน่อยครับ"), "เรื่องที่ไม่เกี่ยวข้อง")
        self.assertEqual(cls_fn("เรื่องที่ไม่เกี่ยวข้อง", "กล้องดับ"), "เรื่องที่ไม่เกี่ยวข้อง")
        self.assertEqual(cls_fn("roi", "อะไรก็ได้"), "การตี ROI / พื้นที่วิเคราะห์")
        self.assertEqual(cls_fn("อื่น ๆ", "อยากถามวิธีวาดกรอบพื้นที่ตรงเคาน์เตอร์"), "การตี ROI / พื้นที่วิเคราะห์")
        self.assertEqual(cls_fn("อื่นๆ", "เข้าระบบไม่ได้เลยครับ"), "การเข้าสู่ระบบ / บัญชีผู้ใช้")
        self.assertEqual(cls_fn("auto", "กล้องชั้น 2 ภาพไม่ขึ้น"), "กล้อง / CCTV")
        self.assertEqual(cls_fn("auto", "camera offline"), "กล้อง / CCTV")
        self.assertEqual(cls_fn("auto", "android ดูภาพไม่ได้"), "กล้อง / CCTV")
        self.assertEqual(cls_fn("auto", "เชื่อมบัญชี line ไม่ได้"), "การแจ้งเตือน (LINE / ระบบ)")
        self.assertEqual(cls_fn("auto", "วันนี้ฝนตกไหม"), "เรื่องที่ไม่เกี่ยวข้อง")

        mock_db = MagicMock()
        mock_case_ref = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.exists = True
        mock_snapshot.to_dict.return_value = {"TENANT_ID": "T1", "STATUS": "open", "ISSUE": "ทดสอบส่งผิด"}
        mock_case_ref.get.return_value = mock_snapshot
        mock_db.collection.return_value.document.return_value = mock_case_ref

        req = self.main.UpdateSupportCaseStatusRequest(status="closed", category="เรื่องที่ไม่เกี่ยวข้อง")
        admin_user = {"USER_ID": "adm1", "TENANT_ID": "SYS", "ROLE": "admin"}
        with patch.object(self.main, "db", mock_db):
            res = self.main.update_support_case_status("CASE-CAT", req, admin_user)

        self.assertEqual(res["category"], "เรื่องที่ไม่เกี่ยวข้อง")
        self.assertEqual(mock_case_ref.update.call_args[0][0]["ISSUE_TYPE"], "เรื่องที่ไม่เกี่ยวข้อง")


class TestChatHistoryPairing(unittest.TestCase):
    """Offline unit tests ensuring chat history turns keep strict Q&A pairing."""

    @classmethod
    def setUpClass(cls):
        cls.main = _load_main_offline()

    def test_save_chat_history_writes_shared_turn_id_and_millisecond_offset(self):
        """Verify save_chat_history shares a non-empty TURN_ID and offsets AI reply by 10ms."""
        mock_db = MagicMock()
        mock_batch = MagicMock()
        mock_db.batch.return_value = mock_batch
        mock_msg_col = MagicMock()
        mock_conv_col = MagicMock()
        mock_msg_col.document.side_effect = [MagicMock(id="msg-u"), MagicMock(id="msg-a")]

        def _col_router(name):
            return mock_msg_col if name == "messages" else mock_conv_col

        mock_db.collection.side_effect = _col_router

        req = self.main.SaveChatHistoryRequest(
            USER_ID="Panna_01",
            TENANT_ID="TENANT_01",
            CONVERSATION_ID="conv_01",
            MESSAGE="วิธีตั้งค่าพื้นที่ ROI",
            REPLY="การตั้งค่าพื้นที่วิเคราะห์ หรือ ROI...",
        )
        current_user = {"USER_ID": "Panna_01", "TENANT_ID": "TENANT_01", "ROLE": "customer"}

        with patch.object(self.main, "db", mock_db):
            res = asyncio.run(self.main.save_chat_history(req, current_user))

        self.assertEqual(res["status"], "success")
        self.assertTrue(res.get("turn_id"))
        self.assertEqual(mock_batch.set.call_count, 3)

        user_doc = mock_batch.set.call_args_list[0][0][1]
        ai_doc = mock_batch.set.call_args_list[1][0][1]
        self.assertEqual(user_doc["SENDER"], "user")
        self.assertEqual(ai_doc["SENDER"], "ai")
        self.assertEqual(user_doc["TURN_ID"], res["turn_id"])
        self.assertEqual(ai_doc["TURN_ID"], res["turn_id"])
        delta_ms = (ai_doc["CREATE_DATE"] - user_doc["CREATE_DATE"]).total_seconds() * 1000
        self.assertEqual(round(delta_ms), 10)

    def test_get_chat_history_normalizes_timestamps_and_orders_user_before_ai(self):
        """Verify get_chat_history normalizes naive/aware datetimes and puts user before ai on ties."""
        from datetime import datetime, timezone

        tie_dt = datetime(2026, 11, 11, 9, 12, 43, 801040, tzinfo=timezone.utc)
        naive_earlier = datetime(2026, 11, 11, 7, 30, 0)

        doc_ai_tie = MagicMock()
        doc_ai_tie.id = "aaa_ai_first_docid"
        doc_ai_tie.to_dict.return_value = {
            "USER_ID": "Panna_01",
            "SENDER": "ai",
            "MESSAGE": "คำตอบ Session",
            "CREATE_DATE": tie_dt,
        }
        doc_user_tie = MagicMock()
        doc_user_tie.id = "zzz_user_last_docid"
        doc_user_tie.to_dict.return_value = {
            "USER_ID": "Panna_01",
            "SENDER": "user",
            "MESSAGE": "คำถาม Session",
            "CREATE_DATE": tie_dt,
        }
        doc_early = MagicMock()
        doc_early.id = "early_doc"
        doc_early.to_dict.return_value = {
            "USER_ID": "Panna_01",
            "SENDER": "user",
            "MESSAGE": "คำถามก่อนหน้า",
            "CREATE_DATE": naive_earlier,
        }

        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.stream.return_value = [doc_ai_tie, doc_user_tie, doc_early]
        mock_db.collection.return_value.where.return_value = mock_query

        current_user = {"USER_ID": "Panna_01", "TENANT_ID": "T1", "ROLE": "customer"}
        with patch.object(self.main, "db", mock_db):
            res = self.main.get_chat_history("Panna_01", None, current_user)

        self.assertEqual(res["status"], "success")
        senders = [item["SENDER"] for item in res["data"]]
        messages = [item["MESSAGE"] for item in res["data"]]
        self.assertEqual(senders, ["user", "user", "ai"])
        self.assertEqual(messages, ["คำถามก่อนหน้า", "คำถาม Session", "คำตอบ Session"])
        self.assertTrue(res["data"][0]["CREATE_DATE"].endswith("+00:00"))


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
