"""Decode displayed source cells from same-batch references without scoring."""

from decimal import Decimal, InvalidOperation


def code_key(value):
    """Normalize numeric reference tokens for matching, retaining raw values elsewhere."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text or text.upper() == "NA":
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return text
    if not number.is_finite() or number != number.to_integral_value():
        return None
    return str(int(number))


def reference_index(references):
    """Index references without silently choosing among conflicting duplicate rows."""
    index = {"definitions": {}, "codebook": {}, "mpi_mapping": {}}
    for row in references.get("definitions", ()):
        key = (row.get("dataset"), row.get("variable_name"))
        index["definitions"].setdefault(key, []).append(row)
    for name in ("codebook", "mpi_mapping"):
        for row in references.get(name, ()):
            key = (row.get("variable_name"), code_key(row.get("code")))
            index[name].setdefault(key, []).append(row)
    return index


def _one_value(rows, key):
    values = {str(row[key]).strip() for row in rows if row.get(key) not in (None, "", "—")}
    return next(iter(values)) if len(values) == 1 else None


def _quantity_meaning(field, raw):
    """Explain scalar quantities and ages without assigning deprivation outcomes."""
    try:
        number = Decimal(str(raw).strip())
    except InvalidOperation:
        return "Invalid recorded number; interpretation needs verification."
    if not number.is_finite() or number < 0:
        return "Invalid recorded number; interpretation needs verification."
    if field == "a07_age":
        if number != number.to_integral_value():
            return "Age is not a completed-year value; interpretation needs verification."
        return f"{int(number)} years old at the historical survey."
    if field == "r07_floor_area":
        return "Recorded floor area in square metres."
    return "Recorded number of items; a positive quantity means ownership."


def evidence_cell(field, raw, dataset, index):
    """Return raw response, decoded label and reference provenance for one field."""
    definitions = index["definitions"].get((dataset, field), ())
    key = (field, code_key(raw))
    labels = index["codebook"].get(key, ())
    mappings = index["mpi_mapping"].get(key, ())
    label = _one_value(labels, "label")
    mapping_meaning = _one_value(mappings, "value_meaning")
    outcome = _one_value(mappings, "deprived")
    missing = raw is None or str(raw).strip().upper() in ("", "NA")
    notes = []
    if missing:
        meaning = "Not recorded or missing; applicability may also explain a skipped response."
    elif field == "l34_has_been_sick":
        meaning = {
            "1": "Affected by illness (provisional project interpretation).",
            "2": "Unaffected by illness (provisional project interpretation).",
        }.get(key[1], "Meaning requires verification under the provisional L34 interpretation.")
    elif field == "a07_age" or field == "r07_floor_area" or field.startswith("r16_"):
        meaning = _quantity_meaning(field, raw)
    elif labels and label is None:
        meaning = "Reference labels conflict or are unavailable; meaning needs verification."
    else:
        meaning = label or mapping_meaning or "No verified code meaning is available in this batch."
    if outcome in ("0", "1"):
        notes.append("Approved response mapping: " + ("Deprived" if outcome == "1" else "Not Deprived") + ".")
    elif mappings and outcome is None:
        notes.append("The approved reference does not provide one determined response mapping.")
    provenance = _one_value(definitions, "provenance")
    if provenance:
        notes.append(f"Variable-definition provenance: {provenance}.")
    if field == "e15_want_more_hours":
        notes.append("E15 is documented Synthetic Support in this pilot; it is not wholly observed CBMS.")
    if field == "l34_has_been_sick":
        notes.append("The export's Yes/No recoding is not independently confirmed by PSA.")
    return {
        "variable": field,
        "label": _one_value(definitions, "display_label") or field.replace("_", " "),
        "description": _one_value(definitions, "description"),
        "section": _one_value(definitions, "section") or field[0].upper(),
        "dataset": dataset,
        "raw": "Not present in source" if raw is None else ("Empty string" if raw == "" else str(raw)),
        "meaning": meaning, "notes": notes,
    }
