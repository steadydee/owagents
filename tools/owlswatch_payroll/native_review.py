"""Deterministic human-only review text: no model paraphrase grants approval."""
import datetime as dt
import re

from security import BoundaryError


def money(value):
    return f"COP {value:,}"


def render_native(review):
    if review.get("already_confirmed"):
        return "Esta acción ya fue confirmada. Consulta la nómina actual; no prepares una operación duplicada."
    action, preview = review["action"], review["preview"]
    lines = ["REVISIÓN DE NÓMINA · " + action]
    if action == "upsert_payee":
        lines += [f"{preview['display_name']} [{preview['payee_id']}] · {preview['kind']}",
                  f"Vigencia: {preview['effective_from']} · activo: {preview['active']}",
                  "Salario mensual: " + money(preview["monthly_salary"]),
                  "Auxilio mensual: " + money(preview["monthly_allowance"]),
                  "Por quincena: salud " + money(preview["health_per_half"]) + ", pensión " + money(preview["pension_per_half"]) + ", otros descuentos " + money(preview["other_deduction_per_half"]),
                  "Destino completo: " + preview["payment_destination"]["institution"] + " / " + preview["payment_destination"]["account"],
                  "Base y reglas aprobadas: " + preview["baseline_note"],
                  "Redondeo: primera mitad = piso del total mensual / 2; segunda mitad = saldo del mes. Salud/pensión son importes explícitos aprobados."]
    elif action == "open_loan":
        lines += [f"Préstamo {preview['loan_id']} · persona {preview['payee_id']}",
                  "Principal original: " + money(preview["original_principal"]),
                  "Saldo inicial confirmado: " + money(preview["opening_balance"]) + " al " + preview["as_of_date"],
                  "Cuota por nómina: " + money(preview["installment"]) + " desde " + preview["start_period"],
                  "Acuerdo: " + preview["agreement_reference"],
                  "El saldo inicial no demuestra ni vuelve a aplicar pagos históricos."]
    elif action == "change_installment":
        lines += ["Préstamo " + preview["loan_id"], "Nueva cuota: " + money(preview["installment"]),
                  "Desde: " + preview["start_period"], "Motivo: " + preview["reason"]]
    elif action == "outside_repayment":
        lines += ["Préstamo " + preview["loan_id"], "Abono real recibido: " + money(preview["amount"]),
                  "Fecha: " + preview["paid_on"], "Referencia: " + preview["reference"], "Motivo: " + preview["reason"]]
    elif action == "reverse_loan_event":
        lines += ["Revertir abono externo: " + preview["event_id"], "Préstamo: " + preview["loan_id"],
                  "Monto que volverá al saldo: " + money(preview["amount"]),
                  "Fecha del abono: " + str(preview.get("paid_on", "")),
                  "Referencia: " + str(preview.get("reference", "")), "Motivo: " + preview["reason"]]
    elif action == "reverse_payment":
        payment = preview["payment"]
        lines += ["Corregir reconocimiento de pago: " + payment["run_id"], "Persona: " + payment["payee_id"],
                  "Pago: " + payment["payment_id"],
                  "Neto registrado: " + money(payment["net"]), "Motivo: " + preview["reason"],
                  "Los abonos de préstamos de este pago se restaurarán y reservarán para su nómina. No se devuelve dinero por banco."]
        for loan in payment.get("loan_allocations", []):
            lines.append("Préstamo " + loan["loan_id"] + ": restaurar y reservar " + money(loan["amount"]))
    elif action in ("finalize", "paid", "supersede"):
        run = preview.get("run", preview)
        lines += [f"{run['period']} · {run['run_id']} · revisión {run.get('revision', 'confirmada')}"]
        for row in run["rows"]:
            bank = row["profile"]["payment_destination"]
            loans = "; ".join(item["loan_id"] + "=" + money(item["amount"]) + " (saldo antes " + money(item["outstanding_before"]) + ", otras reservas " + money(item["reserved_elsewhere"]) + ")" +
                              (" (omitir esta vez)" if item.get("override_reason") and item["amount"] == 0 else "")
                              for item in row["loan_deductions"]) or "ninguno"
            lines += [f"\n{row['display_name']} [{row['payee_id']}] · {row['kind']}",
                      "Salario " + money(row["salary"]) + " + auxilio " + money(row["allowance"]) + " + extras " + money(row["extra_earnings"]),
                      "Salud " + money(row["health"]) + "; pensión " + money(row["pension"]) + "; otros " + money(row["other_deduction"]) + "; ajuste " + money(row["extra_deductions"]),
                      "Préstamos: " + loans,
                      "NETO " + money(row["net"]) + " · " + bank["institution"] + " /***" + bank["account"][-4:]]
        totals = run["totals"]
        lines += ["\nEmpleados: " + money(totals["employees"]) + " · Honorarios: " + money(totals["contractors"]),
                  "TOTAL: " + money(totals["net"]) + " · Préstamos: " + money(totals["loans"])]
        for adjustment in run.get("adjustments", []):
            lines.append(f"Ajuste {adjustment['payee_id']} {adjustment['kind']}: {money(adjustment['amount'])} · {adjustment['reason']}")
        for adjustment in run.get("excluded_adjustments", []):
            lines.append("AJUSTE EXCLUIDO por persona inactiva: " + str(adjustment.get("payee_id")) + " · " + str(adjustment.get("reason", "")))
        lines.append({"finalize": "Finalizar conserva esta nómina y reserva los préstamos. NO registra el pago.",
                      "paid": "Confirmar registra que estos pagos ya se hicieron y reduce saldos de préstamos. NO ejecuta transferencias.",
                      "supersede": "Se conservará esta revisión y se liberarán sus reservas; se abrirá un borrador de reemplazo. Motivo: " + preview.get("reason", "")}[action])
    else:
        raise BoundaryError("INVALID_REVIEW", "Unsupported payroll review action.")
    if preview.get("notice"):
        lines.append(preview["notice"])
    expires = dt.datetime.fromtimestamp(review["expires_at"], dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=-5)))
    lines += ["\nVence: " + expires.strftime("%Y-%m-%d %H:%M Bogotá"),
              "Si estos datos son correctos, envía exactamente:", review["confirmation_command"]]
    # Native Telegram delivery parses Markdown. Put the full reviewed data in a
    # literal block with a fence longer than any external backtick run; account
    # underscores, brackets or asterisks must never change the displayed value.
    literal = "\n".join(lines[:-2])
    fence = "`" * max(3, 1 + max((len(m.group()) for m in re.finditer(r"`+", literal)), default=0))
    summary = fence + "\n" + literal + "\n" + fence + "\n\n" + lines[-2] + "\n`" + lines[-1] + "`"
    # Native Telegram delivery chunks long text. Never truncate reviewed money.
    if len(summary) > 60000:
        raise BoundaryError("PREVIEW_TOO_LARGE", "This review is too large for the native channel; it has not been marked reviewed.")
    return summary
