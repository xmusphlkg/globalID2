import { type ComponentPropsWithoutRef } from "react";

export type MarkdownAnchorProps = ComponentPropsWithoutRef<"a"> & { node?: unknown };

export function CitationAnchor({ node: _node, href, children, ...props }: MarkdownAnchorProps) {
  if (href?.startsWith("#report-ref-")) {
    return (
      <sup className="citation-ref">
        <a href={href} className="citation-link" {...props}>
          {children}
        </a>
      </sup>
    );
  }

  return (
    <a href={href} {...props}>
      {children}
    </a>
  );
}
