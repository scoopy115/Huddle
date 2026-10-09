import { AlertTriangle, Check, Cpu, MemoryStick, Monitor, XCircle } from "lucide-react";
import type { HardwareInfo } from "@/types/engine";
import { fmtBytes } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * This computer in three lines (processor, memory, graphics) and what that means for local AI,
 * as the engine judged it (`hardware.capability`): full (a GPU the models fit in), limited
 * (CPU only, slow) or minimal (transcription only). Shown in onboarding and Settings → Models.
 */
export function HardwareCard({ hw, className }: { hw: HardwareInfo; className?: string }) {
  const cap = hw.capability;
  const Icon = cap.tier === "full" ? Check : cap.tier === "limited" ? AlertTriangle : XCircle;
  const tone = cap.tier === "full" ? "text-emerald-600" : cap.tier === "limited" ? "text-amber-600 dark:text-amber-400" : "text-danger";

  const card = hw.gpus.find((g) => !g.integrated && g.vramBytes) ?? hw.gpus.find((g) => !g.integrated);
  const integrated = hw.gpus.find((g) => g.integrated);
  const graphics = hw.unifiedMemory
    ? `${hw.acceleratorName ?? "Apple Silicon GPU"} · shares the memory`
    : card
      ? `${card.name}${card.vramBytes ? ` · ${fmtBytes(card.vramBytes)}` : ""}`
      : integrated
        ? `${integrated.name} (integrated)`
        : "None found";
  const memory = hw.memoryBytes ? `${fmtBytes(hw.memoryBytes)}${hw.unifiedMemory ? " unified memory" : ""}` : "Unknown";

  const row = "flex items-center gap-2.5 text-[12.5px]";
  const icon = "h-3.5 w-3.5 shrink-0 text-muted";
  return (
    <div className={cn("panel px-4 py-3", className)}>
      <div className="flex flex-col gap-1.5">
        <div className={row}><Cpu className={icon} /><span className="truncate">{hw.cpuBrand ?? "Unknown processor"}</span></div>
        <div className={row}><MemoryStick className={icon} /><span>{memory}</span></div>
        <div className={row}><Monitor className={icon} /><span className="truncate">{graphics}</span></div>
      </div>
      <div className="mt-3 flex items-center gap-2 border-t border-border pt-3">
        <Icon className={cn("h-4 w-4 shrink-0", tone)} />
        <div className="text-[13.5px] font-medium">{cap.title}</div>
      </div>
      <ul className="mt-1.5 flex flex-col gap-1 text-[12px]">
        {cap.details.map((d) => (
          <li key={d} className="flex items-start gap-2"><span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-fg/40" />{d}</li>
        ))}
      </ul>
    </div>
  );
}
