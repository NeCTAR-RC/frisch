async function get(path) {
  const resp = await fetch(path)
  if (!resp.ok) {
    throw new Error(`${path}: ${resp.status} ${resp.statusText}`)
  }
  return resp.json()
}

export const fetchMatrix = () => get('/api/v1/matrix')
export const fetchService = (name) =>
  get(`/api/v1/services/${encodeURIComponent(name)}`)
export const fetchHistory = (name, env) =>
  get(
    `/api/v1/services/${encodeURIComponent(name)}/history` +
      (env ? `?env=${encodeURIComponent(env)}` : ''),
  )
export const fetchStatus = () => get('/api/v1/status')
