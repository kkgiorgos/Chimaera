// Minimal DOM smoke test: exercise offline timing controls without a browser dependency.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(process.argv[2], 'utf8');
class Element {
  constructor(tag='div') { this.tag=tag; this.children=[]; this.style={}; this.attrs={}; this.events={}; this.value=''; }
  append(...nodes) {
    this.children.push(...nodes);
    if(this.tag==='select' && !this.value && nodes.length) this.value=nodes[0].value;
  }
  replaceChildren(...nodes) { this.children=nodes; }
  setAttribute(key,value) { this.attrs[key]=value; }
  addEventListener(name,callback) { this.events[name]=callback; }
}
const elements = new Map();
for(const match of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"[^>]*>/g)) {
  const e=new Element(match[1]); e.value=match[0].match(/\bvalue="([^"]*)"/)?.[1] ?? '';
  e.checked=match[0].includes('checked'); elements.set(match[2],e);
}
elements.get('dataset').textContent=html.match(/<script id="dataset" type="application\/json">([\s\S]*?)<\/script>/)[1];
const data=JSON.parse(elements.get('dataset').textContent);
const context=vm.createContext({
  document:{getElementById:id=>elements.get(id),createElement:tag=>new Element(tag),
    createElementNS:(_,tag)=>new Element(tag),createTextNode:text=>({textContent:text})},
  location:{hash:''},history:{replaceState(){}},URLSearchParams,
  window:{innerWidth:1200,innerHeight:900},setTimeout,Blob,URL,
});
vm.runInContext(html.match(/<\/script><script>([\s\S]*?)<\/script>/)[1],context);
assert.equal(elements.get('selection-count').textContent,`${data.runs.length} selected · ${data.runs.length} shown`);
elements.get('select-visible').events.click();
for(const [key] of data.timing_metrics) {
  elements.get('timing-metric').value=key;
  elements.get('timing-metric').events.change();
  assert.equal(elements.get('timing-chart').children[0].tag,'svg');
}
function validate(node) {
  for(const value of Object.values(node.attrs ?? {})) assert(!/NaN|Infinity/.test(String(value)));
  for(const child of node.children ?? []) validate(child);
}
validate(elements.get('timing-chart'));
assert.equal(elements.get('timing-table').children[0].tag,'table');
elements.get('clear').events.click();
assert.equal(elements.get('content').hidden,true);
elements.get('select-visible').events.click();
assert.equal(elements.get('content').hidden,false);
console.log('Dashboard timing controls and missing-data rendering passed');
