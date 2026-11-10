import React, { useState } from 'react'
import { get, num, compact } from '../api.js'
import {
  NotIncluded, useSteps, Kpi, Status, DataTable, CompanySelect, useCompany, PeriodSelect, periodRange, PeriodNote, downloadCsv, More,
} from '../components/ui.jsx'
import { ChartCard, BarGroup, BarRank } from '../components/Charts.jsx'

const d10 = (v) => (v ? String(v).slice(0, 10) : '—')

const BCOLS = [
  { key: 'item_code', label: 'Item code' },
  { key: 'product', label: 'Product' },
  { key: 'sent_qty', label: 'Sent into transit', type: 'num' },
  { key: 'received_qty', label: 'Received out of transit', type: 'num' },
  { key: 'in_transit_qty', label: 'In transit now', type: 'num',
    render: (v) => <b className={v < 0 ? 'up' : ''}>{num(v, 2)}</b> },
  { key: 'unit_cost', label: 'Latest PO price', type: 'money' },
  { key: 'value', label: 'Value', type: 'money' },
  { key: 'last_sent', label: 'Last sent', render: d10 },
  { key: 'last_received', label: 'Last received', render: d10 },
]
const CCOLS = [
  { key: 'company', label: 'Company' },
  { key: 'sent', label: 'Transfers sent', type: 'num' },
  { key: 'sent_qty', label: 'Qty sent', type: 'num' },
  { key: 'sent_value', label: 'Value sent', type: 'money' },
  { key: 'received', label: 'Transfers received', type: 'num' },
  { key: 'received_qty', label: 'Qty received', type: 'num' },
  { key: 'received_value', label: 'Value received', type: 'money' },
]
const TCOLS = [
  { key: 'picking_type', label: 'Transfer type' },
  { key: 'direction', label: 'Direction' },
  { key: 'transfers', label: 'Transfers', type: 'num' },
  { key: 'qty', label: 'Qty', type: 'num' },
  { key: 'value', label: 'Value', type: 'money' },
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

export default function Transit({ companies }) {
  const [company, setCompany] = useCompany()   // shared by every page
  const [period, setPeriod] = useState('fy')
  const [custom, setCustom] = useState({})
  const pr = periodRange(period, custom)
  const range = { company_id: company, date_from: pr.from, date_to: pr.to }
  const step = useSteps([
    ['summary', () => get('/transit/summary', range)],
    ['balance', () => get('/transit/balance', range)],
    ['companies', () => get('/transit/companies', range)],
    ['types', () => get('/transit/types', range)],
  ], [company, pr.from, pr.to])
  const s = step('summary'), sd = s.data

  return (
    <section>
      <div className="filters">
        <CompanySelect companies={companies} value={company} onChange={setCompany} />
        <PeriodSelect period={period} setPeriod={setPeriod} custom={custom} setCustom={setCustom} />
      </div>
      <PeriodNote from={pr.from} to={pr.to} label="Transfers" />
      <div className="kpis">
        <Kpi label="Transfers sent" value={num(sd?.sent, 0)} loading={s.loading} sub={sd ? compact(sd.sent_value) : ''} />
        <Kpi label="Transfers received" value={num(sd?.received, 0)} loading={s.loading} sub={sd ? compact(sd.received_value) : ''} />
        <Kpi label="In transit now (all companies)" value={compact(sd?.in_transit_value)} loading={s.loading}
             sub={sd ? `${num(sd.products_in_transit, 0)} items` : ''} />
        <Kpi label="Items received more than sent" value={num(sd?.negative_products, 0)} loading={s.loading}
             sub="data to correct" />
      </div>
      <div className="charts two">
        <ChartCard title="Sent vs received by company" note="(value)" loading={step('companies').loading}
                   error={step('companies').error} empty={!(step('companies').data || []).length} height={220}>
          <BarGroup horizontal data={step('companies').data} x="company"
                    series={[{ key: 'sent_value', label: 'Sent' }, { key: 'received_value', label: 'Received', color: '#1baf7a' }]} />
        </ChartCard>
        <ChartCard title="Biggest items in transit now" note="(value)" loading={step('balance').loading}
                   error={step('balance').error} empty={!(step('balance').data || []).filter((r) => r.value > 0).length} height={220}>
          <BarRank data={(step('balance').data || []).filter((r) => r.value > 0).slice(0, 10)} nameKey="product" valueKey="value"
                   name="Value in transit" color="#d97706" />
        </ChartCard>
      </div>
      <Section r={step('balance')} title="Stock sitting in Inter Company Transit" note="(sent in − received out, all companies, up to the end date)"
               cols={BCOLS} file="transit_balance.csv" empty="Nothing in transit" />
      <More title="Sent / received by company and by transfer type">
      <div className="grid2">
        <Section r={step('companies')} title="Sent / received by company" cols={CCOLS} file="transit_by_company.csv" />
        <Section r={step('types')} title="By transfer type" cols={TCOLS} file="transit_by_type.csv" />
      </div>
      </More>
      <div className="muted small">Quantities are exact Odoo stock moves into and out of the transit location.
        Value = quantity × latest purchase price of the item (indicative, not Odoo's stock valuation).</div>
      <NotIncluded items={[
        'Which receipt closed which sent transfer, and days in transit per transfer – Odoo has no direct link between the two pickings.',
        'In-transit stock split by sending / receiving company (transit is one shared location).',
        'Matching each transfer with its inter-branch GST invoice / bill.',
        'Valuation at Odoo stock value instead of latest purchase price.',
      ]} />
    </section>
  )
}
