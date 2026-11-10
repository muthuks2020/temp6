import React, { useState } from 'react'
import { get, num, compact } from '../api.js'
import {
  NotIncluded, useSteps, Kpi, Status, DataTable, CompanySelect, useCompany, PeriodSelect, periodRange, PeriodNote, downloadCsv, More,
} from '../components/ui.jsx'
import { ChartCard, BarGroup } from '../components/Charts.jsx'

const d10 = (v) => (v ? String(v).slice(0, 10) : '—')
const age = (v) => (v === null || v === undefined ? '—' : <span className={v > 60 ? 'up' : ''}>{num(v, 0)}</span>)

const VCOLS = [
  { key: 'vendor', label: 'Job worker' },
  { key: 'pos', label: 'POs', type: 'num' },
  { key: 'ordered_value', label: 'Ordered', type: 'money' },
  { key: 'received_value', label: 'Received back', type: 'money' },
  { key: 'completion_pct', label: 'Done %', type: 'pct', render: (v) => (v == null ? '—' : `${num(v, 1)}%`) },
  { key: 'billed_value', label: 'Billed', type: 'money' },
  { key: 'pending_value', label: 'Still with vendor', type: 'money' },
  { key: 'open_pos', label: 'Open POs', type: 'num' },
  { key: 'oldest_open_days', label: 'Oldest open (days)', type: 'num', render: age },
  { key: 'received_not_billed', label: 'Received, not billed', type: 'money' },
]
const OCOLS = [
  { key: 'po_number', label: 'PO' },
  { key: 'po_date', label: 'PO date', render: d10 },
  { key: 'age_days', label: 'Days open', type: 'num', render: age },
  { key: 'vendor', label: 'Job worker' },
  { key: 'item_code', label: 'Item code' },
  { key: 'product', label: 'Item / operation' },
  { key: 'ordered', label: 'Ordered', type: 'num' },
  { key: 'received', label: 'Received', type: 'num' },
  { key: 'pending_qty', label: 'Pending', type: 'num' },
  { key: 'unit_inr', label: 'Rate', type: 'money' },
  { key: 'pending_value', label: 'Pending value', type: 'money' },
  { key: 'company', label: 'Company' },
]
const PCOLS = [
  { key: 'item_code', label: 'Item code' },
  { key: 'product', label: 'Item / operation' },
  { key: 'vendor', label: 'Job worker' },
  { key: 'pos', label: 'POs', type: 'num' },
  { key: 'qty', label: 'Qty', type: 'num' },
  { key: 'avg_rate', label: 'Avg rate', type: 'money' },
  { key: 'min_rate', label: 'Min', type: 'money' },
  { key: 'max_rate', label: 'Max', type: 'money' },
  { key: 'last_po', label: 'Last PO', render: d10 },
]
const DCOLS = [
  { key: 'dc_no', label: 'DC / transfer' },
  { key: 'dc_date', label: 'Sent on', render: d10 },
  { key: 'kind', label: 'Type' },
  { key: 'vendor', label: 'Vendor' },
  { key: 'reference', label: 'Reference' },
  { key: 'po_numbers', label: 'PO' },
  { key: 'qty', label: 'Qty', type: 'num' },
  { key: 'value', label: 'Value', type: 'money' },
  { key: 'days_since_sent', label: 'Days since sent', type: 'num' },
  { key: 'company', label: 'Company' },
]

function Section({ r, title, note, cols, file, empty }) {
  return (
    <div className="card">
      <div className="card-head">
        <h3>{title} {note && <span className="muted">{note}</span>}</h3>
        <button onClick={() => downloadCsv(file, cols, r.data || [])}>Download CSV</button>
      </div>
      <Status loading={r.loading} error={r.error} />
      {!r.loading && <DataTable columns={cols} rows={r.data} empty={empty || 'Nothing in this period'} />}
    </div>
  )
}

export default function JobWork({ companies }) {
  const [company, setCompany] = useCompany()   // shared by every page
  const [period, setPeriod] = useState('fy')
  const [custom, setCustom] = useState({})
  const pr = periodRange(period, custom)
  const range = { company_id: company, date_from: pr.from, date_to: pr.to }
  const step = useSteps([
    ['summary', () => get('/jobwork/summary', range)],
    ['open', () => get('/jobwork/open', range)],
    ['vendors', () => get('/jobwork/vendors', range)],
    ['dcs', () => get('/jobwork/dcs', range)],
    ['products', () => get('/jobwork/products', range)],
  ], [company, pr.from, pr.to])
  const s = step('summary'), sd = s.data

  return (
    <section>
      <div className="filters">
        <CompanySelect companies={companies} value={company} onChange={setCompany} />
        <PeriodSelect period={period} setPeriod={setPeriod} custom={custom} setCustom={setCustom} />
      </div>
      <PeriodNote from={pr.from} to={pr.to} label="Job work POs / DCs" />
      <div className="kpis">
        <Kpi label="Job work POs" value={num(sd?.pos, 0)} loading={s.loading} sub={sd ? `${num(sd.vendors, 0)} job workers` : ''} />
        <Kpi label="Ordered value" value={compact(sd?.ordered_value)} loading={s.loading}
             sub={sd ? `billed ${compact(sd.billed_value)}` : ''} />
        <Kpi label="Still with job workers" value={compact(sd?.pending_value)} loading={s.loading}
             sub={sd ? `${num(sd.open_pos, 0)} open POs · ${compact(sd.pending_over_60)} over 60 days` : ''} />
        <Kpi label="Received, not yet billed" value={compact(sd?.received_not_billed)} loading={s.loading} sub="expect these bills" />
        <Kpi label="DCs to vendors (not returns)" value={num(sd?.dcs, 0)} loading={s.loading}
             sub={sd ? `${compact(sd.dc_value)} · ${num(sd.dc_vendors, 0)} vendors` : ''} />
      </div>
      <ChartCard title="Job workers: ordered vs received back vs billed" loading={step('vendors').loading}
                 error={step('vendors').error} empty={!(step('vendors').data || []).length}
                 height={Math.max(220, Math.min(10, (step('vendors').data || []).length) * 56)}>
        <BarGroup horizontal data={(step('vendors').data || []).slice(0, 10)} x="vendor"
                  series={[{ key: 'ordered_value', label: 'Ordered' }, { key: 'received_value', label: 'Received back', color: '#1baf7a' },
                           { key: 'billed_value', label: 'Billed', color: '#eb6834' }]} />
      </ChartCard>
      <Section r={step('open')} title="Open job work – material still with the job worker" note="(oldest first)"
               cols={OCOLS} file="jobwork_open.csv" empty="Nothing pending" />
      <More title="By job worker, DCs sent to vendors, job-work rates">
      <Section r={step('vendors')} title="By job worker" cols={VCOLS} file="jobwork_by_vendor.csv" />
      <Section r={step('dcs')} title="DCs / material sent to vendors (not returns)"
               note="(transfers to a vendor location that are not returns)" cols={DCOLS} file="dc_to_vendors.csv" />
      <Section r={step('products')} title="Job work rates by item and job worker" note="(compare rates between job workers)"
               cols={PCOLS} file="jobwork_rates.csv" />
      </More>
      <div className="muted small">Job work = POs whose order type is flagged "subcontracting" in Odoo (Job Work).
        Pending = ordered − received on the PO line; billed = invoiced quantity on the PO line.</div>
      <NotIncluded items={[
        'Whether each DC\'s material came back – Odoo has no link from the DC to the receipt that brings it back (only free-text references like "VSOM-24-OUT-0411").',
        'Components issued to job workers through Odoo subcontracting resupply transfers (if used).',
        'Job work charges added to the cost of the finished item in Costing.',
      ]} />
    </section>
  )
}
