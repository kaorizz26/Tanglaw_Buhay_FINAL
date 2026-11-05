"""Keep assessment identities, search values and portal tokens out of access logs."""

import logging
import re


class AssessmentRequestFilter(logging.Filter):
    """Mask only assessment request targets while retaining method and HTTP status."""

    def filter(self, record):
        message = record.getMessage()
        sanitized = re.sub(
            r"(/assessments)(?:[^\s\"]*)",
            r"\1/[protected]",
            message,
        )
        if sanitized != message:
            record.msg = sanitized
            record.args = ()
        return True


class PortalTokenFilter(logging.Filter):
    """Hide activation and password-reset tokens that appear in request addresses."""

    def filter(self, record):
        message = record.getMessage()
        sanitized = re.sub(
            r"(/portal/(?:activate|reset)/)[^\s\"?#]*",
            r"\1[protected]",
            message,
        )
        if sanitized != message:
            record.msg = sanitized
            record.args = ()
        return True


def protect_portal_token_logs():
    """Install once on the local Werkzeug logger so tokens never reach access logs."""
    logger = logging.getLogger("werkzeug")
    if not any(isinstance(item, PortalTokenFilter) for item in logger.filters):
        logger.addFilter(PortalTokenFilter())


def protect_assessment_access_logs():
    """Install once on the local Werkzeug logger; do not log request values ourselves."""
    logger = logging.getLogger("werkzeug")
    if not any(isinstance(item, AssessmentRequestFilter) for item in logger.filters):
        logger.addFilter(AssessmentRequestFilter())
