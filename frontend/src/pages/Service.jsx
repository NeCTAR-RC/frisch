import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { fetchHistory, fetchService } from '../api.js'
import { shortDate } from '../format.js'

function changedLabel(event) {
  if (event.precision === 'interval') {
    const from = shortDate(event.interval_start)
    const to = shortDate(event.changed_at)
    return from && from !== to ? `between ${from} and ${to}` : `by ${to}`
  }
  return shortDate(event.changed_at)
}

export default function Service() {
  const { name } = useParams()
  const [detail, setDetail] = useState(null)
  const [history, setHistory] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    setDetail(null)
    setHistory(null)
    fetchService(name).then(setDetail).catch(setError)
    fetchHistory(name).then(setHistory).catch(setError)
  }, [name])

  if (error) return <p className="error">{String(error)}</p>
  if (!detail) return <p className="loading">Loading…</p>

  return (
    <>
      <p className="crumbs">
        <Link to="/">← matrix</Link>
      </p>
      <h1>{detail.display_name || detail.service}</h1>

      <h2>Current state</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Env</th>
              <th>Source</th>
              <th>Instance</th>
              <th>Component</th>
              <th>Version</th>
              <th>Changed</th>
              <th>Last seen</th>
            </tr>
          </thead>
          <tbody>
            {detail.instances.map((inst) => (
              <tr key={inst.id} className={inst.active ? '' : 'inactive'}>
                <td>{inst.env}</td>
                <td>
                  <span className={`badge source ${inst.source}`}>
                    {inst.source}
                  </span>
                </td>
                <td>{inst.instance || '—'}</td>
                <td>{inst.component || '—'}</td>
                <td className="mono">
                  {inst.active ? inst.version || '—' : 'removed'}
                  {inst.version_kind === 'floating' && (
                    <span className="badge floating">floating</span>
                  )}
                  {inst.mixed && (
                    <span
                      className="badge mixed"
                      title={(inst.versions || [])
                        .map(
                          (v) =>
                            `${v.version} (${v.node_count}): ${(v.nodes || []).join(', ')}`,
                        )
                        .join(' · ')}
                    >
                      mixed ({(inst.versions || []).length})
                    </span>
                  )}
                </td>
                <td title={inst.changed_at || ''}>
                  {shortDate(inst.changed_at)}
                </td>
                <td title={inst.last_seen_at || ''}>
                  {shortDate(inst.last_seen_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>History</h2>
      {!history && <p className="loading">Loading…</p>}
      {history && history.events.length === 0 && (
        <p className="muted">No recorded changes yet.</p>
      )}
      {history && history.events.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Changed</th>
                <th>Env</th>
                <th>Source</th>
                <th>Instance</th>
                <th>Version</th>
                <th>From</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {history.events.map((event, i) => (
                <tr key={i}>
                  <td title={event.changed_at || ''}>
                    {changedLabel(event)}
                  </td>
                  <td>{event.env}</td>
                  <td>
                    <span className={`badge source ${event.source}`}>
                      {event.source}
                    </span>
                  </td>
                  <td>
                    {event.instance || '—'}
                    {event.component ? ` / ${event.component}` : ''}
                  </td>
                  <td className="mono">
                    {event.version === null ? 'removed' : event.version}
                  </td>
                  <td className="mono muted">
                    {event.previous_version || '—'}
                  </td>
                  <td className="small muted">
                    {event.meta?.subject ||
                      (event.meta?.mixed ? 'mixed across nodes' : '')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
