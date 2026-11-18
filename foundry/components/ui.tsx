"use client";

import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as SwitchPrimitive from "@radix-ui/react-switch";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { cva, type VariantProps } from "class-variance-authority";
import { AlertTriangle, Check, ChevronDown, CircleDot, Loader2, X } from "lucide-react";
import { cn, formatTime, safeJson } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-xs font-medium transition-all duration-150 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-signal-cyan disabled:pointer-events-none disabled:opacity-45",
  {
    variants: {
      variant: {
        default: "border border-signal-cyan/30 bg-signal-cyan/12 text-signal-cyan hover:bg-signal-cyan/18 hover:border-signal-cyan/50",
        secondary: "border border-line bg-graphite-850 text-ink-muted hover:bg-graphite-800 hover:text-ink",
        ghost: "text-ink-muted hover:bg-graphite-850 hover:text-ink",
        danger: "border border-signal-rose/30 bg-signal-rose/10 text-signal-rose hover:bg-signal-rose/18",
        outline: "border border-line bg-transparent text-ink hover:bg-graphite-850"
      },
      size: {
        sm: "h-7 px-2.5",
        default: "h-8 px-3",
        lg: "h-9 px-4 text-sm",
        icon: "h-8 w-8 p-0"
      }
    },
    defaultVariants: { variant: "default", size: "default" }
  }
);

export type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonVariants>;

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />
  )
);
Button.displayName = "Button";

export const Card = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("panel", className)} {...props} />
));
Card.displayName = "Card";

export function CardHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex items-start justify-between gap-3 border-b border-line px-4 py-3", className)} {...props} />;
}
export function CardTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={cn("text-sm font-semibold tracking-tight text-ink", className)} {...props} />;
}
export function CardDescription({ className, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("text-xs leading-relaxed text-ink-faint", className)} {...props} />;
}
export function CardContent({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-4", className)} {...props} />;
}

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(({ className, ...props }, ref) => (
  <input ref={ref} className={cn("h-8 w-full rounded-md border border-line bg-graphite-950/80 px-2.5 text-xs text-ink placeholder:text-ink-faint focus:border-signal-cyan/50 focus:outline-none focus:ring-1 focus:ring-signal-cyan/25", className)} {...props} />
));
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(({ className, ...props }, ref) => (
  <textarea ref={ref} className={cn("w-full rounded-md border border-line bg-graphite-950/80 px-2.5 py-2 font-mono text-xs leading-relaxed text-ink placeholder:text-ink-faint focus:border-signal-cyan/50 focus:outline-none focus:ring-1 focus:ring-signal-cyan/25", className)} {...props} />
));
Textarea.displayName = "Textarea";

export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(({ className, children, ...props }, ref) => (
  <div className="relative">
    <select ref={ref} className={cn("h-8 w-full appearance-none rounded-md border border-line bg-graphite-950/80 px-2.5 pr-8 text-xs text-ink focus:border-signal-cyan/50 focus:outline-none focus:ring-1 focus:ring-signal-cyan/25", className)} {...props}>{children}</select>
    <ChevronDown className="pointer-events-none absolute right-2 top-2 h-4 w-4 text-ink-faint" />
  </div>
));
Select.displayName = "Select";

export const Label = React.forwardRef<HTMLLabelElement, React.LabelHTMLAttributes<HTMLLabelElement>>(({ className, ...props }, ref) => (
  <label ref={ref} className={cn("mb-1 block text-2xs font-medium uppercase tracking-[0.12em] text-ink-faint", className)} {...props} />
));
Label.displayName = "Label";

export function Badge({ className, tone = "neutral", ...props }: React.HTMLAttributes<HTMLSpanElement> & { tone?: "neutral" | "cyan" | "green" | "amber" | "rose" | "violet" | "blue" }) {
  const tones: Record<string, string> = {
    neutral: "border-line bg-graphite-850 text-ink-muted",
    cyan: "border-signal-cyan/25 bg-signal-cyan/10 text-signal-cyan",
    green: "border-signal-green/25 bg-signal-green/10 text-signal-green",
    amber: "border-signal-amber/25 bg-signal-amber/10 text-signal-amber",
    rose: "border-signal-rose/25 bg-signal-rose/10 text-signal-rose",
    violet: "border-signal-violet/25 bg-signal-violet/10 text-signal-violet",
    blue: "border-signal-blue/25 bg-signal-blue/10 text-signal-blue"
  };
  return <span className={cn("inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-2xs uppercase tracking-wider", tones[tone], className)} {...props} />;
}

const statusTone: Record<string, "neutral" | "cyan" | "green" | "amber" | "rose" | "violet" | "blue"> = {
  idle: "neutral", thinking: "blue", executing: "cyan", running: "cyan", completed: "green", done: "green", ok: "green", online: "green",
  waiting_approval: "amber", pending: "amber", queued: "amber", blocked: "rose", failed: "rose", error: "rose", disabled: "neutral",
  skipped: "neutral", cancelled: "neutral", todo: "neutral", connected: "green", disconnected: "rose"
};

export function StatusPill({ status, className }: { status?: string | null; className?: string }) {
  const value = String(status ?? "unknown");
  return (
    <Badge tone={statusTone[value] ?? "neutral"} className={className}>
      <span className={cn("h-1.5 w-1.5 rounded-full bg-current", ["executing", "running", "thinking"].includes(value) && "animate-soft-pulse")} />
      {value.replaceAll("_", " ")}
    </Badge>
  );
}

export function MetricCard({ label, value, hint, icon, tone = "cyan" }: { label: string; value: React.ReactNode; hint?: string; icon?: React.ReactNode; tone?: "cyan" | "green" | "amber" | "rose" | "blue" | "violet" }) {
  const toneClass: Record<string, string> = {
    cyan: "text-signal-cyan bg-signal-cyan/10", green: "text-signal-green bg-signal-green/10", amber: "text-signal-amber bg-signal-amber/10",
    rose: "text-signal-rose bg-signal-rose/10", blue: "text-signal-blue bg-signal-blue/10", violet: "text-signal-violet bg-signal-violet/10"
  };
  return (
    <Card className="p-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-2xs uppercase tracking-[0.14em] text-ink-faint">{label}</div>
          <div className="mt-1 truncate text-xl font-semibold tracking-tight text-ink">{value}</div>
          {hint ? <div className="mt-1 truncate text-2xs text-ink-faint">{hint}</div> : null}
        </div>
        <div className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-lg", toneClass[tone])}>{icon ?? <CircleDot className="h-4 w-4" />}</div>
      </div>
    </Card>
  );
}

export function EmptyState({ title, description, action }: { title: string; description: string; action?: React.ReactNode }) {
  return (
    <div className="flex min-h-32 flex-col items-center justify-center rounded-lg border border-dashed border-line bg-graphite-950/40 px-5 py-8 text-center">
      <div className="mb-2 flex h-8 w-8 items-center justify-center rounded-lg border border-line bg-graphite-850 text-ink-faint"><AlertTriangle className="h-4 w-4" /></div>
      <div className="text-sm font-medium text-ink">{title}</div>
      <p className="mt-1 max-w-md text-xs leading-relaxed text-ink-faint">{description}</p>
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}

export function JsonBlock({ value, className }: { value: unknown; className?: string }) {
  return <pre className={cn("max-h-64 overflow-auto rounded-lg border border-line bg-graphite-950/80 p-3 font-mono text-2xs leading-relaxed text-ink-muted", className)}>{safeJson(value)}</pre>;
}

export function SectionHeader({ eyebrow, title, description, actions }: { eyebrow?: string; title: string; description?: string; actions?: React.ReactNode }) {
  return (
    <div className="mb-5 flex flex-col justify-between gap-3 sm:flex-row sm:items-end">
      <div>
        {eyebrow ? <div className="mb-1 font-mono text-2xs uppercase tracking-[0.18em] text-signal-cyan/70">{eyebrow}</div> : null}
        <h1 className="text-lg font-semibold tracking-tight text-ink">{title}</h1>
        {description ? <p className="mt-1 max-w-3xl text-xs leading-relaxed text-ink-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("h-3.5 w-3.5 animate-spin", className)} />;
}

export function InlineNotice({ tone = "amber", title, children }: { tone?: "amber" | "rose" | "cyan" | "green"; title?: string; children: React.ReactNode }) {
  const tones = {
    amber: "border-signal-amber/25 bg-signal-amber/8 text-signal-amber",
    rose: "border-signal-rose/25 bg-signal-rose/8 text-signal-rose",
    cyan: "border-signal-cyan/25 bg-signal-cyan/8 text-signal-cyan",
    green: "border-signal-green/25 bg-signal-green/8 text-signal-green"
  };
  return (
    <div className={cn("rounded-lg border px-3 py-2 text-xs leading-relaxed", tones[tone])}>
      {title ? <div className="mb-0.5 font-semibold">{title}</div> : null}
      <div className="text-ink-muted">{children}</div>
    </div>
  );
}

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;
export function DialogContent({ title, description, children, className }: { title?: string; description?: string; children: React.ReactNode; className?: string }) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/65 backdrop-blur-[2px] data-[state=open]:animate-fade-in" />
      <DialogPrimitive.Content className={cn("fixed left-1/2 top-1/2 z-50 w-[min(94vw,640px)] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-line bg-graphite-900 p-4 shadow-panel focus:outline-none", className)}>
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            {title ? <DialogPrimitive.Title className="text-sm font-semibold text-ink">{title}</DialogPrimitive.Title> : null}
            {description ? <DialogPrimitive.Description className="mt-1 text-xs text-ink-muted">{description}</DialogPrimitive.Description> : null}
          </div>
          <DialogPrimitive.Close className="rounded p-1 text-ink-faint hover:bg-graphite-850 hover:text-ink"><X className="h-4 w-4" /></DialogPrimitive.Close>
        </div>
        {children}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

export const Tabs = TabsPrimitive.Root;
export const TabsList = React.forwardRef<React.ElementRef<typeof TabsPrimitive.List>, React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>>(({ className, ...props }, ref) => (
  <TabsPrimitive.List ref={ref} className={cn("inline-flex h-8 items-center gap-1 rounded-lg border border-line bg-graphite-950/70 p-0.5", className)} {...props} />
));
TabsList.displayName = "TabsList";
export const TabsTrigger = React.forwardRef<React.ElementRef<typeof TabsPrimitive.Trigger>, React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>>(({ className, ...props }, ref) => (
  <TabsPrimitive.Trigger ref={ref} className={cn("inline-flex h-7 items-center rounded-md px-2.5 text-2xs font-medium text-ink-faint transition-colors data-[state=active]:bg-graphite-800 data-[state=active]:text-ink", className)} {...props} />
));
TabsTrigger.displayName = "TabsTrigger";
export const TabsContent = TabsPrimitive.Content;

export const Switch = React.forwardRef<React.ElementRef<typeof SwitchPrimitive.Root>, React.ComponentPropsWithoutRef<typeof SwitchPrimitive.Root>>(({ className, ...props }, ref) => (
  <SwitchPrimitive.Root ref={ref} className={cn("relative h-5 w-9 rounded-full border border-line bg-graphite-800 transition-colors data-[state=checked]:border-signal-cyan/40 data-[state=checked]:bg-signal-cyan/25", className)} {...props}>
    <SwitchPrimitive.Thumb className="block h-3.5 w-3.5 translate-x-0.5 rounded-full bg-ink-faint transition-transform data-[state=checked]:translate-x-[18px] data-[state=checked]:bg-signal-cyan" />
  </SwitchPrimitive.Root>
));
Switch.displayName = "Switch";

export const TooltipProvider = TooltipPrimitive.Provider;
export const Tooltip = TooltipPrimitive.Root;
export const TooltipTrigger = TooltipPrimitive.Trigger;
export const TooltipContent = React.forwardRef<React.ElementRef<typeof TooltipPrimitive.Content>, React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>>(({ className, ...props }, ref) => (
  <TooltipPrimitive.Portal><TooltipPrimitive.Content ref={ref} sideOffset={5} className={cn("z-50 rounded border border-line bg-graphite-850 px-2 py-1 text-2xs text-ink shadow-panel", className)} {...props} /></TooltipPrimitive.Portal>
));
TooltipContent.displayName = "TooltipContent";

export function Timestamp({ value }: { value?: string | null }) {
  return <span className="font-mono text-2xs text-ink-faint">{formatTime(value)}</span>;
}

export function SuccessLine({ children }: { children: React.ReactNode }) {
  return <div className="flex items-center gap-1.5 text-xs text-signal-green"><Check className="h-3.5 w-3.5" />{children}</div>;
}
