import React, { useState } from 'react'
import { get, compact } from '../api.js'
import { useSteps, Status, DataTable, CompanySelect, useCompany } from '../components/ui.jsx'

const GO = { impact: 'Price Impact', vendors: 'Vendors', p2p: 'Order to Pay', control: 'Control', cost: 'Price & Cost' }

const ACT = [
  { key: 'area', label: 'Area' },
  { key: 'ref', label: 'Reference' },
  { key: 'name', label: 'Product / vendor' },
  { key: 'amount', label: 'Amount', type: 'money', render: (v) => <b>{compact(v)}</b> },
  { key: 'what', label: 'What to do' },
]

// Tile groups load one after another (one database connection at a time); each shows as soon as it is ready.
const ORDER = ['p2p', 'vendors', 'returns', 'audit', 'outside', 'impact']
const SLOTS = { p2p: 3, vendors: 1, returns: 1, audit: 1, outside: 1, impact: 1 }
const SHOW = ['impact', 'vendors', 'p2p', 'returns', 'audit', 'outside']

export default function Home({ companies, go }) {
  const [company, setCompany] = useCompany()   // shared by every page
  const step = useSteps([
    ...ORDER.map((k) => [k, () => get(`/home/tile/${k}`, { company_id: company })]),
    ['actions', () => get('/home/actions', { company_id: company })],
  ], [company])
  const imp = step('impact').data
  const a = step('actions')
  return (
    <section>
      <div className="filters">
        <CompanySelect companies={companies} value={company} onChange={setCompany} />
        {imp?.periods && <span className="muted small">
          Open items: this fiscal year · price impact: {imp.periods.b_from} – {imp.periods.b_to} vs {imp.periods.c_from} – {imp.periods.c_to}
        </span>}
      </div>
      <div className="home-tiles">
        {SHOW.flatMap((k) => {
          const st = step(k)
          if (!st.data) {
            return Array.from({ length: SLOTS[k] }, (_, i) => (
              <div key={`${k}${i}`} className="home-tile">
                <div className="t-label">{st.error ? 'Could not load' : 'Loading…'}</div>
                <div className="t-value">{st.error ? '⚠' : <span className="dots">·</span>}</div>
                <div className="t-sub">{st.error || 'waiting for its turn'}</div>
              </div>))
          }
          return st.data.tiles.map((t) => (
            <button key={t.key} className={`home-tile ${t.tone || ''}`} onClick={() => go(t.page)}>
              <div className="t-label">{t.label}</div>
              <div className="t-value">{compact(t.value)}</div>
              <div className="t-sub">{t.sub}</div>
              <div className="t-go">Open {GO[t.page] || t.page} →</div>
            </button>))
        })}
      </div>
      {imp?.takeaways?.length > 0 && (
        <div className="card takeaways">
          <h3>This year so far</h3>
          <ul>{imp.takeaways.map((t, i) => <li key={i}>{t}</li>)}</ul>
        </div>
      )}
      <div className="card">
        <div className="card-head"><h3>Act on these first <span className="muted small">– click a row to open its page</span></h3></div>
        <Status loading={a.loading} error={a.error} />
        {!a.loading && <DataTable columns={ACT} rows={a.data} onRowClick={(r) => go(r.page)} empty="Nothing to act on" />}
      </div>
      <div className="muted small">
        Every number comes from the silver copy of Odoo (refreshed daily, T-1) and is worked out by the same rules as its page.
        "Received, not yet billed" is the GRNI accrual to book at month end; "GST / ITC at risk" adds up missing or invalid
        GSTIN, wrong tax type and late-booked bills.
      </div>
    </section>
  )
}
