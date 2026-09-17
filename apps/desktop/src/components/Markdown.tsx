import { Fragment, type ReactNode } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";
import { cn } from "@/lib/utils";

/**
 * A small Markdown renderer for release notes: headings, paragraphs, bullet and numbered lists,
 * horizontal rules, and inline bold / italic / code / links. Everything is built as React nodes
 * (no innerHTML), so text from GitHub can never inject markup. Links open in the browser.
 */
export function Markdown({ text, className }: { text: string; className?: string }) {
  const blocks = parseBlocks(text.replace(/\r/g, ""));
  return <div className={cn("selectable text-[12.5px] leading-relaxed text-fg/85", className)}>{blocks.map((b, i) => <Fragment key={i}>{b}</Fragment>)}</div>;
}

const H = ["font-display text-[14px] font-bold mt-3 mb-1 first:mt-0", "font-display text-[13px] font-bold mt-3 mb-1 first:mt-0", "font-semibold mt-2 mb-0.5 first:mt-0"];

function parseBlocks(src: string): ReactNode[] {
  const lines = src.split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    if (!line.trim()) { i++; continue; }
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      const level = Math.min(3, h[1]!.length) - 1;
      out.push(<div className={H[level]}>{inline(h[2]!)}</div>);
      i++; continue;
    }
    if (/^(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { out.push(<hr className="my-2 border-border" />); i++; continue; }
    if (line.startsWith("```")) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i]!.startsWith("```")) code.push(lines[i++]!);
      i++;
      out.push(<pre className="my-1.5 overflow-x-auto rounded-md bg-fg/[0.05] p-2 font-mono text-[11.5px]">{code.join("\n")}</pre>);
      continue;
    }
    const bullet = /^\s*([-*+]|\d+[.)])\s+/;
    if (bullet.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items: string[] = [];
      while (i < lines.length && lines[i]!.trim()) {
        const m = bullet.exec(lines[i]!);
        if (m) items.push(lines[i]!.slice(m[0].length));
        else if (items.length && /^\s+/.test(lines[i]!)) items[items.length - 1] += " " + lines[i]!.trim(); // wrapped item
        else break;
        i++;
      }
      const cls = cn("my-1 pl-5", ordered ? "list-decimal" : "list-disc");
      out.push(ordered ? <ol className={cls}>{items.map((t, k) => <li key={k}>{inline(t)}</li>)}</ol> : <ul className={cls}>{items.map((t, k) => <li key={k}>{inline(t)}</li>)}</ul>);
      continue;
    }
    // paragraph: consecutive non-empty, non-special lines
    const para: string[] = [];
    while (i < lines.length && lines[i]!.trim() && !/^#{1,6}\s/.test(lines[i]!) && !bullet.test(lines[i]!) && !lines[i]!.startsWith("```")) para.push(lines[i++]!.trim());
    out.push(<p className="my-1">{inline(para.join(" "))}</p>);
  }
  return out;
}

/** Inline: `code`, **bold**, *italic* / _italic_, [text](url), bare URLs. */
function inline(text: string): ReactNode[] {
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*|_[^_\s][^_]*_)|(\[[^\]]+\]\((https?:\/\/[^\s)]+)\))|(https?:\/\/[^\s<>)]+)/g;
  const out: ReactNode[] = [];
  let last = 0, k = 0, m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const s = m[0];
    if (m[1]) out.push(<code key={k++} className="rounded bg-fg/[0.06] px-1 font-mono text-[11.5px]">{s.slice(1, -1)}</code>);
    else if (m[2]) out.push(<b key={k++}>{inline(s.slice(2, -2))}</b>);
    else if (m[3]) out.push(<i key={k++}>{inline(s.slice(1, -1))}</i>);
    else if (m[4]) { const label = s.slice(1, s.indexOf("]")); out.push(<a key={k++} href={m[5]} className="text-accent hover:underline" onClick={(e) => { e.preventDefault(); openUrl(m![5]!); }}>{label}</a>); }
    else if (m[6]) out.push(<a key={k++} href={s} className="text-accent hover:underline" onClick={(e) => { e.preventDefault(); openUrl(s); }}>{s}</a>);
    last = m.index + s.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
