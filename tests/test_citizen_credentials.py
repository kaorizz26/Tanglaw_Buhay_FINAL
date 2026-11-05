"""Citizen credential helpers: random values, keyed hashes and code checking."""

from datetime import timedelta
import re
import unittest
from unittest.mock import patch

from flask import Flask

from services import citizen_credentials as credentials

KEY = "synthetic-test-key-" + "k" * 24
OTHER_KEY = "another-synthetic-key-" + "z" * 24


class CredentialHelperTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["CITIZEN_CREDENTIAL_KEY"] = KEY
        context = self.app.app_context()
        context.push()
        self.addCleanup(context.pop)

    def test_the_key_is_required_and_must_be_long_enough(self):
        for value in (None, "", "short", b"b" * 40, 12345):
            with self.subTest(value=value):
                self.app.config["CITIZEN_CREDENTIAL_KEY"] = value
                with self.assertRaises(RuntimeError):
                    credentials.new_reset_credentials()

    def test_it_never_falls_back_to_the_session_key(self):
        self.app.config["CITIZEN_CREDENTIAL_KEY"] = None
        self.app.config["SECRET_KEY"] = "s" * 64
        with self.assertRaises(RuntimeError):
            credentials.hash_token("activation", "token")

    def test_activation_credentials_have_a_url_safe_token_and_a_six_digit_code(self):
        made = credentials.new_activation_credentials()
        self.assertGreaterEqual(len(made.token), 43)
        self.assertRegex(made.token, r"^[A-Za-z0-9_-]+$")
        self.assertRegex(made.code, r"^[0-9]{6}$")
        self.assertRegex(made.token_hash, r"^[0-9a-f]{64}$")
        self.assertRegex(made.code_hash, r"^[0-9a-f]{64}$")

    def test_codes_keep_leading_zeros(self):
        with patch.object(credentials, "randbelow", return_value=42):
            self.assertEqual(credentials.new_activation_credentials().code, "000042")

    def test_tokens_and_codes_are_unpredictable_across_calls(self):
        batch = [credentials.new_activation_credentials() for _ in range(200)]
        self.assertEqual(len({item.token for item in batch}), 200)
        self.assertEqual(len({item.token_hash for item in batch}), 200)
        # Six-digit codes repeat sometimes; they should still vary widely.
        self.assertGreater(len({item.code for item in batch}), 150)

    def test_stored_hashes_never_contain_the_plain_values(self):
        made = credentials.new_activation_credentials()
        for stored in (made.token_hash, made.code_hash):
            self.assertNotIn(made.token, stored)
            self.assertNotIn(made.code, stored)

    def test_hashes_are_repeatable_but_depend_on_the_key_and_purpose(self):
        first = credentials.hash_token("activation", "same-token")
        self.assertEqual(first, credentials.hash_token("activation", "same-token"))
        self.assertNotEqual(first, credentials.hash_token("password_reset", "same-token"))
        self.assertNotEqual(first, credentials.hash_token("activation", "other-token"))
        self.app.config["CITIZEN_CREDENTIAL_KEY"] = OTHER_KEY
        self.assertNotEqual(first, credentials.hash_token("activation", "same-token"))

    def test_a_reset_token_cannot_be_used_as_an_activation_token(self):
        reset = credentials.new_reset_credentials()
        self.assertNotEqual(reset.token_hash, credentials.hash_token("activation", reset.token))
        self.assertRegex(reset.token_hash, r"^[0-9a-f]{64}$")

    def test_a_code_hash_only_matches_its_own_link(self):
        made = credentials.new_activation_credentials()
        other = credentials.new_activation_credentials()
        self.assertTrue(credentials.activation_code_matches(
            made.code_hash, made.token_hash, made.code))
        self.assertFalse(credentials.activation_code_matches(
            made.code_hash, other.token_hash, made.code))
        wrong = f"{(int(made.code) + 1) % 10 ** 6:06d}"
        self.assertFalse(credentials.activation_code_matches(
            made.code_hash, made.token_hash, wrong))

    def test_code_hash_depends_on_the_secret_key(self):
        made = credentials.new_activation_credentials()
        self.app.config["CITIZEN_CREDENTIAL_KEY"] = OTHER_KEY
        self.assertFalse(credentials.activation_code_matches(
            made.code_hash, made.token_hash, made.code))

    def test_typed_codes_ignore_spaces_and_dashes_but_reject_anything_else(self):
        self.assertEqual(credentials.clean_activation_code("482 913"), "482913")
        self.assertEqual(credentials.clean_activation_code(" 482-913 "), "482913")
        self.assertEqual(credentials.clean_activation_code("000123"), "000123")
        for typed in (None, "", "12345", "1234567", "12345a", "١٢٣٤٥٦", "48 29 1x", 482913):
            with self.subTest(typed=typed):
                self.assertIsNone(credentials.clean_activation_code(typed))
        made = credentials.new_activation_credentials()
        spaced = f"{made.code[:3]} {made.code[3:]}"
        self.assertTrue(credentials.activation_code_matches(
            made.code_hash, made.token_hash, spaced))
        self.assertFalse(credentials.activation_code_matches(
            made.code_hash, made.token_hash, "not a code"))

    def test_lifetimes_and_attempt_limit_match_the_approved_rules(self):
        self.assertEqual(credentials.ACTIVATION_LIFETIME, timedelta(hours=48))
        self.assertEqual(credentials.PASSWORD_RESET_LIFETIME, timedelta(hours=1))
        self.assertEqual(credentials.MAX_CODE_ATTEMPTS, 5)

    def test_returned_values_are_plain_strings(self):
        made = credentials.new_activation_credentials()
        self.assertTrue(all(isinstance(value, str) for value in made))
        self.assertIsNotNone(re.fullmatch(r"[0-9]{6}", made.code))


if __name__ == "__main__":
    unittest.main()
