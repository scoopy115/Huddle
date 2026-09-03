import { useEffect, useRef } from "react";
import { Bold, Italic, List } from "lucide-react";
import { cn } from "@/lib/utils";

function Tool({ cmd, icon: Icon, title, exec }: { cmd: string; icon: typeof Bold; title: string; exec: (cmd: string) => void }) {
  return (
    <button type="button" title={title} onMouseDown={(e) => { e.preventDefault(); exec(cmd); }}
      className="pressable rounded-md p-1.5 text-muted hover:bg-fg/[0.06] hover:text-fg"><Icon className="h-3.5 w-3.5" /></button>
  );
}

/**
 * A small WYSIWYG field (bold, italic, bullet list) on top of contentEditable. Produces HTML;
 * the engine turns it into plain text for the model. No dependency, no network.
 */
export function RichTextEditor({ value, onChange, placeholder, className, autoFocus }: {
  value: string; onChange: (html: string) => void; placeholder?: string; className?: string; autoFocus?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);

  // Only write into the DOM when the outside value differs from what the user has typed,
  // otherwise the caret would jump on every keystroke.
  useEffect(() => {
    const el = box.current;
    if (el && el.innerHTML !== value) el.innerHTML = value;
  }, [value]);
  useEffect(() => { if (autoFocus) setTimeout(() => box.current?.focus(), 0); }, [autoFocus]);

  const exec = (cmd: string) => { box.current?.focus(); document.execCommand(cmd); onChange(box.current?.innerHTML ?? ""); };
  const empty = !value || value === "<br>" || value.replace(/<[^>]*>/g, "").trim() === "";

  return (
    <div className={cn("overflow-hidden rounded-lg border border-border bg-surface shadow-sm focus-within:border-accent/60 focus-within:ring-2 focus-within:ring-accent/20", className)}>
      <div className="flex items-center gap-0.5 border-b border-border bg-bg/60 px-1.5 py-1">
        <Tool cmd="bold" icon={Bold} title="Bold" exec={exec} />
        <Tool cmd="italic" icon={Italic} title="Italic" exec={exec} />
        <Tool cmd="insertUnorderedList" icon={List} title="Bulleted list" exec={exec} />
      </div>
      <div className="relative">
        {empty && placeholder && <div className="pointer-events-none absolute left-3 top-2.5 text-[13px] text-muted">{placeholder}</div>}
        <div
          ref={box}
          contentEditable
          suppressContentEditableWarning
          onInput={(e) => onChange((e.target as HTMLDivElement).innerHTML)}
          className="selectable rich min-h-[140px] max-h-[40vh] overflow-y-auto px-3 py-2.5 text-[13.5px] leading-relaxed outline-none"
        />
      </div>
    </div>
  );
}
