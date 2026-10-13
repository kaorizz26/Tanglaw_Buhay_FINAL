"""Explicit local staff bootstrap; no startup writes or password arguments."""

from getpass import GetPassWarning, getpass
import warnings

import click
from flask import current_app
import psycopg
from psycopg.conninfo import conninfo_to_dict

from services.auth import create_initial_admin


def check_local_bootstrap_database(database_url):
    """Prevent bootstrap against the old database, remote servers or services."""
    settings = conninfo_to_dict(database_url or "")
    if (settings.get("dbname") != "tanglaw_buhay_dev"
            or settings.get("host") not in ("localhost", "127.0.0.1", "::1")
            or settings.get("hostaddr", "127.0.0.1") not in ("127.0.0.1", "::1")
            or "service" in settings):
        raise ValueError("Bootstrap requires the local tanglaw_buhay_dev database.")


def register_commands(app):
    """Register local setup commands without running them or opening a connection."""

    @app.cli.command("create-admin")
    def create_admin_command():
        """Prompt locally to create the first administrator in an empty account table."""
        try:
            check_local_bootstrap_database(current_app.config.get("DATABASE_URL"))
        except (psycopg.Error, ValueError):
            raise click.ClickException("Configure the local development database first.") from None
        username = click.prompt("Administrator username")
        try:
            # Never accept a fallback that could echo a password into terminal logs.
            with warnings.catch_warnings():
                warnings.simplefilter("error", GetPassWarning)
                password = getpass("Administrator password (12+ characters): ")
                confirmation = getpass("Confirm password: ")
            if password != confirmation:
                raise ValueError("Passwords did not match.")
            create_initial_admin(username, password)
        except (GetPassWarning, EOFError):
            raise click.ClickException("Use an interactive terminal with hidden input.") from None
        except ValueError as error:
            raise click.ClickException(str(error)) from None
        except (psycopg.Error, RuntimeError):
            raise click.ClickException(
                "Administrator setup failed. Check configuration and migration 0002."
            ) from None
        click.echo("Initial administrator created. No survey records were changed.")
