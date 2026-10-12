"""Check four-program screening with aggregate-only, non-identifying output."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.data_loader import load_data, verify_data
from services.subsidy_screening import PROGRAMS, STATUSES, screen_households


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
    except Exception as error:
        # Parser errors can contain source values; only expose the error type.
        print(f"Screening verification failed ({type(error).__name__}); check schemas, keys and routing.")
        return 1
    print("Data loading, joins, output uniqueness and totals: PASS")
    print(f"{'Program':<8} {'For Review':>12} {'No Historical CBMS Trigger':>28} {'Needs Data Review':>20}")
    for program, review, negative, unknown in totals:
        print(f"{program:<8} {review:>12,} {negative:>28,} {unknown:>20,}")
    print("Each program totals 19,304; 77,216 household/program results. Survey year: 2022.")
    print(f"Unique households with at least one supported flag: {flagged_households:,} ({100 * flagged_households / len(households):.2f}%).")
    print(f"Households with no supported program flags: {no_flags:,} ({100 * no_flags / len(households):.2f}%).")
    print(f"SLP supported pathways: EF={pathways['EF']:,}, MD={pathways['MD']:,}, both={both:,} households.")
    print("4Ps pregnancy flags and SPISC PWD support remain pending L07/L16 coding confirmation; only verified age triggers are used.")
    print("4Ps pregnancy absence uses verified household/demographic skips, never absence of a Section L record alone.")
    print("Employment flags require observed E01 and B06 applicability; E11/E18 are not treated as job evidence from MPI imputation or synthetic E15.")
    print("Unexported E03 job-attachment routing prevents declaring missing E11/E18 verified skips solely from E01=2.")
    print("Broad flag coverage can reflect age-only triggers and the union of four programs; it does not indicate official eligibility.")
    print("SLP can be broad because recorded non-work or enterprise work suffices for a preliminary flag. TUPAD requires recorded irregular work.")
    print("SLP MC 003 s.2025 pp.8-9 and TUPAD DO 239-23 section 8 establish the implemented age floors.")
    print("SPISC MC 022 s.2026 and TUPAD DO 239-E s.2026 are listed but full texts remain unverified; newer exclusions are not invented.")
    print("No household names, UUIDs or individual records printed; no household-level results exported.")
    print("Official conditions are informational only. No assigned tasks, eligibility decisions, MPI calculations or automatic GWR flags.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
