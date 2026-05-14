'use client'

import { useLocalStorage } from '@/lib/useLocalStorage'

interface Props {
  title: React.ReactNode
  storageKey: string
  defaultOpen?: boolean
  children: React.ReactNode
  headerExtra?: React.ReactNode
}

export default function CollapsibleSection({
  title,
  storageKey,
  defaultOpen = true,
  children,
  headerExtra,
}: Props) {
  const [open, setOpen] = useLocalStorage<boolean>(storageKey, defaultOpen)

  return (
    <section>
      <div
        className="flex items-center justify-between mb-3 cursor-pointer select-none group"
        onClick={() => setOpen(o => !o)}
      >
        <h2 className="text-sm font-semibold uppercase text-gray-500 tracking-wider group-hover:text-gray-400 transition-colors">
          {title}
        </h2>
        <div className="flex items-center gap-3 ml-3 shrink-0">
          {headerExtra && (
            <span onClick={e => e.stopPropagation()}>
              {headerExtra}
            </span>
          )}
          <span className="text-[10px] text-gray-600 group-hover:text-gray-300 transition-colors">
            {open ? '▲ Hide' : '▼ Show'}
          </span>
        </div>
      </div>
      {open && children}
    </section>
  )
}
