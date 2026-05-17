/**
 * Inline citation rendering utilities.
 *
 * Converts [n] markers in knowledge text into clickable superscript links
 * that reference the corresponding source in the References section.
 */

/**
 * Convert citation markers like [1], [2][3] in text to HTML superscript links.
 * Each [n] becomes a clickable <sup> element linking to the reference list.
 *
 * @param text - The raw text containing [n] citation markers
 * @param sources - Array of knowledge sources with id fields
 * @returns HTML string with citation markers replaced by superscript links
 */
export function renderCitations(text: string, sources: any[]): string {
  if (!text) return '';

  // Build a map from source id to its 1-based display index
  const idToIndex = new Map<number, number>();
  sources.forEach((source, idx) => {
    if (source?.id != null) {
      idToIndex.set(Number(source.id), idx + 1);
    }
  });

  // Replace [n] patterns with superscript HTML
  // Matches single [n] or consecutive [n1][n2] patterns
  return text.replace(/\[(\d+)\]/g, (match, numStr) => {
    const sourceId = parseInt(numStr, 10);
    const displayIndex = idToIndex.get(sourceId);
    if (displayIndex == null) {
      // If the source id doesn't match any known source, keep original text
      return match;
    }
    return `<sup class="citation-ref"><a href="#ref-${displayIndex}" class="citation-link" title="Reference ${displayIndex}">[${displayIndex}]</a></sup>`;
  });
}

/**
 * Check if text contains any citation markers in [n] format.
 */
export function hasCitations(text: string | null | undefined): boolean {
  if (!text) return false;
  return /\[\d+\]/.test(text);
}

/**
 * Extract all unique citation numbers from text.
 */
export function extractCitationIds(text: string): number[] {
  const ids = new Set<number>();
  let match: RegExpExecArray | null;
  const re = /\[(\d+)\]/g;
  while ((match = re.exec(text)) !== null) {
    ids.add(parseInt(match[1], 10));
  }
  return Array.from(ids).sort((a, b) => a - b);
}
