import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from calculation import half_compensation, period_dates
from engine import PayrollEngine, PayrollError


ACTOR = {"senderId": "111", "chatId": "-222", "threadId": "3", "accountId": "payroll", "sessionKey": "agent:nomina:telegram:group:-222:topic:3", "channel": "telegram", "agentId": "nomina", "source": "tool_context"}


def native(actor=None):
    return {**(actor or ACTOR), "source": "native_command", "authorized": True, "approvedAt": time.time(), "approvalEventId": "test-native-event"}


def payee(payee_id="worker-a", **changes):
    return {"payee_id": payee_id, "display_name": "Synthetic " + payee_id, "kind": "employee", "monthly_salary": 2550905, "monthly_allowance": 249095, "health_per_half": 0, "pension_per_half": 0, "other_deduction_per_half": 0, "payment_destination": {"institution": "Test Institution", "account": "synthetic-0000"}, "effective_from": "2026-08-H1", "baseline_note": "Synthetic test baseline; no legal rule inferred.", "active": True, **changes}


def loan(loan_id="loan-a", payee_id="worker-a", **changes):
    return {"loan_id": loan_id, "payee_id": payee_id, "original_principal": 2100000, "opening_balance": 2100000, "as_of_date": "2026-08-01", "installment": 400000, "start_period": "2026-08-H1", "agreement_reference": "Synthetic written agreement", **changes}


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "state" / "payroll.db"
        self.engine = PayrollEngine(self.database)
        self.sequence = 0

    def tearDown(self):
        self.directory.cleanup()

    def request(self):
        self.sequence += 1
        return "telegram--222-" + str(self.sequence)

    def call(self, name, **args):
        if name in self.engine.MUTATIONS:
            args.setdefault("request_id", self.request())
        return self.engine.call(name, args, ACTOR)

    def confirm(self, pending):
        self.engine.review(pending["pending_id"], native())
        return self.engine.approve(pending["pending_id"], native())

    def change(self, kind, payload):
        return self.confirm(self.call("nomina_prepare_change", kind=kind, payload=payload))

    def seed(self, with_loan=True):
        self.change("upsert_payee", payee())
        if with_loan:
            self.change("open_loan", loan())

    def draft(self, period="2026-08-H1"):
        return self.call("nomina_prepare_run", period=period)

    def finalize(self, run):
        return self.confirm(self.call("nomina_prepare_finalize", run_id=run["run_id"], expected_revision=run["revision"]))["run"]

    def paid(self, run, payee_ids=None):
        return self.confirm(self.call("nomina_prepare_paid", run_id=run["run_id"], expected_revision=run["revision"], payee_ids=payee_ids or [row["payee_id"] for row in run["rows"]]))

    def loans(self):
        return self.call("nomina_get_loans")["loans"]

    def assertError(self, code, function, *args, **kwargs):
        with self.assertRaises(PayrollError) as context:
            function(*args, **kwargs)
        self.assertEqual(context.exception.code, code)


class PayrollArithmeticTests(EngineCase):
    def test_synthetic_accountant_example_and_honorarios(self):
        self.seed()
        for name in ("worker-b", "worker-c"):
            self.change("upsert_payee", payee(name, monthly_salary=1750905, health_per_half=35018, pension_per_half=35018))
        for name, value in (("contractor-a", 500000), ("contractor-b", 450000)):
            self.change("upsert_payee", payee(name, kind="contractor", monthly_salary=value, monthly_allowance=0))
        result = self.draft()
        self.assertEqual(result["totals"], {"employees": 2859928, "contractors": 475000, "gross": 3875000, "deductions": 540072, "loans": 400000, "net": 3334928})
        first = next(row for row in result["rows"] if row["payee_id"] == "worker-a")
        self.assertEqual((first["salary"], first["allowance"], first["gross"]), (1275452, 124548, 1400000))
        self.assertEqual(first["net"], 1000000)
        self.assertTrue(result["ready_to_finalize"])

    def test_rounding_reconciles_monthly_components_for_all_parities(self):
        for salary in (1000, 1001):
            for allowance in (100, 101):
                profile = payee(monthly_salary=salary, monthly_allowance=allowance)
                first = half_compensation(profile, "2026-08-H1")
                second = half_compensation(profile, "2026-08-H2")
                self.assertEqual(sum(first), (salary + allowance) // 2)
                self.assertEqual(first[0] + second[0], salary)
                self.assertEqual(first[1] + second[1], allowance)

    def test_half_month_boundaries_and_leap_february(self):
        self.assertEqual(period_dates("2026-02-H2"), ("2026-02-16", "2026-02-28"))
        self.assertEqual(period_dates("2028-02-H2"), ("2028-02-16", "2028-02-29"))
        self.assertEqual(period_dates("2026-12-H2"), ("2026-12-16", "2026-12-31"))
        self.assertEqual(period_dates("2026-04-H1"), ("2026-04-01", "2026-04-15"))
        self.assertError("INVALID_INPUT", self.draft, "2026-13-H1")

    def test_multiple_loans_final_cap_skip_only_current_half(self):
        self.seed()
        self.change("open_loan", loan("loan-b", original_principal=100000, opening_balance=50000, installment=100000))
        draft = self.draft()
        self.assertEqual(draft["totals"]["loans"], 450000)
        draft = self.call("nomina_adjust_draft", run_id=draft["run_id"], expected_revision=draft["revision"], payee_id="worker-a", kind="loan_override", amount=0, loan_id="loan-a", reason="Skip this half only")
        self.assertEqual(draft["totals"]["loans"], 50000)
        self.paid(self.finalize(draft))
        self.assertEqual([item["outstanding"] for item in self.loans()], [2100000, 0])
        next_half = self.draft("2026-08-H2")
        self.assertEqual(next_half["totals"]["loans"], 400000)
        self.assertEqual(next_half["adjustments"], [])

    def test_negative_draft_can_be_corrected_but_cannot_be_finalized(self):
        self.seed()
        self.change("change_installment", {"loan_id": "loan-a", "installment": 2000000, "start_period": "2026-08-H1", "reason": "Synthetic oversized schedule"})
        draft = self.draft()
        self.assertFalse(draft["ready_to_finalize"])
        self.assertError("NEGATIVE_NET", self.finalize, draft)
        draft = self.call("nomina_adjust_draft", run_id=draft["run_id"], expected_revision=1, payee_id="worker-a", kind="loan_override", amount=400000, loan_id="loan-a", reason="Explicit correction")
        self.assertTrue(draft["ready_to_finalize"])
        self.assertEqual(self.finalize(draft)["status"], "finalized")

    def test_draft_adjustment_replaces_same_kind_and_zero_clears(self):
        self.seed(False)
        draft = self.draft()
        for value in (200000, 100000, 0):
            draft = self.call("nomina_adjust_draft", run_id=draft["run_id"], expected_revision=draft["revision"], payee_id="worker-a", kind="earning", amount=value, reason="Set approved bonus")
            self.assertEqual(draft["totals"]["gross"], 1400000 + value)
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM audit WHERE kind='draft_adjusted'").fetchone()[0], 3)

    def test_money_rejects_float_bool_negative_missing_deductions(self):
        for value in (1.5, True, -1, 10 ** 14):
            self.assertError("INVALID_INPUT", self.change, "upsert_payee", payee(monthly_salary=value))
        payload = payee()
        del payload["health_per_half"]
        self.assertError("INVALID_INPUT", self.change, "upsert_payee", payload)
        self.assertError("INVALID_INPUT", self.change, "upsert_payee", payee(baseline_note=""))
        self.assertEqual(self.call("nomina_list_payees")["payees"], [])


class PayrollApprovalTests(EngineCase):
    def test_approval_requires_exact_native_review_before_execution(self):
        pending = self.call("nomina_prepare_change", kind="upsert_payee", payload=payee())
        self.assertError("REVIEW_REQUIRED", self.engine.approve, pending["pending_id"], native())
        self.assertError("AUTH_REQUIRED", self.engine.review, pending["pending_id"], ACTOR)
        review = self.engine.review(pending["pending_id"], native(), render=lambda item: "Shown " + item["preview"]["display_name"])
        self.assertEqual(review["preview"]["payment_destination"]["account"], "synthetic-0000")
        self.assertTrue(review["summary"].startswith("Shown "))
        self.engine.approve(pending["pending_id"], native())

    def test_failed_native_render_does_not_create_review_marker(self):
        pending = self.call("nomina_prepare_change", kind="upsert_payee", payload=payee())
        def failing_renderer(item):
            raise PayrollError("PREVIEW_TOO_LARGE", "Synthetic oversized preview")
        self.assertError("PREVIEW_TOO_LARGE", self.engine.review, pending["pending_id"], native(), render=failing_renderer)
        self.assertError("REVIEW_REQUIRED", self.engine.approve, pending["pending_id"], native())
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM pending_reviews").fetchone()[0], 0)

    def test_modified_saved_preview_fails_hash_even_when_payload_unchanged(self):
        pending = self.call("nomina_prepare_change", kind="upsert_payee", payload=payee())
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE pending SET preview='{}' WHERE token=?", (pending["pending_id"],))
        self.assertError("INVALID_CONFIRMATION", self.engine.review, pending["pending_id"], native())

    def test_draft_mutations_advance_backup_version_and_host_event_survives_audit(self):
        actor = {**ACTOR, "sourceEventId": "tool-call:synthetic-provider-event"}
        pending = self.engine.call("nomina_prepare_change", {"kind": "upsert_payee", "payload": payee(), "request_id": self.request()}, actor)
        self.confirm(pending)
        first = self.call("nomina_status")["state_version"]
        draft = self.draft()
        second = self.call("nomina_status")["state_version"]
        self.assertGreater(second, first)
        adjusted = self.call("nomina_adjust_draft", run_id=draft["run_id"], expected_revision=1, payee_id="worker-a", kind="earning", amount=1000, reason="Synthetic bonus")
        self.assertGreater(adjusted["state_version"], second)
        with sqlite3.connect(self.database) as db:
            saved_actor = json.loads(db.execute("SELECT actor FROM audit WHERE kind='action_prepared' LIMIT 1").fetchone()[0])
            self.assertEqual(saved_actor["sourceEventId"], actor["sourceEventId"])

    def test_model_cannot_approve_and_native_route_is_bound(self):
        pending = self.call("nomina_prepare_change", kind="upsert_payee", payload=payee())
        self.assertError("AUTH_REQUIRED", self.engine.approve, pending["pending_id"], ACTOR)
        for field, value in (("senderId", "999"), ("chatId", "-999"), ("threadId", "99"), ("accountId", "other-bot"), ("sessionKey", "another-session")):
            self.assertError("WRONG_APPROVER", self.engine.approve, pending["pending_id"], native({**ACTOR, field: value}))
        self.assertError("AUTH_REQUIRED", self.engine.approve, pending["pending_id"], {**native(), "authorized": False})
        self.assertError("AUTH_REQUIRED", self.engine.approve, pending["pending_id"], {**native(), "approvedAt": float("nan")})
        self.assertEqual(self.confirm(pending)["payee"]["payee_id"], "worker-a")

    def test_expired_confirmation_and_payload_hash_fail_closed(self):
        pending = self.call("nomina_prepare_change", kind="upsert_payee", payload=payee())
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE pending SET expires_at=0 WHERE token=?", (pending["pending_id"],))
        self.assertError("CONFIRMATION_EXPIRED", self.confirm, pending)
        pending = self.call("nomina_prepare_change", kind="upsert_payee", payload=payee())
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE pending SET payload='{}' WHERE token=?", (pending["pending_id"],))
        self.assertError("INVALID_CONFIRMATION", self.confirm, pending)

    def test_request_and_confirmation_replay_are_exactly_once(self):
        args = {"kind": "upsert_payee", "payload": payee(), "request_id": "telegram--222-42"}
        first = self.engine.call("nomina_prepare_change", args, ACTOR)
        self.assertEqual(self.engine.call("nomina_prepare_change", args, ACTOR), first)
        changed = copy.deepcopy(args)
        changed["payload"]["monthly_salary"] += 100
        self.assertError("IDEMPOTENCY_CONFLICT", self.engine.call, "nomina_prepare_change", changed, ACTOR)
        result = self.confirm(first)
        self.assertEqual(self.confirm(first), result)
        self.assertEqual(self.call("nomina_status")["state_version"], 1)
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM profiles").fetchone()[0], 1)

    def test_changed_draft_invalidates_previous_finalize_token(self):
        self.seed()
        draft = self.draft()
        pending = self.call("nomina_prepare_finalize", run_id=draft["run_id"], expected_revision=1)
        draft = self.call("nomina_adjust_draft", run_id=draft["run_id"], expected_revision=1, payee_id="worker-a", kind="loan_override", loan_id="loan-a", amount=0, reason="Skip")
        self.assertError("STALE_PREVIEW", self.confirm, pending)
        self.assertEqual(self.loans()[0]["reserved"], 0)
        self.assertEqual(self.finalize(draft)["totals"]["net"], 1400000)

    def test_outside_repayment_invalidates_draft_and_confirmation(self):
        self.seed()
        draft = self.draft()
        pending = self.call("nomina_prepare_finalize", run_id=draft["run_id"], expected_revision=1)
        self.change("outside_repayment", {"loan_id": "loan-a", "amount": 1800000, "paid_on": "2026-08-10", "reference": "external-1", "reason": "Cash acknowledgment"})
        self.assertError("STALE_PREVIEW", self.confirm, pending)
        self.assertTrue(self.call("nomina_get_run", run_id=draft["run_id"])["stale"])
        self.assertError("STALE_PREVIEW", self.finalize, draft)
        refreshed = self.draft()
        self.assertEqual(refreshed["revision"], 2)
        self.assertEqual(refreshed["totals"]["loans"], 300000)
        self.finalize(refreshed)

    def test_multiple_prepare_actions_do_not_stale_each_other_until_execution(self):
        first = self.call("nomina_prepare_change", kind="upsert_payee", payload=payee())
        second = self.call("nomina_prepare_change", kind="upsert_payee", payload=payee("worker-b"))
        self.confirm(first)
        self.assertError("STALE_PREVIEW", self.confirm, second)

    def test_parallel_confirmation_executes_once(self):
        pending = self.call("nomina_prepare_change", kind="upsert_payee", payload=payee())
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: self.confirm(pending), range(4)))
        self.assertTrue(all(item == results[0] for item in results))
        self.assertEqual(self.call("nomina_status")["state_version"], 1)

    def test_concurrent_finalizations_cannot_overreserve(self):
        self.seed()
        self.change("outside_repayment", {"loan_id": "loan-a", "amount": 1600000, "paid_on": "2026-08-02", "reference": "reduce-test", "reason": "Synthetic repayment"})
        draft = self.draft()
        pending = [self.call("nomina_prepare_finalize", run_id=draft["run_id"], expected_revision=1) for _ in range(2)]
        def approve(item):
            try:
                return self.confirm(item)
            except PayrollError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(approve, pending))
        self.assertEqual(sum(isinstance(item, dict) for item in results), 1)
        self.assertIn("STALE_PREVIEW", results)
        self.assertEqual(self.loans()[0]["reserved"], 400000)
        updated = self.draft("2026-08-H2")
        self.assertEqual(updated["totals"]["loans"], 100000)
        self.finalize(updated)
        self.assertEqual(self.loans()[0]["reserved"], 500000)


class PayrollLifecycleTests(EngineCase):
    def test_deactivated_payee_adjustments_remain_visible_without_deadlocking_draft(self):
        self.seed()
        self.change("upsert_payee", payee("worker-b"))
        draft = self.draft()
        draft = self.call("nomina_adjust_draft", run_id=draft["run_id"], expected_revision=1, payee_id="worker-a", kind="loan_override", loan_id="loan-a", amount=0, reason="One-time skip before roster changed")
        self.change("upsert_payee", payee(active=False))
        refreshed = self.draft()
        self.assertEqual([row["payee_id"] for row in refreshed["rows"]], ["worker-b"])
        self.assertEqual(refreshed["excluded_adjustments"][0]["payee_id"], "worker-a")
        self.assertEqual(refreshed["warnings"][0]["code"], "EXCLUDED_ADJUSTMENTS")
        self.assertTrue(refreshed["ready_to_finalize"])
        self.assertEqual(self.finalize(refreshed)["totals"]["loans"], 0)

    def test_empty_draft_is_inspectable_but_cannot_finalize(self):
        draft = self.draft()
        self.assertFalse(draft["ready_to_finalize"])
        self.assertError("NO_PAYEES", self.finalize, draft)
        self.seed(False)
        refreshed = self.draft()
        self.assertEqual(refreshed["run_id"], draft["run_id"])
        self.assertTrue(refreshed["ready_to_finalize"])

    def test_finalized_is_not_paid_and_partial_payment_deducts_only_selected(self):
        self.seed()
        self.change("upsert_payee", payee("worker-b"))
        self.change("open_loan", loan("loan-b", "worker-b"))
        final = self.finalize(self.draft())
        self.assertEqual([(item["outstanding"], item["reserved"]) for item in self.loans()], [(2100000, 400000), (2100000, 400000)])
        result = self.paid(final, ["worker-a"])
        self.assertEqual(result["run"]["status"], "partially_paid")
        self.assertEqual([(item["outstanding"], item["reserved"]) for item in self.loans()], [(1700000, 0), (2100000, 400000)])
        self.assertError("ALREADY_PAID", self.paid, final, ["worker-a"])
        self.assertEqual(self.paid(final, ["worker-b"])["run"]["status"], "paid")
        self.assertEqual(self.draft()["status"], "paid")
        self.assertError("INVALID_STATE", self.paid, final)

    def test_unpaid_supersede_preserves_snapshot_releases_reservations(self):
        self.seed()
        final = self.finalize(self.draft())
        snapshot = self.engine.snapshot(final["run_id"])
        supersede = self.call("nomina_prepare_supersede", run_id=final["run_id"], expected_revision=1, reason="Skip requested before payment")
        result = self.confirm(supersede)
        replacement = result["run"]
        self.assertNotEqual(replacement["run_id"], final["run_id"])
        self.assertEqual(replacement["supersedes_run_id"], final["run_id"])
        self.assertEqual(replacement["status"], "draft")
        self.assertFalse(self.call("nomina_get_run", run_id=replacement["run_id"])["stale"])
        old = self.engine.snapshot(final["run_id"])
        self.assertEqual(old["rows"], snapshot["rows"])
        self.assertEqual(old["status"], "superseded")
        self.assertEqual(self.loans()[0]["reserved"], 0)
        changed = self.call("nomina_adjust_draft", run_id=replacement["run_id"], expected_revision=1, payee_id="worker-a", kind="loan_override", loan_id="loan-a", amount=0, reason="Approved one-time skip")
        self.finalize(changed)
        history = self.call("nomina_history", period="2026-08-H1")
        self.assertEqual(len(history["runs"]), 2)

    def test_payment_reversal_is_append_only_restores_reservation_and_can_repay(self):
        self.seed()
        final = self.finalize(self.draft())
        paid = self.paid(final)
        reversal = self.call("nomina_prepare_reverse_payment", payment_id=paid["payment_ids"][0], reason="Transfer was not completed")
        result = self.confirm(reversal)
        self.assertEqual(result["run"]["status"], "finalized")
        self.assertTrue(result["run"]["payments"][0]["reversed"])
        self.assertEqual((self.loans()[0]["outstanding"], self.loans()[0]["reserved"]), (2100000, 400000))
        self.assertError("ALREADY_REVERSED", self.call, "nomina_prepare_reverse_payment", payment_id=paid["payment_ids"][0], reason="Again")
        self.assertError("INVALID_STATE", self.call, "nomina_prepare_supersede", run_id=final["run_id"], expected_revision=1, reason="Cannot erase paid history")
        repaid = self.paid(final)
        self.assertEqual(len(repaid["run"]["payments"]), 2)
        self.assertEqual(self.loans()[0]["outstanding"], 1700000)
        with sqlite3.connect(self.database) as db:
            for table in ("snapshots", "loan_events", "payments", "payment_reversals", "audit", "profiles", "loans", "terms"):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("DELETE FROM " + table)

    def test_reservations_block_outside_repayment_and_reversal_keeps_balance_valid(self):
        self.seed()
        final = self.finalize(self.draft())
        self.assertError("RESERVATION_CONFLICT", self.change, "outside_repayment", {"loan_id": "loan-a", "amount": 1800000, "paid_on": "2026-08-14", "reference": "bad", "reason": "Would spend reserved amount"})
        outside = self.change("outside_repayment", {"loan_id": "loan-a", "amount": 1700000, "paid_on": "2026-08-14", "reference": "outside-1", "reason": "Cash receipt"})
        self.assertEqual(self.loans()[0]["outstanding"], 400000)
        self.paid(final)
        self.assertEqual(self.loans()[0]["outstanding"], 0)
        self.change("reverse_loan_event", {"event_id": outside["event_id"], "reason": "Receipt entered in error"})
        self.assertEqual(self.loans()[0]["outstanding"], 1700000)
        self.assertError("ALREADY_REVERSED", self.change, "reverse_loan_event", {"event_id": outside["event_id"], "reason": "Again"})

    def test_duplicate_outside_reference_does_not_reapply_balance(self):
        self.seed()
        payload = {"loan_id": "loan-a", "amount": 100000, "paid_on": "2026-08-14", "reference": "receipt-1", "reason": "Cash receipt"}
        self.change("outside_repayment", payload)
        self.assertError("ALREADY_EXISTS", self.change, "outside_repayment", payload)
        self.assertEqual(self.loans()[0]["outstanding"], 2000000)

    def test_historical_profiles_and_immutable_destinations(self):
        self.seed(False)
        august = self.finalize(self.draft())
        original = self.engine.snapshot(august["run_id"])
        self.change("upsert_payee", payee(monthly_salary=3550905, effective_from="2026-09-H1", payment_destination={"institution": "New Institution", "account": "synthetic-9999"}))
        self.assertEqual(self.draft("2026-08-H2")["totals"]["gross"], 1400000)
        september = self.draft("2026-09-H1")
        self.assertEqual(september["totals"]["gross"], 1900000)
        self.assertEqual(self.engine.snapshot(august["run_id"])["rows"], original["rows"])
        self.assertEqual(original["rows"][0]["profile"]["payment_destination"]["account"], "synthetic-0000")
        self.assertEqual(august["rows"][0]["profile"]["payment_destination"]["account"], "***0000")
        self.assertEqual(september["rows"][0]["profile"]["payment_destination"]["account"], "***9999")

    def test_installment_effective_period_does_not_rewrite_earlier_terms(self):
        self.seed()
        self.change("change_installment", {"loan_id": "loan-a", "installment": 100000, "start_period": "2026-09-H1", "reason": "New agreement"})
        self.assertEqual(self.draft()["totals"]["loans"], 400000)
        self.assertEqual(self.draft("2026-09-H1")["totals"]["loans"], 100000)

    def test_exception_during_payment_rolls_back_balance_and_status(self):
        self.seed()
        final = self.finalize(self.draft())
        pending = self.call("nomina_prepare_paid", run_id=final["run_id"], expected_revision=1, payee_ids=["worker-a"])
        with patch.object(self.engine, "_update_payment_status", side_effect=RuntimeError("simulated crash")):
            with self.assertRaises(RuntimeError):
                self.confirm(pending)
        self.assertEqual((self.loans()[0]["outstanding"], self.loans()[0]["reserved"]), (2100000, 400000))
        self.assertEqual(self.call("nomina_get_run", run_id=final["run_id"])["payments"], [])
        self.assertEqual(self.confirm(pending)["run"]["status"], "paid")

    def test_history_filter_and_restart_recover_persisted_state(self):
        self.seed()
        run = self.finalize(self.draft())
        self.paid(run)
        self.engine = PayrollEngine(self.database)
        self.assertEqual(self.loans()[0]["outstanding"], 1700000)
        found = self.call("nomina_history", payee_id="worker-a")
        self.assertEqual(len(found["runs"]), 1)
        self.assertEqual(found["runs"][0]["status"], "paid")
        self.assertEqual(len(found["loan_events"]), 2)
        self.assertEqual(self.call("nomina_history", payee_id="nonexistent")["runs"], [])


if __name__ == "__main__":
    unittest.main()
