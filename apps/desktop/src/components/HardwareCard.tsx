import { AlertTriangle, Check, XCircle } from "lucide-react";
import type { HardwareInfo } from "@/types/engine";
import { fmtBytes } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * What this computer can do for local AI, as the engine judged it (`hardware.capability`):
 * full (a GPU the models fit in), limited (CPU only, slow) or minimal (transcription only).
 * Shown in onboarding and Settings → Models so a Windows laptop with integrated graphics
 * learns its limits before anything is downloaded.
 */
export function HardwareCard({ hw, className }: { hw: HardwareInfo; className?: string }) {
  const cap = hw.capability;
  const gpu = hw.unifiedMemory ? null : hw.acceleratorName ?? hw.gpus.find((g) => g.integrated)?.name ?? null;
  const Icon = cap.tier === "full" ? Check : cap.tier === "limited" ? AlertTriangle : XCircle;
  const tone = cap.tier === "full" ? "text-emerald-600" : cap.tier === "limited" ? "text-amber-600 dark:text-amber-400" : "text-danger";
  return (
    <div className={cn("panel px-4 py-3", className)}>
      <div className="flex items-center gap-2">
        <Icon className={cn("h-4 w-4 shrink-0", tone)} />
        <div className="text-[13.5px] font-medium">{cap.title}</div>
      </div>
      <div className="mt-1 text-[12px] text-muted">
        {[hw.cpuBrand, hw.memoryBytes ? `${fmtBytes(hw.memoryBytes)} memory` : null, gpu].filter(Boolean).join(" · ")}
      </div>
      <ul className="mt-2 flex flex-col gap-1 text-[12px]">
        {cap.details.map((d) => (
          <li key={d} className="flex items-start gap-2"><span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-fg/40" />{d}</li>
        ))}
      </ul>
    </div>
  );
}
