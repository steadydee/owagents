#!/usr/bin/env python3
"""Nómina service boundary. Native approvals are deliberately not tools."""
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

from catalog import TOOLS, validate_call
from security import BoundaryError, private_directory, runtime_config, trusted_actor


def state_version(engine, actor):
    # Internal service read after the caller's provenance has been validated.
    # Native confirmations must never be reclassified as model tool calls.
    with engine.store.connection() as database:
        return engine.store.version(database)


def after_commit(root, config, engine, database, actor, result):
    """Archiving failure is reported separately from the committed transaction."""
    from archive import Archive
    from reports import export_run
    outbox = None
    try:
        report = None
        run_id = result.get("run_id")
        if run_id:
            snapshot = engine.snapshot(run_id)
            if snapshot["status"] != "draft":
                report = export_run(root, snapshot)
        outbox = Archive(root, config)
        return outbox.enqueue(database, state_version(engine, actor), report)
    except Exception:
        return {"status": "needs_retry", "message": "Payroll was saved. Its archive needs attention; query archive status and retry."}
    finally:
        if outbox:
            outbox.close()


def confirmation_summary(result, archive_state):
    action = result.get("action", "change")
    descriptions = {
        "upsert_payee": "Configuración de la persona guardada.",
        "open_loan": "Préstamo y saldo inicial guardados.",
        "change_installment": "Cuota futura guardada.",
        "outside_repayment": "Abono externo registrado.",
        "reverse_loan_event": "Corrección del préstamo registrada con historial.",
        "finalize": "Nómina finalizada. Los descuentos de préstamos están reservados; falta confirmar el pago.",
        "paid": "Pago registrado y abonos a préstamos aplicados. No se ejecutó una transferencia bancaria.",
        "supersede": "Revisión anterior conservada. Se creó un borrador de reemplazo.",
        "reverse_payment": "Corrección de pago registrada con historial. No se ejecutó una devolución bancaria.",
    }
    message = descriptions.get(action, "Confirmación registrada. Consulta el estado actualizado de la nómina.")
    if result.get("run_id") and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", result["run_id"]):
        message += " Referencia: " + result["run_id"] + "."
    run = result.get("run", {})
    totals = result.get("totals", run.get("totals"))
    if action == "paid":
        paid_ids = set(result.get("payment_ids", []))
        totals = {"net": sum(payment["net"] for payment in run.get("payments", []) if payment["payment_id"] in paid_ids)}
    if isinstance(totals, dict) and type(totals.get("net")) is int:
        message += " Total: COP " + f"{totals['net']:,}" + "."
    if archive_state.get("status") != "uploaded":
        message += " Copia externa pendiente; consulta el estado del archivo."
    return message


def execute(command, name, arguments=None):
    # Import lazily: catalog/SDK discovery must work before private setup exists.
    from engine import PayrollEngine
    root, config = runtime_config()
    actor = trusted_actor(config, native=command in ("approve", "review"))
    state = private_directory(root / "state", root)
    database = state / "payroll.sqlite3"
    if database.is_symlink():
        raise BoundaryError("UNSAFE_PATH", "Payroll database must not be a symbolic link.")
    engine = PayrollEngine(database)
    if command == "review":
        from native_review import render_native
        if not re.fullmatch(r"[A-F0-9]{16,32}", name):
            raise BoundaryError("INVALID_CONFIRMATION", "Use the exact review command from the current preview.")
        review = engine.review(name, actor, render=render_native)
        return {"ok": True, "result": {"pending_id": review["pending_id"], "action": review["action"]},
                "summary": review["summary"]}
    if command == "approve":
        if not re.fullmatch(r"[A-F0-9]{16,32}", name):
            raise BoundaryError("INVALID_CONFIRMATION", "Use the exact confirmation command from the current preview.")
        result = engine.approve(name, actor)
        archive_state = after_commit(root, config, engine, database, actor, result)
        return {"ok": True, "result": result, "archive": archive_state,
                "summary": confirmation_summary(result, archive_state)}
    validate_call(name, arguments)
    if name == "nomina_export":
        from reports import export_run
        result = export_run(root, engine.snapshot(arguments["run_id"]))
        archived = after_commit(root, config, engine, database, actor, {"run_id": arguments["run_id"]})
        return {"ok": True, "result": result, "archive": archived}
    if name == "nomina_archive_status":
        from archive import Archive
        outbox = Archive(root, config)
        try:
            version = state_version(engine, actor)
            result = outbox.status(version)
            if arguments.get("retry"):
                # Also repairs the window between a payroll commit and enqueue.
                if result["needs_backup"]:
                    outbox.enqueue(database, version)
                outbox.retry()
                result = outbox.status(state_version(engine, actor))
            return {"ok": True, "result": result}
        finally:
            outbox.close()
    result = engine.call(name, arguments, actor)
    response = {"ok": True, "result": result}
    if name in ("nomina_prepare_run", "nomina_adjust_draft"):
        response["archive"] = after_commit(root, config, engine, database, actor, result)
    return response


def main():
    os.umask(0o077)
    try:
        command = sys.argv[1] if len(sys.argv) > 1 else ""
        if command == "catalog" and len(sys.argv) == 2:
            print(json.dumps(TOOLS, ensure_ascii=False))
            return
        if command == "restore-backup" and len(sys.argv) == 5:
            # Local operator maintenance only: not exported by plugin or catalog.
            from archive import restore_backup
            result = {"ok": True, "result": restore_backup(*sys.argv[2:])}
        elif command in ("call", "approve", "review") and len(sys.argv) == 3:
            arguments = {}
            if command == "call":
                raw = sys.stdin.buffer.read(65537)
                if len(raw) > 65536:
                    raise ValueError("Payroll arguments exceed the allowed size")
                arguments = json.loads(raw)
            result = execute(command, sys.argv[2], arguments)
        else:
            raise ValueError("Use catalog, call TOOL, review TOKEN, approve TOKEN, or offline restore-backup BACKUP KEY NEW_DIRECTORY")
    except Exception as error:
        from engine import PayrollError
        if isinstance(error, (BoundaryError, PayrollError)):
            result = {"ok": False, "error": {"code": error.code, "message": str(error), "retryable": error.retryable}}
        elif isinstance(error, (ValueError, KeyError, TypeError)):
            result = {"ok": False, "error": {"code": "INVALID_INPUT", "message": "Invalid or incomplete payroll request. Check the tool's required fields.", "retryable": False}}
        elif isinstance(error, sqlite3.OperationalError):
            result = {"ok": False, "error": {"code": "STATE_UNAVAILABLE", "message": "Payroll state is temporarily unavailable. Keep the same request or confirmation reference when retrying.", "retryable": True}}
        else:
            result = {"ok": False, "error": {"code": "SERVICE_ERROR", "message": "Payroll request did not complete. Check its current state before preparing another action.", "retryable": False, "uncertain": True}}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
