// Check catalog integrity and execute the real page's filtering/rendering code.
// This is a DOM-stub integration test, not a browser/layout test.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const site = new URL('../outputs/yosuga-sales-preview/', import.meta.url);
const data = JSON.parse(await readFile(new URL('campaigns.json', site), 'utf8'));
const html = await readFile(new URL('index.html', site), 'utf8');
const records = data.campaigns.flatMap(c => c.products);
assert.equal(data.campaignCount, data.campaigns.length);
assert.equal(data.productRecordCount, records.length);
assert.equal(new Set(data.campaigns.map(c => c.slug)).size, data.campaignCount);
assert.equal(new Set(records.map(p => p.recordId)).size, records.length);
assert.deepEqual(data.campaigns.map(c => c.startDate), data.campaigns.map(c => c.startDate).sort().reverse());

const elements = new Map();
const element = selector => {
  if (!elements.has(selector)) elements.set(selector, {
    value: selector === '#characterFilter' || selector === '#typeFilter' ? 'all' : '',
    innerHTML: '', textContent: '', hidden: false, dataset: {},
    classList: { toggle() {} }, addEventListener() {}, setAttribute() {}, focus() {},
  });
  return elements.get(selector);
};
const context = vm.createContext({
  document: { querySelector: element, querySelectorAll: () => [], addEventListener() {} },
  fetch: async url => ({ ok: true, json: async () => {
    assert.equal(url, './campaigns.json');
    return data;
  } }),
  console,
});
const js = html.match(/<script>([\s\S]*?)<\/script>/)[1]
  .replace(/loadArchive\(\);\s*loadLatestJpyCnyRate\(\);\s*$/, '');
new vm.Script(js).runInContext(context);
await vm.runInContext('loadArchive()', context);
const run = code => vm.runInContext(code, context);
assert.equal(run('allRecords.length'), records.length);
assert.equal(run('filteredRecords.length'), records.length);
assert.equal(run('filteredRecords[0].campaign.slug'), data.campaigns[0].slug);
assert.equal(element('#summaryProducts').textContent, records.length.toLocaleString('zh-CN'));
assert.match(element('#archiveUpdated').textContent, /^资料更新 · \d{4}\.\d{1,2}\.\d{1,2}$/);
assert.match(element('#productGrid').innerHTML, /202610a-001/);

for (const slug of ['202609a', '202610a']) {
  const campaign = data.campaigns.find(c => c.slug === slug);
  assert.equal(campaign.products.length, 11);
  for (const product of campaign.products) {
    for (const field of ['artist', 'description', 'image', 'name', 'priceText', 'specification', 'status', 'work']) {
      assert.ok(product[field], `${product.recordId}: ${field}`);
    }
    const escapedId = JSON.stringify(product.recordId);
    const card = run(`cardHtml(allRecords.find(r => r.product.recordId === ${escapedId}))`);
    assert.ok(card.includes(`${campaign.detailUrl}#${product.productId}`));
    assert.ok(card.includes(product.image));
    assert.ok(card.includes(product.priceText));
    assert.ok(card.includes('中文：'));
    assert.ok(card.includes(run(`translateName(${JSON.stringify(product.name)})`)));
  }
}

element('#search').value = '黑色旗袍';
run('applyFilters()');
assert.ok(run('filteredRecords.some(r => r.campaign.slug === "202610a")'));
assert.ok(run('filteredRecords.some(r => r.campaign.slug === "202609a")'));
element('#search').value = '';
element('#characterFilter').value = 'akira';
run('applyFilters()');
assert.equal(run('filteredRecords[0].campaign.slug'), '202610a');
assert.ok(run('filteredRecords.every(r => r.characters.includes("akira"))'));
element('#characterFilter').value = 'all';
run('setSort("date-asc")');
assert.equal(run('filteredRecords.at(-1).campaign.startDate'), '2026-10-02');
run('setSort("type-name")');
assert.equal(run('filteredRecords.length'), records.length);
run('setSort("date-desc")');
assert.equal(run('filteredRecords[0].campaign.slug'), '202610a');
console.log(`PASS: ${data.campaignCount} campaigns / ${records.length} records; new cards, Chinese search, character filtering, dates, sorting, official links.`);
