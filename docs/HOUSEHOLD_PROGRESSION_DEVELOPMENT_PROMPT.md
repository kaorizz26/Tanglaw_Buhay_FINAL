# Development Prompt: Household Progression Tracking

## Purpose and scope

Add a protected Household Progression view to Tanglaw-Buhay for comparing a household's historical CBMS assessments across survey years, initially 2022 and 2024. Reuse the existing batch-scoped source records, designated completed assessment runs, adapted MPI findings, and four independent program screening findings. Preserve all existing imports, runs, reviews, calculations, and access controls.

This module describes changes in *recorded socioeconomic conditions*. Screening findings are historical decision-support signals. They do not establish that a household applied for, was approved for, enrolled in, or received a subsidy. Do not infer program participation or the effect of a subsidy from survey changes. Application and benefit tracking require separate, authorized records and are outside this module.

## 1. Household identification

For this project, assume the source household `uuid` stays the same across survey years. Match records with the same UUID across the selected batches. This is an explicit project assumption, not independently verified cross-survey identity matching. Do not match individual household members across years solely by roster line number.

Keep unmatched records available:

- Present in the earlier year only: show the earlier assessment and "No later assessment available."
- Present in the later year only: show the later assessment and "No earlier assessment available."
- Present in both years: compare only where the relevant findings are available and methodologically comparable.

Do not interpret a missing survey record as relocation, inactivity, household dissolution, or a change in subsidy status.

## 2. Select reproducible historical assessments

Every comparison must identify its two survey years, batch IDs, and designated completed assessment run IDs. Read the stored findings from those runs; do not rerun or modify the MPI or screening engines to produce a comparison. Use the active batch as the default later endpoint when appropriate, while allowing authorized staff to select available historical years. If more than one approved batch exists for a year, make the batch choice explicit or use an administrator-designated comparison batch; never silently choose the newest import or run.

The household search must include authorized historical batches so that households present only in an older batch remain findable. Switching the active batch must not erase historical comparisons or findings.

## 3. Overall MPI score comparison

Show each year's stored `vulnerability_score` on its existing 0–100 scale. When the two assessments are comparable, calculate:

`score_change = later_score - earlier_score`

Classify a negative change as **Improved**, a positive change as **Worsened**, and an exact zero change as **Unchanged**. These labels describe the adapted household vulnerability score, not an official poverty classification or a proven change in overall welfare.

Use **Insufficient Data** for the overall classification if either year's assessment is missing, either year has an unknown MPI indicator, or the MPI indicator definitions, weights, thresholds, source mappings, or missing-data procedures are not confirmed comparable. In that case, show the available scores separately with a clear data-quality note, but do not display a score difference or claim overall improvement or deterioration. This conservative rule prevents unknown indicators, which currently contribute zero points, from creating a misleading improvement.

Do not compare batch-wide vulnerability ranks as a measure of household improvement. Ranks depend on the other households assessed in each batch.

## 4. All 16 indicator changes

Compare all 16 existing adapted MPI indicators, grouped under the application's current dimensions: **Education**, **Health and Nutrition**, **Housing, Water and Sanitation**, and **Employment**. Use the existing indicator names and stored per-year findings. Do not introduce new indicators, weights, thresholds, or interpretations.

For each indicator, show its understandable recorded condition in each year where supported, its deprivation finding, a change label, and a short factual explanation. Classify **Deprived → Not Deprived** as Improved, **Not Deprived → Deprived** as Worsened, and the same known deprivation finding in both years as Unchanged deprivation status. If either finding is unknown, missing, or methodologically incomparable, use Insufficient Data for that indicator. Where reliable source evidence permits, describe a raw-condition change that did not change the deprivation finding; do not call it an indicator improvement or deterioration.

Keep explanations at the household level unless a person's identity is independently supported across years. Never claim that the same child or adult changed status merely because member line numbers or household-level findings differ. Preserve uncertain, supplemental, and derived evidence as such; do not present it as directly observed or PSA-verified.

## 5. Priority and screening history

Show each year's project **Primary Priority**, **Secondary Priority**, or **No Subsidy Priority** classification only when supported by that year's stored score and four screening findings under the existing priority rule. Describe a priority change as a change in project prioritization, not an official poverty or eligibility classification.

Show historical screening findings separately for **4Ps**, **SLP**, **SPISC**, and **TUPAD**, using the system's existing stored statuses: **For Review**, **No Historical CBMS Trigger**, and **Needs Data Review**. Staff-facing labels may follow the current UI specification. Do not use labels such as "eligible," "approved," "enrolled," or "receiving assistance." Do not treat a screening change as an application or benefit event. Keep historical SPISC survey findings distinct from any present-day age-based guidance.

## 6. Staff interface

Integrate the view with existing protected Household Records and assessment navigation. Allow authorized staff to search by household UUID and select available comparison years. Show:

- The two selected survey years and assessment contexts.
- Side-by-side scores, data quality, project priority classifications, and an overall comparison status where valid.
- A chronological chart of available stored household scores on the 0–100 scale, with no invented or interpolated years.
- A filterable table of all 16 indicator changes: All, Improved, Worsened, Unchanged, and Insufficient Data.
- A short summary generated from actual comparison results, with accurate counts of each indicator status and a mention of important worsening even when the overall score improves.
- Separate historical program screening findings, with a clear statement that they are not application or benefit records.

Use text alongside color for every status, support keyboard navigation, and follow `docs/UI_SPEC.md` for responsive staff pages. Use clear messages for absent or incomparable years without displaying a false progression status.

## 7. Future survey years

Support comparisons between any two available, approved survey years, including nonconsecutive years. Show available assessments chronologically. A missing intervening year creates a gap, not an interpolated value or inferred event. Preserve all earlier batches and runs.

## 8. Verification and data handling

Use synthetic fixtures for development and tests. If an authorized 2024 assessed batch is unavailable, show an empty or unavailable state for real users; demonstration data must remain clearly separated from real CBMS findings. Never commit private household or member data or household-level comparison exports.

Verify at least: a matched household with complete comparable findings; score improvement, worsening, and equality; each indicator transition; unknown indicators; incompatible MPI methodology; a household in only one year; multiple batches or runs for one year; a gap in survey years; screening-status changes; active-batch switch-back; and authorized access. Comparison must not mutate source records, stored findings, staff reviews, or existing calculation rules.

## Completion standard

Authorized staff can find a household across available historical batches, inspect its stored assessments, understand valid changes in the adapted MPI score and all 16 indicators, see historical priority and screening findings separately, and recognize when a comparison cannot be made. The module must not claim poverty classification, subsidy eligibility, application, approval, receipt, or causal impact.
