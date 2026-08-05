import { escapeHtml } from "./input.ts";

export function markdownToHtml(markdown: string): string {
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  const html: string[] = [];
  let paragraph: string[] = [];
  let listType: "ul" | "ol" | null = null;
  let inCode = false;
  let codeLines: string[] = [];

  const flushParagraph = () => {
    if (paragraph.length === 0) return;
    html.push(`<p style="margin:0 0 14px">${renderInlineMarkdown(paragraph.join(" "))}</p>`);
    paragraph = [];
  };
  const closeList = () => {
    if (!listType) return;
    html.push(`</${listType}>`);
    listType = null;
  };

  for (const line of lines) {
    const raw = line.replace(/\s+$/, "");
    if (/^```/.test(raw.trim())) {
      if (inCode) {
        html.push(`<pre style="margin:0 0 14px;overflow:auto;background:#f1f5f9;padding:12px"><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
        codeLines = [];
        inCode = false;
      } else {
        flushParagraph();
        closeList();
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeLines.push(raw);
      continue;
    }

    const trimmed = raw.trim();
    if (!trimmed) {
      flushParagraph();
      closeList();
      continue;
    }

    const heading = /^(#{1,4})\s+(.+)$/.exec(trimmed);
    if (heading) {
      flushParagraph();
      closeList();
      const level = Math.min(heading[1].length + 1, 4);
      html.push(`<h${level} style="margin:18px 0 8px;font-size:${level === 2 ? 18 : 16}px;line-height:1.3">${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    const unordered = /^[-*]\s+(.+)$/.exec(trimmed);
    const ordered = /^\d+[.)]\s+(.+)$/.exec(trimmed);
    if (unordered || ordered) {
      flushParagraph();
      const nextType = unordered ? "ul" : "ol";
      if (listType !== nextType) {
        closeList();
        listType = nextType;
        html.push(`<${listType} style="margin:0 0 14px 20px;padding:0">`);
      }
      html.push(`<li style="margin:6px 0">${renderInlineMarkdown((unordered || ordered)?.[1] || "")}</li>`);
      continue;
    }

    closeList();
    paragraph.push(trimmed);
  }

  if (inCode) {
    html.push(`<pre style="margin:0 0 14px;overflow:auto;background:#f1f5f9;padding:12px"><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
  }
  flushParagraph();
  closeList();
  return html.join("\n");
}

export function renderInlineMarkdown(value: string): string {
  const links: string[] = [];
  let text = value.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_match, label, url) => {
    const token = `@@GIDS_LINK_${links.length}@@`;
    links.push(`<a href="${escapeHtml(url)}" style="color:#0f766e">${escapeHtml(label)}</a>`);
    return token;
  });
  text = escapeHtml(text);
  text = text.replace(/`([^`]+)`/g, "<code style=\"background:#f1f5f9;padding:1px 4px\">$1</code>");
  text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  text = text.replace(/@@GIDS_LINK_(\d+)@@/g, (_match, index) => links[Number(index)] || "");
  return text;
}

export function subjectFromMarkdown(markdown: string): string {
  for (const line of markdown.split(/\r?\n/)) {
    const heading = /^#\s+(.+)$/.exec(line.trim());
    if (heading) return heading[1].trim();
  }
  const first = markdown.split(/\r?\n/).find((line) => line.trim());
  return first ? first.trim().replace(/^#+\s*/, "").slice(0, 80) : "GIDS Update";
}

export function cleanMarkdown(value: string): string {
  return value.replace(/\u0000/g, "").trim().slice(0, 50000);
}

export function cleanHeaderValue(value: string, maxLength: number): string {
  return value.replace(/[\r\n]+/g, " ").replace(/\s+/g, " ").trim().slice(0, maxLength);
}
