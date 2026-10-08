from __future__ import annotations

import base64
import hashlib
import os
import tempfile
import time
import unittest
from pathlib import Path

from aira_phone_files import (
    AiraPhoneFileStore,
    CHUNK_BYTES,
    MAX_FILE_BYTES,
    safe_phone_filename,
)


class AiraPhoneFileStoreTests(unittest.TestCase):
    def test_safe_filename_removes_paths_and_windows_devices(self):
        self.assertEqual(safe_phone_filename("../folder/hello?.txt"), "hello_.txt")
        self.assertEqual(safe_phone_filename("CON.txt"), "_CON.txt")
        self.assertEqual(safe_phone_filename(".."), "received.bin")

    def test_upload_list_and_verified_download_round_trip(self):
        payload = (b"mobile-file-content-" * (CHUNK_BYTES // 20 + 1)) + b"end"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as folder:
            store = AiraPhoneFileStore(folder)
            started = store.upload_begin({
                "name": "report?.txt",
                "size": len(payload),
                "sha256": digest,
            }, "phone-one")
            upload_id = started["upload_id"]
            offset = 0
            while offset < len(payload):
                chunk = payload[offset:offset + CHUNK_BYTES]
                response = store.upload_chunk({
                    "upload_id": upload_id,
                    "offset": offset,
                    "data": base64.b64encode(chunk).decode("ascii"),
                    "chunk_sha256": hashlib.sha256(chunk).hexdigest(),
                }, "phone-one")
                offset = response["offset"]
            finished = store.upload_finish({"upload_id": upload_id}, "phone-one")
            self.assertEqual(finished["sha256"], digest)
            self.assertEqual(finished["name"], "report_.txt")
            self.assertEqual(Path(folder, "report_.txt").read_bytes(), payload)

            listed = store.list_files()
            self.assertEqual(listed["count"], 1)
            file_id = listed["files"][0]["id"]
            received = bytearray()
            offset = 0
            while True:
                response = store.download_chunk({"file_id": file_id, "offset": offset})
                chunk = base64.b64decode(response["data"], validate=True)
                self.assertEqual(
                    hashlib.sha256(chunk).hexdigest(), response["chunk_sha256"]
                )
                received.extend(chunk)
                offset = response["next_offset"]
                if response["eof"]:
                    self.assertEqual(response["sha256"], digest)
                    break
            self.assertEqual(bytes(received), payload)

    def test_upload_rejects_other_device_and_bad_chunk(self):
        payload = b"abc"
        with tempfile.TemporaryDirectory() as folder:
            store = AiraPhoneFileStore(folder)
            started = store.upload_begin({
                "name": "a.bin",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }, "phone-one")
            params = {
                "upload_id": started["upload_id"],
                "offset": 0,
                "data": base64.b64encode(payload).decode("ascii"),
                "chunk_sha256": "0" * 64,
            }
            with self.assertRaises(PermissionError):
                store.upload_chunk(params, "phone-two")
            with self.assertRaises(ValueError):
                store.upload_chunk(params, "phone-one")

    def test_old_orphan_part_file_is_removed_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            orphan = Path(folder, ".aira-upload-orphan.part")
            orphan.write_bytes(b"partial")
            old = time.time() - 7200
            os.utime(orphan, (old, old))
            store = AiraPhoneFileStore(folder)
            store.execute("file_share_list", {}, "phone-one")
            self.assertFalse(orphan.exists())

    def test_upload_limit_is_exactly_five_hundred_megabytes(self):
        with tempfile.TemporaryDirectory() as folder:
            store = AiraPhoneFileStore(folder)
            started = store.upload_begin({
                "name": "large.bin",
                "size": MAX_FILE_BYTES,
                "sha256": "0" * 64,
            }, "phone-one")
            store.upload_cancel({"upload_id": started["upload_id"]}, "phone-one")
            with self.assertRaisesRegex(ValueError, "500 MB"):
                store.upload_begin({
                    "name": "too-large.bin",
                    "size": MAX_FILE_BYTES + 1,
                    "sha256": "0" * 64,
                }, "phone-one")


if __name__ == "__main__":
    unittest.main()
