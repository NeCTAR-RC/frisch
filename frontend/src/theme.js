// Theme resolution for the frisch dashboard.
//
// The user's choice is persisted in localStorage under STORAGE_KEY. On first
// visit (no stored value) the theme follows the OS prefers-color-scheme, with
// light as the fallback when the media query is unavailable.
//
// `resolveTheme` is pure (no DOM) so it can be unit-tested without jsdom.
// `getInitialTheme` performs the DOM/storage reads and is used by the React
// hook; a tiny inline copy of the same logic runs in index.html before paint
// to set data-theme on <html> and avoid a flash of the wrong theme.

export const STORAGE_KEY = 'frisch-theme'
export const THEMES = ['light', 'dark']

// prefersDark: boolean | undefined  (undefined = media query unavailable)
// stored:      'light' | 'dark' | null
// returns:     'light' | 'dark'
export function resolveTheme(prefersDark, stored) {
  if (stored === 'light' || stored === 'dark') return stored
  return prefersDark ? 'dark' : 'light'
}

// Reads the current theme from the document root. The inline bootstrap script
// in index.html sets document.documentElement.dataset.theme before the React
// bundle runs, so this reflects the already-resolved, flash-free value.
export function getInitialTheme() {
  const attr = document.documentElement.dataset.theme
  return attr === 'dark' ? 'dark' : 'light'
}

// Reads localStorage + prefers-color-scheme. Used only as a fallback if the
// bootstrap script has not yet set data-theme (e.g. during isolated tests).
export function detectTheme() {
  let stored = null
  try {
    stored = localStorage.getItem(STORAGE_KEY)
  } catch {
    // localStorage may be disabled (private mode); treat as no stored value.
  }
  const prefersDark =
    typeof matchMedia !== 'undefined' &&
    matchMedia('(prefers-color-scheme: dark)').matches
  return resolveTheme(prefersDark, stored)
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    // Ignore write failures (private mode / storage disabled).
  }
}
