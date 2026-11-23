import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createQuotePromptHook } from "../prompt-context.mjs";

const workspace = fileURLToPath(new URL("../../../openclaw/agents/cotiza/", import.meta.url));

test("fresh Cotiza turns receive the complete fixed workflow without a read tool", () => {
  const hook = createQuotePromptHook(workspace);
  const result = hook({}, { agentId: "cotiza" });
  assert.match(result.appendSystemContext, /This satisfies skill loading/);
  assert.match(result.appendSystemContext, /Do not call read/);
  assert.match(result.appendSystemContext, /### Step 1 - Identify Source/);
  assert.match(result.appendSystemContext, /### Step 6 - Memory/);
  assert.match(result.appendSystemContext, /original image before asking/);
  assert.match(result.appendSystemContext, /exact complete `preparedQuote` object/);
  assert.deepEqual(hook({}, { agentId: "cotiza" }), result);
});

test("quote workflow cannot leak into other agents or accept model-selected files", () => {
  const hook = createQuotePromptHook(workspace);
  for (const agentId of ["cuenta", "correo", "cobros", "main", undefined]) {
    assert.equal(hook({ agentId: "cotiza", path: "/tmp/other-skill" }, { agentId }), undefined);
  }
  assert.equal(hook({ agentId: "cotiza" }, undefined), undefined);
});

test("missing or incorrect deployed workflow fails visibly at registration", () => {
  const root = mkdtempSync(resolve(tmpdir(), "cotiza-prompt-test-"));
  try {
    assert.throws(() => createQuotePromptHook(root));
    mkdirSync(resolve(root, "skills/quote-draft"), { recursive: true });
    writeFileSync(resolve(root, "skills/quote-draft/SKILL.md"), "wrong workflow");
    assert.throws(() => createQuotePromptHook(root), /missing or invalid/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
