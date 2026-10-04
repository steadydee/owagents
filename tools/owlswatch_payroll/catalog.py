"""Single source of truth for Nómina's model-visible tool contracts."""

ID = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$"}
PERIOD = {"type": "string", "pattern": "^20[0-9]{2}-(0[1-9]|1[0-2])-H[12]$"}
DATE = {"type": "string", "pattern": "^20[0-9]{2}-(0[1-9]|1[0-2])-[0-3][0-9]$"}
TEXT = {"type": "string", "minLength": 1, "maxLength": 500}
MONEY = {"type": "integer", "minimum": 0, "maximum": 1000000000000}
POSITIVE = {**MONEY, "minimum": 1}
REQUEST = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_:.-]{0,159}$"}


def obj(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


CHANGE_PAYLOADS = {
    "upsert_payee": obj({
        "payee_id": ID, "display_name": {**TEXT, "maxLength": 120},
        "kind": {"enum": ["employee", "contractor"]},
        "monthly_salary": MONEY, "monthly_allowance": MONEY,
        "health_per_half": MONEY, "pension_per_half": MONEY,
        "other_deduction_per_half": MONEY,
        "payment_destination": obj({"institution": {**TEXT, "maxLength": 80},
                                    "account": {**TEXT, "maxLength": 120}}),
        "effective_from": PERIOD, "baseline_note": TEXT, "active": {"type": "boolean"},
    }),
    "open_loan": obj({"loan_id": ID, "payee_id": ID, "original_principal": POSITIVE,
                      "opening_balance": POSITIVE, "as_of_date": DATE,
                      "installment": POSITIVE, "start_period": PERIOD,
                      "agreement_reference": TEXT}),
    "change_installment": obj({"loan_id": ID, "installment": POSITIVE,
                               "start_period": PERIOD, "reason": TEXT}),
    "outside_repayment": obj({"loan_id": ID, "amount": POSITIVE, "paid_on": DATE,
                              "reference": TEXT, "reason": TEXT}),
    "reverse_loan_event": obj({"event_id": ID, "reason": TEXT}),
}


def tool(description, properties=None, required=None):
    return {"description": description, "parameters": obj(properties or {}, required)}


TOOLS = {
    "nomina_status": tool("Read payroll setup, current state version and active run counts."),
    "nomina_list_payees": tool("Read configured payees and approved compensation; accounts are masked."),
    "nomina_get_loans": tool("Read outstanding balances, installment schedules and loan ledger.",
                             {"payee_id": ID}, []),
    "nomina_prepare_change": tool(
        "Prepare a permanent payee or loan change for the authenticated human's native confirmation. "
        "This NEVER applies the change. Use a stable request_id on retry. All amounts are integer COP.",
        {"kind": {"enum": list(CHANGE_PAYLOADS)},
         "payload": {"oneOf": list(CHANGE_PAYLOADS.values())}, "request_id": REQUEST}),
    "nomina_prepare_run": tool(
        "Prepare or refresh the current draft for a calendar quincena. H1 is days 1–15, H2 is 16–month-end. "
        "Uses confirmed compensation and loan balances. Preserve request_id on retries.",
        {"period": PERIOD, "request_id": REQUEST}),
    "nomina_adjust_draft": tool(
        "Set the total extra earnings or extra deductions for this payee in this draft (0 clears), or set one loan's deduction. This replaces that adjustment; it does not add to it. "
        "loan_override amount 0 skips once without forgiveness or catch-up. Use the displayed revision.",
        {"run_id": ID, "expected_revision": POSITIVE, "payee_id": ID,
         "kind": {"enum": ["earning", "deduction", "loan_override"]}, "amount": MONEY,
         "loan_id": ID, "reason": TEXT, "request_id": REQUEST},
        ["run_id", "expected_revision", "payee_id", "kind", "amount", "reason", "request_id"]),
    "nomina_get_run": tool("Read current or immutable historical payroll, totals and payment status.", {"run_id": ID}),
    "nomina_prepare_finalize": tool(
        "Prepare exact draft revision for human confirmation. Finalization reserves loan deductions; it does not record payment.",
        {"run_id": ID, "expected_revision": POSITIVE, "request_id": REQUEST}),
    "nomina_prepare_paid": tool(
        "Prepare recording actual payment for selected payees in a finalized run. Human confirmation posts the loan repayments. No bank transfer occurs.",
        {"run_id": ID, "expected_revision": POSITIVE,
         "payee_ids": {"type": "array", "items": ID, "minItems": 1, "maxItems": 200, "uniqueItems": True},
         "request_id": REQUEST}),
    "nomina_prepare_supersede": tool(
        "Prepare replacing a wholly unpaid finalized run with a new draft, preserving the original and releasing its reservations atomically.",
        {"run_id": ID, "expected_revision": POSITIVE, "reason": TEXT, "request_id": REQUEST}),
    "nomina_prepare_reverse_payment": tool(
        "Prepare correction of a mistakenly recorded payroll payment using compensating ledger events. History is retained; this does not reverse a bank transfer.",
        {"payment_id": ID, "reason": TEXT, "request_id": REQUEST}),
    "nomina_history": tool("Find previous payroll runs and payment events by period or payee.",
                           {"period": PERIOD, "payee_id": ID,
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, []),
    "nomina_export": tool(
        "Save an immutable finalized payroll report and migration snapshot in the private local archive. Reports contain full payment destinations; never paste them into chat.",
        {"run_id": ID}),
    "nomina_archive_status": tool(
        "Read encrypted-backup/private-Drive archive status. retry=true retries bounded queued uploads without repeating payroll transactions.",
        {"retry": {"type": "boolean"}}, []),
}


def validate(value, schema, label="arguments"):
    """Small strict validator for our closed JSON schema vocabulary (no coercion)."""
    import re
    if "oneOf" in schema:
        count = 0
        for choice in schema["oneOf"]:
            try:
                validate(value, choice, label)
                count += 1
            except ValueError:
                pass
        if count != 1:
            raise ValueError(f"{label}: invalid payload shape")
        return
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{label}: unsupported value")
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{label}: expected object")
        props = schema["properties"]
        if set(value) - set(props) or set(schema["required"]) - set(value):
            raise ValueError(f"{label}: missing or unexpected fields")
        for key, item in value.items():
            validate(item, props[key], f"{label}.{key}")
    elif expected == "array":
        if not isinstance(value, list) or not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 1000):
            raise ValueError(f"{label}: invalid array")
        for item in value:
            validate(item, schema["items"], label)
        if schema.get("uniqueItems") and len(set(value)) != len(value):
            raise ValueError(f"{label}: duplicate values")
    elif expected == "integer":
        if type(value) is not int or not schema.get("minimum", -10**12) <= value <= schema.get("maximum", 10**12):
            raise ValueError(f"{label}: expected integer in allowed range")
    elif expected == "boolean":
        if type(value) is not bool:
            raise ValueError(f"{label}: expected boolean")
    elif expected == "string":
        if not isinstance(value, str) or not schema.get("minLength", 1) <= len(value) <= schema.get("maxLength", 500):
            raise ValueError(f"{label}: invalid text length")
        if any(ord(c) < 32 for c in value) or (schema.get("pattern") and not re.fullmatch(schema["pattern"], value)):
            raise ValueError(f"{label}: invalid text format")


def validate_call(name, arguments):
    if name not in TOOLS:
        raise ValueError("Unknown payroll tool")
    validate(arguments, TOOLS[name]["parameters"])
    if name == "nomina_prepare_change":
        validate(arguments["payload"], CHANGE_PAYLOADS[arguments["kind"]], "payload")
    if name == "nomina_adjust_draft" and (arguments["kind"] == "loan_override") != ("loan_id" in arguments):
        raise ValueError("loan_id is required only for loan_override")
