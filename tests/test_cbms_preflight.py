"""Synthetic CSV format, identity and reference preflight checks."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from services.cbms_preflight import _read_csv, validate_csv_bundle
from services.data_loader import EXCEL_FILES, REFERENCE_FILES, load_data, verify_data
from services.development_data import development_sources


class CsvPreflightTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "raw").mkdir()
        (self.root / "reference").mkdir()
        self.datasets, self.references = development_sources()
        households = self.datasets["household"].iloc[:2].copy()
        self.datasets["household"] = households
        for name in EXCEL_FILES[1:]:
            frame = self.datasets[name]
            self.datasets[name] = frame.loc[frame["uuid"].isin(households["uuid"])].copy()
        # The development fixture omits unused declared fields; supply synthetic
        # blanks for the CSV format contract, never read private source files.
        for row in self.references["definitions"].to_dict("records"):
            frame = self.datasets[row["dataset"]]
            if row["variable_name"] not in frame:
                frame[row["variable_name"]] = ""
        self._write_bundle()

    def _source_path(self, name):
        return self.root / "raw" / f"{name}.csv"

    def _write_bundle(self):
        for name, frame in self.datasets.items():
            frame.to_csv(self._source_path(name), index=False, encoding="utf-8-sig")
        for name, frame in self.references.items():
            frame.to_csv(
                self.root / "reference" / REFERENCE_FILES[name],
                index=False, encoding="utf-8-sig",
            )

    def _codes(self, report, severity="errors"):
        return {(issue["source"], issue["code"]) for issue in report[severity]}

    def test_valid_bundle_is_read_only_and_aggregate(self):
        before = {path: path.read_bytes() for path in self.root.rglob("*.csv")}
        report = validate_csv_bundle(self.root)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["indicator_count"], 16)
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        output = json.dumps(report)
        self.assertNotIn("DEV-0001", output)
        self.assertNotIn("Sample Barangay", output)
        self.assertNotIn(str(self.root), output)

    def test_reader_preserves_strings_names_and_row_order(self):
        path = self.root / "example.csv"
        path.write_bytes((
            '\ufeffuuid,Mixed CASE_Name,response,note\n'
            '00002,,NA,"comma, and\nnewline"\n'
            '00001,0007,0,"  spaces  "\n'
        ).encode("utf-8"))
        frame, error = _read_csv(path)
        self.assertIsNone(error)
        self.assertEqual(list(frame.columns), ["uuid", "Mixed CASE_Name", "response", "note"])
        self.assertEqual(frame.values.tolist(), [
            ["00002", "", "NA", "comma, and\nnewline"],
            ["00001", "0007", "0", "  spaces  "],
        ])

    def test_missing_and_malformed_files_are_safe_blockers(self):
        path = self._source_path("household")
        cases = (
            (b"uuid,uuid\nPRIVATE_VALUE,PRIVATE_VALUE\n", "duplicate_header"),
            (b"uuid,\nPRIVATE_VALUE,PRIVATE_VALUE\n", "empty_or_blank_header"),
            (b"uuid\nPRIVATE_VALUE,PRIVATE_VALUE\n", "inconsistent_row_width"),
            (b"uuid\n\n", "inconsistent_row_width"),
            (b'uuid\n"PRIVATE_VALUE', "unreadable_csv"),
            (b"uuid\n\xff\n", "unreadable_csv"),
            (b"", "empty_or_blank_header"),
        )
        for content, code in cases:
            with self.subTest(code=code):
                path.write_bytes(content)
                report = validate_csv_bundle(self.root)
                self.assertFalse(report["passed"])
                self.assertIn(("household", code), self._codes(report))
                self.assertNotIn("PRIVATE_VALUE", json.dumps(report))
        path.unlink()
        self.assertIn(("household", "missing_file"), self._codes(validate_csv_bundle(self.root)))

    def test_blank_and_duplicate_join_keys_block(self):
        for name in EXCEL_FILES:
            with self.subTest(source=name, case="blank"):
                frame = self.datasets[name].copy()
                if frame.empty:
                    frame = pd.DataFrame([{column: "" for column in frame}], columns=frame.columns)
                frame.iloc[0, frame.columns.get_loc("uuid")] = "   "
                frame.to_csv(self._source_path(name), index=False)
                self.assertIn(
                    (name, "blank_join_keys"), self._codes(validate_csv_bundle(self.root)),
                )
                self._write_bundle()
        for name in EXCEL_FILES[:3]:
            with self.subTest(source=name, case="duplicate"):
                frame = self.datasets[name]
                pd.concat([frame, frame.iloc[:1]]).to_csv(self._source_path(name), index=False)
                self.assertIn(
                    (name, "duplicate_join_keys"), self._codes(validate_csv_bundle(self.root)),
                )
                self._write_bundle()

    def test_child_death_repeated_uuid_is_allowed(self):
        frame = self.datasets["section_l11_to_l14"]
        row = dict.fromkeys(frame.columns, "")
        row["uuid"] = "DEV-0001"
        pd.DataFrame([row, row], columns=frame.columns).to_csv(
            self._source_path("section_l11_to_l14"), index=False,
        )
        report = validate_csv_bundle(self.root)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["datasets"]["section_l11_to_l14"]["repeated_household_records"], 2)
        self.assertIn(
            ("section_l11_to_l14", "identical_child_death_rows"), self._codes(report, "warnings"),
        )

    def test_orphan_household_and_member_links_block(self):
        for name in EXCEL_FILES[1:]:
            with self.subTest(source=name):
                frame = self.datasets[name].copy()
                if frame.empty:
                    frame = pd.DataFrame([{column: "" for column in frame}], columns=frame.columns)
                frame.iloc[0, frame.columns.get_loc("uuid")] = "PRIVATE_ORPHAN"
                frame.to_csv(self._source_path(name), index=False)
                report = validate_csv_bundle(self.root)
                self.assertFalse(report["passed"])
                self.assertIn((f"{name}_to_household", "orphan_records"), self._codes(report))
                self.assertNotIn("PRIVATE_ORPHAN", json.dumps(report))
                self._write_bundle()
        frame = self.datasets["section_l02_to_l41"].copy()
        frame.iloc[0, frame.columns.get_loc("section_l_line_number")] = "999"
        frame.to_csv(self._source_path("section_l02_to_l41"), index=False)
        self.assertIn(
            ("section_l_to_members", "orphan_records"),
            self._codes(validate_csv_bundle(self.root)),
        )

    def test_missing_section_l_and_roster_rows_are_warnings(self):
        self.datasets["section_l02_to_l41"].iloc[:0].to_csv(
            self._source_path("section_l02_to_l41"), index=False,
        )
        report = validate_csv_bundle(self.root)
        self.assertTrue(report["passed"], report)
        self.assertIn(
            ("section_l_to_members", "missing_related_records"), self._codes(report, "warnings"),
        )
        self.datasets["section_a_e_indiv"].iloc[:0].to_csv(
            self._source_path("section_a_e_indiv"), index=False,
        )
        report = validate_csv_bundle(self.root)
        self.assertTrue(report["passed"], report)
        self.assertIn(
            ("section_a_e_indiv_to_household", "missing_related_records"),
            self._codes(report, "warnings"),
        )

    def test_leading_zero_keys_pass_without_rewriting(self):
        for name, frame in self.datasets.items():
            frame["uuid"] = frame["uuid"].replace({"DEV-0001": "00001", "DEV-0002": "00002"})
            line_field = "line_number" if name == "section_a_e_indiv" else "section_l_line_number"
            if line_field in frame:
                frame[line_field] = frame[line_field].map(lambda value: f"{value:03d}")
        self._write_bundle()
        report = validate_csv_bundle(self.root)
        self.assertTrue(report["passed"], report)
        frame, _ = _read_csv(self._source_path("section_a_e_indiv"))
        self.assertEqual(frame.iloc[0]["uuid"], "00001")
        self.assertEqual(frame.iloc[0]["line_number"], "001")

    def test_integer_line_errors_and_collisions_block(self):
        for name, field in (
            ("section_a_e_indiv", "line_number"),
            ("section_l02_to_l41", "section_l_line_number"),
        ):
            for value in ("NA", "1.5", "2147483648"):
                with self.subTest(source=name, value=value):
                    frame = self.datasets[name].copy()
                    frame.iloc[0, frame.columns.get_loc(field)] = value
                    frame.to_csv(self._source_path(name), index=False)
                    self.assertIn(
                        (name, "invalid_integer_line_numbers"),
                        self._codes(validate_csv_bundle(self.root)),
                    )
            frame = self.datasets[name].copy()
            row = frame.iloc[:1].copy()
            row[field] = "001"
            pd.concat([frame, row]).to_csv(self._source_path(name), index=False)
            self.assertIn(
                (name, "integer_key_collisions"), self._codes(validate_csv_bundle(self.root)),
            )
            self._write_bundle()

    def test_missing_columns_and_reference_keys_block(self):
        frame = self.datasets["section_a_e_indiv"].drop(columns=["b06_ofi", "a07_age"])
        frame.to_csv(self._source_path("section_a_e_indiv"), index=False)
        report = validate_csv_bundle(self.root)
        self.assertIn(("section_a_e_indiv", "missing_engine_columns"), self._codes(report))
        self.assertIn(("mpi_mapping", "missing_indicator_columns"), self._codes(report))
        self._write_bundle()
        for name, frame in self.references.items():
            path = self.root / "reference" / REFERENCE_FILES[name]
            with self.subTest(reference=name):
                pd.concat([frame, frame.iloc[:1]]).to_csv(path, index=False)
                self.assertIn(
                    (name, "duplicate_reference_keys"),
                    self._codes(validate_csv_bundle(self.root)),
                )
                changed = frame.copy()
                changed.iloc[0, changed.columns.get_loc("variable_name")] = ""
                changed.to_csv(path, index=False)
                self.assertIn(
                    (name, "blank_reference_keys"), self._codes(validate_csv_bundle(self.root)),
                )
                frame.drop(columns=["variable_name"]).to_csv(path, index=False)
                self.assertIn(
                    (name, "missing_reference_columns"),
                    self._codes(validate_csv_bundle(self.root)),
                )
                self._write_bundle()

    def test_reference_failure_is_not_overridden_by_join_flag(self):
        frame = self.references["codebook"]
        self.references["codebook"] = pd.concat([frame, frame.iloc[:1]])
        old_report = verify_data(self.datasets, self.references)
        self.assertTrue(old_report["loading_and_joins_passed"])
        self._write_bundle()
        self.assertFalse(validate_csv_bundle(self.root)["passed"])

    def test_mapping_count_resolution_and_conflicts_block_safely(self):
        mapping = self.references["mpi_mapping"]
        path = self.root / "reference" / REFERENCE_FILES["mpi_mapping"]
        mapping.loc[mapping["indicator"].ne("Water")].to_csv(path, index=False)
        self.assertIn(
            ("mpi_mapping", "incorrect_indicator_count"),
            self._codes(validate_csv_bundle(self.root)),
        )
        changed = mapping.copy()
        changed.loc[changed["indicator"].eq("Water"), "variable_name"] = "PRIVATE_RESPONSE"
        changed.to_csv(path, index=False)
        report = validate_csv_bundle(self.root)
        self.assertIn(("mpi_mapping", "unresolved_indicator_inputs"), self._codes(report))
        self.assertNotIn("PRIVATE_RESPONSE", json.dumps(report))
        extra = mapping.loc[
            mapping["variable_name"].eq("q01_main_water") & mapping["code"].eq("1")
        ].copy()
        extra["code"] = "01"
        extra["deprived"] = "1"
        pd.concat([mapping, extra]).to_csv(path, index=False)
        self.assertIn(
            ("mpi_mapping", "invalid_direct_mappings"),
            self._codes(validate_csv_bundle(self.root)),
        )

    def test_metadata_and_empty_households_block(self):
        self.datasets["household"].iloc[:0].to_csv(self._source_path("household"), index=False)
        self.assertIn(
            ("household", "empty_household_dataset"), self._codes(validate_csv_bundle(self.root)),
        )
        self._write_bundle()
        definitions = self.references["definitions"].copy()
        definitions.iloc[0, definitions.columns.get_loc("dataset")] = "PRIVATE_DATASET"
        definitions.to_csv(self.root / "reference" / REFERENCE_FILES["definitions"], index=False)
        report = validate_csv_bundle(self.root)
        self.assertIn(("definitions", "unknown_source_datasets"), self._codes(report))
        self.assertNotIn("PRIVATE_DATASET", json.dumps(report))

    def test_missing_join_columns_and_empty_references_block(self):
        for name, frame in self.datasets.items():
            with self.subTest(source=name):
                frame.drop(columns=["uuid"]).to_csv(self._source_path(name), index=False)
                self.assertIn(
                    (name, "missing_join_columns"), self._codes(validate_csv_bundle(self.root)),
                )
                self._write_bundle()
        for name, frame in self.references.items():
            with self.subTest(reference=name):
                frame.iloc[:0].to_csv(
                    self.root / "reference" / REFERENCE_FILES[name], index=False,
                )
                self.assertIn(
                    (name, "empty_reference"), self._codes(validate_csv_bundle(self.root)),
                )
                self._write_bundle()

    def test_extra_columns_and_blank_provenance_are_safe_warnings(self):
        self.datasets["household"]["PRIVATE_HEADER"] = "PRIVATE_RESPONSE"
        definitions = self.references["definitions"]
        definitions.iloc[0, definitions.columns.get_loc("provenance")] = ""
        self._write_bundle()
        report = validate_csv_bundle(self.root)
        self.assertTrue(report["passed"], report)
        self.assertIn(
            ("household", "columns_without_definitions"), self._codes(report, "warnings"),
        )
        self.assertIn(("definitions", "missing_provenance"), self._codes(report, "warnings"))
        self.assertNotIn("PRIVATE_HEADER", json.dumps(report))
        self.assertNotIn("PRIVATE_RESPONSE", json.dumps(report))

    def test_line_format_mismatch_is_not_silently_joined(self):
        frame = self.datasets["section_l02_to_l41"].copy()
        frame.iloc[0, frame.columns.get_loc("section_l_line_number")] = "001"
        frame.to_csv(self._source_path("section_l02_to_l41"), index=False)
        self.assertIn(
            ("section_l_to_members", "orphan_records"),
            self._codes(validate_csv_bundle(self.root)),
        )

    def test_excel_loader_read_contract_is_unchanged(self):
        with patch("services.data_loader.pd.read_excel", return_value=pd.DataFrame()) as excel:
            with patch("services.data_loader.pd.read_csv", return_value=pd.DataFrame()) as csv:
                datasets, references = load_data(self.root)
        self.assertEqual(set(datasets), set(EXCEL_FILES))
        self.assertEqual(set(references), set(REFERENCE_FILES))
        self.assertEqual(excel.call_count, 4)
        self.assertEqual(csv.call_count, 3)
        for call in excel.call_args_list:
            self.assertEqual(call.args[0].suffix, ".xlsx")
            self.assertEqual(call.kwargs["dtype"], object)
            self.assertFalse(call.kwargs["keep_default_na"])
        for call in csv.call_args_list:
            self.assertEqual(call.kwargs["dtype"], object)
            self.assertFalse(call.kwargs["keep_default_na"])


if __name__ == "__main__":
    unittest.main()
