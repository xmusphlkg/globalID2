import { createHash } from 'node:crypto';

/** Hash the exact UTF-8 JSON bytes shared by the localized article renders. */
export function researchArticleVersion(bytes: Uint8Array): string {
  return createHash('sha256').update(bytes).digest('hex');
}
