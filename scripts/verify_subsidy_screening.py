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
        totals = []
        for program in PROGRAMS:
            rows = results.loc[results["program_code"].eq(program)]
            counts = tuple(int(rows["screening_status"].eq(status).sum()) for status in STATUSES)
            if sum(counts) != len(households) or rows["household_uuid"].duplicated().any():
                raise ValueError("Each household must have one result per program.")
            if program in ("SLP", "SPISC") and counts != PREVIOUS_COUNTS[program]:
                raise ValueError("Phase 5B must preserve SLP and SPISC screening statuses.")
            totals.append((program, *counts))
        flagged = results.loc[results["screening_status"].eq("For Review")]
        flagged_households = int(flagged["household_uuid"].nunique())
        no_flags = len(households) - flagged_households
        pathways = {path: 0 for path in ("EF", "MD")}
        both = 0
        for evidence in flagged.loc[flagged["program_code"].eq("SLP"), "supporting_evidence"]:
            paths = {item["pathway"] for item in evidence}
            for path in paths:
                pathways[path] += 1
            both += paths == {"EF", "MD"}
        tupad = results.loc[results["program_code"].eq("TUPAD")]
        irregular, irregular_members = evidence_counts(tupad, "irregular_work")
        observed, observed_members = evidence_counts(tupad, "observed_nonwork")
        assumed, assumed_members = evidence_counts(tupad, "assumed_nonwork")
        tupad_flagged = set(tupad.loc[tupad["screening_status"].eq("For Review"), "household_uuid"])
        new_tupad = tupad_flagged - irregular
        if len(irregular) != PREVIOUS_COUNTS["TUPAD"][0]:
            raise ValueError("Original irregular-work flags must remain unchanged.")
        four_ps = results.loc[results["program_code"].eq("4Ps")]
        pregnancy, _ = evidence_counts(four_ps, "household_pregnancy")
        attributed_pregnancy, pregnant_members = evidence_counts(four_ps, "member_pregnancy")
        spisc = results.loc[results["program_code"].eq("SPISC")]
        pwd_context, _ = evidence_counts(spisc, "household_disability")
        senior_pwd, senior_pwd_members = evidence_counts(spisc, "senior_disability")
        spisc_warning_households = int(spisc["data_quality_warnings"].map(bool).sum())
        warning_counts = {}
        for program in ("4Ps", "SPISC", "TUPAD"):
            codes = {}
            for warnings in results.loc[results["program_code"].eq(program), "data_quality_warnings"]:
                for warning in warnings:
                    code = warning["code"]
                    codes[code] = codes.get(code, 0) + 1
            warning_counts[program] = codes
    except Exception as error:
        # Parser errors can contain source values; only expose the error type.
        print(f"Screening verification failed ({type(error).__name__}); check schemas, keys and routing.")
        return 1
    print("Data loading, joins, output uniqueness and totals: PASS")
    print(f"{'Program':<8} {'For Review':>12} {'No Historical CBMS Trigger':>28} {'Needs Data Review':>20}")
    for program, review, negative, unknown in totals:
        print(f"{program:<8} {review:>12,} {negative:>28,} {unknown:>20,}")
    print("Phase 5 -> Phase 5B (For Review / No Historical CBMS Trigger / Needs Data Review)")
    for program, *counts in totals:
        previous = " / ".join(f"{value:,}" for value in PREVIOUS_COUNTS[program])
        current = " / ".join(f"{value:,}" for value in counts)
        print(f"{program}: {previous} -> {current}")
    print("Each program totals 19,304; 77,216 household/program results. Survey year: 2022.")
    print(f"Unique households with at least one supported flag: {flagged_households:,} ({100 * flagged_households / len(households):.2f}%).")
    print(f"Households with no supported program flags: {no_flags:,} ({100 * no_flags / len(households):.2f}%).")
    print(f"SLP supported pathways: EF={pathways['EF']:,}, MD={pathways['MD']:,}, both={both:,} households.")
    print(f"TUPAD existing irregular-work flags: {len(irregular):,} households / {irregular_members:,} members.")
    print(f"TUPAD observed E01=2 signals: {len(observed):,} households / {observed_members:,} members; assumed missing-E01 nonwork: {len(assumed):,} households / {assumed_members:,} members.")
    print(f"New TUPAD reviews beyond the existing irregular-work flags: {len(new_tupad):,}; "
          f"observed-only={len(new_tupad & (observed - assumed)):,}, "
          f"assumed-only={len(new_tupad & (assumed - observed)):,}, "
          f"both={len(new_tupad & observed & assumed):,}.")
    print(f"4Ps household pregnancy signals: {len(pregnancy):,}; provisional member attribution: {pregnant_members:,} members in {len(attributed_pregnancy):,} households.")
    print(f"SPISC household disability context: {len(pwd_context):,} households; provisional senior-specific support: {senior_pwd_members:,} seniors in {len(senior_pwd):,} households.")
    print(f"SPISC unresolved/contradictory disability support: {spisc_warning_households:,} households; age-based statuses are unchanged.")
    for program, counts in warning_counts.items():
        print(f"{program} informational warning counts: {counts}")
    print("L07/L16 Yes/No coding is an approved provisional dataset interpretation, not PSA-confirmed recoding; household corroboration and valid member joins are required.")
    print("Pregnancy and disability remain historical; household disability is never automatically attributed to a senior.")
    print("TUPAD derives its own approved pilot E01 assumption; SLP still uses observed responses only. MPI results and synthetic E15 never become screening evidence.")
    print("Known nonwork triggers override missing E11; actual routing conflicts remain visible. Unexported E03 still limits SLP work-history interpretation.")
    print("Broad flag coverage can reflect age-only triggers and the union of four programs; it does not indicate official eligibility.")
    print("SLP retains its original EF/MD rules. TUPAD coverage increases because observed and assumed nonwork are now approved additional review signals.")
    print("SLP MC 003 s.2025 pp.8-9 and TUPAD DO 239-23 section 8 establish the implemented age floors.")
    print("SPISC MC 022 s.2026 and TUPAD DO 239-E s.2026 are listed but full texts remain unverified; newer exclusions are not invented.")
    print("No household names, UUIDs or individual records printed; no household-level results exported.")
    print("Official conditions are informational only. No assigned tasks, eligibility decisions, MPI calculations or automatic GWR flags.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
