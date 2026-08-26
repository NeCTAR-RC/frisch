import { useCallback, useState } from 'react'
import { applyTheme, getInitialTheme } from './theme.js'

// Track and toggle the dashboard color theme. The inline script in index.html
// has already set document.documentElement.dataset.theme before the React
// bundle runs, so getInitialTheme() reflects the flash-free value and the
// first paint matches the user's choice. Toggling updates the DOM attribute
// and persists the choice to localStorage.
export function useTheme() {
  const [theme, setTheme] = useState(getInitialTheme)

  const toggleTheme = useCallback(() => {
    setTheme((prev) => {
      const next = prev === 'dark' ? 'light' : 'dark'
      applyTheme(next)
      return next
    })
  }, [])

  return { theme, toggleTheme }
}
