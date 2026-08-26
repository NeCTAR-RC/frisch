import { describe, expect, it } from 'vitest'
import { resolveTheme, STORAGE_KEY, THEMES } from './theme.js'

describe('resolveTheme', () => {
  it('returns an explicit stored choice regardless of prefers-color-scheme', () => {
    expect(resolveTheme(true, 'light')).toBe('light')
    expect(resolveTheme(false, 'light')).toBe('light')
    expect(resolveTheme(true, 'dark')).toBe('dark')
    expect(resolveTheme(false, 'dark')).toBe('dark')
  })

  it('follows prefers-color-scheme when no choice is stored', () => {
    expect(resolveTheme(true, null)).toBe('dark')
    expect(resolveTheme(false, null)).toBe('light')
  })

  it('falls back to light when neither stored nor prefersDark', () => {
    expect(resolveTheme(false, null)).toBe('light')
    expect(resolveTheme(undefined, null)).toBe('light')
  })

  it('ignores invalid stored values', () => {
    expect(resolveTheme(true, 'auto')).toBe('dark')
    expect(resolveTheme(false, 'auto')).toBe('light')
    expect(resolveTheme(true, '')).toBe('dark')
    expect(resolveTheme(true, undefined)).toBe('dark')
  })
})

describe('theme constants', () => {
  it('exposes the localStorage key and the two valid themes', () => {
    expect(STORAGE_KEY).toBe('frisch-theme')
    expect(THEMES).toEqual(['light', 'dark'])
  })
})
