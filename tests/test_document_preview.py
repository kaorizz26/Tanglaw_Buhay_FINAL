"""Staff document preview (Digital Dropbox): the preview page and the inline file.

Route tests mock the services, as the Task 3D download tests do, and use synthetic
files in a temporary folder. The database is never touched.
"""

from pathlib import Path
import tempfile
import unittest

from services.citizen_accounts import CitizenAccountError

import test_document_reviews as reviews
from test_citizen_account_routes import FORM

PDF = reviews.PDF
JPEG = b"\xff\xd8\xff\xe0synthetic image"
PNG = b"\x89PNG\r\n\x1a\nsynthetic image"
PREVIEW = "/document-requests/8/files/9/preview"
VIEW = "/document-requests/8/files/9/view"
EARLIER_PREVIEW = "/document-requests/8/files/4/preview"


class PreviewRouteCase(reviews.ReviewRouteCase):
    def stored(self, data=PDF, content_type="application/pdf", name="second-scan.pdf"):
        """Make a synthetic stored file and make the staff check return it."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "0123456789abcdef0123456789abcdef.bin"
        path.write_bytes(data)
        stored = {"path": path, "display_name": name, "content_type": content_type}
        self.mocks["open_file_for_staff"].return_value = stored
        return stored

    def page_text(self, path=PREVIEW):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)


class PreviewPageTests(PreviewRouteCase):
    def test_a_pdf_shows_in_an_embedded_viewer_with_new_tab_download_and_back(self):
        self.stored()
        page = self.page_text()
        self.assertIn(f'<iframe class="document-preview-pdf" src="{VIEW}"', page)
        self.assertIn("Open in New Tab", page)
        self.assertRegex(page, r'href="/document-requests/8/files/9/view" target="_blank" rel="noopener"')
        self.assertIn('href="/document-requests/8/files/9"', page)       # Download is kept
        self.assertIn('href="/document-requests/8"', page)               # Back
        self.assertNotIn('class="document-preview-image"', page)
        self.assertEqual(self.mocks["open_file_for_staff"].call_args.args, (5, 8, 9))

    def test_jpg_and_png_files_show_directly_without_a_viewer_or_new_tab_button(self):
        for content_type, data in (("image/jpeg", JPEG), ("image/png", PNG)):
            with self.subTest(content_type=content_type):
                self.stored(data, content_type)
                page = self.page_text()
                self.assertIn(f'<img class="document-preview-image" src="{VIEW}"', page)
                self.assertIn('alt="Preview of second-scan.pdf"', page)  # the name staff see in the list
                self.assertNotIn("<iframe", page)
                self.assertNotIn("Open in New Tab", page)
                self.assertIn('href="/document-requests/8/files/9"', page)

    def test_the_latest_file_and_an_earlier_version_are_labelled(self):
        self.stored()
        self.assertIn("2 (latest file)", self.page_text(PREVIEW))
        self.stored(name="first-scan.pdf")
        earlier = self.page_text(EARLIER_PREVIEW)
        self.assertIn("1 (earlier version)", earlier)
        self.assertIn('src="/document-requests/8/files/4/view"', earlier)
        self.assertEqual(self.mocks["open_file_for_staff"].call_args.args, (5, 8, 4))

    def test_previews_work_for_finished_requests_like_downloads_do(self):
        self.request_page = reviews.review_page("accepted")
        self.stored()
        self.assertIn("<iframe", self.page_text())

    def test_the_page_never_shows_a_storage_path_key_or_static_address(self):
        stored = self.stored()
        page = self.page_text()
        self.assertNotIn(str(stored["path"]), page)
        self.assertNotIn(stored["path"].name, page)
        self.assertNotIn("/static/uploads", page)
        self.assertNotIn("storage_key", page)

    def test_a_file_that_is_not_part_of_the_request_page_is_not_found(self):
        self.stored()
        self.assertEqual(self.client.get("/document-requests/8/files/77/preview").status_code, 404)

    def test_request_and_detail_pages_offer_preview_next_to_download(self):
        self.stored()
        detail = self.page_text(self.DETAIL)
        for file_id in (9, 4):
            self.assertIn(f'href="/document-requests/8/files/{file_id}/preview"', detail)
            self.assertIn(f'href="/document-requests/8/files/{file_id}"', detail)
        accept = self.page_text(self.ACCEPT)
        self.assertIn('href="/document-requests/8/files/9/preview"', accept)
        self.assertIn('href="/document-requests/8/files/9"', accept)


class InlineFileTests(PreviewRouteCase):
    def test_the_file_is_sent_inline_with_protective_headers(self):
        for content_type, data in (("application/pdf", PDF), ("image/jpeg", JPEG), ("image/png", PNG)):
            with self.subTest(content_type=content_type):
                self.stored(data, content_type)
                response = self.client.get(VIEW)
                self.addCleanup(response.close)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data, data)
                self.assertEqual(response.mimetype, content_type)
                self.assertTrue(response.headers["Content-Disposition"].startswith("inline"))
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(response.headers["X-Frame-Options"], "SAMEORIGIN")
                self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
                policy = response.headers["Content-Security-Policy"]
                self.assertIn("default-src 'none'", policy)
                self.assertIn("frame-ancestors 'self'", policy)

    def test_download_is_still_an_attachment_without_the_inline_policy(self):
        self.stored()
        response = self.client.get(self.DOWNLOAD)
        self.addCleanup(response.close)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertNotIn("Content-Security-Policy", response.headers)

    DOWNLOAD = "/document-requests/8/files/9"


class PreviewAuthorizationTests(PreviewRouteCase):
    PATHS = (PREVIEW, VIEW)

    def test_anonymous_visitors_are_sent_to_sign_in(self):
        with self.client.session_transaction() as session:
            session.clear()
        for path in self.PATHS:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual((response.status_code, response.location), (302, "/login"))
        self.mocks["open_file_for_staff"].assert_not_called()

    def test_administrators_are_refused(self):
        self.sign_in("system_administrator")
        for path in self.PATHS:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)
        self.mocks["open_file_for_staff"].assert_not_called()

    def test_a_missing_or_foreign_file_is_not_found_and_failures_are_safe(self):
        for code, status in (("request_missing", 404), ("unauthorized", 403), ("unavailable", 503)):
            self.mocks["open_file_for_staff"].side_effect = CitizenAccountError(code)
            for path in self.PATHS:
                with self.subTest(code=code, path=path):
                    self.assertEqual(self.client.get(path).status_code, status)

    def test_the_pages_cannot_be_used_with_a_form_post(self):
        for path in self.PATHS:
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, data=FORM).status_code, 405)


if __name__ == "__main__":
    unittest.main()
