"""Public information pages remain separate from staff and CBMS access."""

import unittest
from unittest.mock import patch

from app import create_app
from services.public_programs import PUBLIC_PROGRAMS


class PublicPagesTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            "TESTING": True, "DATABASE_URL": None, "SECRET_KEY": None,
        })
        self.client = self.app.test_client()

    def test_anonymous_pages_need_no_staff_session_or_database(self):
        with patch("services.auth.get_connection") as connection:
            landing = self.client.get("/public")
            self.assertEqual(landing.status_code, 200)
            self.assertIn("Tanglaw-Buhay", landing.text)
            for slug in PUBLIC_PROGRAMS:
                with self.subTest(slug=slug):
                    page = self.client.get(f"/public/programs/{slug}")
                    self.assertEqual(page.status_code, 200)
                    self.assertIn(PUBLIC_PROGRAMS[slug]["name"], page.text)
                    self.assertIn('href="/public#programs"', page.text)
            connection.assert_not_called()

    def test_unknown_program_and_fixture_boundary(self):
        self.assertEqual(self.client.get("/public/programs/unknown").status_code, 404)
        with patch.dict(self.app.extensions["development_workspace"],
                        source_kind="actual_cbms"):
            self.assertEqual(self.client.get("/public").status_code, 403)
            self.assertEqual(self.client.get("/public/programs/4ps").status_code, 403)

    def test_staff_routes_remain_protected(self):
        self.assertEqual(self.client.get("/").status_code, 503)
        self.app.config["SECRET_KEY"] = "synthetic-test-key-" * 3
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/login")

    def test_public_assets_are_served(self):
        paths = [
            "css/public.css", "js/public.js",
            "assets/logos/logo_A.png", "assets/logos/logo_B.png",
            "assets/images/header_backdrop.jpg",
            *(details["icon"] for details in PUBLIC_PROGRAMS.values()),
        ]
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(f"/static/{path}")
                self.assertEqual(response.status_code, 200)
                response.close()


if __name__ == "__main__":
    unittest.main()
