import test from 'node:test';
import assert from 'node:assert/strict';
import { createReadBudget } from '../read-budget.mjs';
const search = query => ({toolName:'owlswatch_cobros_search_gmail_threads',params:{query}});
test('different search spellings share one run budget', () => {
  const check = createReadBudget();
  for (let i=0;i<6;i++) assert.equal(check(search(`query${i}`), {runId:'run1'}), undefined);
  assert.equal(check(search('another'), {runId:'run1'}).block, true);
  assert.equal(check(search('another'), {runId:'run2'}), undefined);
});
test('repeat queries normalize whitespace and cannot provide a reset in params', () => {
  const check = createReadBudget();
  check(search('Bird   Tour'), {runId:'trusted'});
  check(search(' bird tour '), {runId:'trusted'});
  const event=search('BIRD TOUR'); event.params.runId='forged';
  assert.equal(check(event, {runId:'trusted'}).block, true);
});
test('read budget is separate and unrelated tools are unaffected', () => {
  const check=createReadBudget();
  for(let i=0;i<12;i++) assert.equal(check({toolName:'owlswatch_cobros_read_gmail_thread',params:{threadId:`thread${i}`}}, {runId:'a'}), undefined);
  assert.equal(check({toolName:'owlswatch_cobros_read_gmail_thread',params:{threadId:'new'}}, {runId:'a'}).block,true);
  assert.equal(check({toolName:'unrelated',params:{}},{runId:'a'}),undefined);
});
