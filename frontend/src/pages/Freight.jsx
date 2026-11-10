import React, { useState } from 'react'
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid } from 'recharts'
import { get, num, compact, money, SERIES } from '../api.js'
import {
  useSteps, Kpi, Status, DataTable, CompanySelect, useCompany, PeriodSelect, periodRange, PeriodNote, downloadCsv, More,
} from '../components/ui.jsx'

const pct = (v) => (v === null || v === undefined ? '—' : `${num(v, 2)}%`)
const d10 = (v) => (v ? String(v).slice(0, 10) : '—')

const METHOD_COLS = [
  { key: 'method', label: 'How the freight was linked to a purchase' },
  { key: 'lines', label: 'Freight lines', type: 'num' },
  { key: 'freight', label: 'Freight', type: 'money' },
]
const SUP_COLS = [
  { key: 'supplier', label: 'Material supplier' },
  { key: 'source', label: 'Local / Import' },
  { key: 'currency', label: 'PO ccy' },
  { key: 'categories', label: 'Main category' },
  { key: 'pos', label: 'POs', type: 'num' },
  { key: 'po_value', label: 'PO value', type: 'money' },
  { key: 'freight', label: 'Freight linked', type: 'money' },
  { key: 'freight_pct', label: 'Freight % of PO', type: 'pct',
    render: (v) => <span className={v > 5 ? 'up' : ''}>{pct(v)}</span> },
]
const TR_COLS = [
  { key: 'transporter', label: 'Transporter / vendor' },
  { key: 'bills', label: 'Bills', type: 'num' },
  { key: 'freight', label: 'Freight', type: 'money' },
  { key: 'linked_pct', label: 'Linked to a PO', type: 'pct', render: (v) => pct(v) },
  { key: 'detected_by', label: 'Found by' },
  { key: 'last_bill', label: 'Last bill', render: d10 },
]
const LINK_COLS = [
  { key: 'bill_date', label: 'Date', render: d10 },
  { key: 'bill_number', label: 'Freight bill' },
  { key: 'bill_ref', label: 'Reference' },
  { key: 'transporter', label: 'Transporter' },
  { key: 'amount', label: 'Freight', type: 'money' },
  { key: 'linked_by', label: 'Linked by' },
  { key: 'po_numbers', label: 'PO(s)' },
  { key: 'suppliers', label: 'Material supplier' },
  { key: 'source', label: 'Local / Import' },
  { key: 'po_value', label: 'PO value', type: 'money' },
  { key: 'freight_pct', label: 'Freight %', type: 'pct',
    render: (v) => <span className={v > 10 ? 'up' : ''}>{pct(v)}</span> },
  { key: 'company', label: 'Company' },
]
const OPEN_COLS = [
  { key: 'bill_date', label: 'Date', render: d10 },
  { key: 'bill_number', label: 'Freight bill' },
  { key: 'transporter', label: 'Transporter' },
  { key: 'amount', label: 'Freight', type: 'money' },
  { key: 'bill_ref', label: 'Reference' },
  { key: 'reference_text', label: 'All reference text found' },
  { key: 'gl_account', label: 'GL account' },
  { key: 'company', label: 'Company' },
]

const tip = { contentStyle: { background: 'var(--surface-2)', border: '1px solid var(--line)', borderRadius: 6, fontSize: 12 } }

function Section({ r, title, note, cols, file }) {
  return (
    <div className="card">
      <div className="card-head">
        <h3>{title} {note && <span className="muted">{note}</span>}</h3>
        <button onClick={() => downloadCsv(file, cols, r.data || [])}>Download CSV</button>
      </div>
      <Status loading={r.loading} error={r.error} />
      {!r.loading && <DataTable columns={cols} rows={r.data} empty="Nothing in this period" />}
    </div>
  )
}

export default function Freight({ companies }) {
  const [company, setCompany] = useCompany()   // shared by every page
  const [period, setPeriod] = useState('fy')
  const [custom, setCustom] = useState({})
  const pr = periodRange(period, custom)
  const range = { company_id: company, date_from: pr.from, date_to: pr.to }

  // one after another: summary -> monthly -> methods -> suppliers -> transporters -> linked -> not linked
  const step = useSteps([
    ['summary', () => get('/freight/summary', range)],
    ['monthly', () => get('/freight/monthly', range)],
    ['methods', () => get('/freight/methods', range)],
    ['suppliers', () => get('/freight/suppliers', range)],
    ['transporters', () => get('/freight/transporters', range)],
    ['linked', () => get('/freight/bills', { ...range, linked: true })],
    ['open', () => get('/freight/bills', { ...range, linked: false })],
  ], [company, pr.from, pr.to])
  const s = step('summary')
  const sd = s.data
  const months = (step('monthly').data || []).map((m) => ({ ...m, label: String(m.month).slice(0, 7) }))

  return (
    <section>
      <div className="filters">
        <CompanySelect companies={companies} value={company} onChange={setCompany} />
        <PeriodSelect period={period} setPeriod={setPeriod} custom={custom} setCustom={setCustom} />
      </div>
      <PeriodNote from={pr.from} to={pr.to} />

      <div className="kpis">
        <Kpi label="Freight spend" value={compact(sd?.freight)} loading={s.loading}
             sub={sd ? `${num(sd.freight_bills, 0)} bills · ${num(sd.lines, 0)} lines` : ''} />
        <Kpi label="Freight % of material purchases" loading={s.loading}
             value={sd && sd.material ? pct((100 * sd.freight) / sd.material) : '—'}
             sub={sd ? `materials ${compact(sd.material)}` : ''} />
        <Kpi label="Freight on Local / Import POs" loading={s.loading}
             value={sd ? `${compact(sd.freight_local)} / ${compact(sd.freight_import)}` : '—'}
             sub={sd ? `${num(sd.transporters, 0)} transporters / vendors` : ''} />
        <Kpi label="Linked to a purchase" loading={s.loading}
             value={sd && sd.freight ? pct((100 * sd.linked) / sd.freight) : '—'}
             sub={sd ? `${compact(sd.linked)} of freight` : ''} />
        <Kpi label="Not linked" loading={s.loading} value={compact(sd ? sd.freight - sd.linked : null)}
             sub={sd ? `${num(sd.lines - sd.linked_lines, 0)} lines – fix references` : ''} />
      </div>

      <div className="card">
        <h3>Freight by month <span className="muted">(INR; hover for freight % of material purchases)</span></h3>
        <Status loading={step('monthly').loading} error={step('monthly').error} />
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={months} margin={{ right: 20 }}>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis dataKey="label" tick={{ fill: 'var(--text-2)', fontSize: 11 }} stroke="var(--line)" />
            <YAxis tick={{ fill: 'var(--text-2)', fontSize: 11 }} stroke="var(--line)" width={70}
                   tickFormatter={(v) => compact(v).replace('₹ ', '')} />
            <Tooltip {...tip} cursor={{ fill: 'var(--hover)' }}
                     formatter={(v, n, p) => [`${money(v)}  ·  ${pct(p.payload.freight_pct)} of materials`, 'Freight']} />
            <Bar dataKey="freight" name="Freight" fill={SERIES[0]} radius={[4, 4, 0, 0]} maxBarSize={36} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <Section r={step('open')} title="Freight not linked to any purchase"
               note="(no PO / GRN / supplier bill number found in the reference – ask to quote the PO number)"
               cols={OPEN_COLS} file="freight_not_linked.csv" />

      <More title="How freight was matched, transporters, suppliers, linked bills">
      <div className="grid2">
        <Section r={step('methods')} title="How freight was matched" note="(reliability of the links)"
                 cols={METHOD_COLS} file="freight_match_methods.csv" />
        <Section r={step('transporters')} title="Transporters" cols={TR_COLS} file="freight_transporters.csv" />
      </div>
      <Section r={step('suppliers')} title="Freight by material supplier" note="(freight as % of the PO value it belongs to)"
               cols={SUP_COLS} file="freight_by_supplier.csv" />
      <Section r={step('linked')} title="Freight bills linked to purchases" cols={LINK_COLS} file="freight_linked.csv" />
      </More>

      {sd && (
        <div className="muted small">
          Freight = vendor-bill lines whose GL account or product contains: {sd.keywords.join(', ')}; or whose vendor
          name contains: {sd.transporter_keywords.join(', ')}. Change the words in backend/config/database.ini [freight].
        </div>
      )}
    </section>
  )
}
