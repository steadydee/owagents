#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="$ROOT/tools/owlswatch_cobros/server.py"

python3 -m py_compile "$SERVER"
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n' | python3 "$SERVER" >/tmp/owlswatch-cobros-tools.json
grep -q 'owlswatch_cobros_search_gmail_threads' /tmp/owlswatch-cobros-tools.json
grep -q 'owlswatch_cobros_create_packet' /tmp/owlswatch-cobros-tools.json
grep -q 'owlswatch_cobros_create_gmail_draft' /tmp/owlswatch-cobros-tools.json
rm -f /tmp/owlswatch-cobros-tools.json

SERVER_PATH="$SERVER" python3 - <<'PY'
import importlib.util
import os
from pathlib import Path

server_path = Path(os.environ["SERVER_PATH"])
spec = importlib.util.spec_from_file_location("owlswatch_cobros_server", server_path)
server = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(server)

assert server.amount_words_es(3208110) == "Tres millones doscientos ocho mil ciento diez pesos colombianos"
sample = """
Asunto: Cuenta de cobro COLOMBIA57 / Simon Jackson
Buenos dias estimada Adriana.
Amablemente me comunico para consultar si ya fue generada la cuenta de cobro correspondiente al servicio del cliente
SIMON ANTONY WILLIAM JACKSON / AUD-MZFI109854 por valor de $3.208.110,00.
NOMBRE PROVEEDOR:
LUZ ADRIANA VALENCIA ORTIZ
TIPO DE SERVICIO:
ALOJAMIENTO AN13069
FECHA DE SERVICIO:
04 AL 07 DE MARZO DE 2026
REFERENCIA DEL CLIENTE:
SIMON ANTONY WILLIAM JACKSON
AUD-MZFI109854
"""
prepared = server.tool_prepare({"raw_text": sample})
assert prepared["status"] == "ready", prepared
fields = prepared["fields"]
assert fields["debtorLegalName"] == "Colombia57 Tours"
assert fields["debtorNit"] == "090026196-8"
assert fields["payeeKey"] == "luz"
assert fields["amountCop"] == 3208110
assert fields["serviceDates"] == "Mar 4-7 2026"
assert fields["concept"] == "Hospedaje"

dispute = server.tool_prepare({"raw_text": sample + "\nEl valor no coincide con los pagos realizados."})
assert dispute["status"] == "needs_human", dispute

missing = server.tool_prepare({"raw_text": "Necesito una cuenta de cobro para cliente Perez."})
assert missing["status"] == "needs_info", missing
assert "operator_legal_name_and_nit" in missing["missingFields"]
PY

echo "Cobros smoke passed."
