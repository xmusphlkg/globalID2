// Assemble a client-code candidate without accepting freshly rendered content.
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync, constants } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname, resolve, relative } from 'node:path';
import { pathToFileURL } from 'node:url';
import { countryBaselineDigest } from './build-country-template.mjs';

const sha = body => createHash('sha256').update(body).digest('hex');
const files = root => readdirSync(root, { withFileTypes: true }).flatMap(entry => {
  const path = resolve(root, entry.name);
  if (entry.isSymbolicLink()) throw new Error(`Symlink in artifact: ${path}`);
  return entry.isDirectory() ? files(path) : [path];
});
const logicalName = name => name.replace(/\.[^.]+\.js$/, '.js');
const assetReference = /\/_astro\/([A-Za-z0-9_.-]+\.js)(?=["'\s<>?])/g;

export function assembleClientAssets({ baseline, compiled, destination }) {
  baseline = resolve(baseline); compiled = resolve(compiled); destination = resolve(destination);
  if (existsSync(destination) || destination.startsWith(baseline + '/') || destination.startsWith(compiled + '/')) throw new Error('Candidate must be a new independent directory');
  const release = JSON.parse(readFileSync(resolve(baseline, 'release.json')));
  const digest = countryBaselineDigest(baseline);
  if (digest !== release.build_digest) throw new Error('Retained baseline differs from its release digest');
  const oldFiles = files(baseline), generated = files(resolve(compiled, '_astro'));
  const oldHtml = oldFiles.filter(path => path.endsWith('.html'));
  const byName = new Map();
  for (const path of generated.filter(path => path.endsWith('.js'))) {
    const name = logicalName(relative(resolve(compiled, '_astro'), path));
    byName.set(name, [...(byName.get(name) || []), path]);
  }
  const replacements = new Map(), references = new Map();
  for (const path of oldHtml) for (const match of readFileSync(path, 'utf8').matchAll(assetReference)) {
    const name = match[1]; references.set(name, (references.get(name) || 0) + 1);
    const matches = byName.get(logicalName(name)) || [];
    if (matches.length !== 1) throw new Error(`Cannot uniquely map ${name}: ${matches.length} candidates`);
    const mapped = relative(resolve(compiled, '_astro'), matches[0]);
    replacements.set(name, mapped);
  }
  if (new Set(replacements.values()).size !== replacements.size) throw new Error('Multiple old entries map to the same new entry');
  for (const path of generated) {
    const target = resolve(baseline, '_astro', relative(resolve(compiled, '_astro'), path));
    if (existsSync(target) && sha(readFileSync(path)) !== sha(readFileSync(target))) throw new Error(`Immutable asset collision: ${target}`);
  }
  mkdirSync(dirname(destination), { recursive: true });
  cpSync(baseline, destination, { recursive: true, mode: constants.COPYFILE_FICLONE });
  for (const path of generated) {
    const target = resolve(destination, '_astro', relative(resolve(compiled, '_astro'), path));
    mkdirSync(dirname(target), { recursive: true }); cpSync(path, target);
  }
  const reverse = new Map([...replacements].map(([oldName, newName]) => [newName, oldName]));
  let changedHtml = 0, changedReferences = 0;
  for (const path of oldHtml) {
    const oldBody = readFileSync(path, 'utf8');
    const body = oldBody.replace(assetReference, (full, name) => {
      const mapped = replacements.get(name);
      if (mapped !== name) changedReferences++;
      return `/_astro/${mapped}`;
    });
    const restored = body.replace(assetReference, (full, name) => `/_astro/${reverse.get(name) || name}`);
    if (restored !== oldBody) throw new Error(`HTML content changed beyond authorized asset references: ${path}`);
    if (body !== oldBody) changedHtml++;
    writeFileSync(resolve(destination, relative(baseline, path)), body);
  }
  for (const path of oldFiles.filter(path => !path.endsWith('.html'))) {
    if (sha(readFileSync(path)) !== sha(readFileSync(resolve(destination, relative(baseline, path))))) throw new Error(`Baseline resource changed: ${path}`);
  }
  if (countryBaselineDigest(baseline) !== digest) throw new Error('Baseline mutated during assembly');
  const counts = files(destination);
  const receipt = { schema: 1, kind: 'client_assets_only', deployment_ready: false,
    reason: 'Candidate still requires browser parity checks and a separately reviewed release identity before deployment',
    baseline, compiled, destination, baseline_release_id: release.release_id, baseline_digest: digest,
    candidate_digest: countryBaselineDigest(destination), html_count: oldHtml.length, changed_html_count: changedHtml,
    changed_asset_references: changedReferences, preserved_non_html_count: oldFiles.length - oldHtml.length,
    file_count: counts.length, bytes: counts.reduce((sum, path) => sum + readFileSync(path).length, 0),
    mapping: [...replacements].map(([from, to]) => ({ from, to, references: references.get(from),
      from_sha256: sha(readFileSync(resolve(baseline, '_astro', from))), to_sha256: sha(readFileSync(resolve(destination, '_astro', to))) })),
    invariant: 'Every baseline HTML byte is identical after reversing only uniquely mapped /_astro/*.js references; all original non-HTML files are byte-identical; no compiled HTML accepted' };
  writeFileSync(`${destination}.receipt.json`, JSON.stringify(receipt, null, 2));
  return receipt;
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  const [baseline, compiled, destination] = process.argv.slice(2);
  if (!baseline || !compiled || !destination) throw new Error('Usage: <baseline-dist> <compiled-dist> <new-candidate>');
  const receipt = assembleClientAssets({ baseline, compiled, destination });
  console.log(JSON.stringify({ ...receipt, mapping: undefined }));
}
