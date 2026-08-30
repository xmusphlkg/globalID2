import { copyFile, mkdir, readdir } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const packageRoot = join(projectRoot, 'node_modules', 'flag-icons');
const sourceDir = join(packageRoot, 'flags', '4x3');
const targetDir = join(projectRoot, 'public', 'flags');

let sourceEntries;
try {
  sourceEntries = await readdir(sourceDir, { withFileTypes: true });
} catch (error) {
  throw new Error('flag-icons is not installed; run npm install before syncing flags.', { cause: error });
}

const flagFiles = sourceEntries
  .filter(entry => entry.isFile() && entry.name.endsWith('.svg'))
  .map(entry => entry.name)
  .sort();

if (flagFiles.length < 250) {
  throw new Error(`Expected a complete flag-icons set, found only ${flagFiles.length} SVG files.`);
}

await mkdir(targetDir, { recursive: true });
await Promise.all(flagFiles.map(file => copyFile(join(sourceDir, file), join(targetDir, file))));
await copyFile(join(packageRoot, 'LICENSE'), join(targetDir, 'LICENSE.flag-icons.txt'));

console.log(`[flags] synced ${flagFiles.length} local SVG flags from flag-icons`);
