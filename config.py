"""Load local environment settings without connecting to the database."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")


class Config:
    DATABASE_URL = os.getenv("DATABASE_URL")
