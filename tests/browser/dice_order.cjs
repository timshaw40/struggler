/* Exercise the production render/FX queue without a browser or real timers. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('ui/app.js', 'utf8').replace(/boot\(\);\s*$/, '');
async function scenario(kinds, human = 'US', watch = false, backlog = 0, split = false) {
  const context = vm.createContext({console, Promise, queueMicrotask, setTimeout, clearTimeout,
    localStorage: {getItem: () => null}});
  vm.runInContext(source, context);
  vm.runInContext(`
    var painted = [], captions = [], rolls = [], releases = [];
    var resizeBoard = renderBoard, queuePanel = renderPanel;
    for (const name of ['renderBoard','renderPanel','renderHand','renderWinner','renderExperience'])
      this[name] = () => painted.push([name, state.influence.Test.US]);
    for (const name of ['prefetchOdds','enqueuePlacements','enqueueBeat','renderEffectsHud',
      'layoutBoard','renderDecision','renderActionBar','renderStart','renderCue']) this[name] = () => {};
    showAction = (e) => captions.push(e.kind);
    showDice = (item) => new Promise(resolve => { rolls.push(item.kind); releases.push(resolve); });
    state = {game_id:1, seed:1, history_len:0, history:[], influence:{Test:{US:1,USSR:0}},
      human_side:${JSON.stringify(human)}, watch:${watch}, turn_effects:{}, game_effects:{}, decision:null};
    render(); painted = []; captions = [];
    for (let i = 0; i < ${backlog}; i++) enqueueFx(() => {});
  `, context);
  context.events = kinds.map(kind => ({kind, actor:'CHANCE', payload:{value:6}, context:{side:'USSR'}}));
  if (split) {
    vm.runInContext(`state = {...state, history:[events[0]], history_len:1}; render();`, context);
    await new Promise(setImmediate);
    assert.equal(vm.runInContext('painted.length + captions.length + rolls.length', context), 0,
      'a realignment must wait for both dice across polls');
  }
  vm.runInContext(`state = {...state, influence:{Test:{US:0,USSR:2}},
    history:events, history_len:events.length}; render();`, context);
  await new Promise(setImmediate);
  assert.equal(vm.runInContext('painted.length', context), 0, 'resolved board/score/history painted before dice');
  assert.equal(vm.runInContext('captions.length', context), 0, 'result caption revealed before dice');
  assert.equal(vm.runInContext('rolls.length', context), 1, 'every roll must be shown, even with a backlog');
  vm.runInContext('resizeBoard(); queuePanel();', context);
  for (let i = 0; i < (kinds.includes('realignment_actor_roll') ? 1 : kinds.length); i++) {
    vm.runInContext('releases.shift()()', context);
    await new Promise(setImmediate);
  }
  await new Promise(setImmediate);
  await vm.runInContext('fxChain', context);
  assert.ok(vm.runInContext('painted.some(([name,n]) => name === "renderBoard" && n === 0)', context),
    'resolved board must appear after the dice');
}
async function landing() {
  const timers = [];
  const nodes = Object.fromEntries(['.dice','.dtitle','.doutcome'].map(key => [key,{textContent:''}]));
  const box = {hidden:true, classList:{add(){},remove(){}},querySelector:key=>nodes[key] || null};
  const context = vm.createContext({console, Promise, queueMicrotask,
    localStorage:{getItem:()=>null}, document:{querySelector:()=>box},
    setTimeout:(fn,ms)=>timers.push({fn,ms})});
  vm.runInContext(source,context);
  vm.runInContext(`rollDice=()=>100; rollTitle=()=>"Coup"; rollValues=()=>[6];
    rollOutcome=()=>"Resolved result"; var completed=false;
    showDice({kind:"coup_roll"}).then(()=>completed=true);`,context);
  box.onclick(); await new Promise(setImmediate);
  assert.equal(vm.runInContext('completed',context),false,'early click skipped the dice');
  assert.equal(nodes['.doutcome'].textContent,'');
  timers.find(t=>t.ms===100).fn();
  assert.equal(nodes['.doutcome'].textContent,'Resolved result');
  box.onclick(); await new Promise(setImmediate);
  assert.equal(vm.runInContext('completed',context),true);
}
(async () => {
  for (const kind of ['coup_roll','war_roll','space_race_roll','contest_roll','quagmire_roll'])
    await scenario([kind]);
  await scenario(['realignment_actor_roll','realignment_opponent_roll']);
  await scenario(['realignment_actor_roll','realignment_opponent_roll'], 'US', true, 0, true);
  await scenario(['coup_roll','war_roll']);
  await scenario(['coup_roll'], 'US', true);
  await scenario(['coup_roll'], 'US', false, 8);
  await landing();
  console.log('PASS: all roll outcomes remain hidden until dice finish');
})().catch(err => { console.error(err); process.exitCode = 1; });
