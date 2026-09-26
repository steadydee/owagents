// Runtime hook context supplies runId; model arguments cannot change the budget.
export function createReadBudget({ now = Date.now } = {}) {
  const runs = new Map();
  return (event, ctx = {}) => {
    const name = event.toolName;
    if (!["owlswatch_cobros_search_gmail_threads", "owlswatch_cobros_read_gmail_thread"].includes(name)) return;
    const key = ctx.runId || event.runId || ctx.sessionId || ctx.sessionKey || "cobros-unidentified";
    const time = now();
    for (const [id, value] of runs) if (time - value.started > 60 * 60 * 1000) runs.delete(id);
    if (!runs.has(key)) {
      if (runs.size >= 1000) return { block: true, blockReason: "Cobros read budget unavailable; stop and ask for a specific thread." };
      runs.set(key, { started: time, searches: 0, reads: 0, repeats: new Map() });
    }
    const run = runs.get(key);
    const isSearch = name.endsWith("search_gmail_threads");
    const value = String(isSearch ? event.params?.query || "" : event.params?.threadId || "").trim().toLowerCase().replace(/\s+/g, " ");
    const fingerprint = `${name}:${value}`;
    const repeated = run.repeats.get(fingerprint) || 0;
    if (repeated >= 2 || (isSearch ? run.searches >= 6 : run.reads >= 12)) {
      return { block: true, blockReason: "Cobros read budget reached. Stop searching, summarize the available candidates, and ask for one specific thread or missing field. Do not try alternate tools to continue searching." };
    }
    run.repeats.set(fingerprint, repeated + 1);
    if (isSearch) run.searches += 1; else run.reads += 1;
  };
}
