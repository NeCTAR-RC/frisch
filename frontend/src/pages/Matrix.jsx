import { Fragment, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchMatrix, fetchStatus } from '../api.js'
import { lagDays, shortDate } from '../format.js'

function VersionCell({ info }) {
  if (!info) return <td className="cell empty">—</td>
  if (info.removed)
    return (
      <td className="cell removed" title={info.changed_at || ''}>
        removed
      </td>
    )
  if (info.mixed)
    return (
      <td className="cell">
        <span
          className="badge mixed"
          title={(info.versions || []).join(', ')}
        >
          mixed ({(info.versions || []).length})
        </span>
      </td>
    )
  return (
    <td className="cell">
      <span className="mono version" title={info.version || ''}>
        {info.version || '—'}
      </span>
      {info.floating && <span className="badge floating">floating</span>}
      {info.instances > 1 && (
        <span className="badge count">×{info.instances}</span>
      )}
    </td>
  )
}

function ChangedCell({ info }) {
  if (!info || (!info.changed_at && !info.removed))
    return <td className="cell empty">—</td>
  return (
    <td className="cell changed" title={info.changed_at || ''}>
      {shortDate(info.changed_at)}
    </td>
  )
}

function SortHeader({ label, k, sort, onSort }) {
  const active = sort.key === k
  return (
    <th aria-sort={active ? (sort.dir === 1 ? 'ascending' : 'descending') : undefined}>
      <button type="button" className="sort" onClick={() => onSort(k)}>
        {label}
        {active && <span className="arrow">{sort.dir === 1 ? '▲' : '▼'}</span>}
      </button>
    </th>
  )
}

function rowLag(row) {
  const test = row.environments['test']
  const prod = row.environments['prod']
  if (!row.differs || !test || !prod || test.removed) return null
  return lagDays(test.changed_at)
}

export default function Matrix() {
  const [data, setData] = useState(null)
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('')
  const [onlyDiffering, setOnlyDiffering] = useState(false)
  const [sort, setSort] = useState({ key: 'service', dir: 1 })

  useEffect(() => {
    fetchMatrix().then(setData).catch(setError)
    fetchStatus().then(setStatus).catch(() => {})
  }, [])

  const onSort = (key) =>
    setSort((s) =>
      s.key === key
        ? { key, dir: -s.dir }
        : // Lag and dates default to newest/biggest first.
          { key, dir: key === 'service' || key === 'source' ? 1 : -1 },
    )

  const rows = useMemo(() => {
    if (!data) return []
    const needle = filter.trim().toLowerCase()
    const visible = data.rows
      .filter((row) => {
        if (onlyDiffering && !row.differs) return false
        if (!needle) return true
        return (
          row.service.toLowerCase().includes(needle) ||
          row.source.includes(needle)
        )
      })
      .map((row) => ({ ...row, lag: rowLag(row) }))
    const value = (row) => {
      if (sort.key === 'lag') return row.lag
      if (sort.key === 'source') return row.source
      if (sort.key.startsWith('changed:'))
        return row.environments[sort.key.slice(8)]?.changed_at ?? null
      return row.display_name || row.service
    }
    return visible.sort((a, b) => {
      const va = value(a)
      const vb = value(b)
      if (va === vb) return a.service.localeCompare(b.service)
      // Rows without a value (no lag, no date) always sort last.
      if (va === null || va === undefined) return 1
      if (vb === null || vb === undefined) return -1
      return (va < vb ? -1 : 1) * sort.dir
    })
  }, [data, filter, onlyDiffering, sort])

  if (error) return <p className="error">{String(error)}</p>
  if (!data) return <p className="loading">Loading…</p>

  const envs = data.environments
  const stale = (status?.sources || []).filter(
    (s) => s.status !== 'success',
  )

  return (
    <>
      {stale.length > 0 && (
        <div className="banner warn">
          {stale
            .map((s) => `${s.source}/${s.env}: ${s.status}`)
            .join(' · ')}{' '}
          — <Link to="/status">details</Link>
        </div>
      )}
      <div className="toolbar">
        <input
          type="search"
          placeholder="Filter services…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <label className="check">
          <input
            type="checkbox"
            checked={onlyDiffering}
            onChange={(e) => setOnlyDiffering(e.target.checked)}
          />
          only where {envs.join(' ≠ ')}
        </label>
        <span className="muted">
          {rows.length} of {data.rows.length} rows
        </span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <SortHeader label="Service" k="service" sort={sort} onSort={onSort} />
              <SortHeader label="Source" k="source" sort={sort} onSort={onSort} />
              {envs.map((env) => (
                <Fragment key={env}>
                  <th>{env}</th>
                  <SortHeader
                    label="changed"
                    k={`changed:${env}`}
                    sort={sort}
                    onSort={onSort}
                  />
                </Fragment>
              ))}
              <SortHeader label="lag" k="lag" sort={sort} onSort={onSort} />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              return (
                <tr
                  key={row.service + row.source}
                  className={row.differs ? 'differs' : ''}
                >
                  <td>
                    <Link to={`/services/${row.service}`}>
                      {row.display_name || row.service}
                    </Link>
                  </td>
                  <td>
                    <span className={`badge source ${row.source}`}>
                      {row.source}
                    </span>
                  </td>
                  {envs.map((env) => (
                    <Fragment key={env}>
                      <VersionCell info={row.environments[env]} />
                      <ChangedCell info={row.environments[env]} />
                    </Fragment>
                  ))}
                  <td className="cell">
                    {row.lag !== null && row.lag > 0 && (
                      <span className="badge lag">{row.lag}d</span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </>
  )
}
