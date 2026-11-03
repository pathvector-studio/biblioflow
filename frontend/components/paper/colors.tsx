import { cn } from "@/lib/utils";

export const ANNOTATION_COLORS = ["#fde047", "#86efac", "#93c5fd", "#fca5a5"] as const;
export const DEFAULT_COLOR = ANNOTATION_COLORS[0];

export function ColorSwatches({ value, onChange }: { value: string; onChange: (c: string) => void }) {
  return (
    <div className="flex gap-1.5">
      {ANNOTATION_COLORS.map((c) => (
        <button
          key={c}
          type="button"
          aria-label={c}
          aria-pressed={value === c}
          onClick={() => onChange(c)}
          className={cn("h-5 w-5 rounded-full border border-black/20", value === c && "ring-2 ring-ring ring-offset-1")}
          style={{ background: c }}
        />
      ))}
    </div>
  );
}
