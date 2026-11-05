"""Upload checks and private storage for Digital Dropbox, using temporary folders only."""

import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask

from services import document_storage as storage
from services.document_storage import DocumentStorageError, DocumentUploadError

PDF = b"%PDF-1.7\nsynthetic document\n%%EOF"
JPG = b"\xff\xd8\xff\xe0synthetic image"
PNG = b"\x89PNG\r\n\x1a\nsynthetic image"
MEGABYTE = 1024 * 1024


def check(name, data, max_bytes=storage.DEFAULT_MAX_BYTES):
    return storage.validate_upload(name, io.BytesIO(data), max_bytes)


class UploadValidationTests(unittest.TestCase):
    def reason(self, name, data, **keywords):
        """Return the code of the error an upload is refused with."""
        with self.assertRaises(DocumentUploadError) as caught:
            check(name, data, **keywords)
        return caught.exception.code

    def test_each_accepted_type_passes_and_gets_a_fixed_content_type(self):
        cases = (("form.pdf", PDF, "pdf", "application/pdf"),
                 ("photo.JPG", JPG, "jpg", "image/jpeg"),
                 ("photo.jpeg", JPG, "jpg", "image/jpeg"),
                 ("scan.png", PNG, "png", "image/png"))
        for name, data, extension, content_type in cases:
            with self.subTest(name=name):
                upload = check(name, data)
                self.assertEqual((upload.extension, upload.content_type), (extension, content_type))
                self.assertEqual((upload.data, upload.size_bytes), (data, len(data)))
                self.assertRegex(upload.sha256, r"^[0-9a-f]{64}$")

    def test_a_file_must_have_an_accepted_extension(self):
        for name in ("notes.txt", "photo.gif", "letter.docx", "run.exe", "noextension",
                     "evil.pdf.exe", "page.html", "archive.pdf.zip", ".pdf.", ""):
            with self.subTest(name=name):
                self.assertIn(self.reason(name, PDF), ("wrong_type", "no_file"))

    def test_the_file_contents_must_match_the_extension(self):
        for name, data in (("fake.pdf", b"MZ\x90\x00 program"), ("fake.pdf", PNG),
                           ("fake.jpg", PDF), ("fake.png", JPG), ("fake.png", b"<html></html>")):
            with self.subTest(name=name):
                self.assertEqual(self.reason(name, data), "not_matching")

    def test_empty_and_missing_files_are_refused(self):
        self.assertEqual(self.reason("empty.pdf", b""), "empty")
        with self.assertRaises(DocumentUploadError) as caught:
            storage.validate_upload("form.pdf", None)
        self.assertEqual(caught.exception.code, "no_file")

    def test_five_megabytes_is_accepted_and_one_byte_more_is_not(self):
        limit = 5 * MEGABYTE
        self.assertEqual(check("big.pdf", PDF + b"x" * (limit - len(PDF))).size_bytes, limit)
        self.assertEqual(self.reason("big.pdf", PDF + b"x" * (limit - len(PDF) + 1)), "too_large")

    def test_a_huge_file_is_never_read_past_the_limit(self):
        class Endless(io.RawIOBase):
            read_total = 0

            def read(self, size=-1):
                self.read_total += size
                return b"%PDF-" + b"x" * (size - 5)

        stream = Endless()
        with self.assertRaises(DocumentUploadError):
            storage.validate_upload("huge.pdf", stream, 1000)
        self.assertEqual(stream.read_total, 1001)

    def test_every_message_is_plain_english(self):
        for code, message in storage.UPLOAD_MESSAGES.items():
            with self.subTest(code=code):
                self.assertTrue(message.endswith("."))
                for word in ("signature", "mime", "bytes", "exception", "null"):
                    self.assertNotIn(word, message.lower())

    def test_the_display_name_loses_folders_control_characters_and_extra_length(self):
        cases = (("..\\..\\secret\\id card.pdf", "id card.pdf"),
                 ("/etc/passwd.pdf", "passwd.pdf"),
                 ("bad\x00name\n.pdf", "badname.pdf"),
                 ("  spaced   name .pdf", "spaced name .pdf"),
                 ("tälä ID.pdf", "tälä ID.pdf"),
                 ("...", "document.pdf"), ("", "document.pdf"))
        for given, expected in cases:
            with self.subTest(given=given):
                self.assertEqual(storage.clean_display_name(given, "pdf"), expected)
        long_name = storage.clean_display_name("a" * 300 + ".pdf", "pdf")
        self.assertEqual(len(long_name), storage.MAX_DISPLAY_NAME_LENGTH)
        self.assertTrue(long_name.endswith(".pdf"))


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.app = Flask(__name__)
        self.app.config["DOCUMENT_STORAGE_DIR"] = str(self.root)
        context = self.app.app_context()
        context.push()
        self.addCleanup(context.pop)

    def stored_names(self):
        return sorted(item.name for item in self.root.iterdir())


class PrivateStorageTests(StorageTestCase):
    def test_a_stored_file_is_saved_under_a_random_key_and_read_back_unchanged(self):
        upload = check("id card.png", PNG + b"\r\nline endings kept\r\n")
        with storage.stored_upload(upload) as key:
            pass
        self.assertRegex(key, r"^[0-9a-f]{32}\.png$")
        self.assertEqual(self.stored_names(), [key])
        self.assertNotIn("id card", key)
        self.assertEqual(storage.stored_file_path(key).read_bytes(), upload.data)

    def test_keys_are_unique_and_unguessable(self):
        keys = {storage.new_storage_key("pdf") for _ in range(200)}
        self.assertEqual(len(keys), 200)

    def test_a_failed_block_removes_the_file_so_nothing_is_orphaned(self):
        with self.assertRaises(RuntimeError):
            with storage.stored_upload(check("form.pdf", PDF)):
                self.assertEqual(len(self.stored_names()), 1)
                raise RuntimeError("database insert failed")
        self.assertEqual(self.stored_names(), [])

    def test_only_our_own_random_names_can_be_turned_into_paths(self):
        bad_keys = ("../outside.pdf", "..\\outside.pdf", "/etc/passwd", "a" * 32,
                    "a" * 32 + ".exe", "A" * 32 + ".pdf", "a" * 31 + ".pdf",
                    "a" * 32 + ".pdf/../x", "sub/" + "a" * 32 + ".pdf", "", None, 12)
        for key in bad_keys:
            with self.subTest(key=key):
                for action in (storage.stored_file_path, storage.delete_stored_file):
                    with self.assertRaises(DocumentStorageError):
                        action(key)

    def test_a_symbolic_link_in_the_folder_is_never_followed(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        secret = Path(outside.name) / "secret.pdf"
        secret.write_bytes(PDF)
        key = storage.new_storage_key("pdf")
        try:
            (self.root / key).symlink_to(secret)
        except (OSError, NotImplementedError):
            self.skipTest("This account may not create symbolic links.")
        with self.assertRaises(DocumentStorageError) as caught:
            storage.stored_file_path(key)
        self.assertEqual(str(caught.exception), "missing")
        # Saving never writes through a planted link either.
        with patch.object(storage, "new_storage_key", return_value=key):
            with self.assertRaises(FileExistsError):
                with storage.stored_upload(check("form.pdf", PDF + b"new")):
                    pass
        self.assertEqual(secret.read_bytes(), PDF)

    def test_a_missing_file_is_reported_and_deleting_it_is_harmless(self):
        key = storage.new_storage_key("pdf")
        with self.assertRaises(DocumentStorageError) as caught:
            storage.stored_file_path(key)
        self.assertEqual(str(caught.exception), "missing")
        storage.delete_stored_file(key)

    def test_delete_removes_the_file(self):
        with storage.stored_upload(check("form.pdf", PDF)) as key:
            pass
        storage.delete_stored_file(key)
        self.assertEqual(self.stored_names(), [])

    def test_an_existing_file_is_never_overwritten(self):
        with patch.object(storage, "new_storage_key", return_value="a" * 32 + ".pdf"):
            with storage.stored_upload(check("one.pdf", PDF)):
                pass
            with self.assertRaises(FileExistsError):
                with storage.stored_upload(check("two.pdf", PDF + b"other")):
                    pass
        self.assertEqual((self.root / ("a" * 32 + ".pdf")).read_bytes(), PDF)


class StorageLocationTests(unittest.TestCase):
    def folder_error(self, configured):
        app = Flask(__name__)
        app.config["DOCUMENT_STORAGE_DIR"] = configured
        with app.app_context():
            with self.assertRaises(DocumentStorageError) as caught:
                storage.storage_root()
        self.assertEqual(str(caught.exception), "not_configured")

    def test_unset_relative_and_missing_folders_are_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            # "C:folder" and "\folder" are not complete Windows paths, so they are refused too.
            for configured in (None, "", "   ", "relative/folder", "C:folder", "\\folder",
                               str(Path(folder) / "missing")):
                with self.subTest(configured=configured):
                    self.folder_error(configured)

    def test_folders_inside_the_project_or_static_are_refused(self):
        for configured in (storage.PROJECT_ROOT, storage.PROJECT_ROOT / "static",
                           storage.PROJECT_ROOT / "templates"):
            with self.subTest(configured=configured):
                self.folder_error(str(configured))

    def test_without_a_folder_nothing_is_written(self):
        app = Flask(__name__)
        with app.app_context():
            with self.assertRaises(DocumentStorageError):
                with storage.stored_upload(check("form.pdf", PDF)):
                    self.fail("The block must not run without private storage.")


if __name__ == "__main__":
    unittest.main()
