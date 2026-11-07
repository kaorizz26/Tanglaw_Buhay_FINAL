"""Run Tanglaw-Buhay against the isolated empty first-import environment."""

import os
from pathlib import Path
import sys

from dotenv import load_dotenv
from psycopg.conninfo import conninfo_to_dict, make_conninfo


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "tanglaw_first_run"

load_dotenv(ROOT / ".env")
test_url = os.environ.get("TANGLAW_TEST_DATABASE_URL")
if not test_url or conninfo_to_dict(test_url).get("dbname") != "tanglaw_buhay_test":
    raise SystemExit("The configured tanglaw_buhay_test database is unavailable.")

os.environ["DATABASE_URL"] = make_conninfo(
    test_url,
    options=f"-c search_path={SCHEMA}",
)
os.environ["IMPORT_REFERENCE_DIR"] = str((ROOT / "data" / "reference").resolve())
sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402  (configuration must be set before importing app)


if __name__ == "__main__":
    print("First-run test environment: no datasets or stored batches.")
    print("Open http://127.0.0.1:5000 and sign in with the existing administrator account.")
    app.run(host="127.0.0.1", port=5000, debug=False)
