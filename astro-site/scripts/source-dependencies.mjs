// Follow imports instead of treating every route as a dependency of every page.
// Unknown dynamic resolution deliberately uses the full source tree.
import ts from 'typescript';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, relative, resolve } from 'node:path';
import { fileFingerprints, gitSourceIdentities, sourceIdentity } from './file-fingerprints.mjs';

export function sourceFiles(root) {
  if (!existsSync(root)) return [];
  return readdirSync(root, { withFileTypes: true }).flatMap(entry => {
    const path = resolve(root, entry.name);
    return entry.isDirectory() ? sourceFiles(path) : (entry.isFile() || entry.isSymbolicLink()) ? [path] : [];
  });
}

function scripts(body, path) {
  if (!path.endsWith('.astro')) return [body];
  return [body.match(/^---\r?\n([\s\S]*?)\r?\n---/)?.[1] ?? '',
    ...[...body.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(match => match[1])];
}

function cssReferences(body) {
  const references = [];
  let uncertain = false;
  // Strings/comments are tokens too, so quoted prose and commented-out URLs
  // cannot accidentally become dependencies. Escapes are decoded as CSS, not JS.
  const tokens = /\/\*[\s\S]*?\*\/|"(?:\\[\s\S]|[^"\\])*"|'(?:\\[\s\S]|[^'\\])*'|@import\b|\burl\s*\(/gi;
  const string = /^(?:"((?:\\[\s\S]|[^"\\])*)"|'((?:\\[\s\S]|[^'\\])*)')/;
  const url = /^\s*(?:"((?:\\[\s\S]|[^"\\])*)"|'((?:\\[\s\S]|[^'\\])*)'|((?:\\[\s\S]|[^()"'\\])*?))\s*\)/;
  const decode = value => value.replace(/\\(?:([\da-f]{1,6})(?:\r\n|[\t\n\r\f ])?|(\r\n|[\n\r\f])|([\s\S]))/gi,
    (_match, hex, newline, character) => {
      const code = hex ? Number.parseInt(hex, 16) : 0;
      return hex ? String.fromCodePoint(code === 0 || code > 0x10ffff ? 0xfffd : code) : newline ? '' : character;
    });
  const add = (value, isImport) => {
    const specifier = decode(value);
    if (!specifier || /^(?:[a-z][a-z0-9+.-]*:|\/\/|#)/i.test(specifier)) return;
    references.push({ specifier, isImport });
  };
  let importUrlAt = -1;
  for (let token; (token = tokens.exec(body));) {
    if (token[0].toLowerCase() === '@import') {
      const remaining = body.slice(tokens.lastIndex);
      const offset = remaining.match(/^(?:\s|\/\*[\s\S]*?\*\/)*/)[0].length;
      const value = remaining.slice(offset);
      const literal = string.exec(value);
      if (literal) {
        add(literal[1] ?? literal[2], true);
        tokens.lastIndex += offset + literal[0].length;
      } else if (/^url\s*\(/i.test(value)) importUrlAt = tokens.lastIndex + offset;
      else uncertain = true;
    } else if (/^url\s*\(/i.test(token[0])) {
      const value = url.exec(body.slice(tokens.lastIndex));
      if (!value) { uncertain = true; continue; }
      add(value[1] ?? value[2] ?? value[3].trim(), token.index === importUrlAt);
      tokens.lastIndex += value[0].length;
    }
  }
  return { references, uncertain };
}

export function createSourceDependencyFingerprinter({ siteRoot, projectRoot = resolve(siteRoot, '..'), fingerprints = fileFingerprints, onPlan } = {}) {
  const identities = gitSourceIdentities(projectRoot);
  const parsed = new Map();
  const reasons = new Set();
  const dataRoot = resolve(siteRoot, 'src/data') + '/';
  const isData = path => path.startsWith(dataRoot) && path.endsWith('.json');
  const configPath = resolve(siteRoot, 'tsconfig.json');
  const config = existsSync(configPath) ? ts.readConfigFile(configPath, path => readFileSync(path, 'utf8')).config : {};
  const aliases = config?.compilerOptions?.paths ?? {};
  const aliasBase = resolve(siteRoot, config?.compilerOptions?.baseUrl ?? '.');
  const extensions = ['', '.ts', '.tsx', '.mjs', '.js', '.jsx', '.astro', '.css', '.json'];
  function resolveImport(specifier, importer, css = null) {
    let clean = specifier.split(/[?#]/)[0];
    if (css) {
      try { clean = decodeURIComponent(clean); } catch { return false; }
    }
    let base;
    if (clean.startsWith('.')) base = resolve(dirname(importer), clean);
    else if (clean.startsWith('/')) {
      const publicPath = resolve(siteRoot, 'public', `.${clean}`);
      base = existsSync(publicPath) ? publicPath : resolve(siteRoot, `.${clean}`);
    }
    else if (css) {
      const local = resolve(dirname(importer), clean);
      const packaged = resolve(siteRoot, 'node_modules', clean);
      base = css.isImport && !existsSync(local) && existsSync(packaged) ? packaged : local;
    }
    else {
      const alias = Object.keys(aliases).find(key => key.endsWith('*') ? clean.startsWith(key.slice(0, -1)) : clean === key);
      if (!alias) return null; // packages are covered by the lockfile
      const replacements = aliases[alias];
      if (replacements.length !== 1) return false;
      base = resolve(aliasBase, replacements[0].replace('*', clean.slice(alias.endsWith('*') ? alias.length - 1 : alias.length)));
    }
    for (const candidate of [...extensions.map(ext => base + ext), ...extensions.slice(1).map(ext => resolve(base, `index${ext}`))]) {
      if (existsSync(candidate) && statSync(candidate).isFile()) return candidate;
    }
    if (base.endsWith('.js') && existsSync(base.slice(0, -3) + '.ts')) return base.slice(0, -3) + '.ts';
    // An unresolved local import must never silently disappear from the key.
    return false;
  }
  function dependencies(path) {
    if (parsed.has(path)) return parsed.get(path);
    const result = { paths: [], uncertain: false };
    parsed.set(path, result);
    if (!/\.(?:astro|[cm]?[jt]sx?|css)$/.test(path)) return result;
    const body = readFileSync(path, 'utf8');
    const imports = new Set();
    for (const script of scripts(body, path)) {
      if (path.endsWith('.css')) break;
      for (const item of ts.preProcessFile(script, true, true).importedFiles) imports.add(item.fileName);
      const source = ts.createSourceFile(path, script, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
      const visit = node => {
        if (ts.isCallExpression(node)) {
          const expression = node.expression.getText(source);
          if (node.expression.kind === ts.SyntaxKind.ImportKeyword || expression === 'require') {
            if (node.arguments.length !== 1 || !ts.isStringLiteralLike(node.arguments[0])) result.uncertain = true;
          }
          if (expression === 'import.meta.glob' || expression === 'import.meta.globEager') result.uncertain = true;
        }
        if (ts.isNewExpression(node) && node.expression.getText(source) === 'URL'
          && node.arguments?.[1]?.getText(source) === 'import.meta.url') {
          if (ts.isStringLiteralLike(node.arguments[0])) imports.add(node.arguments[0].text);
          else result.uncertain = true;
        }
        ts.forEachChild(node, visit);
      };
      visit(source);
    }
    const styles = path.endsWith('.css') ? [body]
      : path.endsWith('.astro') ? [...body.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)].map(match => match[1]) : [];
    for (const style of styles) {
      const css = cssReferences(style);
      result.uncertain ||= css.uncertain;
      for (const reference of css.references) {
        const dependency = resolveImport(reference.specifier, path, reference);
        if (dependency === false) { result.uncertain = true; reasons.add(`${relative(siteRoot, path)}: unresolved CSS ${reference.specifier}`); }
        else if (dependency) result.paths.push(dependency);
      }
    }
    for (const match of body.matchAll(/<script\b[^>]*\bsrc=["']([^"']+)["']/g)) imports.add(match[1]);
    for (const specifier of imports) {
      const dependency = resolveImport(specifier, path);
      if (dependency === false) { result.uncertain = true; reasons.add(`${relative(siteRoot, path)}: unresolved ${specifier}`); }
      else if (dependency && !isData(dependency)) result.paths.push(dependency);
    }
    return result;
  }
  return entrypoints => {
    const seen = new Set();
    let uncertain = false;
    const visit = path => {
      if (seen.has(path)) return;
      seen.add(path);
      if (!existsSync(path)) return;
      const item = dependencies(path);
      uncertain ||= item.uncertain;
      if (item.uncertain) reasons.add(relative(siteRoot, path));
      item.paths.forEach(visit);
    };
    entrypoints.map(path => resolve(siteRoot, path)).forEach(visit);
    if (uncertain) {
      sourceFiles(resolve(siteRoot, 'src')).forEach(path => seen.add(path));
      sourceFiles(resolve(siteRoot, 'public')).forEach(path => seen.add(path));
      sourceFiles(resolve(projectRoot, 'shared')).forEach(path => seen.add(path));
    }
    const files = [...seen].sort();
    onPlan?.({ files, conservative: uncertain, reasons: [...reasons] });
    const hash = createHash('sha256');
    for (const path of files) {
      hash.update(`${relative(siteRoot, path)}\0`);
      hash.update(existsSync(path) ? sourceIdentity(path, identities, fingerprints) : 'missing');
    }
    return hash.digest('hex');
  };
}
