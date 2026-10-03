// Minimal DOM smoke test: exercise offline timing controls without a browser dependency.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(process.argv[2], 'utf8');
class Element {
  constructor(tag='div') { this.tag=tag; this.children=[]; this.style={}; this.attrs={}; this.events={}; this.value=''; }
  append(...nodes) {
    for(const node of nodes){if(node.parentNode)node.parentNode.children=node.parentNode.children.filter(n=>n!==node);node.parentNode=this;}
    this.children.push(...nodes);
    if(this.tag==='select' && !this.value && nodes.length) this.value=nodes[0].value;
  }
  replaceChildren(...nodes) { this.children=nodes; }
  setAttribute(key,value) { this.attrs[key]=value; }
  addEventListener(name,callback) { this.events[name]=callback; }
  showModal() { this.open=true; }
  close() { this.open=false;this.events.close?.(); }
  focus() { this.focused=true; }
}
const elements = new Map();
for(const match of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"[^>]*>/g)) {
  const e=new Element(match[1]); e.value=match[0].match(/\bvalue="([^"]*)"/)?.[1] ?? '';
  e.checked=match[0].includes('checked'); elements.set(match[2],e);
}
elements.get('dataset').textContent=html.match(/<script id="dataset" type="application\/json">([\s\S]*?)<\/script>/)[1];
const data=JSON.parse(elements.get('dataset').textContent);
const context=vm.createContext({
  document:{body:new Element('body'),getElementById:id=>elements.get(id),createElement:tag=>new Element(tag),
    createElementNS:(_,tag)=>new Element(tag),createTextNode:text=>({textContent:text})},
  location:{hash:''},history:{replaceState(){}},URLSearchParams,
  window:{innerWidth:1200,innerHeight:900},setTimeout,Blob,URL,
});
vm.runInContext(html.match(/<\/script><script>([\s\S]*?)<\/script>/)[1],context);
assert.equal(elements.get('selection-count').textContent,`${data.runs.length} selected · ${data.runs.length} shown`);
elements.get('select-visible').events.click();
for(const [key] of data.simulation_metrics) {
  elements.get('timing-metric').value=key;
  elements.get('timing-metric').events.change();
  assert.equal(elements.get('timing-chart').children[0].tag,'svg');
}
function validate(node) {
  for(const value of Object.values(node.attrs ?? {})) assert(!/NaN|Infinity/.test(String(value)));
  for(const child of node.children ?? []) validate(child);
}
validate(elements.get('timing-chart'));
// Many configurations widen the chart coordinate system without scaling its height.
for(const id of ['bar-chart','timing-chart','architecture-chart']) {
 const svg=elements.get(id).children[0];
 const width=Math.max(700,data.runs.length*130);
 assert.equal(svg.attrs.viewBox,`0 0 ${width} 440`);
 assert.equal(svg.style.width,`${width}px`);
 assert.equal(svg.style.height,'440px');
}
assert(!elements.has('diagram')&&!elements.has('y-axis'));
for(const option of elements.get('x-axis').children) {
 elements.get('x-axis').value=option.value;
 elements.get('x-axis').events.change();
 for(const id of ['bar-chart','timing-chart','architecture-chart']) {
  validate(elements.get(id));
  assert(!elements.get(id).children[0].children.some(n=>['circle','polyline'].includes(n.tag)));
 }
}
for(const [key] of data.task_metrics) {
 elements.get('bar-metric').value=key;
 elements.get('bar-metric').events.change();
 validate(elements.get('bar-chart'));
 const available=data.runs.filter(r=>Number.isFinite(r.metrics[key])).length;
 assert.equal(elements.get('bar-chart').children[0].children.filter(n=>n.tag==='rect').length,available);
}
assert.equal(elements.get('task-note').hidden,!data.runs.some(r=>r.task_notes.length));
for(const [key] of data.architecture_metrics) {
 elements.get('architecture-metric').value=key;
 elements.get('architecture-metric').events.change();
 validate(elements.get('architecture-chart'));
}
assert.equal(vm.runInContext("fmt(1000000000)",context),'1e+9');
assert.equal(vm.runInContext("fmt(.0000000012345)",context),'1.2345e-9');
assert.equal(vm.runInContext("fmt(-.000000001)",context),'-1e-9');
assert.equal(vm.runInContext("fmt(0)",context),'0');
assert.equal(vm.runInContext("fmt(.35)",context),'0.35');
assert(vm.runInContext("domain([1e-9])[1]-domain([1e-9])[0]<1e-9",context));
if(data.runs.some(r=>r.sweep?.l1)) {
 assert.equal(vm.runInContext("dimensions.has('l1')",context),true);
 assert.equal(vm.runInContext("dimensions.has('hardware.l1i_size')",context),false);
 assert.equal(vm.runInContext("dimensions.has('hardware.l1d_size')",context),false);
}
for(const [button,chart,home] of [['maximize-signal','signal-chart','signal-panel'],['maximize-trajectory','trajectory-chart','trajectory-panel']]) {
 const original=elements.get(chart).children[0];
 elements.get(button).events.click();
 assert.equal(elements.get('expanded-chart').open,true);
 assert.equal(elements.get(chart).parentNode,elements.get('expanded-plot'));
 assert.equal(elements.get(chart).children[0],original);
 assert.equal(elements.get('tooltip').parentNode,elements.get('expanded-chart'));
 elements.get('close-expanded').events.click();
 assert.equal(elements.get('expanded-chart').open,false);
 assert.equal(elements.get(chart).parentNode,elements.get(home));
 assert.equal(elements.get(button).focused,true);
}
const allText=node=>[node.textContent??'',...(node.children??[]).map(allText)].join(' ');
assert(!/Group \d+/.test(allText(elements.get('bar-chart'))));
assert.equal(elements.get('timing-table').children[0].tag,'table');
elements.get('clear').events.click();
assert.equal(elements.get('content').hidden,true);
elements.get('select-visible').events.click();
assert.equal(elements.get('content').hidden,false);
console.log('Dashboard bars, formatting, missing-data messages, and expanded charts passed');
