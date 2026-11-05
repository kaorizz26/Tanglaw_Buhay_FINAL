"""Email delivery tests. SMTP is always mocked; no email is ever sent."""

from contextlib import redirect_stdout
import io
import json
import logging
import socket
import smtplib
import ssl
import sys
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask

from scripts import check_email_settings as check_script
from services import email_delivery as delivery
from services.assessment_request_logging import PortalTokenFilter

SENDER = "Tanglaw-Buhay <sender@example.org>"
PASSWORD = "synthetic-smtp-password"
TOKEN = "synthetic-token-abc_123-XYZ"
RECIPIENT = "citizen@example.com"


def base_config(**changes):
    settings = {
        "SMTP_HOST": "smtp.example.org", "SMTP_PORT": "587", "SMTP_SECURITY": "starttls",
        "SMTP_USERNAME": "sender@example.org", "SMTP_PASSWORD": PASSWORD,
        "SMTP_FROM": SENDER, "SMTP_ALLOWED_RECIPIENTS": "",
        "PUBLIC_BASE_URL": "https://portal.example.org",
    }
    settings.update(changes)
    return settings


class DeliveryTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(base_config())
        context = self.app.app_context()
        context.push()
        self.addCleanup(context.pop)
        self.server = MagicMock()
        self.server.__enter__.return_value = self.server
        smtp = patch.object(delivery.smtplib, "SMTP", return_value=self.server)
        smtp_ssl = patch.object(delivery.smtplib, "SMTP_SSL", return_value=self.server)
        self.smtp, self.smtp_ssl = smtp.start(), smtp_ssl.start()
        self.addCleanup(smtp.stop)
        self.addCleanup(smtp_ssl.stop)
        self.block_real_network()

    def block_real_network(self):
        """Fail loudly if any code path tries to open a real connection."""
        guard = patch("socket.create_connection",
                      side_effect=AssertionError("Tests must not use the network"))
        guard.start()
        self.addCleanup(guard.stop)

    def configure(self, **changes):
        self.app.config.update(base_config(**changes))

    def error_code(self, function, *arguments):
        with self.assertRaises(delivery.EmailDeliveryError) as caught:
            function(*arguments)
        return caught.exception.code


class SettingsTests(DeliveryTestCase):
    def test_valid_settings_load_with_defaults(self):
        self.configure(SMTP_PORT="", SMTP_SECURITY="")
        settings = delivery.load_email_settings()
        self.assertEqual((settings.host, settings.port, settings.security),
                         ("smtp.example.org", 587, "starttls"))

    def test_unusable_settings_are_reported_as_not_configured(self):
        for label, change in (
            ("no host", {"SMTP_HOST": ""}), ("port text", {"SMTP_PORT": "abc"}),
            ("port zero", {"SMTP_PORT": "0"}), ("port too big", {"SMTP_PORT": "70000"}),
            ("bad security", {"SMTP_SECURITY": "none"}), ("no sender", {"SMTP_FROM": ""}),
            ("bad sender", {"SMTP_FROM": "not an address"}),
            ("username without password", {"SMTP_PASSWORD": ""}),
            ("password without username", {"SMTP_USERNAME": ""}),
        ):
            with self.subTest(label):
                self.configure(**change)
                self.assertEqual(self.error_code(delivery.send_email, RECIPIENT, "S", "B"),
                                 "not_configured")
        self.smtp.assert_not_called()

    def test_a_server_without_sign_in_is_allowed(self):
        self.configure(SMTP_USERNAME="", SMTP_PASSWORD="")
        delivery.send_email(RECIPIENT, "Subject", "Body")
        self.server.login.assert_not_called()
        self.server.send_message.assert_called_once()


class RecipientTests(DeliveryTestCase):
    def test_invalid_addresses_never_reach_the_server(self):
        for address in ("", "   ", None, "no-at-sign", "a@b", "two@@example.com",
                        "a b@example.com", "a@example.com,b@example.com",
                        "victim@example.com\nBcc: other@example.com", "x" * 250 + "@example.com"):
            with self.subTest(address=address):
                self.assertEqual(self.error_code(delivery.send_email, address, "S", "B"),
                                 "invalid_recipient")
        self.smtp.assert_not_called()

    def test_the_allowed_list_limits_who_can_receive_email(self):
        self.configure(SMTP_ALLOWED_RECIPIENTS=" Tester@Example.com , other@example.com ")
        delivery.send_email("tester@example.com", "S", "B")
        delivery.send_email("OTHER@example.com", "S", "B")
        self.assertEqual(self.server.send_message.call_count, 2)
        self.server.reset_mock()
        self.smtp.reset_mock()
        self.assertEqual(self.error_code(delivery.send_email, RECIPIENT, "S", "B"),
                         "recipient_not_allowed")
        self.smtp.assert_not_called()

    def test_without_an_allowed_list_any_valid_address_is_accepted(self):
        delivery.send_email(RECIPIENT, "S", "B")
        self.server.send_message.assert_called_once()


class SendTests(DeliveryTestCase):
    def test_starttls_flow_signs_in_and_sends_one_plain_text_message(self):
        delivery.send_email("Citizen@Example.com", "Subject line", "Body text")
        self.smtp.assert_called_once_with("smtp.example.org", 587, timeout=10)
        self.smtp_ssl.assert_not_called()
        names = [call[0] for call in self.server.method_calls]
        self.assertEqual([n for n in names if n in ("ehlo", "starttls", "login", "send_message")],
                         ["ehlo", "starttls", "ehlo", "login", "send_message"])
        context = self.server.starttls.call_args.kwargs["context"]
        self.assertIsInstance(context, ssl.SSLContext)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.server.login.assert_called_once_with("sender@example.org", PASSWORD)
        message = self.server.send_message.call_args.args[0]
        self.assertEqual((message["From"], message["To"], message["Subject"]),
                         (SENDER, "citizen@example.com", "Subject line"))
        self.assertIsNone(message["Bcc"])
        self.assertEqual(message.get_content_type(), "text/plain")
        self.assertEqual(message.get_content().strip(), "Body text")
        self.assertNotIn(PASSWORD, message.as_string())

    def test_ssl_flow_uses_an_encrypted_connection_from_the_start(self):
        self.configure(SMTP_SECURITY="SSL", SMTP_PORT="465")
        delivery.send_email(RECIPIENT, "S", "B")
        self.smtp.assert_not_called()
        self.assertEqual(self.smtp_ssl.call_args.args, ("smtp.example.org", 465))
        self.assertEqual(self.smtp_ssl.call_args.kwargs["timeout"], 10)
        self.assertIsInstance(self.smtp_ssl.call_args.kwargs["context"], ssl.SSLContext)
        self.server.starttls.assert_not_called()
        self.server.login.assert_called_once()

    def test_debug_output_is_never_enabled(self):
        delivery.send_email(RECIPIENT, "S", "B")
        self.server.set_debuglevel.assert_not_called()

    def test_send_returns_nothing_and_logs_nothing(self):
        with self.assertNoLogs(level=logging.DEBUG):
            self.assertIsNone(delivery.send_email(RECIPIENT, "S", f"link {TOKEN}"))


class FailureTests(DeliveryTestCase):
    FAILURES = (
        ("authentication_failed", smtplib.SMTPAuthenticationError(535, b"bad credentials")),
        ("rejected", smtplib.SMTPRecipientsRefused({RECIPIENT: (550, b"no such user")})),
        ("rejected", smtplib.SMTPSenderRefused(553, b"sender refused", "sender@example.org")),
        ("rejected", smtplib.SMTPDataError(554, b"message refused")),
        ("connection_failed", smtplib.SMTPConnectError(421, b"busy")),
        ("connection_failed", smtplib.SMTPServerDisconnected("closed")),
        ("connection_failed", socket.timeout("timed out")),
        ("connection_failed", TimeoutError("timed out")),
        ("connection_failed", ConnectionRefusedError("refused")),
        ("connection_failed", socket.gaierror("no such host")),
        ("connection_failed", ssl.SSLError("certificate problem")),
        ("send_failed", smtplib.SMTPException("something else")),
        ("send_failed", smtplib.SMTPNotSupportedError("no starttls")),
    )

    def test_each_failure_maps_to_a_fixed_code_and_message(self):
        for expected, failure in self.FAILURES:
            with self.subTest(expected=expected, failure=type(failure).__name__):
                self.server.send_message.side_effect = failure
                code = self.error_code(delivery.send_email, RECIPIENT, "S", "B")
                self.assertEqual(code, expected)

    def test_failures_can_occur_while_connecting_and_signing_in(self):
        self.smtp.side_effect = socket.timeout("timed out")
        self.assertEqual(self.error_code(delivery.send_email, RECIPIENT, "S", "B"),
                         "connection_failed")
        self.smtp.side_effect = None
        self.server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"nope")
        self.assertEqual(self.error_code(delivery.send_email, RECIPIENT, "S", "B"),
                         "authentication_failed")
        self.server.login.side_effect = None
        self.server.starttls.side_effect = smtplib.SMTPNotSupportedError("no tls")
        self.assertEqual(self.error_code(delivery.send_email, RECIPIENT, "S", "B"),
                         "send_failed")

    def test_errors_never_contain_secrets_addresses_or_original_details(self):
        self.server.login.side_effect = smtplib.SMTPAuthenticationError(
            535, f"{PASSWORD} rejected for {RECIPIENT} {TOKEN}".encode())
        with self.assertNoLogs(level=logging.DEBUG):
            with self.assertRaises(delivery.EmailDeliveryError) as caught:
                delivery.send_email(RECIPIENT, "S", f"open {TOKEN}")
        error = caught.exception
        for value in (PASSWORD, RECIPIENT, TOKEN, "535"):
            self.assertNotIn(value, str(error))
            self.assertNotIn(value, repr(error))
        # The original exception is dropped, so tracebacks cannot print it either.
        self.assertIsNone(error.__cause__)
        self.assertTrue(error.__suppress_context__)
        self.assertEqual(str(error), delivery.ERROR_MESSAGES["authentication_failed"])

    def test_every_error_code_has_a_message(self):
        for code, text in delivery.ERROR_MESSAGES.items():
            self.assertEqual(str(delivery.EmailDeliveryError(code)), text)


class LinkTests(DeliveryTestCase):
    def test_links_use_the_configured_base_url(self):
        self.configure(PUBLIC_BASE_URL="https://portal.example.org/")
        self.assertEqual(delivery.build_activation_link(TOKEN),
                         f"https://portal.example.org/portal/activate/{TOKEN}")
        self.assertEqual(delivery.build_password_reset_link(TOKEN),
                         f"https://portal.example.org/portal/reset/{TOKEN}")

    def test_links_ignore_the_request_host_header(self):
        with self.app.test_request_context(
            "/", headers={"Host": "evil.test", "X-Forwarded-Host": "evil.test"},
        ):
            link = delivery.build_activation_link(TOKEN)
        self.assertTrue(link.startswith("https://portal.example.org/"))
        self.assertNotIn("evil", link)

    def test_plain_http_is_only_allowed_for_local_testing(self):
        for url in ("http://127.0.0.1:5000", "http://localhost:5000", "http://[::1]:5000"):
            with self.subTest(url=url):
                self.configure(PUBLIC_BASE_URL=url)
                self.assertTrue(delivery.build_activation_link(TOKEN).startswith(url))
        for url in ("http://portal.example.org", "", "portal.example.org", "ftp://portal.example.org",
                    "https://", "https://portal.example.org?x=1", "https://portal.example.org/#a",
                    "https://user:pass@portal.example.org"):
            with self.subTest(url=url):
                self.configure(PUBLIC_BASE_URL=url)
                self.assertEqual(self.error_code(delivery.build_activation_link, TOKEN),
                                 "not_configured")

    def test_token_characters_are_quoted(self):
        self.assertTrue(delivery.build_activation_link("a/b?c#d e").endswith("a%2Fb%3Fc%23d%20e"))


class MessageTests(DeliveryTestCase):
    def sent_message(self):
        return self.server.send_message.call_args.args[0]

    def test_activation_email_has_the_link_but_never_the_code(self):
        delivery.send_activation_email(RECIPIENT, TOKEN)
        message = self.sent_message()
        body = message.get_content()
        self.assertEqual(message["Subject"], "Activate your Tanglaw-Buhay account")
        self.assertIn(f"https://portal.example.org/portal/activate/{TOKEN}", body)
        self.assertIn("48 hours", body)
        self.assertIn("activation code that LGU staff gave you separately", body)
        self.assertNotRegex(body, r"\b[0-9]{6}\b")

    def test_reset_email_has_a_one_hour_single_use_link(self):
        delivery.send_password_reset_email(RECIPIENT, TOKEN)
        message = self.sent_message()
        body = message.get_content()
        self.assertEqual(message["Subject"], "Reset your Tanglaw-Buhay password")
        self.assertIn(f"https://portal.example.org/portal/reset/{TOKEN}", body)
        self.assertIn("1 hour", body)
        self.assertNotIn("1 hours", body)
        self.assertIn("once", body)

    def test_emails_contain_no_survey_assessment_or_subsidy_information(self):
        delivery.send_activation_email(RECIPIENT, TOKEN)
        delivery.send_password_reset_email(RECIPIENT, TOKEN)
        forbidden = ("household", "assessment", "cbms", "subsidy", "4ps", "slp", "spisc",
                     "tupad", "mpi", "score", "rank", "poverty", "eligib", "document")
        for call in self.server.send_message.call_args_list:
            text = (call.args[0]["Subject"] + call.args[0].get_content()).lower()
            for word in forbidden:
                self.assertNotIn(word, text)

    def test_an_unconfigured_site_address_stops_the_email_before_connecting(self):
        self.configure(PUBLIC_BASE_URL="")
        self.assertEqual(self.error_code(delivery.send_activation_email, RECIPIENT, TOKEN),
                         "not_configured")
        self.smtp.assert_not_called()

    def test_sending_email_does_not_log_the_token(self):
        with self.assertNoLogs(level=logging.DEBUG):
            delivery.send_activation_email(RECIPIENT, TOKEN)
            delivery.send_password_reset_email(RECIPIENT, TOKEN)


class AccessLogMaskingTests(unittest.TestCase):
    def mask(self, text):
        record = logging.LogRecord("werkzeug", logging.INFO, "", 0, text, (), None)
        PortalTokenFilter().filter(record)
        return record.getMessage()

    def test_activation_and_reset_tokens_are_hidden_but_method_and_status_remain(self):
        line = f'127.0.0.1 - - [06/Nov/2026] "GET /portal/activate/{TOKEN} HTTP/1.1" 200 -'
        self.assertEqual(self.mask(line),
                         '127.0.0.1 - - [06/Nov/2026] "GET /portal/activate/[protected] HTTP/1.1" 200 -')
        reset = self.mask(f'"POST /portal/reset/{TOKEN}?next=x HTTP/1.1" 302 -')
        self.assertNotIn(TOKEN, reset)
        self.assertIn("POST /portal/reset/[protected]", reset)
        self.assertIn("302", reset)

    def test_other_requests_are_left_alone(self):
        for line in ('"GET /portal/login HTTP/1.1" 200 -', '"GET /public HTTP/1.1" 200 -',
                     '"GET /assessments/abc HTTP/1.1" 200 -'):
            self.assertEqual(self.mask(line), line)

    def test_the_filter_is_installed_when_the_app_is_created(self):
        from app import create_app
        create_app({"TESTING": True})
        filters = logging.getLogger("werkzeug").filters
        self.assertEqual(sum(isinstance(item, PortalTokenFilter) for item in filters), 1)


class CheckScriptTests(unittest.TestCase):
    def run_script(self, *arguments, **config):
        output = io.StringIO()
        app = Flask(__name__)
        app.config.update(base_config(**config))
        with patch.object(check_script, "build_app", return_value=app), \
                patch.object(check_script.Config, "SMTP_ALLOWED_RECIPIENTS",
                             app.config["SMTP_ALLOWED_RECIPIENTS"]), \
                redirect_stdout(output):
            code = check_script.main(list(arguments))
        return code, json.loads(output.getvalue())

    def setUp(self):
        smtp = patch.object(delivery.smtplib, "SMTP")
        self.smtp = smtp.start()
        self.addCleanup(smtp.stop)
        smtp_ssl = patch.object(delivery.smtplib, "SMTP_SSL")
        self.smtp_ssl = smtp_ssl.start()
        self.addCleanup(smtp_ssl.stop)
        guard = patch("socket.create_connection",
                      side_effect=AssertionError("Tests must not use the network"))
        guard.start()
        self.addCleanup(guard.stop)

    def test_default_run_only_checks_settings_and_sends_nothing(self):
        code, result = self.run_script()
        self.assertEqual(code, 0)
        self.assertEqual((result["ok"], result["sent"]), (True, False))
        self.assertEqual(result["settings"]["smtp_host"], "smtp.example.org")
        self.assertEqual(result["settings"]["sender_domain"], "example.org")
        self.smtp.assert_not_called()
        self.smtp_ssl.assert_not_called()

    def test_sending_needs_the_exact_confirmation_phrase(self):
        for confirm in (None, "yes", "send-test-email", ""):
            with self.subTest(confirm=confirm):
                arguments = ["--send-to", RECIPIENT] + (["--confirm", confirm] if confirm else [])
                code, result = self.run_script(*arguments)
                self.assertEqual((code, result), (1, {"ok": False, "error": "confirmation_required"}))
        self.smtp.assert_not_called()

    def test_confirmed_send_uses_the_service_once_and_prints_no_secrets(self):
        server = self.smtp.return_value
        server.__enter__.return_value = server
        code, result = self.run_script("--send-to", RECIPIENT, "--confirm", "SEND-TEST-EMAIL")
        self.assertEqual(code, 0)
        self.assertEqual((result["sent"], result["recipient_domain"]), (True, "example.com"))
        server.send_message.assert_called_once()
        text = json.dumps(result)
        for secret in (PASSWORD, "sender@example.org", RECIPIENT):
            self.assertNotIn(secret, text)

    def test_the_allowed_list_applies_to_the_test_message(self):
        code, result = self.run_script(
            "--send-to", RECIPIENT, "--confirm", "SEND-TEST-EMAIL",
            SMTP_ALLOWED_RECIPIENTS="someone.else@example.com")
        self.assertEqual((code, result["error"]), (1, "recipient_not_allowed"))
        self.smtp.assert_not_called()

    def test_bad_settings_print_only_a_fixed_code(self):
        code, result = self.run_script(SMTP_HOST="")
        self.assertEqual((code, result), (1, {"ok": False, "error": "not_configured"}))


if __name__ == "__main__":
    unittest.main()
