import { useState, useEffect } from 'react'

export function useLocalStorage<T>(
  key: string,
  defaultValue: T,
): [T, React.Dispatch<React.SetStateAction<T>>] {
  const [value, setValue] = useState<T>(defaultValue)
  const [loaded, setLoaded] = useState(false)

  // Read from localStorage after mount so SSR and client renders agree on defaultValue
  useEffect(() => {
    try {
      const stored = localStorage.getItem(key)
      if (stored !== null) setValue(JSON.parse(stored) as T)
    } catch { /* corrupt entry — keep default */ }
    setLoaded(true)
  }, [key])

  // Persist on every change, but only after the initial restore is done
  useEffect(() => {
    if (!loaded) return
    try {
      localStorage.setItem(key, JSON.stringify(value))
    } catch { /* storage full */ }
  }, [key, value, loaded])

  return [value, setValue]
}
