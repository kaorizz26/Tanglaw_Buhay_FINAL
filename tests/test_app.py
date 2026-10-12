"""Verify the application can respond without a database connection."""

import unittest

from app import create_app


class AppTestCase(unittest.TestCase):
    def test_test_route(self):
        app = create_app()
        app.config.update(TESTING=True, DATABASE_URL=None)
        with app.test_client() as client:
            response = client.get("/test")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {"status": "ok", "application": "Tanglaw-Buhay"},
        )


if __name__ == "__main__":
    unittest.main()
