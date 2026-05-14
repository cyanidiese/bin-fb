'use client'

import { INPUT_CLS } from '@/lib/risk-styles'

export default function LabeledInput({
  label, tooltip, value, onChange, type = 'number', min, max, step, disabled,
}: {
  label: string; tooltip: string; value: number | string
  onChange: (v: string) => void; type?: string
  min?: number; max?: number; step?: number; disabled?: boolean
}) {
  return (
    <div className="flex items-center gap-3">
      <label
        className="text-xs text-gray-500 w-52 shrink-0"
        title={tooltip}
      >
        {label}
      </label>
      <input
        type={type}
        value={value}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        title={tooltip}
        onChange={e => onChange(e.target.value)}
        className={INPUT_CLS}
      />
    </div>
  )
}
