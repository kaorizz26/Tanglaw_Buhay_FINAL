"""Small, explicitly synthetic CSV bundles shared by preflight/import tests."""

from services.data_loader import EXCEL_FILES, REFERENCE_FILES
from services.development_data import development_sources


def synthetic_csv_frames():
    """Return two generated households plus declared blank fields and references."""
    datasets, references = development_sources()
    households = datasets["household"].iloc[:2].copy()
    datasets["household"] = households
    for name in EXCEL_FILES[1:]:
        frame = datasets[name]
        datasets[name] = frame.loc[frame["uuid"].isin(households["uuid"])].copy()
    for row in references["definitions"].to_dict("records"):
        frame = datasets[row["dataset"]]
        if row["variable_name"] not in frame:
            frame[row["variable_name"]] = ""
    return datasets, references


def write_csv_fixture(root, datasets, references):
    """Write only caller-owned synthetic frames to a temporary fixture directory."""
    (root / "raw").mkdir(exist_ok=True)
    (root / "reference").mkdir(exist_ok=True)
    for name, frame in datasets.items():
        frame.to_csv(root / "raw" / f"{name}.csv", index=False, encoding="utf-8-sig")
    for name, frame in references.items():
        frame.to_csv(
            root / "reference" / REFERENCE_FILES[name], index=False, encoding="utf-8-sig",
        )
