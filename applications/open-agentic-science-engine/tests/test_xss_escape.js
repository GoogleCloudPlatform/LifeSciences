/**
 * Copyright 2026 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

'use strict';

/**
 * Tests for the escapeHtml() XSS mitigation function used across DDE viewer
 * templates.  The function is defined identically in all five viewer HTML
 * files added/patched by PR #237 (issues #225-#230).
 *
 * Run:  node applications/DDE/tests/test_xss_escape.js
 */

const assert = require('assert');
const fs     = require('fs');
const path   = require('path');

// ---------------------------------------------------------------------------
// 1.  Extract escapeHtml from the canonical viewer file
// ---------------------------------------------------------------------------

// The function appears verbatim in every viewer.  We extract it once from the
// tournament viewer, then verify all other viewers carry the same code.

const VIEWERS_DIR = path.resolve(
  __dirname, '..', 'tools', 'dde', 'site_templates', 'viewers'
);

const VIEWER_FILES = [
  'constraint-viewer.html',
  'contacts-viewer.html',
  'admet-viewer.html',
  'tournament-viewer.html',
  'pockets-viewer.html',
  'pae-viewer.html',
  'plddt-viewer.html',
  'expression-viewer.html',
  'docking-scores-viewer.html',
];

/**
 * Extract the escapeHtml function body from an HTML file and return it as a
 * callable function.
 */
function extractEscapeHtml(filePath) {
  const src = fs.readFileSync(filePath, 'utf8');
  // Match the function definition — it spans multiple lines.
  const re = /function escapeHtml\(str\)\s*\{([\s\S]*?)\n\s*\}/;
  const m  = src.match(re);
  if (!m) throw new Error(`escapeHtml not found in ${filePath}`);
  // Build a standalone function from the captured body.
  return new Function('str', m[1]);
}

// The function under test, extracted from the first viewer.
const escapeHtml = extractEscapeHtml(
  path.join(VIEWERS_DIR, VIEWER_FILES[0])
);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    passed++;
    console.log(`  ✓ ${name}`);
  } catch (err) {
    failed++;
    console.error(`  ✗ ${name}`);
    console.error(`    ${err.message}`);
  }
}

// ---------------------------------------------------------------------------
// 2.  Test suite — escapeHtml behaviour
// ---------------------------------------------------------------------------

console.log('\n=== escapeHtml — basic entity escaping ===');

test('escapes ampersand (&)', () => {
  assert.strictEqual(escapeHtml('a&b'), 'a&amp;b');
});

test('escapes less-than (<)', () => {
  assert.strictEqual(escapeHtml('a<b'), 'a&lt;b');
});

test('escapes greater-than (>)', () => {
  assert.strictEqual(escapeHtml('a>b'), 'a&gt;b');
});

test('escapes double quote (")', () => {
  assert.strictEqual(escapeHtml('a"b'), 'a&quot;b');
});

test("escapes single quote (')", () => {
  assert.strictEqual(escapeHtml("a'b"), 'a&#x27;b');
});

test('escapes all 5 entities in a single string', () => {
  assert.strictEqual(
    escapeHtml('&<>"\''),
    '&amp;&lt;&gt;&quot;&#x27;'
  );
});

test('escapes multiple occurrences of the same entity', () => {
  assert.strictEqual(escapeHtml('<<>>'), '&lt;&lt;&gt;&gt;');
});

// ---------------------------------------------------------------------------

console.log('\n=== escapeHtml — XSS payload rejection ===');

test('neutralises <script>alert(1)</script>', () => {
  const out = escapeHtml('<script>alert(1)</script>');
  assert.ok(!out.includes('<script'), 'output still contains <script');
  assert.strictEqual(out, '&lt;script&gt;alert(1)&lt;/script&gt;');
});

test('neutralises "><img onerror=alert(1)>', () => {
  const out = escapeHtml('"><img onerror=alert(1)>');
  assert.ok(!out.includes('<img'), 'output still contains <img');
  assert.ok(!out.includes('"'), 'output still contains unescaped "');
  assert.strictEqual(
    out,
    '&quot;&gt;&lt;img onerror=alert(1)&gt;'
  );
});

test("neutralises ' onmouseover=alert(1)", () => {
  const out = escapeHtml("' onmouseover=alert(1)");
  assert.ok(!out.includes("'"), 'output still contains unescaped single quote');
  assert.strictEqual(out, '&#x27; onmouseover=alert(1)');
});

test('neutralises javascript:alert(1) in attribute context', () => {
  // javascript: URIs are dangerous when injected into href/src attributes.
  // While the protocol itself is not HTML-special, the typical injection
  // vector relies on breaking out of an attribute value first.  Confirm the
  // quotes are escaped so attribute breakout is impossible.
  const payload = '" onclick="javascript:alert(1)"';
  const out = escapeHtml(payload);
  assert.ok(!out.includes('"'), 'unescaped double-quote found');
});

test('neutralises nested/mixed XSS payloads', () => {
  const payload = '<img src=x onerror="alert(\'xss\')">';
  const out = escapeHtml(payload);
  assert.ok(!out.includes('<'), 'unescaped < found');
  assert.ok(!out.includes('>'), 'unescaped > found');
  assert.ok(!out.includes('"'), 'unescaped " found');
  assert.ok(!out.includes("'"), 'unescaped \' found');
});

test('neutralises SVG-based XSS payload', () => {
  const payload = '<svg onload=alert(1)>';
  const out = escapeHtml(payload);
  assert.ok(!out.includes('<svg'), 'output still contains <svg');
  assert.strictEqual(out, '&lt;svg onload=alert(1)&gt;');
});

test('neutralises data URI injection attempt', () => {
  const payload = '"><iframe src="data:text/html,<script>alert(1)</script>">';
  const out = escapeHtml(payload);
  assert.ok(!out.includes('<iframe'), 'output still contains <iframe');
  assert.ok(!out.includes('"'), 'unescaped double-quote found');
});

// ---------------------------------------------------------------------------

console.log('\n=== escapeHtml — safe content passthrough ===');

test('passes through plain text unchanged', () => {
  assert.strictEqual(escapeHtml('hello world'), 'hello world');
});

test('passes through numbers (as strings) unchanged', () => {
  assert.strictEqual(escapeHtml('12345'), '12345');
});

test('passes through a numeric argument via String coercion', () => {
  assert.strictEqual(escapeHtml(42), '42');
});

test('passes through non-HTML special characters unchanged', () => {
  const safe = '!@#$%^*()_+-=[]{}|;:,.?/~`';
  assert.strictEqual(escapeHtml(safe), safe);
});

test('passes through Unicode text unchanged', () => {
  const unicode = '日本語テスト — ñ ü ö 中文';
  assert.strictEqual(escapeHtml(unicode), unicode);
});

test('passes through URLs without HTML-special chars unchanged', () => {
  const url = 'https://example.com/path?q=1';
  // The & in ?a=1&b=2 WOULD be escaped, but a URL with a single param is safe.
  assert.strictEqual(escapeHtml(url), url);
});

// ---------------------------------------------------------------------------

console.log('\n=== escapeHtml — edge cases ===');

test('handles empty string', () => {
  assert.strictEqual(escapeHtml(''), '');
});

test('handles null (coerced to "null")', () => {
  assert.strictEqual(escapeHtml(null), 'null');
});

test('handles undefined (coerced to "undefined")', () => {
  assert.strictEqual(escapeHtml(undefined), 'undefined');
});

test('handles string of only special characters', () => {
  assert.strictEqual(escapeHtml('&<>"\''), '&amp;&lt;&gt;&quot;&#x27;');
});

test('handles very long string (10 000 chars)', () => {
  const long  = '<'.repeat(10000);
  const expected = '&lt;'.repeat(10000);
  assert.strictEqual(escapeHtml(long), expected);
});

test('handles boolean true (coerced to "true")', () => {
  assert.strictEqual(escapeHtml(true), 'true');
});

test('handles zero (coerced to "0")', () => {
  assert.strictEqual(escapeHtml(0), '0');
});

test('handles string with only whitespace', () => {
  assert.strictEqual(escapeHtml('   \t\n  '), '   \t\n  ');
});

// ---------------------------------------------------------------------------

console.log('\n=== escapeHtml — attribute context safety ===');

test('escaped output is safe inside double-quoted attributes', () => {
  // In <div title="VALUE">, an attacker needs an unescaped " to break out.
  const payload = '" onfocus="alert(1)" autofocus="';
  const out = escapeHtml(payload);
  assert.ok(!out.includes('"'), 'unescaped " allows attribute breakout');
});

test('escaped output is safe inside single-quoted attributes', () => {
  // In <div title='VALUE'>, an attacker needs an unescaped ' to break out.
  const payload = "' onfocus='alert(1)' autofocus='";
  const out = escapeHtml(payload);
  assert.ok(!out.includes("'"), 'unescaped \' allows attribute breakout');
});

test('escaped output is safe inside data- attributes', () => {
  const payload = '"><script>alert(document.cookie)</script><div data-x="';
  const out = escapeHtml(payload);
  assert.ok(!out.includes('<script'), 'script tag survives in data attr');
  assert.ok(!out.includes('"'), 'unescaped " allows attribute breakout');
});

// ---------------------------------------------------------------------------
// 3.  Verify escapeHtml presence and usage across all viewer files
// ---------------------------------------------------------------------------

console.log('\n=== Viewer file integrity checks ===');

for (const file of VIEWER_FILES) {
  const filePath = path.join(VIEWERS_DIR, file);

  test(`${file} — contains escapeHtml definition`, () => {
    const src = fs.readFileSync(filePath, 'utf8');
    assert.ok(
      /function escapeHtml\(str\)/.test(src),
      `escapeHtml function not found in ${file}`
    );
  });

  test(`${file} — escapeHtml escapes all 5 entities`, () => {
    const fn = extractEscapeHtml(filePath);
    assert.strictEqual(fn('&'), '&amp;',  'ampersand');
    assert.strictEqual(fn('<'), '&lt;',   'less-than');
    assert.strictEqual(fn('>'), '&gt;',   'greater-than');
    assert.strictEqual(fn('"'), '&quot;', 'double-quote');
    assert.strictEqual(fn("'"), '&#x27;', 'single-quote');
  });

  test(`${file} — escapeHtml is actually called on data values`, () => {
    const src = fs.readFileSync(filePath, 'utf8');
    // Count calls (excluding the definition itself).
    const calls = (src.match(/escapeHtml\(/g) || []).length;
    // At least 1 definition + 1 call site.
    assert.ok(
      calls >= 2,
      `Expected at least 2 occurrences of escapeHtml( in ${file}, found ${calls}`
    );
  });
}

// ---------------------------------------------------------------------------
// 4.  Plotly XSS — verify escapeHtml usage on user-controlled chart fields
//     (Issues #303, #304, #305)
// ---------------------------------------------------------------------------

console.log('\n=== Plotly XSS — contacts-viewer (#303) ===');

test('contacts-viewer: xLabels construction uses escapeHtml', () => {
  const src = fs.readFileSync(path.join(VIEWERS_DIR, 'contacts-viewer.html'), 'utf8');
  // The xLabels map callback should escape res_name, res_num, and chain
  assert.ok(
    src.includes("escapeHtml(r.res_name)"),
    'xLabels does not escape r.res_name'
  );
  assert.ok(
    src.includes("escapeHtml(String(r.res_num))"),
    'xLabels does not escape r.res_num'
  );
  assert.ok(
    src.includes("escapeHtml(r.chain)"),
    'xLabels does not escape r.chain'
  );
});

test('contacts-viewer: yLabel uses escapeHtml', () => {
  const src = fs.readFileSync(path.join(VIEWERS_DIR, 'contacts-viewer.html'), 'utf8');
  assert.ok(
    src.includes('var yLabel = escapeHtml(name)'),
    'yLabel is not escaped with escapeHtml'
  );
});

test('contacts-viewer: does NOT contain unescaped xLabels pattern', () => {
  const src = fs.readFileSync(path.join(VIEWERS_DIR, 'contacts-viewer.html'), 'utf8');
  // The old unsafe pattern should be absent
  assert.ok(
    !src.includes("r.res_name + r.res_num + ' (' + r.chain + ')'"),
    'Unsafe unescaped xLabels pattern still present'
  );
});

console.log('\n=== Plotly XSS — pae-viewer (#304) ===');

test('pae-viewer: contains escapeHtml definition', () => {
  const src = fs.readFileSync(path.join(VIEWERS_DIR, 'pae-viewer.html'), 'utf8');
  assert.ok(
    /function escapeHtml\(str\)/.test(src),
    'escapeHtml function not found in pae-viewer.html'
  );
});

test('pae-viewer: baseName in Plotly title uses escapeHtml', () => {
  const src = fs.readFileSync(path.join(VIEWERS_DIR, 'pae-viewer.html'), 'utf8');
  assert.ok(
    src.includes("escapeHtml(baseName)"),
    'baseName in Plotly title is not escaped'
  );
  // The unsafe pattern should be absent
  assert.ok(
    !src.includes("'Predicted Aligned Error — ' + baseName"),
    'Unsafe unescaped baseName in title still present'
  );
});

test('pae-viewer: document.title uses escapeHtml for baseName', () => {
  const src = fs.readFileSync(path.join(VIEWERS_DIR, 'pae-viewer.html'), 'utf8');
  assert.ok(
    src.includes("document.title = 'PAE: ' + escapeHtml(baseName)"),
    'document.title does not escape baseName'
  );
});

console.log('\n=== Plotly XSS — plddt-viewer (#305) ===');

test('plddt-viewer: contains escapeHtml definition', () => {
  const src = fs.readFileSync(path.join(VIEWERS_DIR, 'plddt-viewer.html'), 'utf8');
  assert.ok(
    /function escapeHtml\(str\)/.test(src),
    'escapeHtml function not found in plddt-viewer.html'
  );
});

test('plddt-viewer: chain in trace name uses escapeHtml', () => {
  const src = fs.readFileSync(path.join(VIEWERS_DIR, 'plddt-viewer.html'), 'utf8');
  assert.ok(
    src.includes("name: 'Chain ' + escapeHtml(chain)"),
    'chain in trace name is not escaped'
  );
  // The unsafe pattern should be absent
  assert.ok(
    !src.includes("name: 'Chain ' + chain,"),
    'Unsafe unescaped chain in trace name still present'
  );
});

test('plddt-viewer: chain in hovertemplate uses escapeHtml', () => {
  const src = fs.readFileSync(path.join(VIEWERS_DIR, 'plddt-viewer.html'), 'utf8');
  assert.ok(
    src.includes("hovertemplate: 'Chain ' + escapeHtml(chain)"),
    'chain in hovertemplate is not escaped'
  );
  // The unsafe pattern should be absent
  assert.ok(
    !src.includes("hovertemplate: 'Chain ' + chain + ' residue"),
    'Unsafe unescaped chain in hovertemplate still present'
  );
});

// ---------------------------------------------------------------------------
// 4b. Plotly XSS — expression-viewer
// ---------------------------------------------------------------------------

console.log('\n=== Plotly XSS — expression-viewer ===');

test('expression-viewer: contains escapeHtml definition', () => {
  const src = fs.readFileSync(path.join(VIEWERS_DIR, 'expression-viewer.html'), 'utf8');
  assert.ok(
    /function escapeHtml\(str\)/.test(src),
    'escapeHtml function not found in expression-viewer.html'
  );
});

test('expression-viewer: geneName in Plotly title uses escapeHtml', () => {
  const src = fs.readFileSync(path.join(VIEWERS_DIR, 'expression-viewer.html'), 'utf8');
  assert.ok(
    src.includes("escapeHtml(geneName)"),
    'geneName in Plotly title is not escaped'
  );
  // The unsafe pattern should be absent
  assert.ok(
    !src.includes("'Tissue Expression — ' + geneName,"),
    'Unsafe unescaped geneName in title still present'
  );
});

test('expression-viewer: document.title uses escapeHtml for geneName', () => {
  const src = fs.readFileSync(path.join(VIEWERS_DIR, 'expression-viewer.html'), 'utf8');
  assert.ok(
    src.includes("document.title = 'Expression: ' + escapeHtml(geneName)"),
    'document.title does not escape geneName'
  );
});

test('expression-viewer: plotTissues (y-axis labels) use escapeHtml', () => {
  const src = fs.readFileSync(path.join(VIEWERS_DIR, 'expression-viewer.html'), 'utf8');
  assert.ok(
    src.includes('escapeHtml(s.tissue)'),
    'tissue names in plotTissues are not escaped'
  );
  // The unsafe pattern should be absent
  assert.ok(
    !src.includes('return s.tissue; }).reverse()'),
    'Unsafe unescaped tissue names in plotTissues still present'
  );
});

test('expression-viewer: does NOT contain unescaped geneName in Plotly title', () => {
  const src = fs.readFileSync(path.join(VIEWERS_DIR, 'expression-viewer.html'), 'utf8');
  // Check there are no unescaped uses of geneName in Plotly config
  const titleMatch = src.match(/title:\s*\{\s*text:\s*'Tissue Expression — '\s*\+\s*(\w+)/);
  assert.ok(titleMatch, 'Could not find title text pattern');
  // The captured group should not be bare geneName without escapeHtml wrapper
  const contextIdx = src.indexOf("'Tissue Expression");
  const contextSlice = src.substring(contextIdx, contextIdx + 80);
  assert.ok(
    contextSlice.includes('escapeHtml(geneName)'),
    'Plotly title uses unescaped geneName: ' + contextSlice
  );
});

// ---------------------------------------------------------------------------
// 4c. Plotly XSS — docking-scores-viewer (#310)
// ---------------------------------------------------------------------------

console.log('\n=== Plotly XSS — docking-scores-viewer (#310) ===');

test('docking-scores-viewer: contains escapeHtml definition', () => {
  const src = fs.readFileSync(path.join(VIEWERS_DIR, 'docking-scores-viewer.html'), 'utf8');
  assert.ok(
    /function escapeHtml\(str\)/.test(src),
    'escapeHtml function not found in docking-scores-viewer.html'
  );
});

test('docking-scores-viewer: pose labels use escapeHtml', () => {
  const src = fs.readFileSync(path.join(VIEWERS_DIR, 'docking-scores-viewer.html'), 'utf8');
  assert.ok(
    src.includes("escapeHtml(p.pose_id"),
    'pose labels are not escaped with escapeHtml'
  );
});

test('docking-scores-viewer: does NOT contain unescaped pose label pattern', () => {
  const src = fs.readFileSync(path.join(VIEWERS_DIR, 'docking-scores-viewer.html'), 'utf8');
  assert.ok(
    !src.includes("return p.pose_id || p.id || p.mode || ('Pose ' + (i + 1));"),
    'Unsafe unescaped pose label pattern still present'
  );
});

// ---------------------------------------------------------------------------
// 4d. Plotly XSS — admet-viewer renderGeneric (#311)
// ---------------------------------------------------------------------------

console.log('\n=== Plotly XSS — admet-viewer renderGeneric (#311) ===');

test('admet-viewer: renderGeneric escapes property names', () => {
  const src = fs.readFileSync(path.join(VIEWERS_DIR, 'admet-viewer.html'), 'utf8');
  assert.ok(
    src.includes("escapeHtml(e.property"),
    'renderGeneric does not escape property names'
  );
});

test('admet-viewer: does NOT contain unescaped property name pattern in renderGeneric', () => {
  const src = fs.readFileSync(path.join(VIEWERS_DIR, 'admet-viewer.html'), 'utf8');
  assert.ok(
    !src.includes("return e.property || e.name || e.endpoint || '';"),
    'Unsafe unescaped property name pattern still present in renderGeneric'
  );
});

// ---------------------------------------------------------------------------
// 5.  Summary
// ---------------------------------------------------------------------------

console.log(`\n${'='.repeat(50)}`);
console.log(`Results: ${passed} passed, ${failed} failed, ${passed + failed} total`);
console.log('='.repeat(50));

if (failed > 0) {
  process.exit(1);
}
