import { useEffect, useState } from 'react'
import { fetchStatus } from '../api.js'
import { relativeAge, shortDate } from '../format.js'

export default function Status() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetchStatus().then(setData).catch(setError)
  }, [])

  if (error) return <p className="error">{String(error)}</p>
  if (!data) return <p className="loading">Loading…</p>

  return (
    <>
      <h1>Collector status</h1>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Source</th>
              <th>Env</th>
              <th>Status</th>
              <th>Last run</th>
              <th>Last success</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {data.sources.map((s) => (
              <tr key={s.source + s.env}>
                <td>
                  <span className={`badge source ${s.source}`}>
                    {s.source}
                  </span>
                </td>
                <td>{s.env}</td>
                <td>
                  <span className={`badge status ${s.status}`}>
                    {s.status}
                  </span>
                </td>
                <td title={s.finished_at || ''}>
                  {shortDate(s.finished_at)} ·{' '}
                  {relativeAge(s.finished_at)}
                </td>
                <td title={s.last_success_at || ''}>
                  {shortDate(s.last_success_at)} ·{' '}
                  {relativeAge(s.last_success_at)}
                </td>
                <td className="small muted">
                  {s.error || JSON.stringify(s.stats)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.sources.length === 0 && (
        <p className="muted">
          No collector runs recorded yet — run <code>frisch-collect</code>.
        </p>
      )}
      <p className="muted small">frisch {data.version}</p>
    </>
  )
}
