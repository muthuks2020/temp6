import React, { useState } from 'react'
import { get, post } from '../api.js'
import { useAsync, Status, DataTable } from '../components/ui.jsx'

const COLS = [
  { key: 'id', label: '#', type: 'num' },
  { key: 'job', label: 'Job' },
  { key: 'window_from', label: 'From (IST)' },
  { key: 'window_to', label: 'To (excl.)' },
  { key: 'started_at', label: 'Started', type: 'datetime' },
  { key: 'finished_at', label: 'Finished', type: 'datetime' },
  { key: 'rows_upserted', label: 'Rows', type: 'num' },
  { key: 'status', label: 'Status',
    render: (v) => <span className={`badge ${String(v).toLowerCase()}`}>
      {v === 'SUCCESS' ? '✓ ' : v === 'FAILED' ? '✕ ' : '… '}{v}</span> },
  { key: 'error', label: 'Error', render: (v) => <span className="err-text">{v || ''}</span> },
]

export default function Loads({ allowRun }) {
  const [tick, setTick] = useState(0)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [msg, setMsg] = useState(null)
  const log = useAsync(() => get('/loads'), [tick])

  const run = async () => {
    setMsg(null)
    try {
      await post('/loads/run', { date_from: from || null, date_to: to || null })
      setMsg({ ok: true, text: 'Load started. Refresh in a minute to see the result.' })
      setTimeout(() => setTick((t) => t + 1), 3000)
    } catch (e) { setMsg({ ok: false, text: e.message }) }
  }

  const rows = log.data || []
  const lastOk = rows.find((r) => r.status === 'SUCCESS')
  const lastFail = rows.find((r) => r.status === 'FAILED')

  return (
    <section>
      <div className="kpis">
        <div className="kpi"><div className="kpi-label">Last successful load</div>
          <div className="kpi-value small">{lastOk ? String(lastOk.finished_at).replace('T', ' ').slice(0, 16) : '—'}</div>
          <div className="kpi-sub">{lastOk?.job}</div></div>
        <div className="kpi"><div className="kpi-label">Last failure</div>
          <div className="kpi-value small">{lastFail ? String(lastFail.started_at).replace('T', ' ').slice(0, 16) : 'None'}</div>
          <div className="kpi-sub">{lastFail?.job}</div></div>
      </div>

      <div className="card">
        <h3>Run load now</h3>
        {allowRun ? (
          <div className="filters">
            <label>From <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
            <label>To (excl.) <input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
            <button className="primary" onClick={run}>Run T-1 load</button>
            <span className="muted">Leave dates empty for the normal daily load (last 2 days).</span>
          </div>
        ) : (
          <div className="msg">Running loads from the UI is turned off. Set <code>ALLOW_RUN=true</code> in backend/.env to enable it.</div>
        )}
        {msg && <div className={msg.ok ? 'msg ok' : 'msg error'}>{msg.text}</div>}
      </div>

      <div className="card-head">
        <h3>Load history</h3>
        <button onClick={() => setTick((t) => t + 1)}>Refresh</button>
      </div>
      <Status loading={log.loading} error={log.error} />
      <DataTable columns={COLS} rows={rows} />
    </section>
  )
}
