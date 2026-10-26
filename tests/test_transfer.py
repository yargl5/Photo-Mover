import hashlib
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PhotoMover import PhoneMover


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.a = Path(self.temp.name) / "A"
        self.b = Path(self.temp.name) / "B"
        self.a.mkdir()
        self.b.mkdir()
        self.worker = PhoneMover.__new__(PhoneMover)
        self.worker.events = queue.Queue()
        self.worker.cancelled = False

    def test_local_nested_empty_and_unicode_files(self):
        fixtures = {"фото.bin": bytes(range(256)) * 256, "sub/file.txt": b"example", "empty": b""}
        for name, data in fixtures.items():
            p = self.a / name
            p.parent.mkdir(exist_ok=True)
            p.write_bytes(data)
        self.worker._transfer(self.a, self.b)
        for name, data in fixtures.items():
            self.assertFalse((self.a / name).exists())
            self.assertEqual((self.b / name).read_bytes(), data)

    def test_existing_destination_is_not_overwritten(self):
        (self.a / "file").write_bytes(b"new")
        (self.b / "file").write_bytes(b"old")
        self.worker._transfer(self.a, self.b)
        self.assertEqual((self.a / "file").read_bytes(), b"new")
        self.assertEqual((self.b / "file").read_bytes(), b"old")

    def test_identical_local_duplicate_removes_only_source(self):
        (self.a / "file").write_bytes(b"same-content")
        (self.b / "file").write_bytes(b"same-content")
        self.worker._transfer(self.a, self.b)
        self.assertFalse((self.a / "file").exists())
        self.assertEqual((self.b / "file").read_bytes(), b"same-content")

    def test_existing_duplicate_with_different_size_is_preserved(self):
        (self.a / "file").write_bytes(b"long content")
        (self.b / "file").write_bytes(b"short")
        self.worker._transfer(self.a, self.b)
        self.assertTrue((self.a / "file").exists())
        self.assertEqual((self.b / "file").read_bytes(), b"short")

    def test_identical_mtp_duplicate_removes_source(self):
        p = self.a / "file"
        p.write_bytes(b"content")
        digest = hashlib.sha256(b"content").hexdigest()
        with patch("mtp_backend.Session") as session:
            session.return_value.__enter__.return_value.request.return_value = {"sha256": digest, "already_present": True, "upload_seconds": 0}
            self.worker._transfer(self.a, ("device-id", "\\Storage"))
        self.assertFalse(p.exists())
        self.assertTrue(any("Копия совпадает" in str(event) for event in list(self.worker.events.queue)))

    def test_source_changed_during_verification_is_preserved(self):
        p = self.a / "file"
        p.write_bytes(b"before")
        def changed(*args, **kwargs):
            p.write_bytes(b"after!")
            return {"sha256": hashlib.sha256(b"before").hexdigest(), "already_present": True}
        with patch("mtp_backend.Session") as session:
            session.return_value.__enter__.return_value.request.side_effect = changed
            self.worker._transfer(self.a, ("device-id", "\\Storage"))
        self.assertEqual(p.read_bytes(), b"after!")

    def test_mtp_success_deletes_source_after_hash_check(self):
        p = self.a / "file"
        p.write_bytes(b"content")
        digest = hashlib.sha256(b"content").hexdigest()
        with patch("mtp_backend.Session") as session:
            session.return_value.__enter__.return_value.request.return_value = {"sha256": digest}
            self.worker._transfer(self.a, ("device-id", "\\Storage"))
        self.assertFalse(p.exists())

    def test_mtp_disconnect_keeps_source(self):
        p = self.a / "file"
        p.write_bytes(b"content")
        with patch("mtp_backend.Session") as session:
            session.return_value.__enter__.return_value.request.side_effect = OSError("USB disconnected")
            self.worker._transfer(self.a, ("device-id", "\\Storage"))
        self.assertEqual(p.read_bytes(), b"content")

    def test_mtp_hash_mismatch_keeps_source(self):
        p = self.a / "file"
        p.write_bytes(b"content")
        with patch("mtp_backend.Session") as session:
            session.return_value.__enter__.return_value.request.return_value = {"sha256": "0" * 64}
            self.worker._transfer(self.a, ("device-id", "\\Storage"))
        self.assertTrue(p.exists())

    def test_cancellation_keeps_source(self):
        p = self.a / "file"
        p.write_bytes(b"content")
        self.worker.cancelled = True
        self.worker._transfer(self.a, self.b)
        self.assertTrue(p.exists())


if __name__ == "__main__":
    unittest.main()
