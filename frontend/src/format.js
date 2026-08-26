export function shortDate(iso) {
  if (!iso) return ''
  return iso.slice(0, 10)
}

export function relativeAge(iso) {
  if (!iso) return ''
  const then = new Date(iso)
  const days = Math.floor((Date.now() - then.getTime()) / 86400000)
  if (Number.isNaN(days)) return ''
  if (days <= 0) return 'today'
  if (days === 1) return '1 day ago'
  if (days < 60) return `${days} days ago`
  const months = Math.floor(days / 30)
  return `${months} months ago`
}

// Days that test has been ahead of prod, from the test change date.
export function lagDays(testIso) {
  if (!testIso) return null
  const days = Math.floor(
    (Date.now() - new Date(testIso).getTime()) / 86400000,
  )
  return Number.isNaN(days) ? null : Math.max(days, 0)
}
