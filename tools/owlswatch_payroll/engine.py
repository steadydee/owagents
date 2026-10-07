"""Payroll state machine. Human approvals enter through a separate native route.

No model-callable tool can finalize a run, acknowledge payment, or alter a
permanent baseline. ``approve`` consumes a saved, expiring, route-bound action.
"""
import copy
import contextlib
import datetime as dt
import hashlib
import json
import math
import re
import secrets
import sqlite3
import time
import uuid

from calculation import ROUNDING_RULE, half_compensation, money, period_dates, period_parts, totals
from store import Store, canonical


class PayrollError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def fail(code, message):
    raise PayrollError(code, message)


def identifier(value, label="Identifier"):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        fail("INVALID_INPUT", label + " must be a safe, nonempty identifier.")
    return value


def text(value, label, maximum=500):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        fail("INVALID_INPUT", label + " is required and must be plain text.")
    return value.strip()


def amount(value, positive=False):
    try:
        return money(value, positive)
    except ValueError as exc:
        fail("INVALID_INPUT", str(exc))


def period(value):
    try:
        period_parts(value)
    except ValueError as exc:
        fail("INVALID_INPUT", str(exc))
    return value


def date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        fail("INVALID_INPUT", "Date must be YYYY-MM-DD.")
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        fail("INVALID_INPUT", "Date must be a valid calendar date.")
    return value


def exact_keys(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        fail("INVALID_INPUT", "Payload fields do not match the requested operation.")


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def masked(value):
    """Bank destinations stay complete in snapshots; ordinary tools mask them."""
    if isinstance(value, dict):
        return {key: (("***" + str(item)[-4:] if len(str(item)) > 4 else "***") if key == "account" else masked(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [masked(item) for item in value]
    return value


class PayrollEngine:
    MUTATIONS = frozenset((
        "nomina_prepare_change", "nomina_prepare_run", "nomina_adjust_draft",
        "nomina_prepare_finalize", "nomina_prepare_paid", "nomina_prepare_supersede",
        "nomina_prepare_reverse_payment",
    ))
    CHANGES = frozenset(("upsert_payee", "open_loan", "change_installment", "outside_repayment", "reverse_loan_event"))

    def __init__(self, db_path):
        self.store = Store(db_path)

    @contextlib.contextmanager
    def _transaction(self):
        try:
            with self.store.transaction() as db:
                yield db
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc):
                raise PayrollError("BUSY", "Payroll is busy. Retry the same request identity or confirmation reference.", True) from None
            raise

    def _actor(self, actor, native=False):
        if not isinstance(actor, dict):
            fail("AUTH_REQUIRED", "Authenticated Telegram context is required.")
        if actor.get("channel") != "telegram" or actor.get("agentId") != "nomina":
            fail("AUTH_REQUIRED", "This action requires the payroll Telegram route.")
        if not re.fullmatch(r"\d{1,20}", str(actor.get("senderId", ""))) or not re.fullmatch(r"-?\d{1,20}", str(actor.get("chatId", ""))):
            fail("AUTH_REQUIRED", "Authenticated Telegram sender and chat are required.")
        if not re.fullmatch(r"\d{0,20}", str(actor.get("threadId", ""))):
            fail("AUTH_REQUIRED", "Invalid Telegram topic.")
        for key in ("sessionKey", "accountId"):
            if not isinstance(actor.get(key), str) or not actor[key] or len(actor[key]) > 300:
                fail("AUTH_REQUIRED", "Authenticated Telegram session and bot are required.")
        expected = "native_command" if native else "tool_context"
        if actor.get("source") != expected:
            fail("AUTH_REQUIRED", "This action requires trusted host provenance.")
        if native:
            approved_at = actor.get("approvedAt")
            if actor.get("authorized") is not True or type(approved_at) not in (int, float) or not math.isfinite(approved_at) or abs(time.time() - approved_at) > 60:
                fail("AUTH_REQUIRED", "A fresh native human confirmation is required.")
            text(actor.get("approvalEventId"), "Approval event", 100)
        if "sourceEventId" in actor:
            text(actor["sourceEventId"], "Host source event", 300)
        return {key: actor[key] for key in ("senderId", "chatId", "threadId", "accountId", "sessionKey", "channel", "agentId", "source", "sourceEventId") if key in actor} | ({key: actor[key] for key in ("authorized", "approvedAt", "approvalEventId")} if native else {})

    @staticmethod
    def _scope(actor):
        return canonical({key: str(actor.get(key, "")) for key in ("senderId", "chatId", "threadId", "accountId", "sessionKey")})

    def call(self, name, args, actor):
        actor = self._actor(actor)
        if not isinstance(args, dict):
            fail("INVALID_INPUT", "Tool arguments must be an object.")
        handlers = {
            "nomina_status": self._status, "nomina_list_payees": self._list_payees,
            "nomina_get_loans": self._get_loans, "nomina_prepare_change": self._prepare_change,
            "nomina_prepare_run": self._prepare_run, "nomina_adjust_draft": self._adjust_draft,
            "nomina_get_run": self._get_run, "nomina_prepare_finalize": self._prepare_finalize,
            "nomina_prepare_paid": self._prepare_paid, "nomina_prepare_supersede": self._prepare_supersede,
            "nomina_prepare_reverse_payment": self._prepare_reverse_payment, "nomina_history": self._history,
        }
        if name not in handlers:
            fail("UNKNOWN_TOOL", "Unknown payroll tool.")
        try:
            with self._transaction() as db:
                request_id = None
                if name in self.MUTATIONS:
                    request_id = text(args.get("request_id"), "Request identity", 200)
                    fingerprint = digest({"tool": name, "arguments": args})
                    prior = db.execute("SELECT * FROM requests WHERE actor_scope=? AND request_id=?", (self._scope(actor), request_id)).fetchone()
                    if prior:
                        if prior["fingerprint"] != fingerprint:
                            fail("IDEMPOTENCY_CONFLICT", "This request identity was already used with different data.")
                        return masked(json.loads(prior["result"]))
                result = handlers[name](db, args, actor)
                if request_id:
                    db.execute("INSERT INTO requests VALUES (?,?,?,?)", (self._scope(actor), request_id, fingerprint, canonical(result)))
                return masked(result)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc):
                raise PayrollError("BUSY", "Payroll is busy. Retry the same request identity.", True) from None
            raise

    def approve(self, token, actor):
        actor = self._actor(actor, native=True)
        if not isinstance(token, str) or not re.fullmatch(r"[A-F0-9]{16}", token):
            fail("INVALID_CONFIRMATION", "Invalid confirmation reference.")
        with self._transaction() as db:
            pending = db.execute("SELECT * FROM pending WHERE token=?", (token,)).fetchone()
            if not pending:
                fail("INVALID_CONFIRMATION", "Confirmation reference was not found.")
            if self._scope(json.loads(pending["actor"])) != self._scope(actor):
                fail("WRONG_APPROVER", "This confirmation belongs to a different manager or Telegram conversation.")
            if pending["result"] is not None:
                return masked(json.loads(pending["result"]))
            if pending["expires_at"] < time.time():
                fail("CONFIRMATION_EXPIRED", "This confirmation expired. Prepare a fresh preview.")
            if pending["version"] != self.store.version(db):
                fail("STALE_PREVIEW", "Payroll data changed. Prepare and review a fresh preview.")
            payload = json.loads(pending["payload"])
            if digest({"payload": payload, "preview": json.loads(pending["preview"])}) != pending["payload_hash"]:
                fail("INVALID_CONFIRMATION", "The saved confirmation failed its integrity check.")
            reviewed = db.execute("SELECT * FROM pending_reviews WHERE token=?", (token,)).fetchone()
            if not reviewed or reviewed["payload_hash"] != pending["payload_hash"] or reviewed["version"] != pending["version"] or reviewed["actor_scope"] != self._scope(actor):
                fail("REVIEW_REQUIRED", "Use /revisar_nomina with this reference to review the exact saved action before confirming.")
            kind = pending["kind"]
            if kind in self.CHANGES:
                result = self._execute_change(db, kind, payload, actor)
            else:
                executor = {"finalize": self._finalize, "paid": self._paid, "supersede": self._supersede, "reverse_payment": self._reverse_payment}.get(kind)
                if executor is None:
                    fail("INVALID_CONFIRMATION", "Unsupported saved action.")
                result = executor(db, payload, actor)
            self._audit(db, "human_confirmation", {"pending_id": token, "kind": kind, "payload_hash": pending["payload_hash"]}, actor)
            result["action"] = kind
            if "run" in result:
                result["run_id"] = result["run"]["run_id"]
            result["state_version"] = self.store.bump(db)
            db.execute("UPDATE pending SET result=? WHERE token=?", (canonical(result), token))
            return masked(result)

    def review(self, token, actor, render=None, *, mark_reviewed=True, delivered_hash=None, delivery_receipts=None):
        """Show the saved deterministic preview through the authenticated host.

        The host supplies its bounded renderer. Rendering errors roll back the
        review marker, so an oversized or invalid preview cannot be approved.
        """
        actor = self._actor(actor, native=True)
        if not isinstance(token, str) or not re.fullmatch(r"[A-F0-9]{16}", token):
            fail("INVALID_CONFIRMATION", "Invalid confirmation reference.")
        with self._transaction() as db:
            pending = db.execute("SELECT * FROM pending WHERE token=?", (token,)).fetchone()
            if not pending:
                fail("INVALID_CONFIRMATION", "Confirmation reference was not found.")
            if self._scope(json.loads(pending["actor"])) != self._scope(actor):
                fail("WRONG_APPROVER", "This confirmation belongs to a different manager or Telegram conversation.")
            if pending["result"] is not None:
                result = {"action": pending["kind"], "pending_id": token, "already_confirmed": True, "result": json.loads(pending["result"]), "preview": json.loads(pending["preview"]), "expires_at": pending["expires_at"], "confirmation_command": "/confirmar_nomina " + token}
                if render:
                    result["summary"] = render(result)
                return result
            if pending["expires_at"] < time.time():
                fail("CONFIRMATION_EXPIRED", "This confirmation expired. Prepare a fresh preview.")
            if pending["version"] != self.store.version(db):
                fail("STALE_PREVIEW", "Payroll data changed. Prepare and review a fresh preview.")
            payload = json.loads(pending["payload"])
            if digest({"payload": payload, "preview": json.loads(pending["preview"])}) != pending["payload_hash"]:
                fail("INVALID_CONFIRMATION", "The saved confirmation failed its integrity check.")
            if "run_id" in payload and "expected_revision" in payload:
                self._revision(self._run(db, payload["run_id"]), payload["expected_revision"])
            result = {"action": pending["kind"], "pending_id": token, "preview": json.loads(pending["preview"]), "expires_at": pending["expires_at"], "confirmation_command": "/confirmar_nomina " + token}
            if render:
                result["summary"] = render(result)
            if not mark_reviewed:
                return result
            if delivered_hash is not None and (not render or
                    hashlib.sha256(result["summary"].encode("utf-8")).hexdigest() != delivered_hash):
                fail("REVIEW_DELIVERY_MISMATCH", "The delivered review differs from the saved review. Open it again before confirming.")
            db.execute("INSERT OR REPLACE INTO pending_reviews VALUES (?,?,?,?,?)", (token, pending["payload_hash"], pending["version"], self._scope(actor), time.time()))
            review_audit = {"pending_id": token, "payload_hash": pending["payload_hash"]}
            if delivered_hash is not None:
                review_audit.update(delivered_sha256=delivered_hash, telegram_message_ids=delivery_receipts)
            self._audit(db, "action_reviewed", review_audit, actor)
            return result

    def snapshot(self, run_id):
        """Internal export API, not model-facing; caller enforces artifact ACL."""
        identifier(run_id)
        with self._transaction() as db:
            return self._run_output(db, self._run(db, run_id))

    def _audit(self, db, kind, data, actor):
        db.execute("INSERT INTO audit VALUES (?,?,?,?,?)", (str(uuid.uuid4()), kind, canonical(data), canonical(actor), time.time()))

    def _pending(self, db, kind, payload, preview, actor):
        token = secrets.token_hex(8).upper()
        expires = time.time() + 15 * 60
        payload_hash = digest({"payload": payload, "preview": preview})
        db.execute("INSERT INTO pending VALUES (?,?,?,?,?,?,?,?,?,?)", (token, kind, canonical(payload), canonical(preview), payload_hash, self.store.version(db), canonical(actor), expires, None, time.time()))
        self._audit(db, "action_prepared", {"pending_id": token, "kind": kind, "payload_hash": payload_hash}, actor)
        return {"pending_id": token, "review_command": "/revisar_nomina " + token, "confirmation_command": "/confirmar_nomina " + token, "expires_at": expires, "action": kind, "preview": preview}

    def _status(self, db, args, actor):
        exact_keys(args, ())
        return {"state_version": self.store.version(db), "rounding_rule": ROUNDING_RULE, "counts": {
            "payees": db.execute("SELECT COUNT(DISTINCT payee_id) FROM profiles").fetchone()[0],
            "loans": db.execute("SELECT COUNT(*) FROM loans").fetchone()[0],
            "runs": db.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
        }}

    def _profile(self, db, payee_id, as_period=None):
        if as_period:
            row = db.execute("SELECT * FROM profiles WHERE payee_id=? AND effective_from<=? ORDER BY effective_from DESC,id DESC LIMIT 1", (payee_id, as_period)).fetchone()
        else:
            row = db.execute("SELECT * FROM profiles WHERE payee_id=? ORDER BY effective_from DESC,id DESC LIMIT 1", (payee_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def _profiles(self, db, as_period=None):
        ids = [row[0] for row in db.execute("SELECT DISTINCT payee_id FROM profiles ORDER BY payee_id")]
        return [item for item in (self._profile(db, value, as_period) for value in ids) if item]

    def _list_payees(self, db, args, actor):
        exact_keys(args, ())
        return {"payees": self._profiles(db), "state_version": self.store.version(db)}

    def _loan_balance(self, db, loan_id):
        return db.execute("SELECT COALESCE(SUM(amount),0) FROM loan_events WHERE loan_id=?", (loan_id,)).fetchone()[0]

    def _reserved(self, db, loan_id, excluding_run=None):
        if excluding_run:
            return db.execute("SELECT COALESCE(SUM(amount),0) FROM reservations WHERE loan_id=? AND state='active' AND run_id!=?", (loan_id, excluding_run)).fetchone()[0]
        return db.execute("SELECT COALESCE(SUM(amount),0) FROM reservations WHERE loan_id=? AND state='active'", (loan_id,)).fetchone()[0]

    def _loan(self, db, loan_id):
        identifier(loan_id, "Loan identity")
        row = db.execute("SELECT * FROM loans WHERE loan_id=?", (loan_id,)).fetchone()
        if not row:
            fail("NOT_FOUND", "Loan was not found.")
        return json.loads(row["data"])

    def _loan_view(self, db, loan):
        result = copy.deepcopy(loan)
        result["outstanding"] = self._loan_balance(db, loan["loan_id"])
        result["reserved"] = self._reserved(db, loan["loan_id"])
        result["available_for_deduction"] = result["outstanding"] - result["reserved"]
        result["terms"] = [json.loads(row[0]) for row in db.execute("SELECT data FROM terms WHERE loan_id=? ORDER BY start_period,id", (loan["loan_id"],))]
        result["events"] = [{"event_id": row["event_id"], "kind": row["kind"], "amount": row["amount"], "data": json.loads(row["data"]), "actor": json.loads(row["actor"]), "created_at": row["created_at"]} for row in db.execute("SELECT * FROM loan_events WHERE loan_id=? ORDER BY created_at DESC,event_id LIMIT 50", (loan["loan_id"],))]
        result["events_truncated"] = db.execute("SELECT COUNT(*) FROM loan_events WHERE loan_id=?", (loan["loan_id"],)).fetchone()[0] > 50
        return result

    def _get_loans(self, db, args, actor):
        exact_keys(args, (), ("payee_id",))
        query, params = "SELECT data FROM loans", ()
        if args.get("payee_id"):
            query += " WHERE payee_id=?"
            params = (identifier(args["payee_id"]),)
        rows = db.execute(query + " ORDER BY loan_id", params)
        return {"loans": [self._loan_view(db, json.loads(row[0])) for row in rows], "state_version": self.store.version(db)}

    def _validate_change(self, db, kind, payload):
        payload = copy.deepcopy(payload)
        if kind == "upsert_payee":
            exact_keys(payload, ("payee_id", "display_name", "kind", "monthly_salary", "monthly_allowance", "health_per_half", "pension_per_half", "other_deduction_per_half", "payment_destination", "effective_from", "baseline_note", "active"))
            identifier(payload["payee_id"])
            payload["display_name"] = text(payload["display_name"], "Payee name", 160)
            if payload["kind"] not in ("employee", "contractor") or type(payload["active"]) is not bool:
                fail("INVALID_INPUT", "Payee kind or active status is invalid.")
            for key in ("monthly_salary", "monthly_allowance", "health_per_half", "pension_per_half", "other_deduction_per_half"):
                amount(payload[key])
            period(payload["effective_from"])
            payload["baseline_note"] = text(payload["baseline_note"], "Approved baseline and deduction rationale", 1000)
            exact_keys(payload["payment_destination"], ("institution", "account"))
            for key in ("institution", "account"):
                payload["payment_destination"][key] = text(payload["payment_destination"][key], "Payment destination", 160)
            # Both halves must be valid before a permanent baseline is accepted.
            for half in ("1", "2"):
                salary, allowance = half_compensation(payload, payload["effective_from"][:-1] + half)
                if salary + allowance < sum(payload[key] for key in ("health_per_half", "pension_per_half", "other_deduction_per_half")):
                    fail("NEGATIVE_NET", "Recurring deductions exceed the approved half-month gross.")
        elif kind == "open_loan":
            exact_keys(payload, ("loan_id", "payee_id", "original_principal", "opening_balance", "as_of_date", "installment", "start_period", "agreement_reference"))
            identifier(payload["loan_id"])
            identifier(payload["payee_id"])
            if not self._profile(db, payload["payee_id"]):
                fail("NOT_FOUND", "Create and confirm the payee baseline first.")
            if db.execute("SELECT 1 FROM loans WHERE loan_id=?", (payload["loan_id"],)).fetchone():
                fail("ALREADY_EXISTS", "Loan identity already exists.")
            amount(payload["original_principal"], True)
            amount(payload["opening_balance"], True)
            amount(payload["installment"], True)
            if payload["opening_balance"] > payload["original_principal"]:
                fail("INVALID_INPUT", "Opening balance cannot exceed original principal.")
            date(payload["as_of_date"])
            period(payload["start_period"])
            if payload["as_of_date"] > period_dates(payload["start_period"])[1]:
                fail("INVALID_INPUT", "The opening balance date cannot follow its first deduction period.")
            payload["agreement_reference"] = text(payload["agreement_reference"], "Loan agreement reference", 500)
        elif kind == "change_installment":
            exact_keys(payload, ("loan_id", "installment", "start_period", "reason"))
            self._loan(db, payload["loan_id"])
            amount(payload["installment"], True)
            period(payload["start_period"])
            payload["reason"] = text(payload["reason"], "Reason")
        elif kind == "outside_repayment":
            exact_keys(payload, ("loan_id", "amount", "paid_on", "reference", "reason"))
            loan = self._loan(db, payload["loan_id"])
            amount(payload["amount"], True)
            date(payload["paid_on"])
            if payload["paid_on"] < loan["as_of_date"]:
                fail("INVALID_INPUT", "Outside repayments cannot precede the opening balance date.")
            payload["reference"] = text(payload["reference"], "Payment reference", 200)
            payload["reason"] = text(payload["reason"], "Reason")
            if db.execute("SELECT 1 FROM loan_events WHERE loan_id=? AND kind='outside_repayment' AND json_extract(data,'$.reference')=?", (payload["loan_id"], payload["reference"])).fetchone():
                fail("ALREADY_EXISTS", "That outside payment reference is already recorded for this loan.")
            if payload["amount"] > self._loan_balance(db, payload["loan_id"]) - self._reserved(db, payload["loan_id"]):
                fail("RESERVATION_CONFLICT", "Repayment exceeds the unreserved balance. Resolve finalized payroll first.")
        elif kind == "reverse_loan_event":
            exact_keys(payload, ("event_id", "reason"))
            identifier(payload["event_id"])
            payload["reason"] = text(payload["reason"], "Reason")
            event = db.execute("SELECT * FROM loan_events WHERE event_id=?", (payload["event_id"],)).fetchone()
            if not event or event["kind"] != "outside_repayment":
                fail("INVALID_REVERSAL", "Only an outside repayment can use this reversal. Reverse payroll through its payment.")
            if db.execute("SELECT 1 FROM loan_events WHERE kind='outside_repayment_reversal' AND json_extract(data,'$.reverses_event_id')=?", (payload["event_id"],)).fetchone():
                fail("ALREADY_REVERSED", "This loan payment was already reversed.")
        else:
            fail("INVALID_INPUT", "Unsupported permanent change.")
        return payload

    def _prepare_change(self, db, args, actor):
        exact_keys(args, ("kind", "payload", "request_id"))
        payload = self._validate_change(db, args["kind"], args["payload"])
        preview = copy.deepcopy(payload)
        if args["kind"] == "reverse_loan_event":
            event = db.execute("SELECT * FROM loan_events WHERE event_id=?", (payload["event_id"],)).fetchone()
            original = json.loads(event["data"])
            preview.update({"loan_id": event["loan_id"], "amount": -event["amount"], "event_kind": event["kind"], "paid_on": original["paid_on"], "reference": original["reference"]})
        return self._pending(db, args["kind"], payload, preview, actor)

    def _loan_event(self, db, loan_id, kind, value, data, actor):
        event_id = str(uuid.uuid4())
        db.execute("INSERT INTO loan_events VALUES (?,?,?,?,?,?,?)", (event_id, loan_id, kind, value, canonical(data), canonical(actor), time.time()))
        return event_id

    def _execute_change(self, db, kind, payload, actor):
        payload = self._validate_change(db, kind, payload)
        now = time.time()
        if kind == "upsert_payee":
            db.execute("INSERT INTO profiles(payee_id,effective_from,data,actor,created_at) VALUES (?,?,?,?,?)", (payload["payee_id"], payload["effective_from"], canonical(payload), canonical(actor), now))
            result = {"payee": payload}
        elif kind == "open_loan":
            db.execute("INSERT INTO loans VALUES (?,?,?,?,?)", (payload["loan_id"], payload["payee_id"], canonical(payload), canonical(actor), now))
            db.execute("INSERT INTO terms(loan_id,start_period,installment,data,actor,created_at) VALUES (?,?,?,?,?,?)", (payload["loan_id"], payload["start_period"], payload["installment"], canonical({"installment": payload["installment"], "start_period": payload["start_period"], "agreement_reference": payload["agreement_reference"]}), canonical(actor), now))
            event_id = self._loan_event(db, payload["loan_id"], "opening_balance", payload["opening_balance"], {"as_of_date": payload["as_of_date"], "original_principal": payload["original_principal"]}, actor)
            result = {"loan": self._loan_view(db, payload), "event_id": event_id}
        elif kind == "change_installment":
            db.execute("INSERT INTO terms(loan_id,start_period,installment,data,actor,created_at) VALUES (?,?,?,?,?,?)", (payload["loan_id"], payload["start_period"], payload["installment"], canonical(payload), canonical(actor), now))
            result = {"loan": self._loan_view(db, self._loan(db, payload["loan_id"]))}
        elif kind == "outside_repayment":
            event_id = self._loan_event(db, payload["loan_id"], kind, -payload["amount"], payload, actor)
            result = {"loan": self._loan_view(db, self._loan(db, payload["loan_id"])), "event_id": event_id}
        else:
            event = db.execute("SELECT * FROM loan_events WHERE event_id=?", (payload["event_id"],)).fetchone()
            event_id = self._loan_event(db, event["loan_id"], "outside_repayment_reversal", -event["amount"], {"reverses_event_id": event["event_id"], "reason": payload["reason"]}, actor)
            result = {"loan": self._loan_view(db, self._loan(db, event["loan_id"])), "event_id": event_id}
        self._audit(db, kind, payload, actor)
        return result

    def _run(self, db, run_id):
        identifier(run_id, "Payroll run identity")
        row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            fail("NOT_FOUND", "Payroll run was not found.")
        return row

    def _revision(self, run, expected):
        if type(expected) is not int or expected != run["revision"]:
            fail("STALE_REVISION", "The payroll revision changed. Review its current revision.")

    def _draft_current(self, db, run):
        if run["status"] != "draft":
            fail("INVALID_STATE", "Only a draft payroll can be changed or finalized.")
        if run["state_version"] != self.store.version(db):
            fail("STALE_PREVIEW", "Payroll data changed. Prepare this period again to refresh the draft.")

    def _calculate(self, db, run_id, run_period, revision, adjustments):
        rows = []
        issues = []
        warnings = []
        profiles = [profile for profile in self._profiles(db, run_period) if profile["active"]]
        if not profiles:
            issues.append({"code": "NO_PAYEES", "message": "No confirmed active payees exist for this period."})
        known = {profile["payee_id"] for profile in profiles}
        excluded = [item for item in adjustments if item["payee_id"] not in known]
        if excluded:
            warnings.append({"code": "EXCLUDED_ADJUSTMENTS", "payee_ids": sorted({item["payee_id"] for item in excluded}), "message": "Inactive payees and their prior draft adjustments are excluded from this payroll. Review the changed roster."})
        for profile in profiles:
            payee_id = profile["payee_id"]
            own = [item for item in adjustments if item["payee_id"] == payee_id]
            salary, allowance = half_compensation(profile, run_period)
            extra_earnings = sum(item["amount"] for item in own if item["kind"] == "earning")
            extra_deductions = sum(item["amount"] for item in own if item["kind"] == "deduction")
            gross = salary + allowance + extra_earnings
            deductions = profile["health_per_half"] + profile["pension_per_half"] + profile["other_deduction_per_half"] + extra_deductions
            loan_items = []
            overrides = {item["loan_id"]: item for item in own if item["kind"] == "loan_override"}
            for loan_row in db.execute("SELECT data FROM loans WHERE payee_id=? ORDER BY loan_id", (payee_id,)):
                loan = json.loads(loan_row[0])
                if loan["start_period"] > run_period or loan["as_of_date"] > period_dates(run_period)[1]:
                    if loan["loan_id"] in overrides:
                        fail("INVALID_INPUT", "Loan override precedes the loan opening period.")
                    continue
                term = db.execute("SELECT installment,data FROM terms WHERE loan_id=? AND start_period<=? ORDER BY start_period DESC,id DESC LIMIT 1", (loan["loan_id"], run_period)).fetchone()
                if not term:
                    continue
                balance = self._loan_balance(db, loan["loan_id"])
                reserved = self._reserved(db, loan["loan_id"], run_id)
                if balance < reserved:
                    fail("INTEGRITY_ERROR", "Loan reservations exceed its balance.")
                installment = overrides.get(loan["loan_id"], {}).get("amount", term["installment"])
                loan_items.append({"loan_id": loan["loan_id"], "amount": min(installment, balance - reserved), "agreed_installment": term["installment"], "requested_installment": installment, "outstanding_before": balance, "reserved_elsewhere": reserved, "agreement_reference": loan["agreement_reference"], "terms": json.loads(term["data"]), "override_reason": overrides.get(loan["loan_id"], {}).get("reason")})
            unknown_overrides = set(overrides) - {item["loan_id"] for item in loan_items}
            if unknown_overrides:
                fail("INVALID_INPUT", "Loan override does not match an eligible loan for this payee.")
            net = gross - deductions - sum(item["amount"] for item in loan_items)
            if net < 0:
                issues.append({"code": "NEGATIVE_NET", "payee_id": payee_id, "message": "Deductions exceed gross pay. Adjust this draft before finalization."})
            rows.append({"payee_id": payee_id, "display_name": profile["display_name"], "kind": profile["kind"], "profile": profile, "salary": salary, "allowance": allowance, "extra_earnings": extra_earnings, "gross": gross, "health": profile["health_per_half"], "pension": profile["pension_per_half"], "other_deduction": profile["other_deduction_per_half"], "extra_deductions": extra_deductions, "loan_deductions": loan_items, "net": net})
        start, end = period_dates(run_period)
        return {"run_id": run_id, "period": run_period, "period_start": start, "period_end": end, "revision": revision, "state_version": self.store.version(db), "rounding_rule": ROUNDING_RULE, "rows": rows, "totals": totals(rows), "adjustments": adjustments, "excluded_adjustments": excluded, "warnings": warnings, "issues": issues, "ready_to_finalize": not issues}

    def _new_draft(self, db, run_period, actor, adjustments=None, supersedes=None):
        run_id = str(uuid.uuid4())
        adjustments = adjustments or []
        self.store.bump(db)
        calculated = self._calculate(db, run_id, run_period, 1, adjustments)
        if supersedes:
            calculated["supersedes_run_id"] = supersedes
        now = time.time()
        db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)", (run_id, run_period, 1, "draft", self.store.version(db), canonical(adjustments), canonical(calculated), now, now))
        self._audit(db, "draft_created", {"run_id": run_id, "period": run_period, "supersedes_run_id": supersedes}, actor)
        return self._run_output(db, self._run(db, run_id))

    def _prepare_run(self, db, args, actor):
        exact_keys(args, ("period", "request_id"))
        run_period = period(args["period"])
        run = db.execute("SELECT * FROM runs WHERE period=? AND status!='superseded'", (run_period,)).fetchone()
        if not run:
            return self._new_draft(db, run_period, actor)
        if run["status"] == "draft" and run["state_version"] != self.store.version(db):
            self.store.bump(db)
            revision = run["revision"] + 1
            calculated = self._calculate(db, run["run_id"], run_period, revision, json.loads(run["adjustments"]))
            previous = json.loads(run["draft"])
            if "supersedes_run_id" in previous:
                calculated["supersedes_run_id"] = previous["supersedes_run_id"]
            db.execute("UPDATE runs SET revision=?,state_version=?,draft=?,updated_at=? WHERE run_id=?", (revision, self.store.version(db), canonical(calculated), time.time(), run["run_id"]))
            self._audit(db, "draft_refreshed", {"run_id": run["run_id"], "revision": revision}, actor)
        return self._run_output(db, self._run(db, run["run_id"]))

    def _payments(self, db, run_id):
        result = []
        for payment in db.execute("SELECT * FROM payments WHERE run_id=? ORDER BY created_at,payment_id", (run_id,)):
            item = json.loads(payment["data"])
            reversal = db.execute("SELECT * FROM payment_reversals WHERE payment_id=?", (payment["payment_id"],)).fetchone()
            item.update({"payment_id": payment["payment_id"], "paid_at": payment["created_at"], "reversed": bool(reversal)})
            if reversal:
                item["reversal"] = json.loads(reversal["data"])
            result.append(item)
        return result

    def _run_output(self, db, run):
        snapshot = db.execute("SELECT data FROM snapshots WHERE run_id=?", (run["run_id"],)).fetchone()
        result = json.loads(snapshot[0] if snapshot else run["draft"])
        result["status"] = run["status"]
        result["stale"] = run["status"] == "draft" and run["state_version"] != self.store.version(db)
        result["payments"] = self._payments(db, run["run_id"])
        return result

    def _get_run(self, db, args, actor):
        exact_keys(args, ("run_id",))
        return self._run_output(db, self._run(db, args["run_id"]))

    def _adjust_draft(self, db, args, actor):
        exact_keys(args, ("run_id", "expected_revision", "payee_id", "kind", "amount", "reason", "request_id"), ("loan_id",))
        run = self._run(db, args["run_id"])
        self._revision(run, args["expected_revision"])
        self._draft_current(db, run)
        payee_id = identifier(args["payee_id"])
        profile = self._profile(db, payee_id, run["period"])
        if not profile or not profile["active"]:
            fail("NOT_FOUND", "Payee is not active in this draft.")
        if args["kind"] not in ("earning", "deduction", "loan_override"):
            fail("INVALID_INPUT", "Invalid draft adjustment kind.")
        adjustment = {"payee_id": payee_id, "kind": args["kind"], "amount": amount(args["amount"]), "reason": text(args["reason"], "Reason"), "actor": actor, "created_at": time.time(), "request_id": args["request_id"]}
        adjustments = json.loads(run["adjustments"])
        if args["kind"] == "loan_override":
            loan = self._loan(db, args.get("loan_id"))
            if loan["payee_id"] != payee_id:
                fail("INVALID_INPUT", "The selected loan belongs to another payee.")
            adjustment["loan_id"] = loan["loan_id"]
            # One override per loan per period, last explicit edit replaces it.
            adjustments = [item for item in adjustments if not (item["kind"] == "loan_override" and item.get("loan_id") == loan["loan_id"])]
        elif "loan_id" in args:
            fail("INVALID_INPUT", "Only a loan override accepts a loan identity.")
        else:
            adjustments = [item for item in adjustments if not (item["payee_id"] == payee_id and item["kind"] == args["kind"])]
        if adjustment["kind"] == "loan_override" or adjustment["amount"]:
            adjustments.append(adjustment)
        revision = run["revision"] + 1
        self.store.bump(db)
        calculated = self._calculate(db, run["run_id"], run["period"], revision, adjustments)
        previous = json.loads(run["draft"])
        if "supersedes_run_id" in previous:
            calculated["supersedes_run_id"] = previous["supersedes_run_id"]
        db.execute("UPDATE runs SET revision=?,state_version=?,adjustments=?,draft=?,updated_at=? WHERE run_id=?", (revision, self.store.version(db), canonical(adjustments), canonical(calculated), time.time(), run["run_id"]))
        self._audit(db, "draft_adjusted", {"run_id": run["run_id"], "revision": revision, "adjustment": adjustment}, actor)
        return self._run_output(db, self._run(db, run["run_id"]))

    def _prepare_finalize(self, db, args, actor):
        exact_keys(args, ("run_id", "expected_revision", "request_id"))
        run = self._run(db, args["run_id"])
        self._revision(run, args["expected_revision"])
        self._draft_current(db, run)
        calculated = self._calculate(db, run["run_id"], run["period"], run["revision"], json.loads(run["adjustments"]))
        if calculated["issues"]:
            fail(calculated["issues"][0]["code"], "Resolve the draft's calculation issues before finalizing.")
        payload = {"run_id": run["run_id"], "expected_revision": run["revision"], "calculation_hash": digest(calculated)}
        return self._pending(db, "finalize", payload, calculated, actor)

    def _finalize(self, db, payload, actor):
        run = self._run(db, payload["run_id"])
        self._revision(run, payload["expected_revision"])
        self._draft_current(db, run)
        calculated = self._calculate(db, run["run_id"], run["period"], run["revision"], json.loads(run["adjustments"]))
        if calculated["issues"]:
            fail(calculated["issues"][0]["code"], "Resolve the draft's calculation issues before finalizing.")
        if digest(calculated) != payload["calculation_hash"]:
            fail("STALE_PREVIEW", "The payroll amounts changed. Prepare a fresh finalization.")
        previous = json.loads(run["draft"])
        if "supersedes_run_id" in previous:
            calculated["supersedes_run_id"] = previous["supersedes_run_id"]
        calculated.update({"finalized_at": time.time(), "finalized_by": actor})
        db.execute("INSERT INTO snapshots VALUES (?,?,?,?)", (run["run_id"], canonical(calculated), canonical(actor), time.time()))
        for row in calculated["rows"]:
            for loan in row["loan_deductions"]:
                if loan["amount"]:
                    db.execute("INSERT INTO reservations VALUES (?,?,?,?,?)", (run["run_id"], row["payee_id"], loan["loan_id"], loan["amount"], "active"))
        db.execute("UPDATE runs SET status='finalized',updated_at=? WHERE run_id=?", (time.time(), run["run_id"]))
        self._audit(db, "run_finalized", {"run_id": run["run_id"], "revision": run["revision"], "snapshot_hash": digest(calculated)}, actor)
        return {"run": self._run_output(db, self._run(db, run["run_id"]))}

    def _active_paid_ids(self, db, run_id):
        return {row[0] for row in db.execute("SELECT p.payee_id FROM payments p WHERE p.run_id=? AND NOT EXISTS (SELECT 1 FROM payment_reversals r WHERE r.payment_id=p.payment_id)", (run_id,))}

    def _payment_candidates(self, db, run, payee_ids):
        if run["status"] not in ("finalized", "partially_paid"):
            fail("INVALID_STATE", "Payments can only be recorded against an unpaid finalized payroll.")
        if not isinstance(payee_ids, list) or not payee_ids or len(payee_ids) > 200 or any(not isinstance(item, str) for item in payee_ids) or len(set(payee_ids)) != len(payee_ids):
            fail("INVALID_INPUT", "Select distinct payee identities to mark paid.")
        for payee_id in payee_ids:
            identifier(payee_id)
        snapshot = self._run_output(db, run)
        rows = {row["payee_id"]: row for row in snapshot["rows"]}
        if not set(payee_ids) <= set(rows):
            fail("NOT_FOUND", "A selected payee is not in this finalized payroll.")
        if set(payee_ids) & self._active_paid_ids(db, run["run_id"]):
            fail("ALREADY_PAID", "A selected payee is already recorded as paid.")
        return [rows[key] for key in sorted(payee_ids)]

    def _prepare_paid(self, db, args, actor):
        exact_keys(args, ("run_id", "expected_revision", "payee_ids", "request_id"))
        run = self._run(db, args["run_id"])
        self._revision(run, args["expected_revision"])
        rows = self._payment_candidates(db, run, args["payee_ids"])
        payload = {"run_id": run["run_id"], "expected_revision": run["revision"], "payee_ids": sorted(args["payee_ids"])}
        return self._pending(db, "paid", payload, {"run_id": run["run_id"], "period": run["period"], "rows": rows, "totals": totals(rows), "notice": "Record transfers already completed. This command does not transfer money."}, actor)

    def _update_payment_status(self, db, run_id):
        snapshot = json.loads(db.execute("SELECT data FROM snapshots WHERE run_id=?", (run_id,)).fetchone()[0])
        paid = self._active_paid_ids(db, run_id)
        status = "paid" if len(paid) == len(snapshot["rows"]) else "partially_paid" if paid else "finalized"
        db.execute("UPDATE runs SET status=?,updated_at=? WHERE run_id=?", (status, time.time(), run_id))

    def _paid(self, db, payload, actor):
        run = self._run(db, payload["run_id"])
        self._revision(run, payload["expected_revision"])
        rows = self._payment_candidates(db, run, payload["payee_ids"])
        payment_ids = []
        for row in rows:
            payment_id = str(uuid.uuid4())
            event_ids = []
            for allocation in row["loan_deductions"]:
                if not allocation["amount"]:
                    continue
                reservation = db.execute("SELECT * FROM reservations WHERE run_id=? AND payee_id=? AND loan_id=?", (run["run_id"], row["payee_id"], allocation["loan_id"])).fetchone()
                if not reservation or reservation["state"] != "active" or reservation["amount"] != allocation["amount"]:
                    fail("INTEGRITY_ERROR", "The finalized loan reservation is unavailable.")
                balance = self._loan_balance(db, allocation["loan_id"])
                if balance < self._reserved(db, allocation["loan_id"]):
                    fail("RESERVATION_CONFLICT", "Loan balance no longer covers finalized payroll.")
                event_ids.append(self._loan_event(db, allocation["loan_id"], "payroll_repayment", -allocation["amount"], {"run_id": run["run_id"], "payee_id": row["payee_id"], "payment_id": payment_id}, actor))
                db.execute("UPDATE reservations SET state='consumed' WHERE run_id=? AND payee_id=? AND loan_id=?", (run["run_id"], row["payee_id"], allocation["loan_id"]))
            data = {"run_id": run["run_id"], "payee_id": row["payee_id"], "net": row["net"], "loan_event_ids": event_ids, "actor": actor}
            db.execute("INSERT INTO payments VALUES (?,?,?,?,?,?)", (payment_id, run["run_id"], row["payee_id"], canonical(data), canonical(actor), time.time()))
            payment_ids.append(payment_id)
            self._audit(db, "payroll_paid", {"payment_id": payment_id, **data}, actor)
        self._update_payment_status(db, run["run_id"])
        return {"payment_ids": payment_ids, "run": self._run_output(db, self._run(db, run["run_id"]))}

    def _supersedable(self, db, run):
        if run["status"] != "finalized" or db.execute("SELECT 1 FROM payments WHERE run_id=?", (run["run_id"],)).fetchone():
            fail("INVALID_STATE", "Only an entirely unpaid finalized payroll can be superseded. Paid corrections require reversal records.")

    def _prepare_supersede(self, db, args, actor):
        exact_keys(args, ("run_id", "expected_revision", "reason", "request_id"))
        run = self._run(db, args["run_id"])
        self._revision(run, args["expected_revision"])
        self._supersedable(db, run)
        payload = {"run_id": run["run_id"], "expected_revision": run["revision"], "reason": text(args["reason"], "Reason")}
        return self._pending(db, "supersede", payload, {"run": self._run_output(db, run), "reason": payload["reason"], "notice": "Preserve this finalized snapshot and create a replacement draft. Its loan reservations will be released."}, actor)

    def _supersede(self, db, payload, actor):
        run = self._run(db, payload["run_id"])
        self._revision(run, payload["expected_revision"])
        self._supersedable(db, run)
        db.execute("UPDATE runs SET status='superseded',updated_at=? WHERE run_id=?", (time.time(), run["run_id"]))
        db.execute("UPDATE reservations SET state='released' WHERE run_id=? AND state='active'", (run["run_id"],))
        # The approval bumps the global version after execution. Store the new
        # draft against that future version so the returned draft is usable.
        successor = self._new_draft(db, run["period"], actor, json.loads(run["adjustments"]), run["run_id"])
        version = self.store.version(db) + 1
        successor["state_version"] = version
        stored = {key: value for key, value in successor.items() if key not in ("status", "stale", "payments")}
        db.execute("UPDATE runs SET state_version=?,draft=? WHERE run_id=?", (version, canonical(stored), successor["run_id"]))
        self._audit(db, "run_superseded", {**payload, "successor_run_id": successor["run_id"]}, actor)
        return {"superseded_run_id": run["run_id"], "run": successor}

    def _reversible_payment(self, db, payment_id):
        identifier(payment_id)
        payment = db.execute("SELECT * FROM payments WHERE payment_id=?", (payment_id,)).fetchone()
        if not payment:
            fail("NOT_FOUND", "Payroll payment was not found.")
        if db.execute("SELECT 1 FROM payment_reversals WHERE payment_id=?", (payment_id,)).fetchone():
            fail("ALREADY_REVERSED", "This payroll payment was already reversed.")
        return payment

    def _prepare_reverse_payment(self, db, args, actor):
        exact_keys(args, ("payment_id", "reason", "request_id"))
        payment = self._reversible_payment(db, args["payment_id"])
        payload = {"payment_id": payment["payment_id"], "reason": text(args["reason"], "Reason")}
        original = json.loads(payment["data"])
        original["payment_id"] = payment["payment_id"]
        original["loan_allocations"] = []
        for event_id in original["loan_event_ids"]:
            event = db.execute("SELECT * FROM loan_events WHERE event_id=?", (event_id,)).fetchone()
            original["loan_allocations"].append({"loan_id": event["loan_id"], "amount": -event["amount"], "event_id": event_id})
        return self._pending(db, "reverse_payment", payload, {"payment": original, "reason": payload["reason"], "notice": "Record a correction to a payment acknowledgment. No bank transfer is reversed. Loan deductions will be restored and reserved."}, actor)

    def _reverse_payment(self, db, payload, actor):
        payment = self._reversible_payment(db, payload["payment_id"])
        data = json.loads(payment["data"])
        reversal_id = str(uuid.uuid4())
        reversed_events = []
        for event_id in data["loan_event_ids"]:
            event = db.execute("SELECT * FROM loan_events WHERE event_id=?", (event_id,)).fetchone()
            if not event or event["kind"] != "payroll_repayment":
                fail("INTEGRITY_ERROR", "The payment's loan event is unavailable.")
            reversed_events.append(self._loan_event(db, event["loan_id"], "payroll_repayment_reversal", -event["amount"], {"reverses_event_id": event_id, "reversal_id": reversal_id, "reason": payload["reason"]}, actor))
            reservation = db.execute("SELECT * FROM reservations WHERE run_id=? AND payee_id=? AND loan_id=?", (payment["run_id"], payment["payee_id"], event["loan_id"])).fetchone()
            if not reservation or reservation["state"] != "consumed" or reservation["amount"] != -event["amount"]:
                fail("INTEGRITY_ERROR", "The consumed loan reservation is unavailable.")
            db.execute("UPDATE reservations SET state='active' WHERE run_id=? AND payee_id=? AND loan_id=?", (payment["run_id"], payment["payee_id"], event["loan_id"]))
            if self._reserved(db, event["loan_id"]) > self._loan_balance(db, event["loan_id"]):
                fail("RESERVATION_CONFLICT", "Reversal would conflict with a finalized loan reservation.")
        reversal = {"reversal_id": reversal_id, "payment_id": payment["payment_id"], "reason": payload["reason"], "loan_event_ids": reversed_events, "actor": actor}
        db.execute("INSERT INTO payment_reversals VALUES (?,?,?,?,?)", (reversal_id, payment["payment_id"], canonical(reversal), canonical(actor), time.time()))
        self._update_payment_status(db, payment["run_id"])
        self._audit(db, "payroll_payment_reversed", reversal, actor)
        return {"reversal": reversal, "run": self._run_output(db, self._run(db, payment["run_id"]))}

    def _history(self, db, args, actor):
        exact_keys(args, (), ("period", "payee_id", "limit"))
        limit = args.get("limit", 50)
        if type(limit) is not int or not 1 <= limit <= 200:
            fail("INVALID_INPUT", "History limit must be between 1 and 200.")
        if "period" in args:
            period(args["period"])
        if "payee_id" in args:
            identifier(args["payee_id"])
        query, params = "SELECT * FROM runs", []
        conditions = []
        if "period" in args:
            conditions.append("period=?")
            params.append(args["period"])
        if "payee_id" in args:
            conditions.append("EXISTS (SELECT 1 FROM json_each(COALESCE((SELECT data FROM snapshots WHERE snapshots.run_id=runs.run_id),runs.draft),'$.rows') WHERE json_extract(value,'$.payee_id')=?)")
            params.append(args["payee_id"])
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY period DESC,created_at DESC LIMIT ?"
        runs = [self._run_output(db, row) for row in db.execute(query, params + [limit])]
        events_query, event_params = "SELECT e.* FROM loan_events e JOIN loans l ON l.loan_id=e.loan_id", []
        if "payee_id" in args:
            events_query += " WHERE l.payee_id=?"
            event_params.append(args["payee_id"])
        events = [{"event_id": row["event_id"], "loan_id": row["loan_id"], "kind": row["kind"], "amount": row["amount"], "data": json.loads(row["data"]), "created_at": row["created_at"]} for row in db.execute(events_query + " ORDER BY e.created_at DESC LIMIT ?", event_params + [limit])]
        return {"runs": runs, "loan_events": events, "state_version": self.store.version(db)}
