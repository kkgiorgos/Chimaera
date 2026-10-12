// Exercise comparison controls using the actual embedded report data.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(process.argv[2], 'utf8');
const data = JSON.parse(html.match(/window.EXPERIMENT_DATA = ([\s\S]*?);\s*<\/script>/)[1]);
class Element {
  constructor(tag = 'div') { this.tag = tag; this.children = []; this.attrs = {}; this.value = ''; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(key, value) { this.attrs[key] = value; }
  get selectedOptions() { return this.children.filter(node => node.value === this.value); }
  click() { this.onclick?.(); }
}
const elements = new Map();
for (const match of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"[^>]*>/g)) {
  elements.set(match[2], new Element(match[1]));
}
for (const id of ['memory-throw', 'memory-rates']) elements.get(id).value = 'all';
const metric = elements.get('memory-metric');
for (const value of ['total_p99_ns', 'total_p95_ns', 'total_mean_ns',
                     'accepted_p99_ns', 'accepted_p95_ns', 'accepted_mean_ns']) {
  const option = new Element('option'); option.value = value; option.textContent = value;
  metric.append(option);
}
metric.value = 'total_p99_ns';
let selected = null, exported = null;
elements.get('points').children = data.points.map(point => ({click() { selected = point.id; }}));
const context = vm.createContext({
  window: {EXPERIMENT_DATA: data},
  document: {getElementById: id => elements.get(id), createElement: tag => new Element(tag),
             createElementNS: (_, tag) => new Element(tag)},
  Blob, URL: {createObjectURL(blob) { exported = blob; return 'blob:test'; }, revokeObjectURL() {}},
  setTimeout(callback) { callback(); },
});
vm.runInContext(html.match(/<script>(\/\* Buildless,[\s\S]*?)<\/script>/)[1], context);
const rows = elements.get('memory-rows');
assert.equal(elements.get('memory-panel').hidden, false);
assert.equal(rows.children.length, 4);
assert.deepEqual(rows.children.map(row => row.children[1].textContent),
                 ['CATCH', 'MISS', 'CATCH', 'infrastructure failure']);
assert.equal(rows.children[1].children[9].textContent, '2.00×');
assert.equal(rows.children[2].children[8].textContent, '—');
assert.equal(rows.children[3].children[8].textContent, '—');
rows.children[1].click(); assert.equal(selected, data.points[1].id);
function validate(node) {
  for (const value of Object.values(node.attrs)) assert(!/NaN|Infinity/.test(String(value)));
  for (const child of node.children) validate(child);
}
for (const option of metric.children) {
  metric.value = option.value; metric.onchange();
  const chart = elements.get('memory-chart'); validate(chart);
  assert.equal(chart.children.filter(node => node.tag === 'circle').length, 2);
}
elements.get('memory-throw').value = 'other'; elements.get('memory-throw').onchange();
assert.equal(rows.children.length, 1);
assert.equal(elements.get('memory-chart').children.filter(node => node.tag === 'circle').length, 0);
elements.get('memory-throw').value = 'all';
elements.get('memory-rates').value = 'reduced'; elements.get('memory-rates').onchange();
assert.equal(rows.children.length, 1);
elements.get('memory-export').click();
assert.equal(exported.type, 'application/json');
exported.text().then(text => {
  const saved = JSON.parse(text); assert.equal(saved.length, 4);
  assert.equal(saved[1].result.success, false);
  assert.equal(saved[3].status, 'infrastructure_failure');
  assert.equal(saved[3].result, undefined);
  console.log('Comparison filters, latency plots, outcomes, baseline ratios and export passed');
});
