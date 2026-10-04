import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";

test("actual SDK entry registers authoritative catalog, isolated skill and native-only approval", t => {
  let sdk;
  try { sdk = dirname(realpathSync(execFileSync("which", ["openclaw"], { encoding: "utf8" }).trim())); }
  catch { return t.skip("Installed OpenClaw unavailable; bridge/context tests still run."); }
  const root = mkdtempSync(join(tmpdir(), "nomina-plugin-"));
  const dir = join(root, "tools", "owlswatch_payroll");
  const localDir = dirname(fileURLToPath(import.meta.url));
  mkdirSync(join(dir, "node_modules"), { recursive: true });
  symlinkSync(sdk, join(dir, "node_modules", "openclaw"), "dir");
  for (const file of ["openclaw-plugin.js", "approval-context.mjs", "tool-bridge.mjs", "openclaw.plugin.json"]) copyFileSync(join(localDir, file), join(dir, file));
  const skill = join(root, "skills", "payroll");
  mkdirSync(skill, { recursive: true });
  writeFileSync(join(skill, "SKILL.md"), "Synthetic trusted payroll workflow.");
  writeFileSync(join(dir, "server.py"), `import json, os, sys
if sys.argv[1] == 'catalog':
    print(json.dumps({'nomina_fixture': {'description': 'Synthetic catalog tool.', 'parameters': {'type': 'object', 'additionalProperties': False}}}))
else:
    args = json.load(sys.stdin)
    print(json.dumps({'ok': True, 'result': {'trusted': json.loads(os.environ['OWLSWATCH_PAYROLL_TRUSTED_CONTEXT']), 'args': args}, 'summary': 'Confirmación sintética registrada.'}))
`);
  const script = `
import { pathToFileURL } from 'node:url';
const { default: plugin } = await import(pathToFileURL(process.argv[1]).href);
const factories=[], commands=[], hooks=[];
plugin.register({registerTool:(factory,options)=>factories.push({factory,options}),registerCommand:command=>commands.push(command),on:(name,handler)=>hooks.push({name,handler})});
const ctx={agentId:'nomina',sessionKey:'agent:nomina:telegram:direct:101',requesterSenderId:'101',messageChannel:'telegram',agentAccountId:'default',deliveryContext:{channel:'telegram',accountId:'default',to:'telegram:101'}};
const tool=factories[0].factory(ctx);
const output=await tool.execute('fixture-call-1',{confirmed:true});
const native={agentId:'nomina',sessionKey:ctx.sessionKey,channel:'telegram',accountId:'default',senderId:'101',isAuthorizedSender:true,from:'telegram:101',to:'telegram:101',args:'ABCDEF0123456789'};
console.log(JSON.stringify({
  id:plugin.id,names:factories.map(x=>x.options.name),description:tool.description,
  otherAgent:factories[0].factory({...ctx,agentId:'main'}),
  prompt:hooks[0].handler({},ctx),otherPrompt:hooks[0].handler({},{agentId:'main'})??null,
  value:JSON.parse(output.content[0].text),
  command:{name:commands[0].name,requireAuth:commands[0].requireAuth,channels:commands[0].channels},
  confirmed:await commands[0].handler(native),
  refused:await commands[0].handler({...native,isAuthorizedSender:false}),
  invalidToken:await commands[0].handler({...native,args:'confirmed=true'}),
  reviewCommand:{name:commands[1].name,requireAuth:commands[1].requireAuth,channels:commands[1].channels},
  reviewed:await commands[1].handler(native),
  refusedReview:await commands[1].handler({...native,isAuthorizedSender:false})
}));
`;
  try {
    // Do not expose a test import to the live profile or provider credentials.
    const result = JSON.parse(execFileSync(process.execPath, ["--input-type=module", "-e", script, join(dir, "openclaw-plugin.js")], {
      env: { PATH: process.env.PATH, HOME: root, OWLSWATCH_PAYROLL_WORKSPACE: root,
        OPENCLAW_STATE_DIR: join(root, "state"), OPENCLAW_CONFIG_PATH: join(root, "absent.json") },
      encoding: "utf8", timeout: 20000, maxBuffer: 262144,
    }));
    assert.equal(result.id, "owlswatch-payroll");
    assert.deepEqual(result.names, ["nomina_fixture"]);
    assert.equal(result.description, "Synthetic catalog tool.");
    assert.equal(result.otherAgent, null);
    assert.equal(result.otherPrompt, null);
    assert.match(result.prompt.prependSystemContext, /Synthetic trusted payroll workflow/);
    assert.equal(result.value.result.trusted.source, "tool_context");
    assert.equal(result.value.result.trusted.sourceEventId, "tool-call:fixture-call-1");
    assert.equal(result.value.result.trusted.authorized, undefined);
    assert.deepEqual(result.command, { name: "confirmar_nomina", requireAuth: true, channels: ["telegram"] });
    assert.equal(result.confirmed.text, "Confirmación sintética registrada.");
    assert.notEqual(result.refused.text, result.confirmed.text);
    assert.notEqual(result.invalidToken.text, result.confirmed.text);
    assert.deepEqual(result.reviewCommand, { name: "revisar_nomina", requireAuth: true, channels: ["telegram"] });
    assert.equal(result.reviewed.text, "Confirmación sintética registrada.");
    assert.notEqual(result.refusedReview.text, result.reviewed.text);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
