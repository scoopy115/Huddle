/** Locale + 12/24 h preference from the OS (set once at startup by App). */
let LOCALE: string | undefined;
let HOUR12: boolean | undefined;
export function configureLocale(locale: string | null | undefined, force24Hour: boolean | null | undefined) {
  LOCALE = locale ?? undefined;
  HOUR12 = force24Hour == null ? undefined : !force24Hour;
}

export function fmtTime(sec: number | null | undefined): string {
  if (sec == null || !isFinite(sec)) return "--:--";
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`
    : `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

export function fmtDuration(sec: number | null | undefined): string {
  if (sec == null || !isFinite(sec)) return "";
  const m = Math.round(sec / 60);
  if (m < 1) return `${Math.round(sec)} s`;
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  return `${h} h ${m % 60} min`;
}

/** Talk time as m:ss (or h:mm:ss) — never rounds short contributions to "0 min". */
export function fmtTalkTime(sec: number): string {
  return fmtTime(sec);
}

export function fmtDate(ts: number | null | undefined, opts: Intl.DateTimeFormatOptions = {}): string {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleDateString(LOCALE, { day: "numeric", month: "long", year: "numeric", ...opts });
}

/** Clock in the system's format (12 h or 24 h). */
export function fmtClock(ts: number | null | undefined): string {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit", hour12: HOUR12 });
}

/** Human language name in the UI language ("nl" → "Dutch"). */
export function languageName(code: string): string {
  try { return new Intl.DisplayNames([LOCALE ?? "en"], { type: "language" }).of(code) ?? code.toUpperCase(); } catch { return code.toUpperCase(); }
}

/** "~2 min left" from a stage's start time and progress fraction. */
export function fmtEta(startedAt: number | null | undefined, progress: number | null | undefined): string | null {
  if (!startedAt || progress == null || progress <= 0.02 || progress >= 1) return null;
  const elapsed = Date.now() / 1000 - startedAt;
  if (elapsed < 3) return null;
  const left = (elapsed / progress) * (1 - progress);
  if (left < 10) return "a few seconds left";
  if (left < 90) return `~${Math.round(left / 10) * 10} s left`;
  return `~${Math.round(left / 60)} min left`;
}


export function fmtRelativeDay(ts: number): string {
  const d = new Date(ts * 1000);
  const today = new Date();
  const diff = Math.floor((startOfDay(today) - startOfDay(d)) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  if (diff < 7) return d.toLocaleDateString(undefined, { weekday: "long" });
  return fmtDate(ts, { year: d.getFullYear() === today.getFullYear() ? undefined : "numeric" });
}

function startOfDay(d: Date) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

export function fmtBytes(n: number | null | undefined): string {
  if (n == null) return "";
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  if (n < 1024 * 1024 * 1024) return `${Math.round(n / (1024 * 1024))} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

export function fmtDueDate(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso + "T00:00:00");
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(LOCALE, { weekday: "short", day: "numeric", month: "short" });
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}
