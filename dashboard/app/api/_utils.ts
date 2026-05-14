import path from 'path'

export const BOT_ROOT = path.resolve(process.cwd(), '..')

/** Returns true if the OS process with `pid` is still alive. */
export function isAlive(pid: number | null): boolean {
  if (!pid) return false
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}
