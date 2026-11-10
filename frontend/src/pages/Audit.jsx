import React, { useState } from 'react'
import { get, num, compact } from '../api.js'
import {
  useAsync, useSteps, Kpi, Status, DataTable, CompanySelect, useCompany, PeriodSelect, periodRange, PeriodNote, downloadCsv, More,
} from '../components/ui.jsx'
import { ChartCard, BarRank, BarGroup } from '../components/Charts.jsx'

const IG_COLS = [
  { key: 'txn_class', label: 'Transaction' },
  { key: 'bills', label: 'Bills', type: 'num' },
  { key: 'taxable', label: 'Taxable', type: 'money' },
  { key: 'cgst', label: 'CGST', type: 'money' },
  { key: 'sgst', label: 'SGST', type: 'money' },
  { key: 'igst', label: 'IGST', type: 'money' },
  { key: 'gst', label: 'Total GST', type: 'money' },
  { key: 'total', label: 'Invoice total', type: 'money' },
  { key: 'settled', label: 'Settled', type: 'num' },
  { key: 'open', label: 'Open', type: 'num' },
]
const PAIR_COLS = [
  { key: 'txn_class', label: 'Type' },
  { key: 'from_party', label: 'From (branch / group company)' },
  { key: 'from_gstin', label: 'From GSTIN' },
  { key: 'to_company', label: 'To' },
  { key: 'to_gstin', label: 'To GSTIN' },
  { key: 'bills', label: 'Bills', type: 'num' },
  { key: 'taxable', label: 'Taxable', type: 'money' },
  { key: 'gst', label: 'GST', type: 'money' },
  { key: 'total', label: 'Total', type: 'money' },
  { key: 'last_bill', label: 'Last bill', render: (v) => (v ? String(v).slice(0, 10) : '—') },
]

function autoCols(rows) {
  if (!rows || !rows.length) return []
  return Object.keys(rows[0]).map((k) => {
    const v = rows[0][k]
    const money = /value|amount|total|gst|cgst|sgst|igst|tds|purchases|taxable|excess/.test(k)
    return { key: k, label: k.replace(/_/g, ' '), type: typeof v === 'number' ? (money ? 'money' : 'num') : undefined,
             render: /date/.test(k) ? (x) => (x ? String(x).slice(0, 10) : '—') : undefined }
  })
}

function CheckCard({ c, range }) {
  const [open, setOpen] = useState(false)
  const rows = useAsync(() => (open ? get(`/audit/check/${c.check}`, range) : Promise.resolve(null)),
    [open, JSON.stringify(range)])
  const cols = autoCols(rows.data)
  const flag = c.n > 0
  return (
    <div className={flag ? 'card check flagged' : 'card check'}>
      <div className="card-head" onClick={() => setOpen(!open)} style={{ cursor: 'pointer' }}>
        <div>
          <h3>{flag ? '⚠ ' : '✓ '}{c.title}</h3>
          <div className="muted small">{c.why}</div>
        </div>
        <div className="check-num">
          {c.error ? <span className="up" title={c.error}>check failed</span> : (
            <><b>{num(c.n, 0)}</b> <span className="muted">items</span>
              {c.value ? <span className="muted"> · {compact(c.value)}</span> : null}</>
          )}
          <span className="muted"> {open ? '▲' : '▼'}</span>
        </div>
      </div>
      {open && (
        <>
          <Status loading={rows.loading} error={rows.error} />
          {!rows.loading && rows.data && (
            <>
              <div className="card-head right">
                <button onClick={() => downloadCsv(`audit_${c.check}.csv`, cols, rows.data)}>Download CSV</button>
              </div>
              <DataTable columns={cols} rows={rows.data} empty="No exceptions – all good" />
            </>
          )}
        </>
      )}
    </div>
  )
}

export default function Audit({ companies }) {
  const [company, setCompany] = useCompany()   // shared by every page
  const [period, setPeriod] = useState('fy')
  const [custom, setCustom] = useState({})
  const pr = periodRange(period, custom)
  const range = { company_id: company, date_from: pr.from, date_to: pr.to }
  const step = useSteps([
    ['ig', () => get('/audit/intergroup-summary', range)],
    ['checks', () => get('/audit/summary', range)],
    ['pairs', () => get('/audit/intergroup-pairs', range)],
  ], [company, pr.from, pr.to])
  const ig = step('ig'), checks = step('checks'), pairs = step('pairs')
  const by = Object.fromEntries((ig.data || []).map((r) => [r.txn_class, r]))
  const flagged = (checks.data || []).filter((c) => c.n > 0).length

  return (
    <section>
      <div className="filters">
        <CompanySelect companies={companies} value={company} onChange={setCompany} />
        <PeriodSelect period={period} setPeriod={setPeriod} custom={custom} setCustom={setCustom} />
      </div>
      <PeriodNote from={pr.from} to={pr.to} />

      <div className="kpis">
        <Kpi label="External purchases" value={compact(by.External?.taxable)} loading={ig.loading}
             sub={by.External ? `GST ${compact(by.External.gst)}` : ''} />
        <Kpi label="Inter-branch (no external payment)" value={compact(by['Inter-branch']?.taxable)} loading={ig.loading}
             sub={by['Inter-branch'] ? `GST ${compact(by['Inter-branch'].gst)} · ${num(by['Inter-branch'].bills, 0)} bills` : 'none'} />
        <Kpi label="Intercompany" value={compact(by.Intercompany?.taxable)} loading={ig.loading}
             sub={by.Intercompany ? `GST ${compact(by.Intercompany.gst)} · ${num(by.Intercompany.bills, 0)} bills` : 'none'} />
        <Kpi label="Checks with exceptions" value={checks.loading ? '' : `${flagged} of ${(checks.data || []).length}`}
             loading={checks.loading} />
      </div>

      <div className="charts two">
        <ChartCard title="Purchases by type" note="(taxable value)" loading={ig.loading} error={ig.error}
                   empty={!(ig.data || []).length} height={200}>
          <BarRank data={ig.data} nameKey="txn_class" valueKey="taxable" name="Taxable value" />
        </ChartCard>
        <ChartCard title="Exceptions found per check" note="(number of items)" loading={checks.loading} error={checks.error}
                   empty={!(checks.data || []).length} height={Math.max(200, (checks.data || []).length * 30)}>
          <BarRank data={(checks.data || []).map((c) => ({ name: c.title, value: c.n || 0 }))} name="Items"
                   color="#d97706" format={(v) => String(v)} axisFormat={(v) => String(v)} />
        </ChartCard>
      </div>
      <h2>Audit &amp; tax checks <span className="muted small">(click a check to see the exceptions)</span></h2>
      <Status loading={checks.loading} error={checks.error} />
      {(checks.data || []).map((c) => <CheckCard key={c.check} c={c} range={range} />)}

      <More title="External vs inter-branch vs intercompany, and flows by GSTIN">
      <div className="card">
        <h3>External vs inter-branch vs intercompany</h3>
        <Status loading={ig.loading} error={ig.error} />
        {!ig.loading && <DataTable columns={IG_COLS} rows={ig.data} />}
      </div>

      <div className="card">
        <div className="card-head">
          <h3>Inter-branch / intercompany flows <span className="muted">(match with the other GSTIN's outward invoices)</span></h3>
          <button onClick={() => downloadCsv('intergroup_flows.csv', PAIR_COLS, pairs.data || [])}>Download CSV</button>
        </div>
        <Status loading={pairs.loading} error={pairs.error} />
        {!pairs.loading && <DataTable columns={PAIR_COLS} rows={pairs.data} empty="No inter-branch or intercompany bills" />}
      </div>
      </More>
    </section>
  )
}
