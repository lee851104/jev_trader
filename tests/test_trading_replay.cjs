const test = require('node:test');
const assert = require('node:assert/strict');
const { createReplay } = require('../jev_ultrafast/trading/static/replay.js');

function fixture({status = 'ready', failAt = null, endAt = 99, wait = async () => {}} = {}) {
  let state = {run_id: 'one', status, version: 0};
  const calls = [], updates = [];
  let days = 0;
  const controller = createReplay({
    getState: () => state,
    execute: async (command, snapshot) => {
      assert.equal(snapshot.version, state.version);
      calls.push(command);
      if (calls.length === failAt) throw new Error('provider failed');
      if (command === 'advance') days++;
      state = {...state, status: command === 'decide' ? 'pending' : days === endAt ? 'finished' : 'ready', version: state.version + 1};
      return state;
    },
    wait,
    onUpdate: update => updates.push({...update}),
  });
  return {controller, calls, updates, setState: value => { state = value; }};
}

test('runs exactly five days, never overlaps or starts a sixth paid request', async () => {
  const f = fixture();
  const first = f.controller.start();
  await f.controller.start();
  await first;
  assert.deepEqual(f.calls, Array.from({length: 5}, () => ['decide', 'advance']).flat());
  assert.equal(f.updates.at(-1).completed, 5);
  assert.equal(f.updates.at(-1).outcome, 'complete');
});

test('existing pending decision settles without calling the model again', async () => {
  const f = fixture({status: 'pending'});
  await f.controller.start();
  assert.equal(f.calls[0], 'advance');
  assert.equal(f.calls.filter(c => c === 'decide').length, 4);
});

test('stop during in-flight decision waits for it but never sends advance', async () => {
  let resolveRequest;
  let state = {run_id: 'one', status: 'ready', version: 0};
  const calls = [], updates = [];
  const controller = createReplay({getState: () => state, wait: async () => {},
    execute: command => { calls.push(command); return new Promise(resolve => { resolveRequest = resolve; }); },
    onUpdate: update => updates.push({...update})});
  const run = controller.start();
  await new Promise(resolve => setImmediate(resolve));
  controller.stop();
  assert.equal(controller.isRunning(), true);
  state = {...state, status: 'pending', version: 1};
  resolveRequest(state);
  await run;
  assert.deepEqual(calls, ['decide']);
  assert.equal(updates.at(-1).outcome, 'stopped');
});

test('HTTP failure ends the run without retry', async () => {
  const f = fixture({failAt: 1});
  await f.controller.start();
  assert.deepEqual(f.calls, ['decide']);
  assert.equal(f.updates.at(-1).outcome, 'error');
  assert.equal(f.updates.at(-1).error, 'provider failed');
});

test('end of dataset stops early', async () => {
  const f = fixture({endAt: 2});
  await f.controller.start();
  assert.equal(f.calls.length, 4);
  assert.equal(f.updates.at(-1).completed, 2);
  assert.equal(f.updates.at(-1).outcome, 'finished');
});

test('changed session during pause does not send an operation to new account', async () => {
  let f;
  f = fixture({wait: async () => f.setState({run_id: 'other', status: 'ready', version: 0})});
  await f.controller.start();
  assert.deepEqual(f.calls, []);
  assert.equal(f.updates.at(-1).outcome, 'error');
});

test('failed server state is not treated as a completed decision', async () => {
  const calls = [], updates = [];
  const controller = createReplay({getState: () => ({run_id:'one', status:'ready', version:0}),
    wait: async () => {}, execute: async command => { calls.push(command); return {run_id:'one', status:'error'}; },
    onUpdate: update => updates.push({...update})});
  await controller.start();
  assert.deepEqual(calls, ['decide']);
  assert.equal(updates.at(-1).outcome, 'error');
});
