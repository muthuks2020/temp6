import React, { useState } from 'react'
import { get, num, compact } from '../api.js'
import {
  NotIncluded, useAsync, useSteps, Kpi, Status, DataTable, CompanySelect, useCompany, PeriodSelect, periodRange, PeriodNote, downloadCsv, More,
} from '../components/ui.jsx'
import { ChartCard, BarGroup, BarRank, fmtPeriod } from '../components/Charts.jsx'

const d10 = (v) => (v ? String(v).slice(0, 10) : '—')
const pct = (v) => (v === null || v === undefined ? '—' : `${num(v, 2)}%`)

const VCOLS = [
  { key: 'vendor', label: 'Vendor' },
  { key: 'returns', label: 'Returns', type: 'num' },
  { key: 'value', label: 'Returned value', type: 'money' },
  { key: 'billed', label: 'Bought in period', type: 'money' },
  { key: 'return_rate_pct', label: 'Return rate', type: 'pct', render: (v) => <span className={v > 2 ? 'up' : ''}>{pct(v)}</span> },
  { key: 'credited', label: 'Debit notes', type: 'money' },
  { key: 'pending_returns', label: 'Returns w/o debit note', type: 'num' },
  { key: 'pending_value', label: 'Value w/o debit note', type: 'money', render: (v) => (v ? <b className="up">₹ {num(v, 0)}</b> : '—') },
  { key: 'reasons', label: 'Reasons' },
]
const RCOLS = [
  { key: 'reason', label: 'Reason' },
  { key: 'returns', label: 'Returns', type: 'num' },
  { key: 'value', label: 'Value', type: 'money' },
  { key: 'vendors', label: 'Vendors', type: 'num' },
  { key: 'without_debit_note', label: 'W/o debit note', type: 'num' },
]
const PCOLS = [
  { key: 'item_code', label: 'Item code' },
  { key: 'product', label: 'Product' },
  { key: 'returns', label: 'Returns', type: 'num' },
  { key: 'qty', label: 'Qty returned', type: 'num' },
  { key: 'value', label: 'Value', type: 'money' },
  { key: 'vendors', label: 'Vendors', type: 'num' },
  { key: 'reasons', label: 'Reasons' },
]
const LCOLS = [
  { key: 'return_no', label: 'Return' },
  { key: 'return_date', label: 'Date', render: d10 },
  { key: 'kind', label: 'How' },
  { key: 'form_no', label: 'Return form' },
  { key: 'age_days', label: 'Age (days)', type: 'num' },
  { key: 'vendor', label: 'Vendor' },
  { key: 'reason', label: 'Reason' },
  { key: 'po_numbers', label: 'PO' },
  { key: 'qty', label: 'Qty', type: 'num' },
  { key: 'value', label: 'Value', type: 'money' },
  { key: 'credit_status', label: 'Debit note status', render: (v) => <span className={/No debit|Partly/.test(v || '') ? 'up' : ''}>{v}</span> },
  { key: 'debit_notes', label: 'Debit notes on PO line' },
  { key: 'credited', label: 'Credited', type: 'money' },
  { key: 'to_recover', label: 'To recover', type: 'money' },
  { key: 'origin', label: 'Source doc' },
  { key: 'company', label: 'Company' },
]

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

export default function Returns({ companies }) {
  const [company, setCompany] = useCompany()   // shared by every page
  const [period, setPeriod] = useState('fy')
  const [custom, setCustom] = useState({})
  const [kind, setKind] = useState('returns')
  const pr = periodRange(period, custom)
  const range = { company_id: company, date_from: pr.from, date_to: pr.to, kind }
  const types = useAsync(() => get('/returns/types', { company_id: company, date_from: pr.from, date_to: pr.to }), [company, pr.from, pr.to])

  const step = useSteps([
    ['summary', () => get('/returns/summary', range)],
    ['monthly', () => get('/returns/monthly', range)],
    ['pending', () => get('/returns/list', { ...range, pending_only: true })],
    ['vendors', () => get('/returns/by-vendor', range)],
    ['reasons', () => get('/returns/by-reason', range)],
    ['products', () => get('/returns/by-product', range)],
    ['all', () => get('/returns/list', range)],
  ], [company, pr.from, pr.to, kind])
  const s = step('summary'), sd = s.data

  return (
    <section>
      <div className="filters">
        <CompanySelect companies={companies} value={company} onChange={setCompany} />
        <PeriodSelect period={period} setPeriod={setPeriod} custom={custom} setCustom={setCustom} />
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="returns">All returns (Odoo return + bulk form)</option>
          {(types.data || []).filter((t) => /return/i.test(t.value)).map((t) =>
            <option key={t.value} value={t.value}>{t.value} ({num(t.returns, 0)})</option>)}
        </select>
      </div>
      <PeriodNote from={pr.from} to={pr.to} label="Returned" extra={<span className="muted"> · every done transfer back to a vendor location (DCs are on the Job Work tab)</span>} />

      <div className="kpis">
        <Kpi label="Returns" value={num(sd?.returns, 0)} loading={s.loading} sub={sd ? `${num(sd.vendors, 0)} vendors` : ''} />
        <Kpi label="Returned value" value={compact(sd?.value)} loading={s.loading} sub="at PO price, INR" />
        <Kpi label="Covered by debit notes" value={compact(sd?.credited)} loading={s.loading} />
        <Kpi label="Returns not fully credited" loading={s.loading} value={num(sd?.without_debit_note, 0)}
             sub={sd ? `${compact(sd.value_without_debit_note)} to recover` : ''} />
        <Kpi label="Cannot check (no PO line)" value={num(sd?.not_checkable, 0)} loading={s.loading} />
      </div>

      <div className="charts two">
        <ChartCard title="Returned vs credited by month" loading={step('monthly').loading} error={step('monthly').error}
                   empty={!(step('monthly').data || []).length}>
          <BarGroup data={step('monthly').data} x="month" xFormat={fmtPeriod('month')}
                    series={[{ key: 'value', label: 'Returned value' }, { key: 'credited', label: 'Covered by debit notes', color: '#1baf7a' }]} />
        </ChartCard>
        <ChartCard title="Top vendors by returned value" loading={step('vendors').loading} error={step('vendors').error}
                   empty={!(step('vendors').data || []).length}>
          <BarRank data={(step('vendors').data || []).slice(0, 10)} nameKey="vendor" valueKey="value" name="Returned value" color="#eb6834" />
        </ChartCard>
      </div>
      <Section r={step('pending')} title="Returned but not fully credited" note="(money owed by vendors – follow up)"
               cols={LCOLS} file="returns_without_debit_note.csv" />
      <More title="Returns by vendor, reason and product – and the full list">
      <Section r={step('vendors')} title="Returns by vendor" note="(return rate = returned value ÷ bought from them in the period)"
               cols={VCOLS} file="returns_by_vendor.csv" />
      <div className="grid2">
        <Section r={step('reasons')} title="By reason" cols={RCOLS} file="returns_by_reason.csv" />
        <Section r={step('products')} title="Most returned products" cols={PCOLS} file="returns_by_product.csv" />
      </div>
      <Section r={step('all')} title="All returns" cols={LCOLS} file="returns_all.csv" />
      </More>
      <div className="muted small">Debit note status = quantity on posted vendor refunds of the same PO line ÷ quantity returned on
        that PO line (all time). Bulk-form returns also use the form's own debit-note link. Value at PO price excl. GST.</div>
      <NotIncluded items={[
        'Return reason for Odoo "Return" button returns – Odoo does not store it (only the bulk return form has a reason).',
        'Separating price-difference debit notes from return debit notes on the same PO line – both are counted as credit today.',
        'Debit notes raised without a PO line, matched by vendor + date or by reference text.',
        'Days taken to receive each debit note (needs a direct return → debit note link).',
      ]} />
    </section>
  )
}
