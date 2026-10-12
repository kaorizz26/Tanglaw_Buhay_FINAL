"""Print aggregate CBMS loading and join checks without personal records."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.data_loader import load_data, verify_data


def main():
    try:
        datasets, references = load_data()
        report = verify_data(datasets, references)
    except Exception as error:
        # Exception details from parsers can contain source values.
        print(json.dumps({"error": type(error).__name__, "message": "Loading or verification failed; check source files, schemas and dependencies."}))
        return 1
    print(json.dumps(report, indent=2))
    # Mapping ambiguities also require review before vulnerability assessment.
    return 1 if report["issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
