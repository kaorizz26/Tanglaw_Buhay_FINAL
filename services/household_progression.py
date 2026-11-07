"""Compare stored household findings across approved historical survey batches."""

from collections import Counter
from decimal import Decimal
from pathlib import Path
import re

import psycopg
from psycopg.pq import TransactionStatus

from db import get_connection
from repositories import assessment_evidence, household_progression
from services import read_cache
from services.assessment_details import _indicator
from services.assessment_evidence_view import DISPLAY_NAMES, direct_condition
from services.assessment_queries import AssessmentQueryError
from services.mpi_evidence import build_evidence_index
from services.subsidy_priority import derive_subsidy_priority
from services.subsidy_screening_content import STATUS_LABELS
from services.vulnerability.scoring import DIMENSIONS, INDICATOR_WEIGHTS


ROOT = Path(__file__).resolve().parents[1]
UUID_PREFIX = re.compile(r"[0-9a-fA-F-]{4,36}\Z")
PROGRAMS = ("4Ps", "SLP", "SPISC", "TUPAD")
CHANGE_LABELS = ("Improved", "Worsened", "Unchanged", "Insufficient Data")
DASHBOARD_CACHE_SECONDS = 1800
SEARCH_MODES = ("uuid", "name")
MAX_SEARCH_LENGTH = 100


class InvalidProgressionSelection(ValueError):
    """Reject malformed or unavailable historical comparison choices."""


def _read_snapshot(connection_factory):
    """Create one read-only snapshot for all batch and household lookups."""
    connection = connection_factory()
    if connection.info.transaction_status != TransactionStatus.IDLE:
        connection.close()
        raise AssessmentQueryError("progression_connection_not_idle")
    return connection


def search_households(prefix, *, connection_factory=get_connection):
    """Search historical designated assessments by a UUID prefix."""
    prefix = prefix.strip()
    if prefix and not UUID_PREFIX.fullmatch(prefix):
        raise InvalidProgressionSelection("invalid_household_search")
    try:
        with _read_snapshot(connection_factory) as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            batches = _label_batches(household_progression.list_ready_batches(connection))
            matches = household_progression.search_households(connection, prefix) if prefix else []
            return {"batches": batches, "matches": matches, "query": prefix}
    except AssessmentQueryError:
        raise
    except (psycopg.Error, RuntimeError):
        raise AssessmentQueryError("progression_unavailable") from None


def _search_text(raw, search_by):
    """Normalize a staff search while keeping UUID and name rules explicit."""
    if search_by not in SEARCH_MODES:
        raise InvalidProgressionSelection("invalid_search_mode")
    query = " ".join((raw or "").split())
    if len(query) > MAX_SEARCH_LENGTH or "\x00" in query:
        raise InvalidProgressionSelection("invalid_household_search")
    if query and search_by == "uuid" and not UUID_PREFIX.fullmatch(query):
        raise InvalidProgressionSelection("invalid_household_search")
    if query and search_by == "name" and len(query) < 2:
        raise InvalidProgressionSelection("invalid_household_search")
    return query


def _display_source(value, fallback="Not recorded"):
    value = value.strip() if isinstance(value, str) else ""
    return fallback if not value or value == "NA" else value


def _head_name(row):
    if row["head_count"] != 1:
        return "Not clearly recorded"
    parts = [_display_source(row[field], "")
             for field in ("first_name", "middle_name", "last_name")]
    return " ".join(part for part in parts if part) or "Not recorded"


def _present_match(row, comparison_years):
    years = list(row["years"])
    if len(comparison_years) == 2 and set(years) == set(comparison_years):
        availability = "Both years"
    elif len(years) == 1:
        availability = f"{years[0]} only"
    else:
        availability = ", ".join(map(str, years))
    return {
        "household_uuid": row["household_uuid"],
        "head_name": _head_name(row),
        "barangay": _display_source(row["barangay"]),
        "years": years,
        "availability": availability,
    }


def _valid_period_findings(findings, year):
    return (_valid_mpi(findings)
            and findings.get("survey_year") == year)


def _build_dashboard(connection, earlier, later):
    """Aggregate stored household and indicator changes for one comparison pair."""
    rows = household_progression.list_comparison_findings(
        connection, earlier["batch_id"], earlier["run_id"],
        later["batch_id"], later["run_id"],
    )
    earlier_method = _mpi_signature(connection, earlier)
    later_method = _mpi_signature(connection, later)
    method_compatible = earlier_method is not None and earlier_method == later_method
    households = Counter()
    outcomes = Counter()
    indicator_counts = {name: Counter() for name in INDICATOR_WEIGHTS}
    for row in rows:
        first, second = row["earlier_findings"], row["later_findings"]
        if first is not None:
            households["earlier"] += 1
        if second is not None:
            households["later"] += 1
        if first is None:
            households["later_only"] += 1
            continue
        if second is None:
            households["earlier_only"] += 1
            continue
        households["matched"] += 1
        first_valid = _valid_period_findings(first, earlier["survey_year"])
        second_valid = _valid_period_findings(second, later["survey_year"])
        if (method_compatible and first_valid and second_valid
                and first["mpi"]["unknown_indicator_count"] == 0
                and second["mpi"]["unknown_indicator_count"] == 0):
            score_change = (Decimal(str(second["mpi"]["vulnerability_score"]))
                            - Decimal(str(first["mpi"]["vulnerability_score"])))
            outcome = ("Improved" if score_change < 0 else
                       "Worsened" if score_change > 0 else "Unchanged")
        else:
            outcome = "Insufficient Data"
        outcomes[outcome] += 1
        for name in INDICATOR_WEIGHTS:
            if method_compatible and first_valid and second_valid:
                change = _status(first["mpi"].get(name), second["mpi"].get(name), True)
            else:
                change = "Insufficient Data"
            indicator_counts[name][change] += 1
    indicators = []
    for dimension, (_, names) in DIMENSIONS.items():
        for name in names:
            indicators.append({
                "dimension": dimension,
                "name": DISPLAY_NAMES.get(name, name),
                "counts": {label: indicator_counts[name][label]
                           for label in CHANGE_LABELS},
            })
    return {
        "households": {key: households[key] for key in
                       ("earlier", "later", "matched", "earlier_only", "later_only")},
        "outcomes": {label: outcomes[label] for label in CHANGE_LABELS},
        "comparable": households["matched"] - outcomes["Insufficient Data"],
        "indicators": indicators,
        "method_compatible": method_compatible,
    }


def _dashboard(connection, earlier, later):
    key = (
        "progression-dashboard", connection.info.dbname,
        earlier["batch_id"], earlier["run_id"],
        later["batch_id"], later["run_id"],
    )
    return read_cache.cached(
        key, DASHBOARD_CACHE_SECONDS,
        lambda: _build_dashboard(connection, earlier, later),
    )


def get_progression_index(query="", search_by="uuid", barangay="",
                          earlier_raw=None, later_raw=None,
                          *, connection_factory=get_connection):
    """Build the read-only progression dashboard and historical search page."""
    query = _search_text(query, search_by)
    barangay = (barangay or "").strip()
    if len(barangay) > 200 or "\x00" in barangay:
        raise InvalidProgressionSelection("invalid_barangay")
    try:
        with _read_snapshot(connection_factory) as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            batches = _label_batches(household_progression.list_ready_batches(connection))
            pending = household_progression.list_unready_batches(connection)
            selected = _choose_batches(batches, earlier_raw, later_raw)
            by_id = {batch["batch_id"]: batch for batch in batches}
            earlier = by_id[selected[0]] if selected else None
            later = by_id[selected[1]] if selected else None
            scope = [selected[0], selected[1]] if selected else [
                batch["batch_id"] for batch in batches
            ]
            barangays = household_progression.list_search_barangays(connection, scope)
            if barangay and barangay not in barangays:
                raise InvalidProgressionSelection("invalid_barangay")
            matches = []
            if query or barangay:
                rows = household_progression.search_progression_households(
                    connection, scope, query, search_by, barangay,
                )
                comparison_years = ([earlier["survey_year"], later["survey_year"]]
                                    if selected else [])
                matches = [_present_match(row, comparison_years) for row in rows]
            ready_years = sorted({batch["survey_year"] for batch in batches})
            pending_years = sorted({batch["survey_year"] for batch in pending
                                    if batch["survey_year"] not in ready_years})
            if selected:
                state = "ready"
            elif len(ready_years) >= 2:
                state = "choose"
            elif pending_years:
                state = "preparing"
            else:
                state = "needs_import"
            active = next((batch for batch in batches if batch["is_active"]), None)
            return {
                "batches": batches,
                "pending": pending,
                "ready_years": ready_years,
                "pending_years": pending_years,
                "active": active,
                "selected": selected,
                "earlier": earlier,
                "later": later,
                "state": state,
                "dashboard": _dashboard(connection, earlier, later) if selected else None,
                "filters": {"query": query, "search_by": search_by,
                            "barangay": barangay},
                "barangays": barangays,
                "matches": matches,
            }
    except (InvalidProgressionSelection, AssessmentQueryError):
        raise
    except (psycopg.Error, RuntimeError, ValueError):
        raise AssessmentQueryError("progression_unavailable") from None


def _batch_id(raw):
    if raw is None:
        return None
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 18 or int(raw) <= 0:
        raise InvalidProgressionSelection("invalid_batch_selection")
    return int(raw)


def _choose_batches(batches, earlier_raw, later_raw):
    """Default to the latest two unique years; require explicit duplicate-year choice."""
    by_id = {row["batch_id"]: row for row in batches}
    if earlier_raw is not None or later_raw is not None:
        if earlier_raw is None or later_raw is None:
            raise InvalidProgressionSelection("incomplete_batch_selection")
        earlier, later = _batch_id(earlier_raw), _batch_id(later_raw)
        if (earlier not in by_id or later not in by_id
                or by_id[earlier]["survey_year"] >= by_id[later]["survey_year"]):
            raise InvalidProgressionSelection("invalid_batch_selection")
        return earlier, later
    years = sorted({row["survey_year"] for row in batches})
    if len(years) < 2:
        return None
    latest, previous = years[-1], years[-2]
    later = [row for row in batches if row["survey_year"] == latest]
    earlier = [row for row in batches if row["survey_year"] == previous]
    if len(later) != 1 or len(earlier) != 1:
        return None
    return earlier[0]["batch_id"], later[0]["batch_id"]


def _label_batches(batches):
    """Give repeated imports in one year a staff-readable version label."""
    totals = Counter(batch["survey_year"] for batch in batches)
    positions = Counter()
    for batch in batches:
        year = batch["survey_year"]
        positions[year] += 1
        batch["display_label"] = (
            str(year) if totals[year] == 1 else
            f"{year} · Dataset {positions[year]} of {totals[year]}"
        )
    return batches


def _mpi_provenance(connection, batch):
    provenance = batch["calculation_provenance"]
    if not isinstance(provenance, dict):
        return None
    source_run = provenance.get("mpi_source_run_id")
    if source_run is not None:
        if type(source_run) is not int or source_run <= 0:
            return None
        provenance = household_progression.find_run_provenance(
            connection, source_run, batch["batch_id"],
        )
    return provenance


def _mpi_signature(connection, batch):
    """Require matching saved MPI code and mapping provenance, not a guessed version."""
    provenance = _mpi_provenance(connection, batch)
    if not isinstance(provenance, dict):
        return None
    hashes = provenance.get("engine_sha256", {})
    if not isinstance(hashes, dict):
        return None
    mpi_hashes = {name: digest for name, digest in hashes.items()
                  if name.startswith("services/vulnerability/")}
    required = {path.relative_to(ROOT).as_posix()
                for path in (ROOT / "services/vulnerability").glob("*.py")}
    mapping = household_progression.find_mapping_fingerprint(
        connection, batch["batch_id"],
    )
    if not required or not required.issubset(mpi_hashes) or not mapping:
        return None
    return tuple(sorted(mpi_hashes.items())), mapping


def _valid_mpi(findings):
    if not isinstance(findings, dict) or not isinstance(findings.get("mpi"), dict):
        return False
    mpi = findings["mpi"]
    flags = [mpi.get(name) for name in INDICATOR_WEIGHTS]
    score = mpi.get("vulnerability_score")
    return (all(flag is None or type(flag) is int and flag in (0, 1) for flag in flags)
            and type(score) in (int, float) and 0 <= score <= 100
            and type(mpi.get("unknown_indicator_count")) is int
            and mpi["unknown_indicator_count"] == flags.count(None))


def _source_for_indicator_details(connection, batch, findings, household_uuid):
    """Use the same batch-specific source and reference reads as assessment detail."""
    batch_id = batch["batch_id"]
    household = assessment_evidence.find_household_sources(
        connection, batch_id, household_uuid,
    )
    if household is None:
        raise AssessmentQueryError("household_source_missing")
    return {
        "context": {"survey_year": batch["survey_year"]},
        "findings": findings,
        "household": household,
        "members": assessment_evidence.list_member_sources(
            connection, batch_id, household_uuid,
        ),
        "references": assessment_evidence.list_evidence_references(
            connection, batch_id,
        ),
    }


def _present_indicators(connection, batch, findings, household_uuid):
    if findings is None:
        return {}
    source = _source_for_indicator_details(connection, batch, findings, household_uuid)
    reference = build_evidence_index(source)
    result = {}
    for name in INDICATOR_WEIGHTS:
        item = _indicator(name, findings["mpi"], source, reference)
        item["raw_condition"] = direct_condition(
            name, item["flag"], item["evidence"],
        )
        result[name] = item
    return result


def _status(before, after, comparable):
    if not comparable or before is None or after is None:
        return "Insufficient Data"
    if before == after:
        return "Unchanged"
    return "Improved" if before == 1 else "Worsened"


def _programs(findings):
    if findings is None:
        return {}
    programs = findings.get("programs", {})
    return {code: programs.get(code, {}).get("screening_status") for code in PROGRAMS}


def _period(batch, findings):
    batch = {"display_label": str(batch["survey_year"]), **batch}
    if findings is None:
        return {**batch, "findings": None, "score": None, "priority": None,
                "screening": {}}
    if not _valid_mpi(findings) or findings.get("survey_year") != batch["survey_year"]:
        raise AssessmentQueryError("invalid_historical_mpi")
    score = findings["mpi"]["vulnerability_score"]
    screening = _programs(findings)
    if any(status not in STATUS_LABELS for status in screening.values()):
        raise AssessmentQueryError("invalid_historical_screening")
    return {**batch, "findings": findings, "score": score,
            "priority": derive_subsidy_priority(score, screening),
            "screening": {code: {"status": screening[code],
                                 "label": STATUS_LABELS[screening[code]]}
                          for code in PROGRAMS}}


def _compare(connection, earlier, later, household_uuid):
    first, second = earlier["findings"], later["findings"]
    first_method = _mpi_signature(connection, earlier) if first is not None else None
    second_method = _mpi_signature(connection, later) if second is not None else None
    method_compatible = first_method is not None and first_method == second_method
    complete = (method_compatible
                and first["mpi"]["unknown_indicator_count"] == 0
                and second["mpi"]["unknown_indicator_count"] == 0)
    score_change = (Decimal(str(later["score"])) - Decimal(str(earlier["score"]))) if complete else None
    overall = ("Insufficient Data" if score_change is None else
               "Improved" if score_change < 0 else
               "Worsened" if score_change > 0 else "Unchanged")
    first_indicators = _present_indicators(connection, earlier, first, household_uuid)
    second_indicators = _present_indicators(connection, later, second, household_uuid)
    dimensions = []
    counts = Counter()
    worsened = []
    improved = []
    for dimension, (_, names) in DIMENSIONS.items():
        rows = []
        for name in names:
            before = first_indicators.get(name)
            after = second_indicators.get(name)
            change = _status(before["flag"] if before else None,
                             after["flag"] if after else None, method_compatible)
            counts[change] += 1
            if change == "Worsened":
                worsened.append(DISPLAY_NAMES.get(name, name))
            elif change == "Improved":
                improved.append(DISPLAY_NAMES.get(name, name))
            raw_change = (
                change == "Unchanged" and before.get("raw_condition")
                and after.get("raw_condition")
                and before["raw_condition"] != after["raw_condition"]
            )
            rows.append({"name": DISPLAY_NAMES.get(name, name), "change": change,
                         "before": before, "after": after,
                         "raw_change": raw_change})
        dimensions.append({"name": dimension, "indicators": rows})
    if overall == "Insufficient Data":
        summary = (f"Between {earlier['survey_year']} and {later['survey_year']}, "
                   "an overall score change cannot be determined from these assessments.")
    else:
        direction = ("decreased" if score_change < 0 else
                     "increased" if score_change > 0 else "did not change")
        summary = (f"Between {earlier['survey_year']} and {later['survey_year']}, "
                   f"the recorded vulnerability score {direction} from "
                   f"{earlier['score']:.2f} to {later['score']:.2f}.")
    if improved:
        summary += " Improved indicators include " + ", ".join(improved[:3]) + "."
    if worsened:
        summary += " Worsened indicators include " + ", ".join(worsened[:3]) + "."
    return {"overall": overall, "score_change": score_change,
            "method_compatible": method_compatible, "dimensions": dimensions,
            "counts": {label: counts[label] for label in CHANGE_LABELS},
            "summary": summary}


def get_household_progression(household_uuid, earlier_raw=None, later_raw=None,
                              *, connection_factory=get_connection):
    """Present historical periods and one selected comparison in a stable snapshot."""
    try:
        with _read_snapshot(connection_factory) as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            batches = _label_batches(household_progression.list_ready_batches(connection))
            history = household_progression.find_history(connection, household_uuid)
            if not history:
                raise AssessmentQueryError("household_progression_missing")
            selected = _choose_batches(batches, earlier_raw, later_raw)
            findings_by_batch = {row["batch_id"]: row["findings"] for row in history}
            periods = [_period(batch, findings_by_batch.get(batch["batch_id"]))
                       for batch in batches]
            by_id = {period["batch_id"]: period for period in periods}
            comparison = (_compare(connection, by_id[selected[0]], by_id[selected[1]],
                                   household_uuid) if selected else None)
            return {"household_uuid": household_uuid, "periods": periods,
                    "available": [period for period in periods if period["findings"]],
                    "selected": selected, "earlier": by_id[selected[0]] if selected else None,
                    "later": by_id[selected[1]] if selected else None,
                    "comparison": comparison}
    except AssessmentQueryError:
        raise
    except (psycopg.Error, RuntimeError):
        raise AssessmentQueryError("progression_unavailable") from None
