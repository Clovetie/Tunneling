"use client";

import React, { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/util";
import { ChevronDown, X, Check, CircleAlert, Loader2 } from "lucide-react";

/* ------------------------------------------------------------------ button */

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "subtle";
type ButtonSize = "sm" | "md" | "lg" | "icon";

const variants: Record<ButtonVariant, string> = {
  primary:
    "bg-accent text-[#0a0d14] hover:brightness-110 active:brightness-95 font-medium shadow-[0_1px_0_rgba(255,255,255,0.25)_inset]",
  secondary:
    "bg-raised text-ink border border-line hover:border-line-bright hover:bg-overlay",
  ghost: "text-muted hover:text-ink hover:bg-raised",
  danger: "bg-danger/12 text-danger border border-danger/30 hover:bg-danger/20",
  subtle: "bg-overlay/60 text-muted hover:text-ink",
};

const sizes: Record<ButtonSize, string> = {
  sm: "h-7 px-2.5 text-[12px] gap-1.5 rounded-[6px]",
  md: "h-8.5 px-3 text-[12.5px] gap-1.5 rounded-[7px]",
  lg: "h-10 px-4 text-[13.5px] gap-2 rounded-[9px]",
  icon: "h-8 w-8 rounded-[7px] justify-center",
};

export function Button({
  variant = "secondary",
  size = "md",
  className,
  children,
  loading,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
}) {
  return (
    <button
      {...rest}
      disabled={rest.disabled || loading}
      className={cn(
        "inline-flex items-center select-none whitespace-nowrap transition-[background,color,border-color,filter,transform] duration-150",
        "disabled:opacity-45 disabled:pointer-events-none active:scale-[0.985]",
        variants[variant],
        sizes[size],
        className,
      )}
    >
      {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
      {children}
    </button>
  );
}

/* ------------------------------------------------------------------- badge */

export function Badge({
  children,
  tone = "neutral",
  className,
  dot,
}: {
  children: React.ReactNode;
  tone?: "neutral" | "accent" | "success" | "warn" | "danger" | "violet";
  className?: string;
  dot?: boolean;
}) {
  const tones = {
    neutral: "bg-overlay text-muted border-line",
    accent: "bg-accent/12 text-accent border-accent/25",
    success: "bg-signal/10 text-signal border-signal/25",
    warn: "bg-warn/10 text-warn border-warn/25",
    danger: "bg-danger/10 text-danger border-danger/25",
    violet: "bg-violet/10 text-violet border-violet/25",
  } as const;
  const dots = {
    neutral: "bg-faint",
    accent: "bg-accent",
    success: "bg-signal",
    warn: "bg-warn",
    danger: "bg-danger",
    violet: "bg-violet",
  } as const;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-[2px] text-[10.5px] font-medium tracking-wide uppercase",
        tones[tone],
        className,
      )}
    >
      {dot && <span className={cn("h-1.5 w-1.5 rounded-full", dots[tone])} />}
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ inputs */

export function Field({
  label,
  hint,
  children,
  className,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <label className={cn("block", className)}>
      <span className="mb-1.5 flex items-baseline justify-between">
        <span className="text-[11px] font-medium tracking-wide text-muted uppercase">
          {label}
        </span>
        {hint && <span className="text-[10.5px] text-faint">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

const fieldBase =
  "w-full rounded-[7px] border border-line bg-abyss px-2.5 py-2 text-[13px] text-ink placeholder:text-faint transition-colors duration-150 focus:border-accent/50 focus:bg-void focus:outline-none";

export function Input({
  className,
  ...rest
}: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...rest} className={cn(fieldBase, className)} />;
}

export function Textarea({
  className,
  ...rest
}: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...rest}
      className={cn(fieldBase, "resize-none leading-relaxed", className)}
    />
  );
}

export function Select({
  className,
  children,
  ...rest
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <div className="relative">
      <select
        {...rest}
        className={cn(fieldBase, "appearance-none pr-8", className)}
      >
        {children}
      </select>
      <ChevronDown className="pointer-events-none absolute top-1/2 right-2.5 h-3.5 w-3.5 -translate-y-1/2 text-faint" />
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  description,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  description?: string;
}) {
  return (
    <div className="flex items-start justify-between gap-4 py-2.5">
      <div className="min-w-0">
        <div className="text-[13px] text-ink">{label}</div>
        {description && (
          <div className="mt-0.5 text-[11.5px] leading-relaxed text-faint">{description}</div>
        )}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative mt-0.5 h-[20px] w-[34px] shrink-0 rounded-full border transition-colors duration-200",
          checked ? "border-accent/40 bg-accent/85" : "border-line bg-overlay",
        )}
      >
        <span
          className={cn(
            "absolute top-[2px] h-[14px] w-[14px] rounded-full bg-white shadow transition-transform duration-200",
            checked ? "translate-x-[16px]" : "translate-x-[2px]",
          )}
        />
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ modal */

export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  width = "max-w-lg",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: React.ReactNode;
  width?: string;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[90] flex items-start justify-center overflow-y-auto p-4 sm:p-8">
      <div
        className="anim-fade fixed inset-0 bg-void/78 backdrop-blur-[3px]"
        onClick={onClose}
      />
      <div
        className={cn(
          "anim-fade-up relative mt-[7vh] w-full rounded-xl border border-line-bright bg-surface shadow-[0_30px_80px_-20px_rgba(0,0,0,0.9)]",
          width,
        )}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div>
            <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-ink">{title}</h2>
            {description && (
              <p className="mt-1 text-[12px] leading-relaxed text-muted">{description}</p>
            )}
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-faint transition-colors hover:bg-raised hover:text-ink"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ misc */

export function Progress({
  value,
  tone = "accent",
  className,
}: {
  value: number;
  tone?: "accent" | "success" | "warn" | "danger" | "violet";
  className?: string;
}) {
  const tones = {
    accent: "bg-accent",
    success: "bg-signal",
    warn: "bg-warn",
    danger: "bg-danger",
    violet: "bg-violet",
  } as const;
  return (
    <div className={cn("h-1 w-full overflow-hidden rounded-full bg-overlay", className)}>
      <div
        className={cn("h-full rounded-full transition-[width] duration-500", tones[tone])}
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </div>
  );
}

export function Ring({
  value,
  size = 44,
  stroke = 3,
  tone = "#7c9cff",
  children,
}: {
  value: number;
  size?: number;
  stroke?: number;
  tone?: string;
  children?: React.ReactNode;
}) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#1f252e" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={tone}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c - (c * Math.max(0, Math.min(100, value))) / 100}
          style={{ transition: "stroke-dashoffset 600ms cubic-bezier(0.22,1,0.36,1)" }}
        />
      </svg>
      <span className="absolute num text-[10.5px] font-medium text-muted">{children}</span>
    </div>
  );
}

export function PriorityDot({ priority }: { priority: string }) {
  const map: Record<string, string> = {
    critical: "bg-danger",
    high: "bg-warn",
    medium: "bg-accent",
    low: "bg-faint",
  };
  return (
    <span
      className={cn("inline-block h-1.5 w-1.5 shrink-0 rounded-full", map[priority] ?? "bg-faint")}
      title={priority}
    />
  );
}

export function StatusPill({ status }: { status: string }) {
  const map: Record<string, { tone: Parameters<typeof Badge>[0]["tone"]; label: string }> = {
    todo: { tone: "neutral", label: "Todo" },
    doing: { tone: "accent", label: "In progress" },
    blocked: { tone: "danger", label: "Blocked" },
    done: { tone: "success", label: "Done" },
    active: { tone: "success", label: "Active" },
    planning: { tone: "accent", label: "Planning" },
    paused: { tone: "warn", label: "Paused" },
    shipped: { tone: "violet", label: "Shipped" },
    archived: { tone: "neutral", label: "Archived" },
    connected: { tone: "success", label: "Connected" },
    available: { tone: "neutral", label: "Available" },
    error: { tone: "danger", label: "Error" },
    syncing: { tone: "accent", label: "Syncing" },
    success: { tone: "success", label: "Success" },
    failed: { tone: "danger", label: "Failed" },
    running: { tone: "accent", label: "Running" },
    skipped: { tone: "neutral", label: "Skipped" },
    ok: { tone: "success", label: "OK" },
    warn: { tone: "warn", label: "Warn" },
    pending: { tone: "warn", label: "Pending" },
  };
  const s = map[status] ?? { tone: "neutral" as const, label: status };
  return (
    <Badge tone={s.tone} dot>
      {s.label}
    </Badge>
  );
}

/* --------------------------------------------------------------- hovercard */

export function HoverTip({
  content,
  children,
  side = "bottom",
}: {
  content: string;
  children: React.ReactNode;
  side?: "bottom" | "right";
}) {
  const [show, setShow] = useState(false);
  const timer = useRef<number | null>(null);
  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => {
        timer.current = window.setTimeout(() => setShow(true), 380);
      }}
      onMouseLeave={() => {
        if (timer.current) window.clearTimeout(timer.current);
        setShow(false);
      }}
    >
      {children}
      {show && (
        <span
          className={cn(
            "anim-fade pointer-events-none absolute z-50 rounded-md border border-line-bright bg-overlay px-2 py-1 text-[11px] whitespace-nowrap text-ink shadow-lg",
            side === "bottom"
              ? "top-[calc(100%+6px)] left-1/2 -translate-x-1/2"
              : "top-1/2 left-[calc(100%+8px)] -translate-y-1/2",
          )}
        >
          {content}
        </span>
      )}
    </span>
  );
}

/* ------------------------------------------------------------ section head */

export function SectionHeader({
  title,
  eyebrow,
  description,
  actions,
}: {
  title: string;
  eyebrow?: string;
  description?: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3 border-b border-line px-6 py-4 md:px-8">
      <div className="min-w-0">
        {eyebrow && (
          <div className="mb-1 text-[10.5px] font-medium tracking-[0.14em] text-faint uppercase">
            {eyebrow}
          </div>
        )}
        <h1 className="text-[19px] font-semibold tracking-[-0.02em] text-ink">{title}</h1>
        {description && (
          <p className="mt-1 max-w-2xl text-[12.5px] leading-relaxed text-muted">{description}</p>
        )}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
      <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-xl border border-line bg-raised text-faint">
        {icon}
      </div>
      <div className="text-[13.5px] font-medium text-ink">{title}</div>
      <p className="mt-1 max-w-sm text-[12px] leading-relaxed text-faint">{description}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function CheckLine({ done, label }: { done?: boolean; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11.5px]">
      {done ? (
        <Check className="h-3 w-3 text-signal" />
      ) : (
        <CircleAlert className="h-3 w-3 text-faint" />
      )}
      <span className={done ? "text-muted" : "text-faint"}>{label}</span>
    </span>
  );
}

export function Sparkline({ data, tone = "#7c9cff", height = 28 }: { data: number[]; tone?: string; height?: number }) {
  if (!data.length) return null;
  const max = Math.max(...data, 1);
  const min = Math.min(...data, 0);
  const span = max - min || 1;
  const w = 100;
  const points = data.map((v, i) => {
    const x = (i / Math.max(1, data.length - 1)) * w;
    const y = height - ((v - min) / span) * (height - 4) - 2;
    return `${x},${y}`;
  });
  return (
    <svg viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" className="h-full w-full">
      <polyline
        points={points.join(" ")}
        fill="none"
        stroke={tone}
        strokeWidth="1.5"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
