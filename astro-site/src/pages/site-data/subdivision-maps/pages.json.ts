import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import type { APIRoute } from 'astro';
import type { SurveillanceFile } from '../../../lib/surveillance-data';

export const prerender = true;

// The template and these map files are copied from the same Pages build input.
// Pin the browser to those bytes even across a subsequent Pages deployment.
export const GET: APIRoute = () => {
  const root = resolve(process.env.GIDS_BUILD_PUBLIC_DIR || resolve(process.env.GIDS_CODE_BUILD_DIR || process.cwd(), 'public'));
  const files: Record<string, SurveillanceFile> = {};
  function include(key: string) {
    const bytes = readFileSync(join(root, 'site-data', key));
    const hash = createHash('sha256').update(bytes).digest('hex');
    files[key] = { url: `/site-data/${key}?v=${hash}`, sha256: hash, bytes: bytes.length };
    return bytes;
  }
  const index = 'subdivision-maps/index.json';
  if (existsSync(join(root, 'site-data', index))) {
    const catalog = JSON.parse(include(index).toString());
    for (const code of ['cn', 'us']) {
      if (!catalog.countries.some((entry: { code: string }) => entry.code === code.toUpperCase())) continue;
      const dir = `subdivision-maps/${code}`;
      const country = JSON.parse(include(`${dir}/index.json`).toString());
      for (const key of [country.boundary.path, country.population_path, ...country.diseases.map((item: { path: string }) => item.path)]) {
        if (typeof key !== 'string' || !key.startsWith(`/site-data/${dir}/`) || !/^[\w-]+\.json$/.test(key.slice(`/site-data/${dir}/`.length))) {
          throw new Error(`Invalid map file for ${code}: ${key}`);
        }
        include(key.slice('/site-data/'.length));
      }
    }
  }
  const hash = createHash('sha256').update(JSON.stringify(files)).digest('hex');
  return Response.json({ schema_version: 1, release_id: `pages-maps-${hash}`, files });
};
