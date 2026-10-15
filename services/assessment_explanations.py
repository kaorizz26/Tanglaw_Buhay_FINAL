"""Explain stored MPI findings from evidence without assigning assessment flags.

Specific reasons are emitted only when the evidence supports the stored status.
The finalized engines, adapted rules and same-batch mappings remain authoritative.
"""

from decimal import Decimal, InvalidOperation

from services.assessment_evidence_view import (
    DISPLAY_NAMES, FIELD_LABELS, STAFF_EXPLANATIONS, _display_value, direct_condition,
)
from services.assessment_reference_labels import code_key
from services.vulnerability.housing import (
    COMMUNICATION_ASSETS, DURABLE_ASSETS, MOBILITY_ASSETS,
)


def _cell(cells, field):
    return next((cell for cell in cells if cell["variable"] == field), None)


def _code(cells, field):
    cell = _cell(cells, field)
    return code_key(cell["raw"]) if cell else None


def _number(raw):
    try:
        number = Decimal(str(raw))
    except InvalidOperation:
        return None
    return number if number.is_finite() and number >= 0 else None


def _age(member):
    age = _number(member["age"])
    return int(age) if age is not None and age == age.to_integral_value() else None


def _mapped(cell, reference):
    """Read one unambiguous existing mapping; never interpret an absent answer."""
    if not cell or code_key(cell["raw"]) is None:
        return None
    rows = reference["mpi_mapping"].get((cell["variable"], code_key(cell["raw"])), ())
    outcomes = {code_key(row.get("deprived")) for row in rows}
    return int(next(iter(outcomes))) if outcomes in ({"0"}, {"1"}) else None


def _readable(cell):
    return cell is not None and _display_value(cell) not in ("Not recorded", "Meaning unclear")


def _list(items):
    return " and ".join(items) if len(items) < 3 else ", ".join(items[:-1]) + " and " + items[-1]


def _gaps(cells, reference):
    missing = [FIELD_LABELS.get(cell["variable"], cell["label"])
               for cell in cells if _mapped(cell, reference) is None]
    if missing:
        return f"The recorded answers for {_list(missing)} are missing or unclear, so this result cannot be established."
    return None


def _direct(name, flag, evidence, reference):
    cells = evidence["household_cells"]
    if not cells:
        return None
    cell = cells[0]
    mapped = _mapped(cell, reference)
    if flag is None:
        return _gaps(cells, reference) if mapped is None else None
    if mapped != flag or not _readable(cell):
        return None
    # Keep the already clear decoded water/tenure/access wording.
    finding = direct_condition(name, flag, evidence)
    if name == "Water":
        return finding
    status = "Deprived" if flag else "Not Deprived"
    if name in ("Electricity", "Internet", "Public Transportation"):
        label = "Electricity access" if name == "Electricity" else FIELD_LABELS[cell["variable"]]
        return f"{label} is recorded as {_display_value(cell)}, so this indicator is {status}."
    return f"{finding} This recorded condition accounts for the {status} result."


def _components(name, flag, evidence, reference):
    cells = evidence["household_cells"]
    components = [(cell, _mapped(cell, reference)) for cell in cells]
    causes = [cell for cell, result in components if result == 1]
    if flag is None:
        return _gaps(cells, reference) if not causes else None
    if flag == 1:
        selected = causes
    else:
        selected = cells if components and all(result == 0 for _, result in components) else []
    if not selected or not all(_readable(cell) for cell in selected):
        return None
    descriptions = []
    for cell in selected:
        value = _display_value(cell)
        if cell["variable"] == "q19_share_toilet" and value in ("Yes", "No"):
            descriptions.append("the toilet is shared with other households" if value == "Yes"
                                else "the toilet is not shared with other households")
        else:
            label = FIELD_LABELS.get(cell["variable"], cell["label"])
            descriptions.append(f"{label.lower()}: {value}")
    condition = _list(descriptions)
    if flag:
        return f"The Deprived finding is explained by the recorded conditions: {condition}."
    subject = "toilet conditions" if name == "Sanitation" else "housing materials"
    return f"Recorded {subject}: {condition}. None is classified as a condition of concern."


def _assets(flag, evidence):
    cells = evidence["household_cells"]
    groups = []
    for label, fields in (("communication items", COMMUNICATION_ASSETS),
                          ("durable household items", DURABLE_ASSETS),
                          ("transportation items", MOBILITY_ASSETS)):
        quantities = [_number((_cell(cells, field) or {}).get("raw")) for field in fields]
        owned = True if any(q is not None and q > 0 for q in quantities) else (
            False if all(q == 0 for q in quantities) else None
        )
        groups.append((label, owned))
    communication, durable, transport = [owned for _, owned in groups]
    if flag == 0 and transport is True:
        return "A transportation item is recorded as owned, which supports the Not Deprived assets result."
    if flag == 0 and communication is True and durable is True:
        return ("Both communication and durable household items are recorded as owned, "
                "supporting the Not Deprived assets result.")
    if flag == 1 and transport is False and (communication is False or durable is False):
        absent = [label for label, owned in groups[:2] if owned is False]
        return (f"No transportation items are recorded as owned, and no {_list(absent)} "
                "are recorded as owned. These absences explain the Deprived result.")
    if (flag is None and transport is not True
            and not (communication is True and durable is True)
            and not (transport is False and (communication is False or durable is False))):
        unclear = [label for label, owned in groups if owned is None]
        if unclear:
            return (f"Quantities of {_list(unclear)} are missing or unclear, "
                    "so household ownership cannot establish this result.")
    return None


def _space(flag, evidence):
    cells, count = evidence["household_cells"], evidence["roster_count"]
    area = _number(cells[0]["raw"]) if cells else None
    if flag is None:
        if not count:
            return "Household member records are unavailable, so living space per person cannot be established."
        if area is None:
            return "Floor area is missing or unclear, so living space per person cannot be established."
    elif area is not None and count and (area < 4 * count) == bool(flag):
        comparison = "below" if flag else "at least"
        return (f"The recorded home has {area:g} m² for {count} members "
                f"({area / count:.2f} m² per person), {comparison} the minimum of 4 m² per person.")
    return None


def _food(flag, evidence):
    codes = [code_key(cell["raw"]) for cell in evidence["household_cells"]]
    if len(codes) != 8:
        return None
    yes, unclear = codes.count("1"), sum(code not in ("1", "2") for code in codes)
    if flag == 1 and yes >= 4:
        return (f"The household reported {yes} of eight food-access difficulties "
                "because money or other resources for food were insufficient.")
    if flag == 0 and yes + unclear < 4:
        if unclear:
            return (f"Only {yes} food-access difficulties were reported; even allowing for "
                    f"{unclear} unclear answers, these do not reach a Deprived result.")
        return f"The household reported {yes} of eight food-access difficulties, below the level used for a Deprived result."
    if flag is None and yes < 4 <= yes + unclear:
        return (f"The household reported {yes} food-access difficulties, "
                f"but {unclear} missing or unclear answers could change the result.")
    return None


def _member_gaps(members, fields):
    """Name actual response gaps without turning them into negative answers."""
    if any(_age(member) is None for member in members):
        return "A member’s age is missing or unclear, so the relevant age group cannot be established."
    for field, label, allowed in fields:
        if any(_code(member["cells"], field) not in allowed for member in members):
            return f"{label} is missing or unclear for a relevant member, so this result cannot be established."
    return None


def _education(name, flag, evidence):
    members = evidence["members"]
    if not members:
        if evidence["roster_missing"]:
            if flag is None:
                return ("Household member records are unavailable, so the education "
                        "finding cannot be explained from member answers.")
            return None
        if name == "School Attendance" and flag == 0:
            return "No household members aged 5–17 were recorded, so this indicator is Not Deprived."
        if name == "Educational Attainment" and flag == 1:
            return "No household members aged 19 or older were recorded, so this indicator is Deprived."
        return None
    if name == "School Attendance":
        nonattending = [m for m in members if _age(m) is not None
                        and 5 <= _age(m) <= 17
                        and _code(m["cells"], "c03_currently_attending_school") == "2"]
        if flag == 1 and nonattending:
            member = nonattending[0]
            extra = f" and {len(nonattending) - 1} other members aged 5–17" if len(nonattending) > 1 else ""
            verb = "were" if extra else "was"
            return (f"{member['name']} (age {_age(member)}){extra} {verb} recorded as "
                    "not attending school, explaining the Deprived result.")
        if flag == 0 and all(
            _age(m) is not None and _code(m["cells"], "c03_currently_attending_school") == "1"
            for m in members
        ):
            if len(members) == 1:
                return "The recorded member aged 5–17 is attending school, so this indicator is Not Deprived."
            return f"All {len(members)} recorded members aged 5–17 are attending school, so this indicator is Not Deprived."
        if flag is None and not nonattending:
            relevant = [m for m in members if _code(m["cells"], "c03_currently_attending_school") != "1"]
            return _member_gaps(relevant, (("c03_currently_attending_school", "School attendance", {"1", "2"}),))
        return None
    completed, incomplete = [], []
    for member in members:
        age, code = _age(member), _code(member["cells"], "c02_hgc_group")
        if age is None or code not in {str(n) for n in range(1, 18)}:
            continue
        (completed if int(code) >= 7 or (code == "5" and age >= 25) else incomplete).append(member)
    if flag == 0 and completed:
        member = completed[0]
        cell = _cell(member["cells"], "c02_hgc_group")
        if _readable(cell):
            return (f"{member['name']} (age {_age(member)}) has recorded education of "
                    f"{_display_value(cell)}, supporting the assessment’s Not Deprived result.")
    if flag == 1 and len(incomplete) == len(members):
        return "No recorded member aged 19 or older meets the assessment’s secondary-education requirement."
    if flag is None and not completed:
        possible = [m for m in members if _code(m["cells"], "c02_hgc_group") not in ("1", "2", "3", "4", "6")]
        return _member_gaps(possible, (("c02_hgc_group", "Highest-grade information", {str(n) for n in range(1, 18)}),))
    return None


def _illness(flag, evidence):
    household = _code(evidence["household_cells"], "l33_member_who_has_been_sick")
    members = evidence["members"]
    if flag == 0 and household == "2":
        return "No household illness, sickness or injury was reported in the month before the survey."
    if flag is None and household not in ("1", "2"):
        return "The household illness answer is missing or unclear, so this result cannot be established."
    if household != "1":
        return None
    if flag is None and not members:
        return "Household member illness records are unavailable, so this result cannot be established."
    affected = [m for m in members if not m["health_missing"]
                and _code(m["cells"], "l34_has_been_sick") == "1"]
    untreated = [m for m in affected if _code(m["cells"], "l38_availed_medical_treatment") == "2"]
    if flag == 1 and untreated:
        return f"{untreated[0]['name']} was recorded as affected by illness and as not receiving medical treatment."
    conflicts = [m for m in members if any("conflicting follow-up" in note for note in m["notes"])]
    missing = [m for m in members if m["health_missing"]
               or _code(m["cells"], "l34_has_been_sick") not in ("1", "2")]
    treatment_gaps = [m for m in affected if _code(m["cells"], "l38_availed_medical_treatment") not in ("1", "2")]
    if flag == 0 and affected and not (untreated or conflicts or missing or treatment_gaps):
        return "Every member recorded as affected by illness also has a recorded Yes for medical treatment."
    if flag is None and not untreated:
        if conflicts:
            return ("Recorded member illness and treatment answers conflict, "
                    "so a clear household result cannot be established.")
        if missing:
            return ("Member illness answers or follow-up records are missing or unclear, "
                    "so the household result cannot be established.")
        if treatment_gaps:
            return (f"Medical treatment is not established for {treatment_gaps[0]['name']}, "
                    "who was recorded as affected by illness.")
    return None


def _adult_work(name, flag, evidence):
    """Describe complete applicable populations; leave uncertain shares unattributed."""
    members = evidence["members"]
    if evidence["roster_missing"] or any(_age(m) is None for m in members):
        return _member_gaps(members, ()) if flag is None and members else None
    eligible, unresolved = [], []
    for member in members:
        code = _code(member["cells"], "b06_ofi")
        work = _code(member["cells"], "e01_employed")
        if name == "Employment Proxy" and code in ("1", "2", "3"):
            if any("assumed-working" in note for note in member["notes"]):
                eligible.append((member, "1"))
            else:
                unresolved.append(member)
        elif code in ("1", "2", "3", "6"):
            continue
        elif name == "Underemployment Proxy" and work == "2":
            continue
        elif code not in ("4", "5", "7") or work not in ("1", "2"):
            unresolved.append(member)
        elif name == "Employment Proxy" or work == "1":
            eligible.append((member, work))
    if unresolved:
        if flag is None:
            if any("routing conflict" in note for m in unresolved for note in m["notes"]):
                return ("Recorded work and member-status answers conflict, "
                        "so the household employment result cannot be established.")
            return _member_gaps(unresolved, (
                ("b06_ofi", "Member-status information", {str(n) for n in range(1, 8)}),
                ("e01_employed", "Past-week work information", {"1", "2"}),
            ))
        return None
    if not eligible:
        if flag == 0:
            population = "working adults" if name == "Underemployment Proxy" else "adults"
            return f"No applicable {population} aged 18–65 were identified, so this indicator is Not Deprived."
        return None
    if name == "Employment Proxy":
        count = sum(work == "2" for _, work in eligible)
        description = "classified by the assessment as not working"
    else:
        codes = [_code(member["cells"], "e15_want_more_hours") for member, _ in eligible]
        if any(code not in ("1", "2") for code in codes):
            if flag is None:
                return ("Additional-hours answers are missing or unclear for working adults, "
                        "so this result cannot be established.")
            return None
        count = codes.count("1")
        description = "recorded as wanting more work hours"
    if flag is not None and (count * 2 > len(eligible)) == bool(flag):
        share = "more than half" if flag else "half or fewer"
        population = "working adult" if name == "Underemployment Proxy" else "adult"
        if len(eligible) != 1:
            population += "s"
        return (f"Of {len(eligible)} applicable {population} aged 18–65, {count} "
                f"{'is' if count == 1 else 'are'} {description} ({share}), explaining this result.")
    return None


def _working_child(flag, evidence):
    members = evidence["members"]
    if not members and not evidence["roster_missing"] and flag == 0:
        return "No household members aged 5–17 were recorded, so this indicator is Not Deprived."
    concerning, excluded, unresolved = [], [], []
    for member in members:
        age = _age(member)
        work = _code(member["cells"], "e01_employed")
        school = _code(member["cells"], "c03_currently_attending_school")
        status = _code(member["cells"], "b06_ofi")
        if work == "2" or school == "1" or (age is not None and age >= 15 and status in ("1", "2", "3", "6")):
            excluded.append(member)
        elif age is not None and (age < 15 or status in ("4", "5", "7")) and work == "1" and school == "2":
            concerning.append(member)
        else:
            unresolved.append(member)
    if flag == 1 and concerning:
        member = concerning[0]
        return f"{member['name']} (age {_age(member)}) was recorded as working in the past week and not attending school."
    if flag == 0 and members and not (concerning or unresolved):
        if any(_age(member) is None for member in members):
            return ("Recorded work or school answers rule out the combined condition of "
                    "working and nonattendance for every potentially school-age member.")
        return ("Recorded children are attending school, not working, or outside the "
                "applicable work group; none is established as both working and out of school.")
    if flag is None and not concerning:
        return _member_gaps(unresolved, (
            ("e01_employed", "Past-week work information", {"1", "2"}),
            ("c03_currently_attending_school", "School attendance", {"1", "2"}),
            ("b06_ofi", "Member-status information", {str(n) for n in range(1, 8)}),
        ))
    return None


def explain_indicator(name, flag, evidence, reference):
    """Return a supported reason, never replace or recompute a stored MPI flag."""
    if (evidence["roster_missing"] and evidence["member_based"] and flag is None
            and name != "Illness/Treatment Proxy"):
        return "Household member records are unavailable, so this result cannot be established."
    if name in ("Water", "Tenure", "Electricity", "Internet", "Public Transportation"):
        reason = _direct(name, flag, evidence, reference)
    elif name in ("Housing Materials", "Sanitation"):
        reason = _components(name, flag, evidence, reference)
    elif name == "Assets":
        reason = _assets(flag, evidence)
    elif name == "Overcrowding":
        reason = _space(flag, evidence)
    elif name == "Food Insecurity":
        reason = _food(flag, evidence)
    elif name in ("School Attendance", "Educational Attainment"):
        reason = _education(name, flag, evidence)
    elif name == "Illness/Treatment Proxy":
        reason = _illness(flag, evidence)
    elif name in ("Employment Proxy", "Underemployment Proxy"):
        reason = _adult_work(name, flag, evidence)
    else:
        reason = _working_child(flag, evidence)
    if reason:
        return reason
    if flag is None:
        return f"The available information does not establish a clear result for {DISPLAY_NAMES.get(name, name).lower()}."
    if name in ("Employment Proxy", "Underemployment Proxy"):
        return STAFF_EXPLANATIONS[name][flag]
    status, label = ("Deprived" if flag else "Not Deprived"), DISPLAY_NAMES.get(name, name)
    return (f"The assessment result for {label.lower()} is {status}; "
            "the available answers do not identify a more specific reason.")
