"""Citizen Portal pages for My Documents: see requests, upload files, download own files.

These routes use the citizen session and portal CSRF only; they never use the LGU
Staff session. Every record is looked up through the signed-in citizen's account.
"""

from flask import Blueprint, abort, g, redirect, render_template, request, send_file, url_for

from routes.citizen_portal import finish_citizen_response, prepare_citizen_session
from services import citizen_sessions as sessions
from services.citizen_documents import (
    MAX_VERSIONS, DocumentAccessError, get_my_document, list_my_documents, open_my_file,
    upload_document,
)
from services.document_storage import DEFAULT_MAX_BYTES, DocumentUploadError

citizen_documents = Blueprint("citizen_documents", __name__, url_prefix="/portal/documents")
citizen_documents.before_request(prepare_citizen_session)
citizen_documents.after_request(finish_citizen_response)

# The upload page accepts a request a little larger than the file, for the form fields.
# Only this route raises the app-wide 1 MB limit, and it still caps the whole request.
UPLOAD_REQUEST_LIMIT = DEFAULT_MAX_BYTES + 100 * 1024

UNAVAILABLE = "My Documents is unavailable right now. Please try again later."
STORAGE_UNAVAILABLE = "Uploads are unavailable right now. Please try again later."
TOO_LARGE = "The file is larger than 5 MB. Choose a smaller file."
VERSION_LIMIT = (
    f"You have already uploaded this document {MAX_VERSIONS} times. "
    "Please contact the LGU office for help."
)
# Notices on the list page; the address only picks one of these fixed messages.
LIST_NOTICES = {
    "uploaded": ("success", "Your file was uploaded. The LGU office will review it."),
    "closed": ("error", "This document can no longer be uploaded. See its status below."),
}


@citizen_documents.errorhandler(400)
@citizen_documents.errorhandler(404)
@citizen_documents.errorhandler(503)
def documents_error(error):
    return render_template("citizen_portal/error.html", message=error.description), error.code


@citizen_documents.errorhandler(413)
def file_too_large(error):
    """The request body was bigger than the upload limit."""
    return _render("document_upload_problem.html", 413, message=TOO_LARGE)


def _render(template, status=200, **context):
    return render_template(
        f"citizen_portal/{template}", csrf_token=sessions.csrf_token(), **context,
    ), status


def _stop_for(failure):
    """Turn a fixed service error code into the matching error page."""
    if failure.code == "not_found":
        abort(404, description="We could not find that document.")
    if failure.code == "storage_unavailable":
        abort(503, description=STORAGE_UNAVAILABLE)
    abort(503, description=UNAVAILABLE)


def _load_document_or_stop(request_id):
    try:
        return get_my_document(g.citizen["id"], request_id)
    except DocumentAccessError as failure:
        _stop_for(failure)


@citizen_documents.get("/")
@sessions.citizen_required
def index():
    """List the documents the LGU office asked for, with each one's status and next step."""
    try:
        documents = list_my_documents(g.citizen["id"])
    except DocumentAccessError as failure:
        _stop_for(failure)
    notice = LIST_NOTICES.get(request.args.get("notice"))
    return _render("documents.html", documents=documents, notice=notice)


def _render_upload_page(document, status=200, error=None):
    return _render(
        "document_upload.html", status, document=document, error=error,
        max_megabytes=DEFAULT_MAX_BYTES // (1024 * 1024),
    )


@citizen_documents.route("/<int:request_id>/upload", methods=["GET", "POST"])
@sessions.citizen_required
def upload(request_id):
    """Choose a file for one requested document and upload it."""
    if request.method == "POST":
        # Must be set before the form is read, or the 1 MB app limit would apply.
        request.max_content_length = UPLOAD_REQUEST_LIMIT
        sessions.check_csrf()
    document = _load_document_or_stop(request_id)
    if not document["can_upload"]:
        return redirect(url_for("citizen_documents.index", notice="closed"), code=303)
    if request.method == "GET":
        return _render_upload_page(document)
    chosen = request.files.get("document")
    try:
        upload_document(
            g.citizen, request_id, chosen.filename if chosen else None,
            chosen.stream if chosen else None,
        )
    except DocumentUploadError as problem:
        return _render_upload_page(document, 400, str(problem))
    except DocumentAccessError as failure:
        if failure.code == "not_open":
            return redirect(url_for("citizen_documents.index", notice="closed"), code=303)
        if failure.code == "version_limit":
            return _render_upload_page(document, 400, VERSION_LIMIT)
        _stop_for(failure)
    return redirect(url_for("citizen_documents.index", notice="uploaded"), code=303)


@citizen_documents.get("/files/<int:file_id>")
@sessions.citizen_required
def download(file_id):
    """Download one of the citizen's own uploaded files."""
    try:
        stored = open_my_file(g.citizen["id"], file_id)
    except DocumentAccessError as failure:
        _stop_for(failure)
    response = send_file(
        stored["path"], mimetype=stored["content_type"], as_attachment=True,
        download_name=stored["display_name"], max_age=0,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response
