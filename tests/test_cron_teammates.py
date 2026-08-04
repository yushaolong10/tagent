import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("MODEL_ID", "deepseek-v4-flash")

import cron
import teammates
import toolpool


class CronAcknowledgementTests(unittest.TestCase):
    def setUp(self):
        cron.scheduled_jobs.clear()
        cron.cron_queue.clear()
        cron._last_fired.clear()
        cron._in_flight.clear()

    def tearDown(self):
        self.setUp()

    def test_one_shot_is_removed_only_after_success(self):
        with TemporaryDirectory() as directory:
            durable_path = Path(directory) / "jobs.json"
            job = cron.CronJob("cron_test", "* * * * *", "work", False, True)
            cron.scheduled_jobs[job.id] = job
            cron._in_flight.add(job.id)
            with patch.object(cron, "DURABLE_PATH", durable_path):
                cron.finish_fired_jobs([job], successful=True)
            self.assertNotIn(job.id, cron.scheduled_jobs)
            self.assertNotIn(job.id, cron._in_flight)
            self.assertEqual(durable_path.read_text().strip(), "[]")

    def test_failed_one_shot_remains_available_for_retry(self):
        job = cron.CronJob("cron_test", "* * * * *", "work", False, False)
        cron.scheduled_jobs[job.id] = job
        cron._in_flight.add(job.id)
        cron._last_fired[job.id] = "2026-08-04 12:00"
        cron.finish_fired_jobs([job], successful=False)
        self.assertIn(job.id, cron.scheduled_jobs)
        self.assertNotIn(job.id, cron._in_flight)
        self.assertNotIn(job.id, cron._last_fired)


class TeammateProtocolTests(unittest.TestCase):
    def test_recent_window_keeps_tool_pair(self):
        messages = [
            {"role": "user", "content": "goal"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "read_file"}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": "result"}]},
            {"role": "assistant", "content": "done"},
        ]
        self.assertEqual(teammates.recent_messages(messages, limit=2),
                         messages[1:])

    def test_plan_request_and_response_round_trip(self):
        with TemporaryDirectory() as directory:
            teammates.pending_requests.clear()
            with patch.object(teammates, "MAILBOX_DIR", Path(directory)):
                submitted = teammates._teammate_submit_plan("worker", "plan")
                request_id = submitted.split("(", 1)[1].rstrip(")")
                lead_messages = teammates.consume_lead_inbox(route_protocol=True)
                review = toolpool.run_review_plan(request_id, True, "approved")
                worker_messages = teammates.BUS.read_inbox("worker")
        self.assertEqual(lead_messages[0]["type"], "plan_approval_request")
        self.assertEqual(review, "Plan approved")
        self.assertEqual(worker_messages[0]["type"], "plan_approval_response")
        self.assertTrue(worker_messages[0]["metadata"]["approve"])


if __name__ == "__main__":
    unittest.main()
