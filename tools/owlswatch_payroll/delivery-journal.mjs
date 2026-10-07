import { DatabaseSync } from "node:sqlite";
import { createHash } from "node:crypto";
import { chmodSync, closeSync, existsSync, lstatSync, mkdirSync, openSync, readFileSync, realpathSync } from "node:fs";
import { resolve } from "node:path";

const hash = value => createHash("sha256").update(String(value)).digest("hex");
const safe = value => typeof value === "string" && value.length > 0 && value.length <= 512 && !/[\x00-\x1f]/.test(value);
const route = value => /^(?:telegram:)?(?:group:)?(-?[1-9][0-9]{0,19})(?::(?:topic|thread):([1-9][0-9]{0,19}))?$/.exec(String(value ?? ""));

export function privateSettings(workspace) {
  const root = realpathSync(workspace);
  for (const path of [root, resolve(root, "payroll-config.json")]) {
    const stat = lstatSync(path);
    if (stat.isSymbolicLink() || (stat.mode & 0o077) || stat.uid !== process.getuid()) throw new Error("private_payroll_config_required");
  }
  const config = JSON.parse(readFileSync(resolve(root, "payroll-config.json"), "utf8"));
  if (config.enabled !== true || config.schema_version !== 1) throw new Error("payroll_not_enabled");
  return config;
}

export class DeliveryJournal {
  constructor(workspace, now = () => Date.now() / 1000) {
    this.workspace = workspace;
    this.now = now;
    this.settings = privateSettings(workspace);
    const state = resolve(realpathSync(workspace), "state");
    mkdirSync(state, { mode: 0o700, recursive: true });
    if (lstatSync(state).isSymbolicLink() || (lstatSync(state).mode & 0o077)) throw new Error("private_state_required");
    const path = resolve(state, "delivery.sqlite3");
    if (!existsSync(path)) closeSync(openSync(path, "wx", 0o600));
    if (lstatSync(path).isSymbolicLink() || (lstatSync(path).mode & 0o077)) throw new Error("private_journal_required");
    this.db = new DatabaseSync(path);
    chmodSync(path, 0o600);
    this.db.exec(`PRAGMA busy_timeout=5000;
      CREATE TABLE IF NOT EXISTS deliveries (
        id TEXT PRIMARY KEY, account_id TEXT NOT NULL, chat_id TEXT NOT NULL, thread_id TEXT NOT NULL,
        sender_id TEXT NOT NULL, message_id TEXT NOT NULL, session_key TEXT NOT NULL, run_id TEXT,
        received_at REAL NOT NULL, updated_at REAL NOT NULL, status TEXT NOT NULL,
        payload_digest TEXT, platform_message_id TEXT, alerted_at REAL, kind TEXT NOT NULL DEFAULT 'message');
      CREATE INDEX IF NOT EXISTS delivery_run ON deliveries(run_id);
      CREATE INDEX IF NOT EXISTS delivery_pending ON deliveries(status,received_at);
      CREATE TABLE IF NOT EXISTS delivery_payloads (
        id TEXT PRIMARY KEY, delivery_id TEXT NOT NULL, status TEXT NOT NULL,
        updated_at REAL NOT NULL, receipt TEXT);`);
  }
  close() { this.db.close(); }
  allowed(account, chat, thread, sender) {
    const tg = this.settings.telegram;
    return account === tg.account_id && tg.allowed_sender_ids.includes(sender) &&
      tg.allowed_routes.some(route => route.chat_id === chat && route.thread_id === thread);
  }
  received(event, ctx) {
    if (ctx.channelId !== "telegram") return;
    const target = route(ctx.conversationId);
    if (!target || (target[2] && event.threadId != null && target[2] !== String(event.threadId))) return;
    const account = String(ctx.accountId ?? ""), chat = target[1];
    const sender = String(event.senderId ?? ctx.senderId ?? ""), thread = String(event.threadId ?? target[2] ?? "");
    const message = String(event.messageId ?? ctx.messageId ?? ""), session = event.sessionKey ?? ctx.sessionKey;
    if (!this.allowed(account, chat, thread, sender) || !safe(session) || !/^[0-9]{1,30}$/.test(message)) return;
    const id = hash(JSON.stringify([account, chat, thread, message]));
    const run = safe(event.runId ?? ctx.runId) ? event.runId ?? ctx.runId : null;
    this.db.prepare(`INSERT OR IGNORE INTO deliveries
      (id,account_id,chat_id,thread_id,sender_id,message_id,session_key,run_id,received_at,updated_at,status)
      VALUES(?,?,?,?,?,?,?,?,?,?,'received')`).run(id, account, chat, thread, sender, message, session, run, this.now(), this.now());
    if (run) this.db.prepare("UPDATE deliveries SET run_id=coalesce(run_id,?) WHERE id=?").run(run, id);
    return id;
  }
  // Bind only a host-supplied message ID, never the 'latest' session message.
  dispatch(event) {
    const ctx = event.ctx ?? {};
    const channel = ctx.Provider ?? ctx.Surface;
    if (channel !== "telegram") return;
    const to = ctx.OriginatingTo ?? ctx.To;
    return this.received({ senderId: ctx.SenderId, messageId: ctx.MessageSidFull ?? ctx.MessageSid,
      threadId: ctx.MessageThreadId, sessionKey: event.sessionKey ?? ctx.SessionKey, runId: event.runId },
    { channelId: channel, accountId: ctx.AccountId, conversationId: to });
  }
  payload(event, ctx) {
    if (event.kind !== "final" || (event.channel ?? ctx.channelId) !== "telegram") return;
    const run = event.runId ?? ctx.runId;
    const text = event.payload?.text;
    if (!safe(run) || typeof text !== "string" || !text.trim()) return;
    // Telegram ingress has no run ID yet. The later host hook supplies both
    // the exact inbound message ID and the run; never bind by latest session.
    const target = route(ctx.conversationId);
    if (!target || !ctx.messageId || !ctx.senderId) return;
    const matches = this.db.prepare(`SELECT * FROM deliveries WHERE account_id=? AND chat_id=?
      AND message_id=? AND sender_id=? AND session_key=? AND kind='message'`).all(String(ctx.accountId ?? ""),
      target[1], String(ctx.messageId), String(ctx.senderId), event.sessionKey ?? ctx.sessionKey);
    if (matches.length !== 1) return;
    const row = matches[0];
    if ((target[2] && target[2] !== row.thread_id) || !this.allowed(row.account_id, row.chat_id, row.thread_id, row.sender_id)) return;
    const id = hash(row.id + JSON.stringify(event.payload));
    const claim = this.db.prepare("INSERT OR IGNORE INTO delivery_payloads VALUES(?,?,'reply_queued',?,NULL)")
      .run(id, row.id, this.now());
    if (claim.changes === 0) return { id, alreadyOwned: true };
    this.db.prepare("UPDATE deliveries SET payload_digest=?,run_id=?,status='reply_queued',updated_at=?,alerted_at=NULL WHERE id=?")
      .run(hash(text), run, this.now(), row.id);
    return { id, accountId: row.account_id, chatId: row.chat_id, threadId: row.thread_id,
      sessionKey: row.session_key, messageId: row.message_id };
  }
  receipt(id, result) {
    const ids = result?.receipt?.platformMessageIds;
    const delivered = result?.status === "sent" && Array.isArray(ids) && ids.length > 0 && ids.length <= 100 &&
      ids.every(value => typeof value === "string" && /^[1-9][0-9]{0,19}$/.test(value));
    const part = this.db.prepare("SELECT delivery_id FROM delivery_payloads WHERE id=?").get(id);
    if (!part) return;
    this.db.prepare("UPDATE delivery_payloads SET status=?,receipt=?,updated_at=? WHERE id=?")
      .run(delivered ? "delivered" : "delivery_unverified", delivered ? ids.join(",") : null, this.now(), id);
    const incomplete = this.db.prepare("SELECT count(*) AS n FROM delivery_payloads WHERE delivery_id=? AND status!='delivered'").get(part.delivery_id).n;
    this.db.prepare("UPDATE deliveries SET status=?,platform_message_id=?,updated_at=? WHERE id=?")
      .run(incomplete ? "delivery_unverified" : "delivered", delivered ? ids.join(",") : null, this.now(), part.delivery_id);
  }
  ended(event, ctx) {
    if (ctx.agentId !== "nomina" || event.success === true) return;
    const run = event.runId ?? ctx.runId;
    if (!safe(run)) return;
    this.db.prepare("UPDATE deliveries SET status='generation_failed',updated_at=? WHERE run_id=? AND status!='delivered'").run(this.now(), run);
  }
  native(trusted, stage, status) {
    if (!this.allowed(trusted.accountId, trusted.chatId, trusted.threadId, trusted.senderId)) return false;
    const id = hash(`native:${trusted.approvalEventId}:${stage}`);
    this.db.prepare(`INSERT OR REPLACE INTO deliveries
      (id,account_id,chat_id,thread_id,sender_id,message_id,session_key,received_at,updated_at,status,kind)
      VALUES(?,?,?,?,?,'',?,?,?,?, 'native')`).run(id, trusted.accountId, trusted.chatId, trusted.threadId,
      trusted.senderId, trusted.sessionKey, this.now(), this.now(), status === "sent" ? "delivered" : "delivery_unverified");
    return true;
  }
}

export function registerDeliveryHooks(api, workspace) {
  let journal;
  const run = (method, ...args) => {
    try {
      journal ??= new DeliveryJournal(workspace);
      return journal[method](...args);
    } catch {
      // Never log chat content, tokens, or raw exceptions from financial tools.
      api.logger?.error?.("nomina_delivery_journal_unavailable");
    }
  };
  api.on("message_received", (event, ctx) => run("received", event, ctx));
  api.on("reply_dispatch", event => { run("dispatch", event); });
  api.on("agent_end", (event, ctx) => run("ended", event, ctx));
  api.on("gateway_stop", () => { journal?.close(); journal = undefined; });
  return { payload: (event, ctx) => run("payload", event, ctx),
    receipt: (id, result) => run("receipt", id, result),
    native: (trusted, stage, status) => run("native", trusted, stage, status) };
}

// A single delivery owner for source-bound conversational finals. The SDK's
// generic message_sent hook lacks inbound identity and cannot certify delivery.
export async function deliverFinalReply(event, ctx, journal, send) {
  if (!event.runId || !ctx.messageId || !String(event.sessionKey ?? ctx.sessionKey ?? "").startsWith("agent:nomina:")) return { payload: event.payload };
  const binding = journal.payload(event, ctx);
  if (!binding) return { payload: event.payload }; // log-only monitoring gap; no false receipt
  if (binding.alreadyOwned) return { cancel: true, reason: "nomina_already_owned" };
  try {
    journal.receipt(binding.id, await send(binding, event.payload));
  } catch {
    journal.receipt(binding.id, { status: "failed" });
  }
  // Even an unknown outcome must suppress the second core send. OpenClaw owns
  // its durable outbox; maintenance only warns and never replays payroll.
  return { cancel: true, reason: "nomina_durable_delivery_owned" };
}
