import React, { createContext, useContext, useEffect, useRef, useState } from 'react'
import { get, num, money, withSignal } from '../api.js'

export function useAsync(fn, deps) {
  const [state, set] = useState({ loading: true, data: null, error: null })
  useEffect(() => {
    let alive = true
    const ctrl = new AbortController()
    set((s) => ({ ...s, loading: true, error: null }))
    withSignal(ctrl.signal, fn).then(
      (data) => alive && set({ loading: false, data, error: null }),
      (error) => alive && set({ loading: false, data: null, error: error.message }),
    )
    return () => { alive = false; ctrl.abort() }
  }, deps) // eslint-disable-line
  return state
}

/** Runs async steps ONE AFTER ANOTHER (never in parallel). steps = [[name, fn], ...].
 *  A step whose fn returns undefined is skipped (keeps its previous data). */
export function useSteps(steps, deps) {
  const [st, set] = useState({})
  useEffect(() => {
    let alive = true
    const ctrl = new AbortController()   // leaving the page / changing a filter cancels this page's requests
    ;(async () => {
      for (const [name, fn] of steps) {
        if (!alive) return
        const pending = withSignal(ctrl.signal, fn)
        if (pending === undefined) continue
        set((x) => ({ ...x, [name]: { ...(x[name] || {}), loading: true, error: null } }))
        try {
          const data = await pending
          if (!alive) return
          set((x) => ({ ...x, [name]: { loading: false, data, error: null } }))
        } catch (e) {
          if (!alive) return
          set((x) => ({ ...x, [name]: { loading: false, data: null, error: e.message } }))
        }
      }
    })()
    return () => { alive = false; ctrl.abort() }
  }, deps) // eslint-disable-line
  return (name) => st[name] || { loading: false, data: null, error: null }
}

/** Indian fiscal year (1 Apr - 31 Mar). offset 0 = current FY, -1 = last FY */
export function fiscalYear(offset = 0) {
  const d = new Date()
  const y = (d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1) + offset
  return { from: `${y}-04-01`, to: offset === 0 ? '' : `${y + 1}-03-31`, label: `FY ${y}-${String(y + 1).slice(2)}` }
}

export function periodRange(period, custom = {}) {
  if (period === 'fy') return fiscalYear(0)
  if (period === 'lfy') return fiscalYear(-1)
  if (period === '5fy') return { from: fiscalYear(-4).from, to: '', label: `last 5 FYs (${fiscalYear(-4).label.slice(3)} to ${fiscalYear(0).label.slice(3)})` }
  if (period === '12m') {
    const d = new Date(); d.setFullYear(d.getFullYear() - 1)
    return { from: d.toISOString().slice(0, 10), to: '', label: 'last 12 months' }
  }
  if (period === 'custom') return { from: custom.from || '', to: custom.to || '', label: 'selected dates' }
  return { from: '', to: '', label: 'all years' }
}

export function PeriodSelect({ period, setPeriod, custom, setCustom }) {
  return (
    <>
      <select value={period} onChange={(e) => setPeriod(e.target.value)}>
        <option value="fy">This fiscal year ({fiscalYear(0).label})</option>
        <option value="lfy">Last fiscal year ({fiscalYear(-1).label})</option>
        <option value="5fy">Last 5 fiscal years</option>
        <option value="12m">Last 12 months</option>
        <option value="all">All years</option>
        <option value="custom">Custom dates…</option>
      </select>
      {period === 'custom' && (
        <>
          <label>From <input type="date" value={custom.from || ''} onChange={(e) => setCustom({ ...custom, from: e.target.value })} /></label>
          <label>To <input type="date" value={custom.to || ''} onChange={(e) => setCustom({ ...custom, to: e.target.value })} /></label>
        </>
      )}
    </>
  )
}

export const monthStart = () => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-01`
}

export function Kpi({ label, value, sub, loading, error }) {
  return (
    <div className={loading ? 'kpi loading' : 'kpi'} title={error || ''}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{loading ? <span className="dots">loading</span> : error ? '⚠' : value}</div>
      {sub && !loading && !error && <div className="kpi-sub">{sub}</div>}
    </div>
  )
}

/** Steps for "table first, then each KPI tile one by one". Tiles are not re-queried while the
 *  filters are unchanged (paging/sorting only reloads the table). */
export function useTiles(rowsFn, tileNames, tileFn, params, rowDeps) {
  const pk = JSON.stringify(params)
  const done = useRef({})
  const step = useSteps([
    ['rows', rowsFn],
    ...tileNames.map((t) => [t, () => (done.current[t] === pk ? undefined
      : tileFn(t).then((r) => { done.current[t] = pk; return r }))]),
  ], [pk, ...rowDeps])
  // a tile still waiting for its turn also counts as loading (not "—")
  return (name) => {
    const st = step(name)
    if (name === 'rows' || st.error) return st
    return { ...st, loading: st.loading || done.current[name] !== pk }
  }
}

function Waiting() {
  const [sec, setSec] = useState(0)
  useEffect(() => { const t = setInterval(() => setSec((x) => x + 1), 1000); return () => clearInterval(t) }, [])
  return (
    <div className="msg">
      <span className="spinner" /> Loading{sec >= 3 ? ` – ${sec}s` : '…'}
      {sec >= 30 && <span className="muted"> · large data, still working. <a href="/api/status" target="_blank" rel="noreferrer">See what is running</a></span>}
    </div>
  )
}

export function Status({ loading, error }) {
  if (error) return <div className="msg error">⚠ {error}</div>
  if (loading) return <Waiting />
  return null
}

function fmt(v, type) {
  if (type === 'money') return money(v)
  if (type === 'num') return num(v, 3)
  if (type === 'pct') return v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${num(v, 1)}%`
  if (type === 'datetime') return v ? String(v).replace('T', ' ').slice(0, 16) : '—'
  return v ?? '—'
}

/** columns: [{key,label,type,render,sortable}] ; client-side sort unless onSort given */
export function DataTable({ columns, rows, onSort, sort, order, onRowClick, empty = 'No data' }) {
  const [cs, setCs] = useState({ key: null, dir: 'desc' })
  let data = rows || []
  const active = onSort ? { key: sort, dir: order } : cs
  if (!onSort && cs.key) {
    data = [...data].sort((a, b) => {
      const x = a[cs.key], y = b[cs.key]
      if (x === y) return 0
      if (x === null || x === undefined) return 1
      if (y === null || y === undefined) return -1
      return (x > y ? 1 : -1) * (cs.dir === 'asc' ? 1 : -1)
    })
  }
  const click = (k) => {
    const dir = active.key === k && active.dir === 'desc' ? 'asc' : 'desc'
    onSort ? onSort(k, dir) : setCs({ key: k, dir })
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} className={['num', 'money', 'pct'].includes(c.type) ? 'r' : ''}
                  onClick={() => c.sortable !== false && click(c.key)}>
                {c.label}{active.key === c.key ? (active.dir === 'asc' ? ' ▲' : ' ▼') : ''}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.length === 0 && (
            <tr><td colSpan={columns.length} className="empty">{empty}</td></tr>
          )}
          {data.map((r, i) => (
            <tr key={i} onClick={onRowClick ? () => onRowClick(r) : undefined}
                className={onRowClick ? 'clickable' : ''}>
              {columns.map((c) => (
                <td key={c.key} className={['num', 'money', 'pct'].includes(c.type) ? 'r' : ''}>
                  {c.render ? c.render(r[c.key], r, i) : fmt(r[c.key], c.type)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** async search dropdown: endpoint = '/products' | '/vendors' */
export function Picker({ endpoint, value, onChange, placeholder }) {
  const [text, setText] = useState(value?.name || '')
  const [opts, setOpts] = useState([])
  const [open, setOpen] = useState(false)
  const t = useRef()
  useEffect(() => { setText(value?.name || '') }, [value])
  const search = (s) => {
    setText(s); setOpen(true)
    clearTimeout(t.current)
    t.current = setTimeout(() => get(endpoint, { search: s }).then(setOpts).catch(() => setOpts([])), 250)
  }
  return (
    <div className="picker">
      <input value={text} placeholder={placeholder} onChange={(e) => search(e.target.value)}
             onFocus={() => search(text)} onBlur={() => setTimeout(() => setOpen(false), 200)} />
      {value && <button className="x" onClick={() => { onChange(null); setText('') }}>×</button>}
      {open && opts.length > 0 && (
        <div className="picker-menu">
          {opts.map((o) => (
            <div key={o.id} className="picker-opt" onMouseDown={() => { onChange(o); setOpen(false) }}>
              {o.item_code && <span className="code">{o.item_code}</span>} {o.name}
              {o.gstin && <span className="muted"> · {o.gstin}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ONE company choice for the whole dashboard: picked on any page, every page uses it (kept after reload too)
export const CompanyContext = createContext([null, () => {}])
export const useCompany = () => useContext(CompanyContext)

export function CompanySelect({ companies, value, onChange }) {
  // value: '' / null = all companies, otherwise comma-separated ids ("1,3").
  // Each tick refreshes the page straight away (short pause so quick ticks load once); the menu stays open.
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState(new Set())
  const box = useRef(null)
  const timer = useRef(null)
  const all = companies || []
  const sel = new Set(String(value || '').split(',').filter(Boolean).map(Number))
  const emit = (set) => {
    const ids = [...set].sort((a, b) => a - b)
    onChange(ids.length && ids.length < all.length ? ids.join(',') : null)
  }
  const push = (set) => { setDraft(set); clearTimeout(timer.current); timer.current = setTimeout(() => emit(set), 400) }
  // closing with nothing ticked (after "Deselect all") means all companies again
  const finish = () => { if (draft.size === 0 && sel.size > 0) { clearTimeout(timer.current); onChange(null) } setOpen(false) }
  useEffect(() => {
    if (!open) return
    const close = (e) => { if (box.current && !box.current.contains(e.target)) finish() }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open, draft, value]) // eslint-disable-line
  useEffect(() => () => clearTimeout(timer.current), [])
  const show = () => { if (!open) setDraft(sel.size ? sel : new Set(all.map((c) => c.id))); setOpen(!open) }
  const toggle = (id) => { const n = new Set(draft); n.has(id) ? n.delete(id) : n.add(id); push(n) }
  const label = sel.size === 0 ? 'All companies'
    : sel.size === 1 ? (all.find((c) => c.id === [...sel][0])?.name || '1 company') : `${sel.size} companies`
  return (
    <div className="gl-select" ref={box}>
      <button className={sel.size ? 'gl-btn on' : 'gl-btn'} onClick={show}>{label} ▾</button>
      {open && (
        <div className="gl-menu">
          <div className="gl-actions">
            <button onClick={() => push(new Set(all.map((c) => c.id)))}>Select all</button>
            <button onClick={() => { clearTimeout(timer.current); setDraft(new Set()) }}>Deselect all</button>
            <button onClick={finish}>Done</button>
          </div>
          <div className="gl-list">
            {all.map((c) => (
              <label key={c.id} className="gl-opt">
                <input type="checkbox" checked={draft.has(c.id)} onChange={() => toggle(c.id)} />
                {c.name}
              </label>
            ))}
          </div>
          <div className="muted small" style={{ padding: '6px 8px' }}>
            {draft.size === 0 ? 'None ticked – tick the companies you want (none = all companies)' : draft.size === all.length ? 'All companies ticked'
              : `${draft.size} of ${all.length} ticked – page updates as you tick`}
          </div>
        </div>
      )}
    </div>
  )
}

export function downloadCsv(filename, columns, rows) {
  const esc = (v) => {
    const s = v === null || v === undefined ? '' : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const lines = [columns.map((c) => esc(c.label)).join(',')]
  rows.forEach((r) => lines.push(columns.map((c) => esc(r[c.key])).join(',')))
  const blob = new Blob(['﻿' + lines.join('\n')], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob); a.download = filename; a.click()
}

/** Multi-select GL account filter. value = array of account ids */
export function GlSelect({ value, onChange }) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const list = useAsync(() => get('/gl-accounts'), [])
  const all = list.data || []
  const sel = new Set(value || [])
  const shown = all.filter((a) => !q || `${a.code || ''} ${a.name}`.toLowerCase().includes(q.toLowerCase()))
  const toggle = (id) => {
    const n = new Set(sel); n.has(id) ? n.delete(id) : n.add(id); onChange([...n])
  }
  const label = sel.size === 0 ? 'All GL accounts'
    : sel.size === 1 ? (() => { const a = all.find((x) => x.id === [...sel][0]); return a ? `${a.code || ''} ${a.name}` : '1 GL account' })()
    : `${sel.size} GL accounts`
  return (
    <div className="gl-select">
      <button className={sel.size ? 'gl-btn on' : 'gl-btn'} onClick={() => setOpen(!open)} title="Filter by GL account">
        {label} ▾
      </button>
      {open && (
        <div className="gl-menu" onMouseLeave={() => setOpen(false)}>
          <input autoFocus placeholder="Search GL code or name… (e.g. raw, packing, consum)" value={q}
                 onChange={(e) => setQ(e.target.value)} />
          <div className="gl-actions">
            <button onClick={() => onChange(shown.map((a) => a.id).concat([...sel]).filter((v, i, x) => x.indexOf(v) === i))}>
              Select shown</button>
            <button onClick={() => onChange([])}>Clear</button>
          </div>
          {list.loading && <div className="muted small">Loading accounts…</div>}
          {list.error && <div className="msg error">{list.error}</div>}
          <div className="gl-list">
            {shown.map((a) => (
              <label key={a.id} className="gl-opt">
                <input type="checkbox" checked={sel.has(a.id)} onChange={() => toggle(a.id)} />
                <span className="code">{a.code || '—'}</span> {a.name}
                <span className="muted"> · {num(a.lines, 0)} lines</span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

const fmtD = (d) => (d ? new Date(d + 'T00:00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : null)

/** One clear line telling which period / GL the numbers cover */
export function PeriodNote({ from, to, gl, extra, label }) {
  const f = fmtD(from) || 'the first bill'
  const t = fmtD(to) || `today (${fmtD(new Date().toISOString().slice(0, 10))})`
  return (
    <div className="period-note">
      📅 {label || 'Bills'} dated <b>{f}</b> to <b>{t}</b>
      {label ? null : (gl && gl.length ? <> · GL filter: <b>{gl.length} account{gl.length > 1 ? 's' : ''}</b></> : <> · all GL accounts</>)}
      {extra}
    </div>
  )
}

/** Local / Import filter */
export function SourceSelect({ value, onChange }) {
  return (
    <select value={value || ''} onChange={(e) => onChange(e.target.value || '')}
            title="Import = foreign-currency bill, overseas vendor, or GST treatment 'overseas'">
      <option value="">Local + Import</option>
      <option value="Local">Local only</option>
      <option value="Import">Import only</option>
    </select>
  )
}

/** External / Inter-branch / Intercompany filter */
export function TxnSelect({ value, onChange }) {
  return (
    <select value={value || ''} onChange={(e) => onChange(e.target.value || '')}
            title="Inter-branch = same PAN (own GSTIN) or 'Branch Transfer' order; Intercompany = group company or 'Intercompany' order">
      <option value="">External + inter-branch + intercompany</option>
      <option value="External">External purchases only</option>
      <option value="Inter-branch">Inter-branch only</option>
      <option value="Intercompany">Intercompany only</option>
    </select>
  )
}

const OT_PRESETS = [
  ['Material only', /regular|import|schedule|confirmatory|trial|excess/i],
  ['Job work', /job/i],
  ['Services', /service|amc|testing|calibration/i],
  ['Inter-branch', /branch|intercompany/i],
]

/** Purchase order type multi-select with presets. value = array of ids */
export function OrderTypeSelect({ value, onChange }) {
  const [open, setOpen] = useState(false)
  const list = useAsync(() => get('/order-types'), [])
  const all = list.data || []
  if (!list.loading && all.length === 0) return null
  const sel = new Set(value || [])
  const toggle = (id) => { const n = new Set(sel); n.has(id) ? n.delete(id) : n.add(id); onChange([...n]) }
  const label = sel.size === 0 ? 'All order types'
    : sel.size === 1 ? (all.find((x) => x.id === [...sel][0])?.name || '1 order type') : `${sel.size} order types`
  return (
    <div className="gl-select">
      <button className={sel.size ? 'gl-btn on' : 'gl-btn'} onClick={() => setOpen(!open)}>{label} ▾</button>
      {open && (
        <div className="gl-menu" onMouseLeave={() => setOpen(false)}>
          <div className="gl-actions" style={{ flexWrap: 'wrap' }}>
            {OT_PRESETS.map(([n, rx]) => (
              <button key={n} onClick={() => onChange(all.filter((a) => rx.test(a.name)).map((a) => a.id))}>{n}</button>
            ))}
            <button onClick={() => onChange([])}>Clear</button>
          </div>
          <div className="gl-list">
            {all.map((a) => (
              <label key={a.id} className="gl-opt">
                <input type="checkbox" checked={sel.has(a.id)} onChange={() => toggle(a.id)} />
                {a.name}{a.is_subcontracting && <span className="muted"> · job work</span>}
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/** Points we know exist in the data but are NOT computed because the link is not certain */
export function NotIncluded({ items }) {
  return (
    <div className="card notincl">
      <h3>Not included – needs confirmation</h3>
      <div className="muted small">These exist in Odoo, but there is no exact link to calculate them reliably, so they are left out
        rather than guessed. Confirm the rule and they can be added.</div>
      <ul>{items.map((t, i) => <li key={i}>{t}</li>)}</ul>
    </div>
  )
}

// "More detail": secondary tables stay folded until asked for, and only then load their data.
export function More({ title, children, open: startOpen = false }) {
  const [open, setOpen] = useState(startOpen)
  return (
    <div className={open ? 'more open' : 'more'}>
      <button className="more-head" onClick={() => setOpen(!open)}>
        <span className="more-caret">{open ? '▾' : '▸'}</span> {title}
      </button>
      {open && <div className="more-body">{children}</div>}
    </div>
  )
}
