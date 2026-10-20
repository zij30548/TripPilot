"use client";

import { useId, useState } from "react";

/** Visibility only changes the current input, never storage or network behavior. */
export function PasswordInput({ label, name, value, onChange, autoComplete, minLength, maxLength = 256, disabled = false }: {
  label: string;
  name: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: "current-password" | "new-password";
  minLength?: number;
  maxLength?: number;
  disabled?: boolean;
}) {
  const id = useId();
  const [visible, setVisible] = useState(false);
  return <div className="grid min-w-0 gap-2 text-sm">
    <label htmlFor={id}>{label}</label>
    <div className="relative flex min-w-0 items-center">
      <input id={id} name={name} type={visible ? "text" : "password"} autoComplete={autoComplete}
        value={value} required minLength={minLength} maxLength={maxLength} disabled={disabled}
        className="min-w-0 w-full rounded-xl border p-3 pr-14"
        onChange={event => onChange(event.target.value)} />
      <button type="button" aria-label={`${visible ? "隐藏" : "显示"}${label}`} aria-pressed={visible}
        disabled={disabled} onClick={() => setVisible(current => !current)}
        className="absolute right-1 flex h-10 w-10 items-center justify-center rounded-lg text-slate-600 focus-visible:outline-2 focus-visible:outline-offset-2 disabled:opacity-50">
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-5 w-5">
          <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" />
          <circle cx="12" cy="12" r="3" />
          {visible && <path d="m3 3 18 18" />}
        </svg>
      </button>
    </div>
  </div>;
}
