/** A small entry point pins both immediate and later reads to one publication. */
export async function bootstrap(object: R2ObjectBody, channel: string): Promise<string> {
  if (object.size > 2 * 1024 * 1024) throw new Error('Data manifest exceeds supported size');
  const bytes = await object.arrayBuffer();
  const manifest = JSON.parse(new TextDecoder().decode(bytes));
  if (manifest.schema_version !== 1 || typeof manifest.release_id !== 'string' || !manifest.files
    || Array.isArray(manifest.files) || typeof manifest.files !== 'object') throw new Error('Invalid publication');
  const sha256 = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(x => x.toString(16).padStart(2, '0')).join('');
  const names = channel === 'surveillance' ? ['home-summary.json', 'home-influenza.json', 'site-status.json'] : ['summary.json'];
  return JSON.stringify({ schema_version: 1, release_id: manifest.release_id, generated_at: manifest.generated_at,
    files: Object.fromEntries(names.filter(name => manifest.files[name]).map(name => [name, manifest.files[name]])),
    manifest: { url: `manifests/${sha256}.json`, sha256, bytes: bytes.byteLength } });
}
