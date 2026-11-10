import React, { useState } from 'react'
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from 'recharts'
import { get, num, compact, money, SERIES } from '../api.js'
import { useAsync, Kpi, Status, DataTable, CompanySelect, useCompany, PeriodSelect, periodRange, TxnSelect, Picker, downloadCsv, More } from '../components/ui.jsx'
import { ChartCard, BarTrend, fmtPeriod } from '../components/Charts.jsx'

const d10 = (v) => (v ? String(v).slice(0, 10) : '—')
const pct = (v) => (v == null ? '—' : <span className={v > 0 ? 'up' : v < 0 ? 'down' : ''}>{v > 0 ? '+' : ''}{num(v, 1)}%</span>)
const gap = (v) => (v == null ? '—' : v === 0 ? '—'
  : <span className={`pill ${v >= 10 ? 'pill-bad' : v >= 5 ? 'pill-warn' : ''}`}>{v > 0 ? '+' : ''}{num(v, 1)}%</span>)
const TAG = { 'Cheapest & fastest': 'pill-good', Cheapest: 'pill-good', Fastest: 'pill-warn' }

const PCOLS = [
  { key: 'vendor', label: 'Vendor' },
  { key: 'uom', label: 'UoM' },
  { key: 'tag', label: 'Best on', render: (v) => (v ? <span className={`pill ${TAG[v] || ''}`}>{v}</span> : '') },
  { key: 'avg_price', label: 'Avg price', type: 'money', render: (v) => <b>{money(v)}</b> },
  { key: 'vs_best_pct', label: 'vs cheapest', type: 'num', render: gap },
  { key: 'last_price', label: 'Last price', type: 'money' },
  { key: 'last_price_date', label: 'Last bill', render: d10 },
  { key: 'price_trend_pct', label: 'First → last', type: 'num', render: pct },
  { key: 'min_price', label: 'Lowest', type: 'money' },
  { key: 'max_price', label: 'Highest', type: 'money' },
  { key: 'qty', label: 'Qty bought', type: 'num' },
  { key: 'qty_share_pct', label: 'Share of qty', type: 'num', render: (v) => (v == null ? '—' : `${num(v, 1)}%`) },
  { key: 'spend', label: 'Spend', type: 'money', render: (v) => compact(v) },
  { key: 'extra_paid', label: 'Paid extra', type: 'money', render: (v) => (v > 0.5 ? <b className="up">{money(v)}</b> : '—') },
  { key: 'avg_lead', label: 'Avg lead (days)', type: 'num', render: (v) => (v == null ? <span className="muted">no receipt</span> : num(v, 1)) },
  { key: 'vs_fastest_days', label: 'Slower than fastest', type: 'num', render: (v) => (v > 0 ? `+${num(v, 1)} d` : '—') },
  { key: 'on_time_pct', label: 'On time %', type: 'num' },
  { key: 'open_po_qty', label: 'Still to receive', type: 'num' },
]
const VCOLS = [
  { key: 'item_code', label: 'Item' },
  { key: 'product', label: 'Product' },
  { key: 'uom', label: 'UoM' },
  { key: 'qty', label: 'Qty', type: 'num' },
  { key: 'spend', label: 'Spend', type: 'money', render: (v) => compact(v) },
  { key: 'avg_price', label: 'Their avg price', type: 'money', render: (v) => <b>{money(v)}</b> },
  { key: 'last_price', label: 'Last price', type: 'money' },
  { key: 'price_trend_pct', label: 'First → last', type: 'num', render: pct },
  { key: 'other_vendors', label: 'Other vendors', type: 'num' },
  { key: 'cheapest_other', label: 'Cheapest other vendor' },
  { key: 'cheapest_other_price', label: 'Their price', type: 'money' },
  { key: 'vs_other_pct', label: 'vs cheapest other', type: 'num', render: gap },
  { key: 'extra_paid', label: 'Paid extra', type: 'money', render: (v) => (v > 0.5 ? <b className="up">{money(v)}</b> : '—') },
  { key: 'avg_lead', label: 'Lead (days)', type: 'num' },
  { key: 'open_po_qty', label: 'Still to receive', type: 'num' },
]
const OPEN = [
  { key: 'po_number', label: 'PO' }, { key: 'po_date', label: 'PO date', render: d10 },
  { key: 'age_days', label: 'Age (days)', type: 'num' }, { key: 'company', label: 'Company' },
  { key: 'grn_status', label: 'GRN' }, { key: 'bill_status', label: 'Bill' }, { key: 'pay_status', label: 'Payment' },
  { key: 'ordered_value', label: 'PO value', type: 'money' },
  { key: 'grn_pending_value', label: 'Not yet received', type: 'money' },
  { key: 'to_bill_value', label: 'Received, not billed', type: 'money' },
]
const RET = [
  { key: 'return_no', label: 'Return' }, { key: 'return_date', label: 'Date', render: d10 }, { key: 'kind', label: 'How' },
  { key: 'po_numbers', label: 'PO' }, { key: 'value', label: 'Value', type: 'money' },
  { key: 'credited', label: 'Credited', type: 'money' }, { key: 'credit_status', label: 'Debit note status' },
]

function PriceTrend({ rows: all, uom }) {
  // monthly average price per vendor, one line each, for one unit of measure
  const rows = all.filter((r) => (r.uom || '') === (uom || ''))
  const vendors = [...new Set(rows.map((r) => r.vendor))].slice(0, 6)
  const byMonth = {}
  rows.forEach((r) => { const m = String(r.month).slice(0, 10); byMonth[m] = { ...(byMonth[m] || { month: m }), [r.vendor]: Number(r.avg_price) } })
  const data = Object.values(byMonth).sort((a, b) => (a.month > b.month ? 1 : -1))
  const f = fmtPeriod('month')
  return (
    <ResponsiveContainer>
      <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid vertical={false} stroke="#f0f2f6" />
        <XAxis dataKey="month" tickFormatter={f} tick={{ fontSize: 11, fill: '#8a91a6' }} tickLine={false} axisLine={false} />
        <YAxis tick={{ fontSize: 11, fill: '#8a91a6' }} tickLine={false} axisLine={false} width={64} tickFormatter={(v) => compact(v)} />
        <Tooltip labelFormatter={f} formatter={(v, n) => [money(v), n]} />
        <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />
        {vendors.map((v, i) => <Line key={v} dataKey={v} stroke={SERIES[i % SERIES.length]} strokeWidth={2} dot={{ r: 3 }} connectNulls isAnimationActive={false} />)}
      </LineChart>
    </ResponsiveContainer>
  )
}

export default function Explore({ companies }) {
  const [mode, setMode] = useState('product')
  const [company, setCompany] = useCompany()   // shared by every page
  const [period, setPeriod] = useState('fy')
  const [custom, setCustom] = useState({})
  const [txn, setTxn] = useState('External')
  const [product, setProduct] = useState(null)
  const [vendor, setVendor] = useState(null)
  const [uom, setUom] = useState('')
  const pr = periodRange(period, custom)
  const prm = { company_id: company, date_from: pr.from || '2000-01-01', date_to: pr.to, txn: txn || 'all' }
  const dep = [company, pr.from, pr.to, txn]
  const P = useAsync(() => (mode === 'product' && product ? get(`/explore/product/${product.id}`, prm) : Promise.resolve(null)),
    [mode, product?.id, ...dep])
  const V = useAsync(() => (mode === 'vendor' && vendor ? get(`/explore/vendor/${vendor.id}`, prm) : Promise.resolve(null)),
    [mode, vendor?.id, ...dep])
  const p = P.data, v = V.data

  return (
    <section>
      <div className="subtabs" style={{ marginBottom: 12 }}>
        <button className={mode === 'product' ? 'sub active' : 'sub'} onClick={() => setMode('product')}>By product – all its vendors</button>
        <button className={mode === 'vendor' ? 'sub active' : 'sub'} onClick={() => setMode('vendor')}>By vendor – all its products</button>
      </div>
      <div className="filters">
        {mode === 'product'
          ? <Picker endpoint="/products" value={product} onChange={setProduct} placeholder="Type item code or product name…" />
          : <Picker endpoint="/vendors" value={vendor} onChange={setVendor} placeholder="Type vendor name or GSTIN…" />}
        <CompanySelect companies={companies} value={company} onChange={setCompany} />
        <PeriodSelect period={period} setPeriod={setPeriod} custom={custom} setCustom={setCustom} />
        <TxnSelect value={txn} onChange={setTxn} />
      </div>

      {mode === 'product' && !product && <div className="msg">Pick a product above: you get every vendor's price, the gap to the cheapest,
        lead time and a monthly price trend for the companies you tick.</div>}
      {mode === 'vendor' && !vendor && <div className="msg">Pick a vendor above: you get what we buy from them, how their prices compare
        with other vendors, delivery, returns, open POs and what we owe them.</div>}

      {mode === 'product' && product && <>
        <Status loading={P.loading} error={P.error} />
        {p && <>
          <div className="period-note">📅 Bills dated <b>{String(p.period.from).startsWith('2000') ? 'the first bill' : p.period.from}</b> to <b>{p.period.to}</b>
            {p.item_code && <> · <b>[{p.item_code}] {p.product}</b> · {p.category}</>}</div>
          <div className="kpis">
            <Kpi label="Bought" value={compact(p.spend)} sub={`${num(p.qty, 2)} ${p.uoms.join(' / ')}`} />
            <Kpi label="Vendors" value={num(p.vendors, 0)} sub={p.cheapest ? `cheapest: ${p.cheapest}` : 'one vendor only'} />
            <Kpi label="Avg price" value={p.avg_price != null ? money(p.avg_price) : 'by UoM'} sub={p.uoms.length > 1 ? 'several UoMs – see table' : `per ${p.uoms[0] || ''}`} />
            <Kpi label="Paid above cheapest" value={compact(p.extra_paid)} sub="same item, same UoM" />
            <Kpi label="Fastest vendor" value={p.fastest || '—'} sub="average lead time" />
            <Kpi label="Still to receive" value={num(p.open_po_qty, 2)} sub="on confirmed POs" />
          </div>
          {p.uoms.length > 1 && (
            <div className="inline" style={{ margin: '0 0 8px' }}>
              <span className="muted small">Price trend per</span>
              <select value={p.uoms.includes(uom) ? uom : p.uoms[0]} onChange={(e) => setUom(e.target.value)}>
                {p.uoms.map((u) => <option key={u} value={u}>{u || 'no UoM'}</option>)}
              </select>
            </div>
          )}
          <ChartCard title="Price trend by vendor" note={`(average net price per month, per ${p.uoms.includes(uom) ? uom : p.uoms[0] || 'unit'})`}
                     empty={!p.trend.length} height={280}>
            <PriceTrend rows={p.trend} uom={p.uoms.includes(uom) ? uom : p.uoms[0]} />
          </ChartCard>
          <div className="card">
            <div className="card-head">
              <h3>Vendors of this product <span className="muted small">– price, gap to cheapest, delivery</span></h3>
              <button onClick={() => downloadCsv(`product_${p.item_code || product.id}_vendors.csv`, PCOLS, p.vendors_rows)}>Download CSV</button>
            </div>
            <DataTable columns={PCOLS} rows={p.vendors_rows} empty="Not bought in this period" />
          </div>
        </>}
      </>}

      {mode === 'vendor' && vendor && <>
        <Status loading={V.loading} error={V.error} />
        {v && <>
          <div className="period-note">📅 Bills dated <b>{String(v.period.from).startsWith('2000') ? 'the first bill' : v.period.from}</b> to <b>{v.period.to}</b>
            {v.vendor && <> · <b>{v.vendor}</b> {v.gstin && <span className="muted">· {v.gstin}</span>} · {v.source} · buying since {d10(v.first_bill)}</>}</div>
          <div className="kpis">
            <Kpi label="Spend" value={compact(v.spend)} sub={`${num(v.bills, 0)} bills · ${num(v.items, 0)} items`} />
            <Kpi label="Price vs other vendors" value={`${num(v.items_dearer, 0)} dearer`} sub={`${compact(v.extra_paid)} paid above cheapest · cheapest on ${num(v.items_cheapest, 0)}`} />
            <Kpi label="Delivery" value={v.avg_lead != null ? `${num(v.avg_lead, 1)} days` : '—'} sub={v.on_time_pct != null ? `${num(v.on_time_pct, 0)}% on time · ${num(v.receipts, 0)} receipts` : `${num(v.receipts || 0, 0)} receipts`} />
            <Kpi label="Returns" value={v.return_pct != null ? `${num(v.return_pct, 2)}%` : '—'} sub={`${num(v.returns, 0)} returns · ${compact(v.to_recover)} debit note due`} />
            <Kpi label="Open POs" value={compact(v.open_po_value)} sub={`${num(v.open_pos, 0)} POs still to receive`} />
            <Kpi label="We owe them" value={compact(v.unpaid)} sub={`${compact(v.overdue)} overdue`} />
          </div>
          <ChartCard title="Monthly spend with this vendor" empty={!v.monthly.length} height={220}>
            <BarTrend data={v.monthly} x="month" y="spend" xFormat={fmtPeriod('month')} name="Spend" />
          </ChartCard>
          <div className="card">
            <div className="card-head">
              <h3>What we buy from them <span className="muted small">– each item vs the cheapest other vendor (same UoM)</span></h3>
              <button onClick={() => downloadCsv(`vendor_${vendor.id}_items.csv`, VCOLS, v.items_rows)}>Download CSV</button>
            </div>
            <DataTable columns={VCOLS} rows={v.items_rows} empty="No bills in this period" />
          </div>
          <More title={`Open POs (${v.open_pos}) and returns waiting for a debit note (${v.pending_returns.length})`}>
            <div className="card">
              <div className="card-head"><h3>Open POs <span className="muted small">– any date, still to receive</span></h3>
                <button onClick={() => downloadCsv(`vendor_${vendor.id}_open_pos.csv`, OPEN, v.open_po_rows)}>Download CSV</button></div>
              <DataTable columns={OPEN} rows={v.open_po_rows} empty="No open POs" />
            </div>
            <div className="card">
              <div className="card-head"><h3>Returns not fully credited</h3>
                <button onClick={() => downloadCsv(`vendor_${vendor.id}_returns.csv`, RET, v.pending_returns)}>Download CSV</button></div>
              <DataTable columns={RET} rows={v.pending_returns} empty="Nothing to recover" />
            </div>
          </More>
        </>}
      </>}
      <div className="muted small">Same rules as the other pages: prices from posted vendor bills (INR per unit after discount, excl. GST),
        compared within the same item and UoM; lead time from receipts linked to the PO line; open POs and unpaid bills of any date.</div>
    </section>
  )
}
