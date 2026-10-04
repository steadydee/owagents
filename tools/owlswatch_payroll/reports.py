"""Immutable private reports and versioned exports for a future authority cutover."""
import csv
import hashlib
import html
import io
import json
import os
from pathlib import Path
import tempfile

from security import BoundaryError, private_directory

EXPORT_VERSION = 1


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def immutable_write(path, data):
    """Atomic, exclusive publication; identical retries return the existing file."""
    path = Path(path)
    if path.is_symlink():
        raise BoundaryError("UNSAFE_PATH", "Archive path must not be a symbolic link.")
    if path.exists():
        if path.read_bytes() != data:
            raise BoundaryError("ARCHIVE_CONFLICT", "Existing immutable archive content differs.")
        return
    fd, temporary = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != data:
                raise BoundaryError("ARCHIVE_CONFLICT", "Existing immutable archive content differs.")
    finally:
        os.unlink(temporary)


def spreadsheet_cell(value):
    text = str(value)
    # Payment destinations and names are external text. Avoid CSV formula execution.
    return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def export_run(root, snapshot):
    if snapshot.get("status") == "draft":
        raise BoundaryError("DRAFT_NOT_FINAL", "Finalize the payroll before exporting an immutable payment report.")
    payload = {"schema_version": EXPORT_VERSION, "authority": "owlswatch-nomina",
               "currency": "COP", "import_semantics": "snapshot_only_do_not_reapply_loan_payments",
               "run": snapshot}
    content = canonical(payload)
    digest = hashlib.sha256(content).hexdigest()
    parent = private_directory(Path(root) / "exports", root)
    destination = private_directory(parent / digest, root)
    immutable_write(destination / "payroll.json", content + b"\n")
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    headings = ["payee_id", "name", "kind", "salary_cop", "allowance_cop", "earnings_cop",
                "health_cop", "pension_cop", "other_deduction_cop", "extra_earnings_cop",
                "extra_deductions_cop", "loans_cop", "net_cop", "institution", "account"]
    writer.writerow(headings)
    rendered_rows = []
    for row in snapshot.get("rows", []):
        bank = row.get("profile", {}).get("payment_destination", {})
        loans = sum(item["amount"] for item in row.get("loan_deductions", []))
        values = [row["payee_id"], row["display_name"], row["kind"], row["salary"], row["allowance"],
                  row["gross"], row["health"], row["pension"], row["other_deduction"],
                  row["extra_earnings"], row["extra_deductions"], loans, row["net"],
                  bank.get("institution", ""), bank.get("account", "")]
        writer.writerow([spreadsheet_cell(value) for value in values])
        rendered_rows.append("<tr>" + "".join("<td>" + html.escape(str(value)) + "</td>" for value in
                             [row["display_name"], row["kind"], row["gross"], loans, row["net"],
                              bank.get("institution", ""), bank.get("account", "")]) + "</tr>")
    immutable_write(destination / "payments.csv", output.getvalue().encode("utf-8-sig"))
    title = "Nómina · " + str(snapshot["period"])
    totals = snapshot["totals"]
    document = """<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>""" + html.escape(title) + """</title><style>
body{font:15px system-ui,sans-serif;max-width:1100px;margin:40px auto;padding:0 24px;color:#18312b}
h1{font-size:28px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:10px;border-bottom:1px solid #ccd8d2}
th{background:#edf3ef}.total{font-size:22px;font-weight:700}.note{color:#54635c} @media print{body{margin:0;padding:0}}
</style><h1>""" + html.escape(title) + "</h1><p>" + html.escape(
        f"Run {snapshot['run_id']} · revision {snapshot['revision']} · {snapshot['status']}") + "</p>" + \
        "<p class=note>Registro privado. Finalizar no confirma pago; este informe no ejecuta transferencias.</p>" + \
        "<p class=total>Total COP " + str(totals["net"]) + "</p><p>Empleados: " + str(totals["employees"]) + \
        " · Honorarios: " + str(totals["contractors"]) + " · Préstamos: " + str(totals["loans"]) + "</p>" + \
        "<table><thead><tr><th>Persona</th><th>Tipo</th><th>Devengado COP</th><th>Préstamos COP</th><th>Neto COP</th><th>Entidad</th><th>Cuenta</th></tr></thead><tbody>" + \
        "".join(rendered_rows) + "</tbody></table><p class=note>" + html.escape(
            "Regla: " + str(snapshot.get("rounding_rule", ""))) + "</p></html>"
    immutable_write(destination / "payroll.html", document.encode("utf-8"))
    files = [destination / name for name in ("payroll.json", "payments.csv", "payroll.html")]
    return {"run_id": snapshot["run_id"], "revision": snapshot["revision"], "sha256": digest,
            "schema_version": EXPORT_VERSION, "private": True,
            "files": [str(path.relative_to(root)) for path in files]}
