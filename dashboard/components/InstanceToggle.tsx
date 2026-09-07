'use client'

/** Chooses which instance's data this page shows.
 *
 *  Pure data view: it changes which files are read and nothing else. It never writes
 *  bot_mode.json or bot_command.json, so it cannot alter what the bot trades. The
 *  bot-mode control lives in Settings and takes effect only on restart.
 *
 *  'primary' is the bot that places real orders, in whatever mode it was started in.
 *  'shadow' is the virtual-only mirror running the opposite market: it holds no
 *  credentials and is structurally unable to trade.
 *
 *  Labelled by role rather than by market, because which market each one reads depends
 *  on the bot mode — the market is shown alongside so the reader always knows what they
 *  are looking at. Defaults to 'primary' so the page behaves as before.
 */
export type Instance = 'primary' | 'shadow'

export function oppositeMode(m: 'test' | 'live'): 'test' | 'live' {
  return m === 'live' ? 'test' : 'live'
}

export default function InstanceToggle({
  value, onChange, botMode,
}: {
  value: Instance
  onChange: (i: Instance) => void
  botMode: 'test' | 'live'
}) {
  const shadowMode = oppositeMode(botMode)
  const label: Record<Instance, string> = {
    primary: `Primary (${botMode})`,
    shadow: `Shadow (${shadowMode}, virtual)`,
  }
  const title: Record<Instance, string> = {
    primary: `The trading bot — places real orders on the ${botMode} market`,
    shadow: `Virtual-only mirror on the ${shadowMode} market — no credentials, never trades`,
  }
  return (
    <div className="inline-flex rounded border border-gray-700 overflow-hidden text-xs">
      {(['primary', 'shadow'] as Instance[]).map(i => (
        <button
          key={i}
          onClick={() => onChange(i)}
          title={title[i]}
          className={`px-3 py-1 transition-colors ${
            value === i ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-white'
          }`}
        >
          {label[i]}
        </button>
      ))}
    </div>
  )
}
