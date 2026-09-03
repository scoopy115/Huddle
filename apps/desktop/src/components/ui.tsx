import * as React from "react";
import { AlertTriangle, ChevronDown, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { sounds } from "@/lib/sounds";

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger" | "record";
  size?: "sm" | "md" | "lg";
  loading?: boolean;
};

/**
 * primary   — ink (warm near-black; inverted in dark mode), white/dark content
 * secondary — light gray with dark content
 * danger    — brand red #ea3d3d, white content (destructive)
 * record    — brand red #ea3d3d with a glow, white content (start/new recording)
 */
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "secondary", size = "md", loading, children, disabled, onClick, ...props }, ref) => (
    <button
      ref={ref}
      disabled={disabled || loading}
      onClick={(e) => { sounds.tap(); onClick?.(e); }}
      className={cn(
        "pressable inline-flex items-center justify-center gap-1.5 rounded-lg font-medium whitespace-nowrap",
        "disabled:opacity-50 disabled:pointer-events-none",
        size === "sm" && "h-7 px-2.5 text-[12px]",
        size === "md" && "h-8 px-3 text-[13px]",
        size === "lg" && "h-10 px-5 text-[14px]",
        variant === "primary" && "bg-ink text-ink-fg hover:brightness-[1.15] dark:hover:brightness-95 shadow-sm [&_svg]:text-ink-fg",
        variant === "secondary" && "bg-zinc-200 text-zinc-800 hover:bg-zinc-300 shadow-sm dark:bg-zinc-700 dark:text-zinc-100 dark:hover:bg-zinc-600 [&_svg]:text-current",
        variant === "ghost" && "text-fg/80 hover:bg-fg/[0.06] hover:text-fg",
        variant === "danger" && "bg-danger text-white hover:brightness-110 shadow-sm [&_svg]:text-white",
        variant === "record" && "bg-record text-white hover:brightness-110 glow-accent [&_svg]:text-white",
        className,
      )}
      {...props}
    >
      {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      {children}
    </button>
  ),
);
Button.displayName = "Button";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        "h-8 w-full rounded-lg border border-border bg-surface px-2.5 text-[13px] shadow-sm selectable",
        "placeholder:text-muted focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/20",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

/** An obvious dropdown: bordered field with a chevron, one fixed width everywhere in Settings. */
export function Select({ className, children, wide, ...props }: React.SelectHTMLAttributes<HTMLSelectElement> & { wide?: boolean }) {
  return (
    <div className={cn("relative", wide ? "w-[300px]" : "w-[240px]", className)}>
      <select
        className={cn(
          "h-8 w-full appearance-none rounded-lg border border-border bg-surface pl-2.5 pr-8 text-[13px] shadow-sm",
          "hover:border-fg/25 focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/20",
          "disabled:opacity-50",
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted" />
    </div>
  );
}

export function Switch({ checked, onChange, disabled }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => { if (checked) sounds.toggleOff(); else sounds.toggleOn(); onChange(!checked); }}
      className={cn(
        "pressable relative inline-flex h-[20px] w-[34px] shrink-0 items-center rounded-full transition-colors disabled:opacity-50",
        checked ? "bg-ink" : "bg-fg/20",
      )}
    >
      <span
        className={cn(
          "inline-block h-4 w-4 rounded-full shadow transition-transform",
          checked ? "translate-x-[16px] bg-ink-fg" : "translate-x-[2px] bg-white",
        )}
      />
    </button>
  );
}

export function Badge({ children, className, tone = "neutral", title }: { children: React.ReactNode; className?: string; tone?: "neutral" | "good" | "warn" | "bad" | "accent"; title?: string }) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium leading-none",
        tone === "neutral" && "bg-fg/[0.06] text-muted",
        tone === "good" && "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
        tone === "warn" && "bg-amber-500/10 text-amber-700 dark:text-amber-300",
        tone === "bad" && "bg-danger/10 text-danger",
        tone === "accent" && "bg-accent-soft text-accent",
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("h-4 w-4 animate-spin text-muted", className)} />;
}

/** The "h" of the app icon as an inline glyph; takes `currentColor`. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="150 150 700 700" className={className} aria-hidden="true" fill="currentColor">
      <path d="M724.1989,461.6645c-4.9843-91.6463-40.8535-131.4614-116.538-131.4614-81.6777,0-113.5417,47.796-132.4702,128.4649.9791-97.6095,0-196.1983-1.0086-293.8077-66.7248-7.9809-133.4493-7.9809-200.1739,0,4.9546,214.118,4.9546,461.1385,0,670.302,67.7037,7.9512,136.4457,7.9512,204.1791,0l-5.9931-247.9996h58.7734l-9.9686,247.9996c67.7334,7.9512,134.458,7.9512,202.1914,0,2.9966-180.2663,3.9755-314.7242,1.0086-373.4977Z" />
    </svg>
  );
}

export function EmptyState({ icon, title, body, action }: { icon?: React.ReactNode; title: string; body?: string; action?: React.ReactNode }) {
  return (
    <div className="relative flex h-full flex-col items-center justify-center gap-2 overflow-hidden p-10 text-center">
      <BrandMark className="pointer-events-none absolute left-1/2 top-1/2 h-[340px] w-[340px] -translate-x-1/2 -translate-y-[58%] text-accent opacity-[0.045] dark:opacity-[0.07]" />
      {icon && <div className="relative mb-1 flex h-12 w-12 items-center justify-center rounded-2xl bg-accent text-white glow-accent [&_svg]:text-white">{icon}</div>}
      <div className="relative font-display text-[17px] font-bold tracking-tight">{title}</div>
      {body && <div className="relative max-w-sm text-[13px] text-muted">{body}</div>}
      {action && <div className="relative mt-3">{action}</div>}
    </div>
  );
}

/** Section heading with the brand's little red tick. */
export function SectionTitle({ children, right }: { children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="mb-2 flex items-center justify-between gap-3">
      <h3 className="inline-flex items-center gap-2 font-display text-[12px] font-bold uppercase tracking-wider text-muted">
        <span className="h-[10px] w-[3px] rounded-full bg-accent" />
        {children}
      </h3>
      {right}
    </div>
  );
}

export function Card({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("panel", className)}>{children}</div>;
}

export function Row({ label, hint, hintNode, children, info }: { label: string; hint?: string; hintNode?: React.ReactNode; children: React.ReactNode; info?: string }) {
  return (
    <div className="flex items-center justify-between gap-6 px-4 py-3 border-b border-border last:border-b-0">
      <div className="min-w-0">
        <div className="flex items-center gap-1.5 text-[13px]">
          {label}
          {info && <InfoTip text={info} />}
        </div>
        {hint && <div className="text-[12px] text-muted">{hint}</div>}
        {hintNode && <div className="mt-0.5 text-[12px] text-muted">{hintNode}</div>}
      </div>
      <div className="shrink-0 flex items-center gap-2">{children}</div>
    </div>
  );
}

export function InfoTip({ text }: { text: string }) {
  return (
    <span className="group relative inline-flex">
      <span className="inline-flex h-[15px] w-[15px] cursor-help items-center justify-center rounded-full border border-border text-[10px] font-semibold text-muted">?</span>
      <span className="pointer-events-none absolute left-1/2 top-[22px] z-30 hidden w-[260px] -translate-x-1/2 rounded-lg border border-border bg-surface p-2.5 text-[12px] font-normal leading-snug text-fg shadow-xl group-hover:block">
        {text}
      </span>
    </span>
  );
}

export function Dialog({ open, onClose, title, children, footer, width = 420 }: { open: boolean; onClose: () => void; title: string; children: React.ReactNode; footer?: React.ReactNode; width?: number }) {
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-[2px]" onMouseDown={onClose}>
      <div
        className="animate-rise max-w-[92vw] rounded-2xl border border-border bg-surface p-5 shadow-2xl"
        style={{ width }}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="mb-3 font-display text-[16px] font-bold tracking-tight">{title}</div>
        <div className="text-[13px]">{children}</div>
        {footer && <div className="mt-5 flex justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}

/**
 * Confirmation for destructive, irreversible actions: a 5-second countdown before the
 * red confirm button becomes active. It stays in the same place the whole time.
 */
export function DangerDialog({ open, onClose, onConfirm, title, children, confirmLabel = "Delete", seconds = 5 }: { open: boolean; onClose: () => void; onConfirm: () => Promise<void> | void; title: string; children: React.ReactNode; confirmLabel?: string; seconds?: number }) {
  const [left, setLeft] = React.useState(seconds);
  const [busy, setBusy] = React.useState(false);
  React.useEffect(() => {
    if (!open) return;
    setLeft(seconds);
    setBusy(false);
    const t = setInterval(() => setLeft((n) => (n > 0 ? n - 1 : 0)), 1000);
    return () => clearInterval(t);
  }, [open, seconds]);
  const ready = left === 0;
  return (
    <Dialog open={open} onClose={onClose} title={title}>
      <div className="flex items-start gap-3 rounded-lg border border-danger/30 bg-danger/[0.06] p-3">
        <AlertTriangle className="mt-[1px] h-4 w-4 shrink-0 text-danger" />
        <div className="text-[13px] leading-relaxed">{children}</div>
      </div>
      <div className="mt-5 flex items-center justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button
          variant="danger"
          disabled={!ready || busy}
          loading={busy}
          className={cn("min-w-[170px]", !ready && "opacity-70")}
          onClick={async () => { setBusy(true); try { await onConfirm(); } finally { setBusy(false); } }}
        >
          {ready ? confirmLabel : `${confirmLabel} (${left})`}
        </Button>
      </div>
    </Dialog>
  );
}
