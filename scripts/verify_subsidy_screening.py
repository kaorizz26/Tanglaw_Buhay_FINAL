"""Check four-program screening with aggregate-only, non-identifying output."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.data_loader import load_data, verify_data
from services.subsidy_screening import PROGRAMS, STATUSES, screen_households


PREVIOUS_COUNTS = {
    "4Ps": (12082, 7151, 71), "SLP": (12770, 2385, 4149),
    "SPISC": (6339, 12965, 0), "TUPAD": (6809, 2692, 9803),
}


def evidence_counts(rows, code):
    """Return internal household set and member count; never print identifiers."""
    households, members = set(), 0
    for uuid, evidence in rows[["household_uuid", "supporting_evidence"]].itertuples(index=False, name=None):
        matching = [item for item in evidence if item.get("trigger_code") == code]
        if matching:
            households.add(uuid)
            members += sum(item["member_line_number"] is not None for item in matching)
    return households, members


def _program_status_totals(results, household_count):
    """Validate one result per program/household and count the three statuses."""
    totals = []
    for program in PROGRAMS:
        rows = results.loc[results["program_code"].eq(program)]
        counts = tuple(int(rows["screening_status"].eq(status).sum()) for status in STATUSES)
        if sum(counts) != household_count or rows["household_uuid"].duplicated().any():
            raise ValueError("Each household must have one result per program.")
        if program == "SPISC" and counts != PREVIOUS_COUNTS[program]:
            raise ValueError("SPISC screening statuses must retain the historical baseline.")
        totals.append((program, *counts))
    return totals


def _slp_pathway_counts(flagged):
    """Count EF, MD and their overlap without double-counting household paths."""
    pathway_counts = {path: 0 for path in ("EF", "MD")}
    both_pathways = 0
    slp_evidence = flagged.loc[flagged["program_code"].eq("SLP"), "supporting_evidence"]
    for evidence in slp_evidence:
        household_paths = {item["pathway"] for item in evidence}
        for path in household_paths:
            pathway_counts[path] += 1
        if household_paths == {"EF", "MD"}:
            both_pathways += 1
    return pathway_counts, both_pathways


def _warning_counts(results):
    """Count informational warning events by program and code."""
    counts_by_program = {}
    for program in ("4Ps", "SPISC", "TUPAD"):
        counts_by_code = {}
        program_warnings = results.loc[results["program_code"].eq(program), "data_quality_warnings"]
        for warnings in program_warnings:
            for warning in warnings:
                code = warning["code"]
                counts_by_code[code] = counts_by_code.get(code, 0) + 1
        counts_by_program[program] = counts_by_code
    return counts_by_program


def main() -> int:
    try:
        datasets, references = load_data()
        integrity = verify_data(datasets, references)
        if integrity["issues"] or not integrity["loading_and_joins_passed"]:
            raise ValueError("Data loading and joins must pass before screening.")
        households = datasets["household"]
        if len(households) != 19304:
            raise ValueError("Expected 19,304 households in the pilot batch.")
        results = screen_households(datasets, survey_year=2022)
        if not results["screening_status"].isin(STATUSES).all():
            raise ValueError("Unexpected screening status.")
        totals = _program_status_totals(results, len(households))
        flagged = results.loc[results["screening_status"].eq("For Review")]
        flagged_households = int(flagged["household_uuid"].nunique())
        no_flags = len(households) - flagged_households
        pathways, both = _slp_pathway_counts(flagged)
        tupad = results.loc[results["program_code"].eq("TUPAD")]
        irregular, irregular_members = evidence_counts(tupad, "irregular_work")
        observed, observed_members = evidence_counts(tupad, "observed_nonwork")
        assumed, _ = evidence_counts(tupad, "assumed_nonwork")
        overseas_assumed, _ = evidence_counts(tupad, "assumed_overseas_work")
        if assumed or overseas_assumed:
            raise ValueError("Refined TUPAD must not generate employment assumptions.")
        tupad_flagged = set(tupad.loc[tupad["screening_status"].eq("For Review"), "household_uuid"])
        nonwork_only = tupad_flagged - irregular
        four_ps = results.loc[results["program_code"].eq("4Ps")]
        pregnancy, _ = evidence_counts(four_ps, "household_pregnancy")
        attributed_pregnancy, pregnant_members = evidence_counts(four_ps, "member_pregnancy")
        spisc = results.loc[results["program_code"].eq("SPISC")]
        pwd_context, _ = evidence_counts(spisc, "household_disability")
        senior_pwd, senior_pwd_members = evidence_counts(spisc, "senior_disability")
        spisc_warning_households = int(spisc["data_quality_warnings"].map(bool).sum())
        warning_counts = _warning_counts(results)
    except Exception as error:
        # Parser errors can contain source values; only expose the error type.
        print(f"Screening verification failed ({type(error).__name__}); check schemas, keys and routing.")
        return 1
    print("Data loading, joins, output uniqueness and totals: PASS")
    print(f"{'Program':<8} {'For Review':>12} {'No Historical CBMS Trigger':>28} {'Needs Data Review':>20}")
    for program, review, negative, unknown in totals:
        print(f"{program:<8} {review:>12,} {negative:>28,} {unknown:>20,}")
    print("Historical Phase 5 -> current engines (For Review / No Historical CBMS Trigger / Needs Data Review)")
    for program, *counts in totals:
        previous = " / ".join(f"{value:,}" for value in PREVIOUS_COUNTS[program])
        current = " / ".join(f"{value:,}" for value in counts)
        print(f"{program}: {previous} -> {current}")
    print("Each program totals 19,304; 77,216 household/program results. Survey year: 2022.")
    print(f"Unique households with at least one supported flag: {flagged_households:,} ({100 * flagged_households / len(households):.2f}%).")
    print(f"Households with no supported program flags: {no_flags:,} ({100 * no_flags / len(households):.2f}%).")
    print(f"SLP supported pathways: EF={pathways['EF']:,}, MD={pathways['MD']:,}, both={both:,} households.")
    print(f"TUPAD recorded B06=7 irregular-work signals: {len(irregular):,} households / {irregular_members:,} members.")
    print(f"TUPAD recorded B06=7 E01=2 signals: {len(observed):,} households / {observed_members:,} members.")
    print(f"TUPAD nonwork reviews beyond irregular-work signals: {len(nonwork_only):,}; "
          f"both recorded triggers={len(observed & irregular):,} households.")
    print(f"4Ps household pregnancy signals: {len(pregnancy):,}; provisional member attribution: {pregnant_members:,} members in {len(attributed_pregnancy):,} households.")
    print(f"SPISC household disability context: {len(pwd_context):,} households; provisional senior-specific support: {senior_pwd_members:,} seniors in {len(senior_pwd):,} households.")
    print(f"SPISC unresolved/contradictory disability support: {spisc_warning_households:,} households; age-based statuses are unchanged.")
    for program, counts in warning_counts.items():
        print(f"{program} informational warning counts: {counts}")
    print("L07/L16 Yes/No coding is an approved provisional dataset interpretation, not PSA-confirmed recoding; household corroboration and valid member joins are required.")
    print("Pregnancy and disability remain historical; household disability is never automatically attributed to a senior.")
    print("TUPAD uses recorded responses for B06=7 adults only; no employment assumptions. SLP uses recorded responses for B06=7 members only. MPI results and synthetic E15 never become screening evidence.")
    print("Recorded nonwork needs no E11/E18; missing essential answers remain unresolved unless another member triggers review. SLP E01=2 excludes MD and needs no E18; working-member E18 is essential for MD.")
    print("Broad flag coverage can reflect age-only triggers and the union of four programs; it does not indicate official eligibility.")
    print("Refined SLP/TUPAD results are not the historical stored Run 2 findings.")
    print("SLP MC 003 s.2025 pp.8-9 and TUPAD DO 239-23 section 8 establish the implemented age floors.")
    print("SPISC MC 022 s.2026 and TUPAD DO 239-E s.2026 are listed but full texts remain unverified; newer exclusions are not invented.")
    print("No household names, UUIDs or individual records printed; no household-level results exported.")
    print("Official conditions are informational only. No assigned tasks, eligibility decisions, MPI calculations or automatic GWR flags.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
