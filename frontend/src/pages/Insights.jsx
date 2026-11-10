import React, { useState } from 'react'
import { get, num, compact, money } from '../api.js'
import {
  NotIncluded, More, useSteps, Kpi, Status, DataTable, CompanySelect, useCompany, PeriodSelect, periodRange, PeriodNote, TxnSelect, downloadCsv,
} from '../components/ui.jsx'
import { ChartCard, BarRank } from '../components/Charts.jsx'

const d10 = (v) => (v ? String(v).slice(0, 10) : '—')
const pctBadge = (warn) => (v) => (v == null ? '—'
  : <span className={`pill ${v >= warn * 2 ? 'pill-bad' : v >= warn ? 'pill-warn' : ''}`}>{num(v, 1)}%</span>)

const GAP = [
  { key: 'item_code', label: 'Item code' },
  { key: 'product', label: 'Product' },
  { key: 'uom', label: 'UoM' },
  { key: 'vendors', label: 'Vendors', type: 'num' },
  { key: 'best_price', label: 'Best vendor avg', type: 'money' },
  { key: 'worst_price', label: 'Highest vendor avg', type: 'money' },
  { key: 'spread_pct', label: 'Gap', type: 'pct', render: pctBadge(10) },
  { key: 'extra_paid', label: 'Paid above best price', type: 'money', render: (v) => <b className="up">{money(v)}</b> },
  { key: 'cheapest_vendor', label: 'Cheapest vendor' },
  { key: 'dearest_vendor', label: 'Costliest vendor' },
  { key: 'vendor_prices', label: 'All vendors (avg price × qty)' },
  { key: 'category', label: 'Category' },
]
const SWING = [
  { key: 'item_code', label: 'Item code' },
  { key: 'product', label: 'Product' },
  { key: 'uom', label: 'UoM' },
  { key: 'vendor', label: 'Vendor' },
  { key: 'bills', label: 'Bills', type: 'num' },
  { key: 'min_price', label: 'Lowest price', type: 'money' },
  { key: 'max_price', label: 'Highest price', type: 'money' },
  { key: 'avg_price', label: 'Average', type: 'money' },
  { key: 'swing_pct', label: 'Swing', type: 'pct', render: pctBadge(15) },
  { key: 'last_bill', label: 'Last bill', render: d10 },
]
const SINGLE = [
  { key: 'item_code', label: 'Item code' },
  { key: 'product', label: 'Product' },
  { key: 'uom', label: 'UoM' },
  { key: 'vendor', label: 'Only vendor' },
  { key: 'bills', label: 'Bills', type: 'num' },
  { key: 'qty', label: 'Qty', type: 'num' },
  { key: 'spend', label: 'Spend', type: 'money' },
  { key: 'avg_price', label: 'Avg price', type: 'money' },
  { key: 'last_bill', label: 'Last bill', render: d10 },
]
const SPLIT = [
  { key: 'week_of', label: 'Week of', render: d10 },
  { key: 'vendor', label: 'Vendor' },
  { key: 'item_code', label: 'Item code' },
  { key: 'product', label: 'Product' },
  { key: 'bills', label: 'Separate bills', type: 'num', render: (v) => <span className="pill pill-warn">{v}</span> },
  { key: 'bill_numbers', label: 'Bills' },
  { key: 'po_numbers', label: 'POs' },
  { key: 'qty', label: 'Qty', type: 'num' },
  { key: 'value', label: 'Value', type: 'money' },
]
const NEWV = [
  { key: 'vendor', label: 'New vendor' },
  { key: 'vendor_gstin', label: 'GSTIN' },
  { key: 'first_bill', label: 'First ever bill', render: d10 },
  { key: 'bills', label: 'Bills', type: 'num' },
  { key: 'products', label: 'Items', type: 'num' },
  { key: 'value', label: 'Value', type: 'money' },
  { key: 'bills_without_po', label: 'Bills without PO', type: 'num',
    render: (v) => (v ? <span className="pill pill-bad">{v}</span> : '0') },
]
const LEAD = [
  { key: 'vendor', label: 'Vendor' },
  { key: 'pos', label: 'POs', type: 'num' },
  { key: 'receipts', label: 'Receipts', type: 'num' },
  { key: 'avg_days', label: 'Avg lead (days)', type: 'num', render: (v) => <b>{num(v, 1)}</b> },
  { key: 'median_days', label: 'Typical (median)', type: 'num' },
  { key: 'p90_days', label: 'Slowest 10%', type: 'num' },
  { key: 'fastest', label: 'Fastest', type: 'num' },
  { key: 'slowest', label: 'Slowest', type: 'num' },
  { key: 'on_time_pct', label: 'On time vs promised', type: 'pct',
    render: (v) => (v == null ? '—' : <span className={`pill ${v < 70 ? 'pill-bad' : v < 90 ? 'pill-warn' : 'pill-good'}`}>{num(v, 0)}%</span>) },
  { key: 'avg_days_late', label: 'Avg days late', type: 'num' },
  { key: 'last_receipt', label: 'Last receipt', render: d10 },
]
const RHYTHM = [
  { key: 'vendor', label: 'Vendor' },
  { key: 'item_code', label: 'Item code' },
  { key: 'product', label: 'Product' },
  { key: 'receipts', label: 'Deliveries', type: 'num' },
  { key: 'avg_gap_days', label: 'Delivers every ~ (days)', type: 'num', render: (v) => <b>{num(v, 0)}</b> },
  { key: 'min_gap', label: 'Shortest gap', type: 'num' },
  { key: 'max_gap', label: 'Longest gap', type: 'num' },
  { key: 'avg_lead_days', label: 'Avg lead (days)', type: 'num' },
  { key: 'last_receipt', label: 'Last delivery', render: d10 },
  { key: 'next_expected', label: 'Next expected', render: d10 },
  { key: 'overdue_days', label: 'Overdue by', type: 'num',
    render: (v) => (v > 0 ? <span className="pill pill-bad">{v} days</span> : '—') },
]

const TAG = { 'Cheapest & fastest': 'pill-good', Cheapest: 'pill-good', Fastest: 'pill-warn' }
const IV = [
  { key: 'item_code', label: 'Item code' },
  { key: 'product', label: 'Product' },
  { key: 'uom', label: 'UoM' },
  { key: 'vendor', label: 'Vendor' },
  { key: 'tag', label: 'Best on', render: (v) => (v ? <span className={`pill ${TAG[v] || ''}`}>{v}</span> : '') },
  { key: 'avg_price', label: 'Avg price (wtd)', type: 'money', render: (v) => <b>{money(v)}</b> },
  { key: 'vs_best_pct', label: 'Price vs cheapest', type: 'num', render: (v) => (v > 0 ? pctBadge(10)(v) : '—') },
  { key: 'avg_lead', label: 'Avg lead (days)', type: 'num', render: (v) => (v == null ? <span className="muted">no receipt</span> : <b>{num(v, 1)}</b>) },
  { key: 'vs_fastest_days', label: 'Days slower than fastest', type: 'num',
    render: (v) => (v > 0 ? <span className={`pill ${v >= 7 ? 'pill-bad' : 'pill-warn'}`}>+{num(v, 1)}</span> : '—') },
  { key: 'last_price', label: 'Last price', type: 'money' },
  { key: 'last_price_date', label: 'Last price on', render: d10 },
  { key: 'price_trend_pct', label: 'First → last price', type: 'num',
    render: (v) => (v == null ? '—' : <span className={v > 0 ? 'up' : ''}>{v > 0 ? '+' : ''}{num(v, 1)}%</span>) },
  { key: 'qty_share_pct', label: 'Share of qty', type: 'num', render: (v) => (v == null ? '—' : `${num(v, 1)}%`) },
  { key: 'bills', label: 'Bills', type: 'num' },
  { key: 'qty', label: 'Qty bought', type: 'num' },
  { key: 'min_price', label: 'Lowest', type: 'money' },
  { key: 'max_price', label: 'Highest', type: 'money' },
  { key: 'best_price', label: 'Cheapest vendor avg', type: 'money' },
  { key: 'extra_paid', label: 'Paid extra', type: 'money', render: (v) => (v > 0.5 ? <b className="up">{money(v)}</b> : '—') },
  { key: 'receipts', label: 'Receipts', type: 'num' },
  { key: 'median_lead', label: 'Median lead', type: 'num' },
  { key: 'min_lead', label: 'Fastest (days)', type: 'num' },
  { key: 'max_lead', label: 'Slowest (days)', type: 'num' },
  { key: 'promised_lead', label: 'Promised lead (days)', type: 'num' },
  { key: 'on_time_pct', label: 'On time %', type: 'num' },
  { key: 'open_po_qty', label: 'Still to receive (PO qty)', type: 'num' },
  { key: 'last_receipt', label: 'Last receipt', render: d10 },
]

function ItemVendor({ r, focus }) {
  const [text, setText] = useState(focus || '')
  const [multi, setMulti] = useState(!focus)
  const t = text.trim().toLowerCase()
  const rows = (r.data || []).filter((x) => (!multi || x.vendors > 1)
    && (!t || [x.item_code, x.product, x.vendor].some((v) => String(v || '').toLowerCase().includes(t))))
  return (
    <div className="card">
      <div className="card-head">
        <h3>Item × vendor: price vs lead time <span className="muted small">– each item's vendors side by side; gap to the
          cheapest and to the fastest vendor of the same item &amp; UoM</span></h3>
        <div className="inline">
          <input placeholder="Item code, product, vendor…" value={text} onChange={(e) => setText(e.target.value)} />
          <label className="small"><input type="checkbox" checked={multi} onChange={(e) => setMulti(e.target.checked)} /> 2+ vendors only</label>
          <button onClick={() => downloadCsv('item_vendor_price_lead_time.csv', IV, rows)}>Download CSV</button>
        </div>
      </div>
      <Status loading={r.loading} error={r.error} />
      {!r.loading && <DataTable columns={IV} rows={rows} empty="No items in this period" />}
    </div>
  )
}

const LIMITS = [
  { key: 'company', label: 'Company' },
  { key: 'limit_amount', label: 'Approval limit', type: 'money' },
  { key: 'l1_approvers', label: 'L1 approvers' },
]
function Section({ r, title, note, cols, file, empty }) {
  return (
    <div className="card">
      <div className="card-head">
        <h3>{title} {note && <span className="muted small">{note}</span>}</h3>
        <button onClick={() => downloadCsv(file, cols, r.data || [])}>Download CSV</button>
      </div>
      <Status loading={r.loading} error={r.error} />
      {!r.loading && <DataTable columns={cols} rows={r.data} empty={empty || 'Nothing to flag in this period'} />}
    </div>
  )
}

export default function Insights({ companies, categories, focus }) {
  const [company, setCompany] = useCompany()   // shared by every page
  const [period, setPeriod] = useState('fy')
  const [custom, setCustom] = useState({})
  const [category, setCategory] = useState('')
  const [txn, setTxn] = useState('External')
  const [minPct, setMinPct] = useState(10)
  const pr = periodRange(period, custom)
  const range = { company_id: company, date_from: pr.from, date_to: pr.to, category, txn, min_pct: minPct }
  const lr = { company_id: company, date_from: pr.from, date_to: pr.to }
  const step = useSteps([
    ['summary', () => get('/insights/summary', range)],
    ['gap', () => get('/insights/price-gap', range)],
    ['lead', () => get('/insights/lead-time', lr)],
    ['swing', () => get('/insights/price-swing', { ...range, min_pct: Math.max(15, minPct) })],
    ['aplimits', () => get('/insights/approval-limits')],
    ['split', () => get('/insights/split-bills', range)],
    ['newv', () => get('/insights/new-vendors', range)],
    ['single', () => get('/insights/single-source', range)],
    ['rhythm', () => get('/insights/supply-rhythm', lr)],
    ['iv', () => get('/insights/item-vendor', { company_id: company, date_from: pr.from, date_to: pr.to, category, txn })],
  ], [company, pr.from, pr.to, category, txn, minPct])
  const s = step('summary'), sd = s.data, gap = step('gap'), lead = step('lead')

  return (
    <section>
      <div className="filters">
        <CompanySelect companies={companies} value={company} onChange={setCompany} />
        <PeriodSelect period={period} setPeriod={setPeriod} custom={custom} setCustom={setCustom} />
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">All categories</option>
          {(categories || []).map((c) => <option key={c.name}>{c.name}</option>)}
        </select>
        <TxnSelect value={txn} onChange={setTxn} />
        <select value={minPct} onChange={(e) => setMinPct(Number(e.target.value))} title="Flag price gaps of at least">
          {[5, 10, 15, 20, 30].map((v) => <option key={v} value={v}>Flag gaps ≥ {v}%</option>)}
        </select>
      </div>
      <PeriodNote from={pr.from} to={pr.to} label="Bills" />

      <div className="kpis">
        <Kpi label="Items with a vendor price gap" value={num(sd?.gap_products, 0)} loading={s.loading}
             sub={sd ? `of ${num(sd.multi_vendor_products, 0)} items bought from 2+ vendors` : ''} />
        <Kpi label="Paid above the best vendor price" value={compact(sd?.extra_paid)} loading={s.loading}
             sub="same item, same UoM, same period" />
        <Kpi label="Single-source items" value={num(sd?.single_source_products, 0)} loading={s.loading}
             sub={sd ? `${compact(sd.single_source_spend)} spend – no comparison` : ''} />
        <Kpi label="Avg vendor lead time" loading={lead.loading}
             value={lead.data?.length ? `${num(lead.data.reduce((a, r) => a + r.avg_days * r.receipts, 0)
               / lead.data.reduce((a, r) => a + r.receipts, 0), 1)} days` : '—'}
             sub="PO approval → goods received" />
      </div>

      <div className="charts two">
        <ChartCard title="Biggest overpayments vs best vendor" loading={gap.loading} error={gap.error}
                   empty={!(gap.data || []).length}>
          <BarRank data={(gap.data || []).slice(0, 10)} nameKey="product" valueKey="extra_paid"
                   name="Paid above best price" color="#e34948" />
        </ChartCard>
        <ChartCard title="Vendor lead time" note="(avg days, PO → receipt)" loading={lead.loading} error={lead.error}
                   empty={!(lead.data || []).length}>
          <BarRank data={(lead.data || []).slice(0, 10)} nameKey="vendor" valueKey="avg_days" name="Avg lead time (days)"
                   color="#0891b2" format={(v) => `${num(v, 1)} days`} axisFormat={(v) => String(v)} />
        </ChartCard>
      </div>

      <ItemVendor r={step('iv')} focus={focus} />
      <Section r={gap} title="Same item, different vendors, different price"
               note="– extra paid compared with the cheapest vendor's average for the same item & UoM"
               cols={GAP} file="price_gap_by_vendor.csv" />

      <More title="Unstable prices and lead time by vendor">
        <Section r={step('swing')} title="Same vendor, unstable price" note="– lowest vs highest price from one vendor in the period (≥15%)"
                 cols={SWING} file="price_swing_same_vendor.csv" />
        <Section r={lead} title="Lead time by vendor" note="– days from PO approval to goods received (each receipt)"
                 cols={LEAD} file="vendor_lead_time.csv" />
        <Section r={step('rhythm')} title="How often each vendor delivers each item" note="– 3+ deliveries; next expected = last delivery + usual gap"
                 cols={RHYTHM} file="vendor_delivery_rhythm.csv" />
      </More>

      <More title="Review list – approval limits, split billing, new vendors, single source">
        <div className="card">
          <div className="card-head">
            <h3>Approval limits <span className="muted small">– from Odoo PO approval settings</span></h3>
          </div>
          <Status loading={step('aplimits').loading} error={step('aplimits').error} />
          {!step('aplimits').loading && <DataTable columns={LIMITS} rows={step('aplimits').data}
                                                    empty="No approval limits found in Odoo" />}
        </div>
        <Section r={step('split')} title="Split billing" note="– same vendor & item billed in 3+ separate bills in one week"
                 cols={SPLIT} file="split_bills.csv" />
        <Section r={step('newv')} title="New vendors this period" note="– first ever bill in the period"
                 cols={NEWV} file="new_vendors.csv" />
        <Section r={step('single')} title="Single-source items" note="– only one vendor supplied this item in the period"
                 cols={SINGLE} file="single_source.csv" />
      </More>

      <div className="muted small">Prices = posted vendor bills, INR per unit after discount, excl. GST, compared only within the
        same item and the same unit of measure. Lead time = receipt done date − PO approval date. On-time uses the PO line's
        promised (scheduled) date where Odoo has it. Item × vendor: avg price = Σ bill value ÷ Σ qty (quantity-weighted purchase price, not the FIFO / AVCO stock
        valuation); lead time from receipts done in the period, matched on the vendor company; "no receipt" = no receipt linked to that
        vendor's PO line in the period (services, or receipts outside the dates).</div>
      <NotIncluded items={[
        'Quality / rejection rate per vendor – there is no quality inspection data in Odoo.',
        'Whether a price gap was justified (different specification, urgent order, smaller lot size) – needs a buyer\'s note.',
        'POs split to stay under an approval limit, and POs just below a limit – not shown until it is confirmed whether the limit is checked on the PO value incl. or excl. GST and how the limit levels apply.',
        'Who actually approved each PO and when – the approval log is not in the shared tables.',
        'Department-wise limits – the approval lines are set per company, not per department.',
        'Vendors sharing the same bank account, address or phone with an employee – needs the HR / bank master data.',
      ]} />
    </section>
  )
}
