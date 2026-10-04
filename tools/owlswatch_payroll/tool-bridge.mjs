import { spawn } from "node:child_process";

const CONTEXT_ENV = "OWLSWATCH_PAYROLL_TRUSTED_CONTEXT";

export function toolTimeout(name) {
  // Archive upload is a bounded four-file job with per-request network timeouts.
  // Financial approvals remain local and keep the shorter default.
  return name === "nomina_archive_status" ? 180000 : 45000;
}

export function trustedEnvironment(env, trusted) {
  // Explicit replacement prevents a parent process or stale test environment
  // from accidentally donating approval authority to a normal tool call.
  return { ...env, [CONTEXT_ENV]: JSON.stringify(trusted ?? {}) };
}

function failure(code, uncertain) {
  return { ok: false, error: { code, retryable: false, uncertain,
    message: uncertain
      ? "El resultado no está confirmado. Consulta el estado y conserva la misma referencia o clave de solicitud; no prepares una operación duplicada."
      : "No se pudo iniciar la consulta de nómina. No se ha ejecutado esta solicitud." } };
}

export function callPython({ server, env, python = "python3", command = "call", name, args = {},
  trusted = null, timeoutMs = 45000, maxOutputBytes = 262144 }) {
  const valid = command === "call" ? /^nomina_[a-z_]+$/.test(name ?? "")
    : (command === "approve" || command === "review") && /^[A-F0-9]{16,32}$/.test(name ?? "");
  if (!valid) return Promise.resolve(failure("invalid_bridge_request", false));
  let input;
  try { input = JSON.stringify(args); } catch { return Promise.resolve(failure("invalid_tool_arguments", false)); }
  if (Buffer.byteLength(input ?? "") > 65536) return Promise.resolve(failure("tool_input_too_large", false));
  return new Promise(resolve => {
    let child, timer, settled = false, started = false, size = 0;
    const chunks = [];
    const finish = value => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(value);
    };
    const fail = code => {
      child?.kill("SIGKILL");
      finish(failure(code, started));
    };
    try {
      child = spawn(python, [server, command, name], {
        env: trustedEnvironment(env, trusted), stdio: ["pipe", "pipe", "pipe"],
      });
      timer = setTimeout(() => fail("tool_timeout"), timeoutMs);
      child.once("spawn", () => { started = true; });
      child.once("error", () => fail("tool_start_failed"));
      child.stdin.on("error", () => fail("tool_input_failed"));
      child.stderr.resume(); // Never forward interpreter traces or credential text.
      child.stdout.on("data", chunk => {
        size += chunk.length;
        if (size > maxOutputBytes) return fail("tool_response_too_large");
        chunks.push(chunk);
      });
      child.once("close", code => {
        if (settled) return;
        if (code !== 0) return fail("tool_process_failed");
        try {
          const result = JSON.parse(Buffer.concat(chunks).toString("utf8"));
          if (!result || Array.isArray(result) || typeof result !== "object" || typeof result.ok !== "boolean") return fail("invalid_tool_response");
          finish(result);
        } catch { fail("invalid_tool_response"); }
      });
      child.stdin.end(input);
    } catch { fail("tool_start_failed"); }
  });
}
