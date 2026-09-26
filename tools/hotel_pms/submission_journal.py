"""Crash-safe submission state. Unknown outcomes require reconciliation.

Provider side effects are not transactionally coupled to PMS. Persist the claim
before each step and the receipt after each step. A surviving `sending` record
means that a process may have died after the provider accepted the request.
"""
from contextvars import ContextVar
from durable_state import StateError, fingerprint, locked_record

_ACTIVE = ContextVar("hotel_submission_journal", default=None)


def unknown():
    return StateError("government_outcome_unknown", "El envío pudo haberse recibido. Revisa el comprobante en el portal y reconcilia el registro; no se reenviará automáticamente.")


class Attempt:
    def __init__(self, record, save):
        self.record, self.save = record, save

    def step(self, name, call, verify=None):
        steps = self.record.setdefault("steps", {})
        prior = steps.get(name, {})
        if prior.get("state") == "confirmed":
            return prior["result"]
        if prior.get("state") in {"sending", "unknown"}:
            raise unknown()
        steps[name] = {"state": "sending"}
        self.save()
        try:
            result = call()
            if verify is not None and not verify(result):
                steps[name] = {"state": "unknown"}
                self.save()
                raise unknown()
            steps[name] = {"state": "confirmed", "result": result}
            self.save()
            return result
        except BaseException:
            # Even a shutdown between the network return and persist is unknown.
            # Do not include provider responses, IDs or exception text in errors.
            if steps[name].get("state") != "confirmed":
                steps[name] = {"state": "unknown"}
                self.save()
            raise


def external_step(name, call, verify=None):
    attempt = _ACTIVE.get()
    # Direct unit tests exercise adapters without a tool invocation. Production
    # always enters execute() before adapters are reached.
    if attempt is None:
        return call()
    return attempt.step(name, call, verify)


def execute(root, key, payload, submit, persist_receipt):
    with locked_record(root, key) as (record, save):
        digest = fingerprint(payload)
        if record.get("payloadHash") and record["payloadHash"] != digest:
            raise StateError("government_payload_changed", "El envío ya tiene un intento con otros datos. Reconcílialo antes de modificarlo o reenviarlo.")
        if record.get("state") == "recorded":
            return record["outcome"], record["recorded"]
        if record.get("state") == "unknown":
            raise unknown()
        if not record:
            record.update(version=1, payloadHash=digest, state="prepared", steps={})
            save()
        outcome = record.get("outcome")
        if not outcome:
            record["state"] = "submitting"
            save()
            token = _ACTIVE.set(Attempt(record, save))
            try:
                outcome = submit()
            except BaseException:
                record["state"] = "unknown"
                save()
                raise unknown()
            finally:
                _ACTIVE.reset(token)
            if isinstance(outcome, dict) and outcome.get("status") == "blocked" and not record.get("steps"):
                # Pure local readiness/config checks performed no network write.
                record["state"] = "prepared"
                save()
                return outcome, None
            if not isinstance(outcome, dict) or outcome.get("status") != "submitted" or not outcome.get("receiptReference"):
                # Adapters can fail after a server has accepted a write. An
                # unverified response is never evidence that nothing happened.
                record.update(state="unknown", lastReason=(outcome or {}).get("reason") if isinstance(outcome, dict) else "invalid_response")
                save()
                raise unknown()
            record.update(state="receipt_pending", outcome=outcome)
            save()
        # A PMS outage must not lose a known external receipt. Subsequent calls
        # execute only this branch, even if the process was killed here.
        try:
            recorded = persist_receipt(outcome)
        except Exception as exc:
            raise StateError("government_receipt_pending", "El portal confirmó el envío; falta guardar el comprobante en PMS. Repite la misma operación para guardar el comprobante sin reenviar.", retryable=True) from exc
        record.update(state="recorded", recorded=recorded)
        save()
        return outcome, recorded
