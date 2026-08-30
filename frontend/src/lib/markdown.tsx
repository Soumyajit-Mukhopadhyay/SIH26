/**
 * The two lines of markdown ORCA's answers actually use.
 *
 * Shared rather than duplicated because the localised answer had its own,
 * simpler renderer, and that renderer stripped a leading `-` or `*` from every
 * line to turn bullets into "· ". On `**UNVERIFIABLE**` — a verdict at the start
 * of a line — it ate one asterisk of the opening pair and the verdict rendered
 * as `· *UNVERIFIABLE**`. The verdict is the one thing on the page that must not
 * come out mangled, so both panels now go through the same code.
 */

import type { ReactNode } from 'react';

/** Bold spans, in a single line of text. */
export function inline(text: string): ReactNode {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, index) =>
    part.startsWith('**') && part.endsWith('**') ? (
      <strong key={index} className="text-ink-0 font-semibold">
        {part.slice(2, -2)}
      </strong>
    ) : (
      <span key={index}>{part}</span>
    ),
  );
}

/**
 * Strip a list marker, and only a list marker.
 *
 * A bullet is a `-` or `*` followed by whitespace. `**bold**` has no space after
 * the marker, which is exactly what distinguishes the two — so the space is
 * required rather than optional.
 */
export function stripBullet(line: string): { text: string; bullet: boolean } {
  const match = /^\s*[-*]\s+/.exec(line);
  return match ? { text: line.slice(match[0].length), bullet: true } : { text: line, bullet: false };
}
