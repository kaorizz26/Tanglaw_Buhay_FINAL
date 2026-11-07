"""Submissions directories (Task 3E): Citizen Accounts and Document Requests lists.

Service tests use an isolated schema in the test database with synthetic data. Route
tests mock the services. The development database is never touched.
"""

import re
import unittest
from unittest.mock import patch

from psycopg.types.json import Jsonb
from werkzeug.datastructures import MultiDict

from assessment_detail_fixtures import HOUSEHOLD_UUID
from repositories import citizen_accounts, document_requests
from routes import submissions as routes
from services import read_cache
from services import submissions as service
from services.assessment_queries import AssessmentQueryError
from services.citizen_account_panel import present_account_panel
from services.document_request_panel import present_document_requests
from services.submissions import (
    DirectoryFilters, InvalidDirectoryFilters, parse_directory_arguments, parse_return,
    return_parameters,
)

import test_citizen_account_creation as creation
from test_citizen_account_routes import NOW, RouteTestCase, account_row, household_page
from test_document_requests_staff import DocumentRouteCase, request_page

REQUEST_STATUSES = ("requested", "submitted", "needs_correction", "accepted", "cancelled")
ACCOUNT_STATUSES = ("pending_activation", "active", "deactivated")
SERVICE = "services.submissions"


def args(**values):
    """Build address parameters the way Flask reads them."""
    return MultiDict({name: str(value) for name, value in values.items()})


# ---- Reading the address -------------------------------------------------------

class AddressTests(unittest.TestCase):
    def test_a_plain_address_means_page_one_with_no_filters(self):
        page, filters = parse_directory_arguments(args(), REQUEST_STATUSES)
        self.assertEqual((page, filters.parameters), (1, {}))

    def test_filters_are_cleaned_and_only_set_ones_are_kept(self):
        page, filters = parse_directory_arguments(
            args(q="  Valid   ID ", status="submitted", barangay=" Poblacion ", page=3),
            REQUEST_STATUSES)
        self.assertEqual(page, 3)
        self.assertEqual(filters.parameters,
                         {"q": "Valid ID", "status": "submitted", "barangay": "Poblacion"})

    def test_unusable_addresses_are_refused(self):
        bad = [args(status="deleted"), args(page=0), args(page="abc"), args(page="1" * 12),
               args(q="x" * 201), args(barangay="x" * 201), args(q="a\x00b"),
               MultiDict([("q", "a"), ("q", "b")])]
        for case in bad:
            with self.subTest(case=dict(case)):
                with self.assertRaises(InvalidDirectoryFilters):
                    parse_directory_arguments(case, REQUEST_STATUSES)

    def test_the_list_state_survives_a_round_trip_through_a_request_link(self):
        filters = DirectoryFilters(search="Valid ID", status="submitted", barangay="Poblacion")
        parameters = return_parameters(4, filters)
        self.assertEqual(parse_return(args(**parameters)), (4, filters))

    def test_a_request_page_without_list_state_has_no_way_back(self):
        self.assertIsNone(parse_return(args()))

    def test_a_tampered_way_back_is_refused(self):
        for case in (args(from_list=1, list_next="http://example.com"), args(list_page=2),
                     args(from_list=1, list_status="deleted"), args(from_list=2)):
            with self.subTest(case=dict(case)):
                with self.assertRaises(InvalidDirectoryFilters):
                    parse_return(case)


# ---- The directories, on a real isolated schema ---------------------------------

class DirectoryServiceTests(unittest.TestCase):
    """Reuses the isolated-schema fixture of the Task 2C account creation tests."""

    _creation_setup = creation.AccountCreationTests.setUp
    _drop_schema = creation.AccountCreationTests._drop_schema
    _connect = creation.AccountCreationTests._connect
    _seed = creation.AccountCreationTests._seed
    _record_send = creation.AccountCreationTests._record_send
    error_code = creation.AccountCreationTests.error_code
    detail = creation.AccountCreationTests.detail

    def setUp(self):
        self._creation_setup()
        read_cache.clear()  # The barangay list is cached per database and batch number.
        self.addCleanup(read_cache.clear)
        self.next_household = 3  # Households 1 and 2 come from the fixture.

    # ---- helpers ----------------------------------------------------------

    def add_household(self, barangay="Poblacion", batch=None):
        number = self.next_household
        self.next_household += 1
        uuid = f"00000000-0000-4000-8000-{number:012d}"
        self.connection.execute(
            "INSERT INTO households (batch_id, uuid, source_row_number, responses)"
            " VALUES (%s, %s, %s, %s)",
            (batch or self.batch, uuid, number, Jsonb({"barangay": barangay})))
        return uuid

    def add_account(self, name="Maria Santos", status="active", barangay="Poblacion",
                    batch=None, uuid=None):
        """Create an account the way staff do, then move it to the wanted status."""
        uuid = uuid or self.add_household(barangay, batch)
        number = len(self.connection.execute("SELECT 1 FROM citizen_accounts").fetchall()) + 1
        account = citizen_accounts.insert_account(
            self.connection, batch or self.batch, uuid, name, f"person{number}@example.com")
        if status != "pending_activation":
            self.connection.execute(
                "UPDATE citizen_accounts SET status = %s, password_hash = 'scrypt:x$y$z',"
                " activated_at = CASE WHEN %s = 'active' THEN now() END WHERE id = %s",
                (status, status, account["id"]))
        return account["id"], uuid

    def add_request(self, account_id, title="Valid ID", status="requested"):
        created = document_requests.insert_request(
            self.connection, account_id, self.staff_id, title, None)
        document_requests.insert_event(self.connection, created["id"], "requested",
                                       staff_id=self.staff_id)
        steps = {"requested": [], "submitted": ["submitted"],
                 "needs_correction": ["submitted", "needs_correction"],
                 "accepted": ["submitted", "accepted"], "cancelled": ["cancelled"]}[status]
        for step in steps:
            document_requests.change_status(self.connection, created["id"], step)
        return created["id"]

    def accounts(self, page=1, **filters):
        return service.get_account_directory(
            page, DirectoryFilters(**filters), connection_factory=self._connect)

    def requests(self, page=1, **filters):
        return service.get_request_directory(
            page, DirectoryFilters(**filters), connection_factory=self._connect)

    def names(self, results):
        return [item["representative"] for item in results["items"]]

    def titles(self, results):
        return [item["title"] for item in results["items"]]

    # ---- Citizen Accounts -------------------------------------------------

    def test_a_new_account_appears_in_the_directory_with_the_columns_staff_need(self):
        self.assertEqual(self.accounts()["total"], 0)
        _, uuid = self.add_account("Maria Santos", "pending_activation", "Poblacion")
        item = self.accounts()["items"][0]
        self.assertEqual(
            (item["household_id"], item["representative"], item["barangay"],
             item["status_label"], item["household_uuid"]),
            ("HH-00003", "Maria Santos", "Poblacion", "Pending Activation", uuid))

    def test_accounts_are_listed_newest_first_and_never_show_private_details(self):
        for name in ("First", "Second", "Third"):
            self.add_account(name)
        results = self.accounts()
        self.assertEqual(self.names(results), ["Third", "Second", "First"])
        shown = repr(results["items"])
        self.assertNotIn("example.com", shown)
        self.assertNotIn("password", shown)

    def test_accounts_can_be_found_by_household_id_or_part_of_the_name(self):
        self.add_account("Maria Santos")
        self.add_account("Juan dela Cruz")
        self.assertEqual(self.names(self.accounts(search="HH-00004")), ["Juan dela Cruz"])
        self.assertEqual(self.names(self.accounts(search="santos")), ["Maria Santos"])
        self.assertEqual(self.accounts(search="nobody")["total"], 0)

    def test_search_text_is_matched_literally(self):
        self.add_account("Maria Santos")
        self.add_account("100% Real_Name")
        self.assertEqual(self.names(self.accounts(search="%")), ["100% Real_Name"])
        self.assertEqual(self.names(self.accounts(search="_")), ["100% Real_Name"])

    def test_accounts_can_be_filtered_by_status_and_barangay(self):
        self.add_account("Active One", "active", "Poblacion")
        self.add_account("Waiting", "pending_activation", "Poblacion")
        self.add_account("Blocked", "deactivated", "Bantay")
        self.assertEqual(self.names(self.accounts(status="pending_activation")), ["Waiting"])
        self.assertEqual(self.names(self.accounts(barangay="Bantay")), ["Blocked"])
        self.assertEqual(self.names(self.accounts(status="active", barangay="Bantay")), [])
        self.assertEqual(sorted(self.accounts()["barangays"]), ["Bantay", "Poblacion",
                                                                 "Synthetic Barangay"])

    def test_an_unknown_barangay_is_refused(self):
        with self.assertRaises(InvalidDirectoryFilters):
            self.accounts(barangay="Nowhere")

    def test_accounts_are_paged_twenty_five_at_a_time(self):
        for number in range(27):
            self.add_account(f"Person {number:02d}")
        first, second = self.accounts(1), self.accounts(2)
        self.assertEqual((first["total"], first["page_count"]), (27, 2))
        self.assertEqual((len(first["items"]), len(second["items"])), (25, 2))
        self.assertEqual((first["range_start"], first["range_end"]), (1, 25))
        self.assertEqual((second["range_start"], second["range_end"]), (26, 27))
        self.assertIsNone(self.accounts(3))

    def test_an_account_of_another_survey_batch_is_not_listed(self):
        self.add_account("Selected Batch")
        other = self.connection.execute(
            "INSERT INTO dataset_batches (survey_year, municipality, source_columns)"
            " VALUES (2022, 'Pozorrubio', %s) RETURNING id", (Jsonb({}),)).fetchone()[0]
        self.add_account("Other Batch", batch=other)
        self.assertEqual(self.names(self.accounts()), ["Selected Batch"])

    # ---- Document requests ------------------------------------------------

    def test_requests_waiting_for_review_come_first_with_the_longest_wait_on_top(self):
        account, _ = self.add_account()
        self.add_request(account, "Accepted early", "accepted")
        first_wait = self.add_request(account, "Waiting longest", "submitted")
        self.add_request(account, "Not uploaded", "requested")
        self.add_request(account, "Waiting recently", "submitted")
        results = self.requests()
        self.assertEqual(self.titles(results), [
            "Waiting longest", "Waiting recently", "Not uploaded", "Accepted early"])
        self.assertEqual([item["awaiting_review"] for item in results["items"]],
                         [True, True, False, False])
        self.assertEqual(results["items"][0]["id"], first_wait)

    def test_the_waiting_count_covers_the_whole_batch_not_just_the_filter(self):
        account, _ = self.add_account()
        self.add_request(account, "First ID", "submitted")
        self.add_request(account, "Second ID", "submitted")
        self.add_request(account, "Third ID", "accepted")
        results = self.requests(status="accepted")
        self.assertEqual((results["total"], results["awaiting_review_count"]), (1, 2))

    def test_requests_show_the_household_and_a_plain_status(self):
        account, _ = self.add_account("Maria Santos", barangay="Bantay")
        self.add_request(account, "Valid ID", "needs_correction")
        item = self.requests()["items"][0]
        self.assertEqual(
            (item["household_id"], item["representative"], item["barangay"],
             item["status_label"], item["awaiting_review"]),
            ("HH-00003", "Maria Santos", "Bantay", "Needs correction", False))

    def test_requests_can_be_found_by_document_name_household_or_representative(self):
        first, _ = self.add_account("Maria Santos")
        second, _ = self.add_account("Juan dela Cruz")
        self.add_request(first, "Valid ID")
        self.add_request(second, "Proof of income")
        self.assertEqual(self.titles(self.requests(search="income")), ["Proof of income"])
        self.assertEqual(self.titles(self.requests(search="HH-00003")), ["Valid ID"])
        self.assertEqual(self.titles(self.requests(search="dela cruz")), ["Proof of income"])
        self.assertEqual(self.requests(search="nothing")["total"], 0)

    def test_requests_can_be_filtered_by_status_and_barangay(self):
        near, _ = self.add_account("Near", barangay="Poblacion")
        far, _ = self.add_account("Far", barangay="Bantay")
        self.add_request(near, "Near ID", "submitted")
        self.add_request(far, "Far ID", "submitted")
        self.add_request(far, "Far income", "accepted")
        self.assertEqual(self.titles(self.requests(status="submitted", barangay="Bantay")),
                         ["Far ID"])
        self.assertEqual(self.requests(status="cancelled")["total"], 0)

    def test_requests_are_paged_and_batch_scoped(self):
        account, _ = self.add_account()
        for number in range(26):
            self.add_request(account, f"Document {number:02d}")
        self.assertEqual((len(self.requests(1)["items"]), len(self.requests(2)["items"])), (25, 1))
        self.assertIsNone(self.requests(3))
        other = self.connection.execute(
            "INSERT INTO dataset_batches (survey_year, municipality, source_columns)"
            " VALUES (2022, 'Pozorrubio', %s) RETURNING id", (Jsonb({}),)).fetchone()[0]
        stranger, _ = self.add_account("Other", batch=other)
        self.add_request(stranger, "Other batch document", "submitted")
        self.assertEqual(self.requests()["total"], 26)
        self.assertEqual(self.requests()["awaiting_review_count"], 0)

    # ---- One account ------------------------------------------------------

    def test_the_account_page_reads_the_same_facts_as_the_assessment_page(self):
        account, uuid = self.add_account("Maria Santos", "active")
        self.connection.execute(
            "INSERT INTO household_assessments (run_id, batch_id, household_uuid, findings)"
            " VALUES (%s, %s, %s, %s)", (self.run, self.batch, uuid, Jsonb({
                "household_uuid": uuid, "survey_year": 2022,
                "mpi": {"uuid": uuid, "vulnerability_rank": 2},
                "programs": {name: {} for name in ("4Ps", "SLP", "SPISC", "TUPAD")}})))
        self.add_request(account, "Valid ID", "submitted")
        page = service.get_account_detail(uuid, connection_factory=self._connect)
        self.assertEqual(page["citizen_account"]["state"], "active")
        self.assertEqual(page["citizen_account"]["name"], "Maria Santos")
        self.assertTrue(page["citizen_account"]["can_deactivate"])
        self.assertEqual([r["title"] for r in page["document_requests"]["requests"]], ["Valid ID"])
        self.assertEqual(page["reference_id"], "HH-00003")

    def test_a_household_without_an_account_reads_as_none(self):
        page = service.get_account_detail(creation.HOUSEHOLD, connection_factory=self._connect)
        self.assertEqual(page["citizen_account"]["state"], "none")

    def test_a_household_outside_the_selected_survey_is_not_found(self):
        with self.assertRaises(AssessmentQueryError):
            service.get_account_detail(creation.NO_ASSESSMENT, connection_factory=self._connect)


# ---- Pages --------------------------------------------------------------------

def directory(items, **changes):
    """What the directory services return, for page tests."""
    base = {
        "filters": DirectoryFilters(), "barangays": ["Bantay", "Poblacion"],
        "total": len(items), "page": 1, "page_count": 1, "items": items,
        "range_start": 1 if items else 0, "range_end": len(items), "statuses": [],
    }
    base.update(changes)
    return base


def request_item(request_id=8, status="submitted", **changes):
    labels = {"requested": "Waiting for upload", "submitted": "Ready for review",
              "needs_correction": "Needs correction", "accepted": "Accepted",
              "cancelled": "Cancelled"}
    item = {"id": request_id, "title": "Valid ID", "household_id": "HH-00008",
            "representative": "Maria Santos", "barangay": "Poblacion", "status": status,
            "status_label": labels[status], "awaiting_review": status == "submitted",
            "updated_at": NOW}
    item.update(changes)
    return item


def account_item(status="active", **changes):
    labels = {"pending_activation": "Pending Activation", "active": "Active",
              "deactivated": "Deactivated"}
    item = {"household_uuid": HOUSEHOLD_UUID, "household_id": "HH-00008",
            "representative": "Maria Santos", "barangay": "Poblacion", "status": status,
            "status_label": labels[status]}
    item.update(changes)
    return item


REQUESTS_URL = "/submissions/document-requests"
ACCOUNTS_URL = "/submissions/citizen-accounts"
ACCOUNT_URL = f"/submissions/citizen-accounts/household/{HOUSEHOLD_UUID}"


class SubmissionsRouteCase(RouteTestCase):
    def setUp(self):
        super().setUp()
        self.request_status_options = [("submitted", "Ready for review"),
                                       ("accepted", "Accepted")]
        self.requests_page = directory([request_item()], awaiting_review_count=1,
                                       statuses=self.request_status_options)
        self.accounts_page = directory([account_item()],
                                       statuses=[("active", "Active")])
        patches = {
            "requests": patch.object(routes, "get_request_directory",
                                     side_effect=lambda *a, **k: self.requests_page),
            "accounts": patch.object(routes, "get_account_directory",
                                     side_effect=lambda *a, **k: self.accounts_page),
            "detail": patch.object(routes, "get_account_detail",
                                   side_effect=lambda *a, **k: self.detail_page),
        }
        for name, patcher in patches.items():
            self.mocks["submissions_" + name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.detail_page = {
            **{key: value for key, value in household_page().items() if key != "account"},
            "citizen_account": present_account_panel(account_row("active")),
            "document_requests": present_document_requests([]),
        }
        self.sign_in()


class AuthorizationTests(SubmissionsRouteCase):
    PATHS = (REQUESTS_URL, ACCOUNTS_URL, ACCOUNT_URL)

    def test_anonymous_visitors_are_sent_to_sign_in(self):
        with self.client.session_transaction() as session:
            session.clear()
        for path in self.PATHS:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual((response.status_code, response.location), (302, "/login"))
        for name in ("requests", "accounts", "detail"):
            self.mocks["submissions_" + name].assert_not_called()

    def test_administrators_are_refused(self):
        self.sign_in("system_administrator")
        for path in self.PATHS:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)
        for name in ("requests", "accounts", "detail"):
            self.mocks["submissions_" + name].assert_not_called()

    def test_the_pages_only_read(self):
        for path in self.PATHS:
            for method in ("post", "put", "patch", "delete"):
                with self.subTest(path=path, method=method):
                    self.assertEqual(getattr(self.client, method)(path).status_code, 405)


class SidebarTests(SubmissionsRouteCase):
    def test_staff_see_submissions_with_both_lists_and_the_right_one_is_marked(self):
        text = self.text(self.client.get(REQUESTS_URL))
        nav = re.search(r'<nav class="staff-navigation".*?</nav>', text, re.S).group(0)
        for words in ("Submissions", "Document Requests", "Citizen Accounts"):
            self.assertIn(words, nav)
        self.assertNotIn("navigation-item-planned", nav)
        self.assertNotIn("Planned", nav)
        current = re.findall(r'navigation-item-active" href="([^"]+)"', nav)
        self.assertIn(REQUESTS_URL, current)
        self.assertNotIn(ACCOUNTS_URL, current)
        accounts_nav = self.text(self.client.get(ACCOUNTS_URL))
        self.assertIn(f'navigation-item-active" href="{ACCOUNTS_URL}"', accounts_nav)

    def test_the_request_page_keeps_document_requests_marked(self):
        page = request_page("submitted")
        with patch("routes.document_requests.get_document_request_page", return_value=page):
            text = self.text(self.client.get("/document-requests/8"))
        self.assertIn(f'navigation-item-active" href="{REQUESTS_URL}"', text)


class DocumentRequestListTests(SubmissionsRouteCase):
    def test_the_page_explains_itself_and_shows_the_columns(self):
        text = self.main_text(self.client.get(REQUESTS_URL))
        for words in ("Document Requests", "Household ID, representative or document", "Status",
                      "Barangay", "Apply Filters", "Valid ID", "HH-00008", "Maria Santos",
                      "Ready for review"):
            self.assertIn(words, text)
        self.assertIn('class="page-header portal-hero submissions-header"', text)
        self.assertIn('class="records-search-control"', text)

    def test_requests_waiting_for_review_are_prominent_with_a_review_button(self):
        self.requests_page = directory(
            [request_item(8, "submitted"), request_item(9, "accepted", title="Proof")],
            awaiting_review_count=1)
        text = self.main_text(self.client.get(REQUESTS_URL))
        self.assertIn("1 document is waiting for your review.", text)
        self.assertIn("submissions-row-awaiting", text)
        self.assertEqual(text.count(">Review<"), 1)
        self.assertEqual(text.count(">View<"), 1)
        self.assertIn(f"{REQUESTS_URL}?status=submitted", text)

    def test_no_waiting_notice_when_nothing_waits(self):
        self.requests_page = directory([request_item(8, "accepted")], awaiting_review_count=0)
        self.assertNotIn("waiting for your review", self.main_text(self.client.get(REQUESTS_URL)))

    def test_each_row_links_to_the_request_with_a_way_back_to_the_same_list(self):
        self.client.get(REQUESTS_URL)
        text = self.text(self.client.get(f"{REQUESTS_URL}?q=ID&status=submitted&page=1"))
        link = re.search(r'href="(/document-requests/8\?[^"]+)"', text).group(1)
        self.assertIn("from_list=1", link)
        self.assertIn("list_q=ID", link)
        self.assertIn("list_status=submitted", link)

    def test_the_address_filters_reach_the_service(self):
        self.client.get(f"{REQUESTS_URL}?q=Valid+ID&status=accepted&barangay=Poblacion&page=2")
        page, filters = self.mocks["submissions_requests"].call_args.args
        self.assertEqual(page, 2)
        self.assertEqual(filters.parameters,
                         {"q": "Valid ID", "status": "accepted", "barangay": "Poblacion"})

    def test_filters_are_shown_and_can_be_cleared(self):
        self.requests_page = directory(
            [], filters=DirectoryFilters(search="zzz", status="accepted"),
            awaiting_review_count=0, statuses=self.request_status_options)
        text = self.main_text(self.client.get(f"{REQUESTS_URL}?q=zzz&status=accepted"))
        self.assertIn("No requests match your search", text)
        self.assertIn('value="zzz"', text)
        self.assertIn("Clear Filters", text)
        self.assertIn("Applied filters", text)
        self.assertIn("Search: zzz", text)
        self.assertIn("Status: Accepted", text)

    def test_an_empty_list_tells_staff_how_requests_are_made(self):
        self.requests_page = directory([], awaiting_review_count=0)
        text = self.main_text(self.client.get(REQUESTS_URL))
        self.assertIn("No documents have been requested yet", text)
        self.assertIn("Request Documents", text)

    def test_paging_links_keep_the_filters(self):
        self.requests_page = directory(
            [request_item()], total=60, page=2, page_count=3, range_start=26, range_end=50,
            filters=DirectoryFilters(status="submitted"), awaiting_review_count=1)
        text = self.main_text(self.client.get(f"{REQUESTS_URL}?status=submitted&page=2"))
        self.assertIn("Page 2 of 3", text)
        self.assertIn(f"{REQUESTS_URL}?page=1&status=submitted", text)
        self.assertIn(f"{REQUESTS_URL}?page=3&status=submitted", text)
        self.assertIn("Showing <strong>26–50</strong> of 60 requests", text)

    def test_bad_addresses_and_missing_pages_get_clear_errors(self):
        for query in ("?status=deleted", "?page=0", "?q=" + "x" * 201):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(REQUESTS_URL + query).status_code, 400)
        self.requests_page = None
        self.assertEqual(self.client.get(REQUESTS_URL + "?page=9").status_code, 404)
        self.mocks["submissions_requests"].side_effect = AssessmentQueryError(
            "active_batch_missing")
        response = self.client.get(REQUESTS_URL)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Document Requests", response.text)
        self.assertIn("No active dataset yet", response.text)
        self.assertIn("system administrator must import", response.text)
        self.assertNotIn("Request Unavailable", response.text)
        self.mocks["submissions_requests"].side_effect = AssessmentQueryError("unavailable")
        self.assertEqual(self.client.get(REQUESTS_URL).status_code, 503)
        self.mocks["submissions_requests"].side_effect = InvalidDirectoryFilters("barangay")
        self.assertEqual(self.client.get(REQUESTS_URL + "?barangay=Nowhere").status_code, 400)

    def test_no_private_details_appear_in_the_list(self):
        text = self.text(self.client.get(REQUESTS_URL))
        for private in ("citizen@example.com", "/portal/activate/", "storage_key", "/static/uploads"):
            self.assertNotIn(private, text)


class CitizenAccountListTests(SubmissionsRouteCase):
    def test_the_page_shows_the_agreed_columns_and_a_view_action(self):
        text = self.main_text(self.client.get(ACCOUNTS_URL))
        for words in ("Citizen Accounts", "Household ID", "Representative", "Barangay",
                      "Account Status", "HH-00008", "Maria Santos", "Poblacion", "Active"):
            self.assertIn(words, text)
        self.assertIn(f"{ACCOUNT_URL}", text)
        self.assertIn(">View<", text)
        self.assertIn('class="page-header portal-hero submissions-header"', text)

    def test_every_account_status_is_written_out(self):
        self.accounts_page = directory([
            account_item("pending_activation"), account_item("active"),
            account_item("deactivated")])
        text = self.main_text(self.client.get(ACCOUNTS_URL))
        for label in ("Pending Activation", "Active", "Deactivated"):
            self.assertIn(label, text)

    def test_view_links_keep_the_list_state(self):
        self.accounts_page = directory(
            [account_item()], filters=DirectoryFilters(search="Maria", status="active"),
            page=2, page_count=3, total=60, range_start=26, range_end=26)
        text = self.text(self.client.get(f"{ACCOUNTS_URL}?q=Maria&status=active&page=2"))
        link = re.search(rf'href="({re.escape(ACCOUNT_URL)}\?[^"]+)"', text).group(1)
        for part in ("page=2", "q=Maria", "status=active"):
            self.assertIn(part, link)

    def test_empty_states_differ_for_no_accounts_and_no_matches(self):
        self.accounts_page = directory([])
        self.assertIn("New accounts appear here automatically",
                      self.main_text(self.client.get(ACCOUNTS_URL)))
        self.accounts_page = directory([], filters=DirectoryFilters(search="x"))
        self.assertIn("No accounts match your search",
                      self.main_text(self.client.get(ACCOUNTS_URL + "?q=x")))

    def test_no_email_or_activation_detail_is_listed(self):
        text = self.text(self.client.get(ACCOUNTS_URL))
        for private in ("citizen@example.com", "/portal/activate/", "activation code"):
            self.assertNotIn(private, text)

    def test_no_active_dataset_is_a_setup_state_but_real_failures_stay_unavailable(self):
        self.mocks["submissions_accounts"].side_effect = AssessmentQueryError(
            "active_batch_missing")
        response = self.client.get(ACCOUNTS_URL)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Citizen Accounts", response.text)
        self.assertIn("No active dataset yet", response.text)
        self.assertIn("system administrator must import", response.text)
        self.assertNotIn("Request Unavailable", response.text)

        self.mocks["submissions_accounts"].side_effect = AssessmentQueryError("unavailable")
        self.assertEqual(self.client.get(ACCOUNTS_URL).status_code, 503)


class CitizenAccountPageTests(SubmissionsRouteCase):
    def test_the_page_reuses_the_account_actions_and_offers_a_way_back(self):
        response = self.client.get(f"{ACCOUNT_URL}?page=2&q=Maria&status=active")
        text = self.main_text(response)
        for words in ("Citizen Account", "Back to Citizen Accounts", "Synthetic Citizen",
                      "Change Email", "Deactivate Account", "Account History",
                      "Open Household Assessment", "Document Requests"):
            self.assertIn(words, text)
        back = re.search(r'href="([^"]+)"><span aria-hidden="true">←</span> Back to Citizen Accounts',
                         text).group(1)
        for part in ("/submissions/citizen-accounts", "page=2", "q=Maria", "status=active"):
            self.assertIn(part, back)
        self.assertIn(f"/assessments/{HOUSEHOLD_UUID}", text)

    def test_a_household_without_an_account_returns_to_the_list(self):
        self.detail_page["citizen_account"] = {"state": "none"}
        response = self.client.get(ACCOUNT_URL + "?q=Maria")
        self.assertEqual((response.status_code, response.location),
                         (303, "/submissions/citizen-accounts?q=Maria"))

    def test_missing_or_unavailable_households_get_clear_errors(self):
        self.mocks["submissions_detail"].side_effect = AssessmentQueryError(
            "active_batch_missing")
        response = self.client.get(ACCOUNT_URL)
        self.assertEqual(response.status_code, 200)
        self.assertIn("No active dataset yet", response.text)
        self.mocks["submissions_detail"].side_effect = AssessmentQueryError(
            "household_assessment_missing")
        self.assertEqual(self.client.get(ACCOUNT_URL).status_code, 404)
        self.mocks["submissions_detail"].side_effect = AssessmentQueryError("unavailable")
        self.assertEqual(self.client.get(ACCOUNT_URL).status_code, 503)

    def test_a_bad_list_state_is_refused(self):
        self.assertEqual(self.client.get(ACCOUNT_URL + "?status=deleted").status_code, 400)


class RequestPageBackLinkTests(DocumentRouteCase):
    def setUp(self):
        super().setUp()
        self.request_page = request_page("submitted")
        self.request_page["files"] = []
        self.sign_in()

    def test_a_request_opened_from_the_list_can_return_to_it_with_the_same_search(self):
        text = self.main_text(self.client.get(
            "/document-requests/8?from_list=1&list_page=2&list_q=Valid&list_status=submitted"))
        back = re.search(r'href="([^"]+)"><span aria-hidden="true">←</span> Back to Document Requests',
                         text).group(1)
        for part in ("/submissions/document-requests", "page=2", "q=Valid", "status=submitted"):
            self.assertIn(part, back)
        self.assertIn("Back to Household Assessment", text)

    def test_a_request_opened_from_the_assessment_has_no_list_link(self):
        self.assertNotIn("Back to Document Requests",
                         self.main_text(self.client.get("/document-requests/8")))

    def test_a_tampered_way_back_is_refused(self):
        for query in ("?from_list=1&list_next=http://example.com", "?list_page=2",
                      "?from_list=1&list_status=deleted"):
            with self.subTest(query=query):
                self.assertEqual(self.client.get("/document-requests/8" + query).status_code, 400)


if __name__ == "__main__":
    unittest.main()
