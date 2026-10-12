"""Success and failure notification routing for the ai-runner worker."""

from pathlib import Path
from types import ModuleType
import unittest
from unittest.mock import Mock


WORKER = Path(__file__).parents[2] / "roles/ai_runner/templates/ai-job-worker.py.j2"


def _load_worker():
    import os

    os.environ["NTFY_URL"] = "https://notify.invalid/ai-jobs"
    os.environ["NTFY_SUCCESS_URL"] = "https://notify.invalid/status"
    module = ModuleType("ai_job_worker_under_test")
    exec(compile(WORKER.read_text(), str(WORKER), "exec"), module.__dict__)
    return module


class WorkerNotificationRouteTests(unittest.TestCase):
    def setUp(self):
        self.env = unittest.mock.patch.dict(
            "os.environ",
            {
                "NTFY_URL": "https://notify.invalid/ai-jobs",
                "NTFY_SUCCESS_URL": "https://notify.invalid/status",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_success_notification_uses_status_topic(self):
        worker = _load_worker()
        sent = []

        class Response:
            def read(self):
                return b""

        worker.claim = Mock()
        worker.run_job = Mock(return_value=(True, "completed"))
        worker.comment = Mock()
        worker.mark_done = Mock()
        with unittest.mock.patch.object(
            worker.urllib.request,
            "urlopen",
            lambda req, timeout: sent.append(req) or Response(),
        ):
            worker.process({"id": 8, "title": "job"}, 4)

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].full_url, "https://notify.invalid/status")
        self.assertEqual(sent[0].get_header("Priority"), "default")
        self.assertEqual(worker.mark_done.call_count, 1)

    def test_failure_notification_keeps_existing_topic_and_high_priority(self):
        worker = _load_worker()
        sent = []

        class Response:
            def read(self):
                return b""

        worker.claim = Mock()
        worker.run_job = Mock(return_value=(False, "failed"))
        worker.comment = Mock()
        worker.mark_done = Mock()
        with unittest.mock.patch.object(
            worker.urllib.request,
            "urlopen",
            lambda req, timeout: sent.append(req) or Response(),
        ):
            worker.process({"id": 9, "title": "job"}, 4)

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].full_url, "https://notify.invalid/ai-jobs")
        self.assertEqual(sent[0].get_header("Priority"), "high")
        self.assertEqual(worker.mark_done.call_count, 1)

    def test_ntfy_helper_accepts_explicit_url_and_skips_empty_url(self):
        worker = _load_worker()
        sent = []

        class Response:
            def read(self):
                return b""

        with unittest.mock.patch.object(
            worker.urllib.request,
            "urlopen",
            lambda req, timeout: sent.append(req) or Response(),
        ):
            worker.ntfy("title", "message", url="https://notify.invalid/explicit")
            worker.ntfy("title", "message", url="")

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].full_url, "https://notify.invalid/explicit")


if __name__ == "__main__":
    unittest.main()
