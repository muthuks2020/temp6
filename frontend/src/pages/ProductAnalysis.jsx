import React, { useMemo, useState } from 'react'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, Legend, CartesianGrid, BarChart, Bar,
} from 'recharts'
import { get, money, num, compact, SERIES } from '../api.js'
import CategoryPrices from './CategoryPrices.jsx'
import { useSteps, PeriodSelect, periodRange, SourceSelect, TxnSelect, Kpi, Status, DataTable, Picker, CompanySelect, useCompany, downloadCsv } from '../components/ui.jsx'

const VCOLS = [
  { key: 'company', label: 'Company' },
  { key: 'vendor', label: 'Vendor' },
  { key: 'pos', label: 'Bills', type: 'num' },
  { key: 'qty', label: 'Qty', type: 'num' },
  { key: 'base_uom', label: 'UoM' },
  { key: 'uoms', label: 'UoMs bought in' },
  { key: 'min_price', label: 'Min price', type: 'money' },
  { key: 'max_price', label: 'Max price', type: 'money' },
  { key: 'avg_price', label: 'Weighted avg', type: 'money' },
  { key: 'last_price', label: 'Last price', type: 'money' },
  { key: 'spend', label: 'Spend', type: 'money' },
  { key: 'first_po', label: 'First bill' },
  { key: 'last_po', label: 'Last bill' },
]
const PCOLS = [
  { key: 'bill_date', label: 'Bill date' },
  { key: 'bill_number', label: 'Bill no.' },
  { key: 'vendor', label: 'Vendor' },
  { key: 'company', label: 'Company' },
  { key: 'po_number', label: 'PO' },
  { key: 'quantity', label: 'Qty', type: 'num' },
  { key: 'uom', label: 'UoM' },
  { key: 'price_unit', label: 'Rate', type: 'num' },
  { key: 'discount', label: 'Disc %', type: 'num' },
  { key: 'net_unit_price', label: 'Net price (INR)', type: 'money' },
  { key: 'taxable_value', label: 'Amount (INR)', type: 'money' },
  { key: 'source', label: 'Local / Import' },
  { key: 'currency', label: 'Bill ccy' },
  { key: 'amount_fc', label: 'Amount (bill ccy)', type: 'num' },
]

const ACOLS = [
  { key: 'po_date', label: 'Date' },
  { key: 'company', label: 'Company' },
  { key: 'item_code', label: 'Item code' },
  { key: 'product', label: 'Product' },
  { key: 'vendor', label: 'Vendor' },
  { key: 'prev_po', label: 'Previous bill' },
  { key: 'prev_price', label: 'Previous price', type: 'money' },
  { key: 'po_number', label: 'Current bill' },
  { key: 'current_price', label: 'Current price', type: 'money' },
  { key: 'change_pct', label: 'Change',
    type: 'pct', render: (v) => <span className={v > 0 ? 'up' : 'down'}>{v > 0 ? '▲' : '▼'} {num(Math.abs(v), 1)}%</span> },
]

const tip = { contentStyle: { background: 'var(--surface-2)', border: '1px solid var(--line)', borderRadius: 6, fontSize: 12 } }

function ProductDetail({ companies, product, setProduct }) {
  const [company, setCompany] = useCompany()   // shared by every page
  const [minPct, setMinPct] = useState(5)
  const [days, setDays] = useState(90)
  const [period, setPeriod] = useState('fy')          // default: this fiscal year
  const [custom, setCustom] = useState({})
  const [source, setSource] = useState('')
  const [txn, setTxn] = useState('External')
  const pr = periodRange(period, custom)
  const range = { company_id: company, date_from: pr.from, date_to: pr.to, source, txn }

  // one after another: vendors -> purchases -> trend -> price alerts
  const step = useSteps([
    ['vendors', () => (product ? get(`/product/${product.id}/vendors`, range) : Promise.resolve([]))],
    ['purchases', () => (product ? get('/register', { ...range, product_id: product.id, page_size: 500,
                                                       sort: 'bill_date', order: 'desc' }) : Promise.resolve(null))],
    ['trend', () => (product ? get(`/product/${product.id}/trend`, range) : Promise.resolve([]))],
    ['alerts', () => get('/price-alerts', { min_pct: minPct, days, company_id: company })],
  ], [product?.id, company, minPct, days, pr.from, pr.to, source, txn])
  const vendors = step('vendors')
  const purchases = step('purchases')
  const trend = step('trend')
  const alerts = step('alerts')

  const v = vendors.data || []
  const k = useMemo(() => {
    if (!v.length) return null
    const prices = v.map((r) => r.avg_price).filter((x) => x !== null)
    const lo = Math.min(...prices), hi = Math.max(...prices)
    return {
      vendors: new Set(v.map((r) => r.vendor_id)).size,
      companies: new Set(v.map((r) => r.company)).size,
      lo, hi, spread: lo ? ((hi - lo) / lo) * 100 : null,
      avg: (() => { const q = v.reduce((a, r) => a + (r.qty || 0), 0)
                    return q ? v.reduce((a, r) => a + (r.spend || 0), 0) / q : null })(),
      mixedUom: new Set(v.map((r) => r.base_uom)).size > 1,
      qty: v.reduce((a, r) => a + (r.qty || 0), 0), uom: v[0].base_uom,
      spend: v.reduce((a, r) => a + (r.spend || 0), 0),
    }
  }, [v])

  // vendor avg price bar (one series) - label vendor + company
  const bars = v.map((r) => ({ name: companies.length > 1 && !company ? `${r.vendor} · ${r.company}` : r.vendor, avg: r.avg_price }))

  // trend: top 8 vendors by qty (fixed palette order, never cycled)
  const { series, points } = useMemo(() => {
    const t = trend.data || []
    const qtyBy = {}
    t.forEach((r) => { qtyBy[r.vendor] = (qtyBy[r.vendor] || 0) + (r.qty || 0) })
    const top = Object.keys(qtyBy).sort((a, b) => qtyBy[b] - qtyBy[a]).slice(0, 8)
    const byMonth = {}
    t.filter((r) => top.includes(r.vendor)).forEach((r) => {
      byMonth[r.month] = byMonth[r.month] || Object.fromEntries([['month', r.month.slice(0, 7)], ...top.map((v) => [v, null])])
      byMonth[r.month][r.vendor] = r.avg_price
    })
    return { series: top, points: Object.values(byMonth).sort((a, b) => a.month.localeCompare(b.month)) }
  }, [trend.data])

  return (
    <section>
      <div className="filters">
        <Picker endpoint="/products" value={product} onChange={setProduct} placeholder="Pick a product / item code…" />
        <CompanySelect companies={companies} value={company} onChange={setCompany} />
        <PeriodSelect period={period} setPeriod={setPeriod} custom={custom} setCustom={setCustom} />
        <SourceSelect value={source} onChange={setSource} />
        <TxnSelect value={txn} onChange={setTxn} />
      </div>

      {!product && <div className="msg">Pick a product to see what was bought in the period, the average price, all its vendors and the price trend.</div>}

      {product && (
        <>
          <h2>{product.item_code ? `[${product.item_code}] ` : ''}{product.name}</h2>
          <Status loading={vendors.loading} error={vendors.error} />
          {!vendors.loading && !vendors.error && v.length === 0 && (
            <div className="msg">No vendor bills for this product in {pr.label}. Try "All years".</div>
          )}
          {k && (
            <div className="kpis">
              <Kpi label={`Avg price – ${pr.label}`} value={money(k.avg)}
                   sub={k.mixedUom ? 'mixed UoMs – see table' : `per ${k.uom}, weighted by qty`} />
              <Kpi label="Vendors" value={k.vendors} sub={`across ${k.companies} compan${k.companies > 1 ? 'ies' : 'y'}`} />
              <Kpi label="Lowest avg price" value={money(k.lo)} sub={`per ${k.uom}`} />
              <Kpi label="Highest avg price" value={money(k.hi)} sub={`per ${k.uom}`} />
              <Kpi label="Price spread" value={k.spread === null ? '—' : `${num(k.spread, 1)}%`} sub="highest vs lowest" />
              <Kpi label="Total bought" value={`${num(k.qty, 2)} ${k.uom || ''}`} sub={compact(k.spend)} />
            </div>
          )}

          <div className="grid2">
            <div className="card">
              <h3>Average billed price by vendor <span className="muted">(INR per unit, from vendor bills)</span></h3>
              <ResponsiveContainer width="100%" height={Math.max(180, bars.length * 34 + 40)}>
                <BarChart data={bars} layout="vertical" margin={{ left: 10, right: 30 }}>
                  <CartesianGrid horizontal={false} stroke="var(--grid)" />
                  <XAxis type="number" tick={{ fill: 'var(--text-2)', fontSize: 11 }} stroke="var(--line)" />
                  <YAxis type="category" dataKey="name" width={190} tick={{ fill: 'var(--text-2)', fontSize: 11 }} stroke="var(--line)" />
                  <Tooltip {...tip} formatter={(x) => money(x)} cursor={{ fill: 'var(--hover)' }} />
                  <Bar dataKey="avg" name="Avg price" fill={SERIES[0]} radius={[0, 4, 4, 0]} barSize={18} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="card">
              <h3>Monthly price trend <span className="muted">(top {series.length} vendors by qty)</span></h3>
              <Status loading={trend.loading} error={trend.error} />
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={points} margin={{ right: 20 }}>
                  <CartesianGrid vertical={false} stroke="var(--grid)" />
                  <XAxis dataKey="month" tick={{ fill: 'var(--text-2)', fontSize: 11 }} stroke="var(--line)" />
                  <YAxis tick={{ fill: 'var(--text-2)', fontSize: 11 }} stroke="var(--line)" width={70} />
                  <Tooltip {...tip} formatter={(x) => money(x)} />
                  <Legend wrapperStyle={{ fontSize: 12, color: 'var(--text-2)' }} />
                  {series.map((s, i) => (
                    <Line key={s} dataKey={s} stroke={SERIES[i]} strokeWidth={2} connectNulls
                          dot={{ r: 4, strokeWidth: 2, stroke: 'var(--surface-1)', fill: SERIES[i] }}
                          activeDot={{ r: 6, strokeWidth: 2, stroke: 'var(--surface-1)', fill: SERIES[i] }} />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <h3>Vendors &amp; prices</h3>
              <button onClick={() => downloadCsv('product_vendors.csv', VCOLS, v)}>Download CSV</button>
            </div>
            <DataTable columns={VCOLS} rows={v} />
          </div>

          <div className="card">
            <div className="card-head">
              <h3>Purchases in {pr.label} <span className="muted">(every bill line, newest first)</span></h3>
              <button onClick={() => downloadCsv('product_purchases.csv', PCOLS, purchases.data?.rows || [])}>Download CSV</button>
            </div>
            <Status loading={purchases.loading} error={purchases.error} />
            <DataTable columns={PCOLS} rows={purchases.data?.rows} empty={`No purchases in ${pr.label}`} />
          </div>
        </>
      )}

      <div className="card">
        <div className="card-head">
          <h3>Price-change alerts <span className="muted">(same vendor, vs previous bill)</span></h3>
          <div className="inline">
            <label>Min change % <input type="number" value={minPct} onChange={(e) => setMinPct(e.target.value)} style={{ width: 70 }} /></label>
            <label>Last <input type="number" value={days} onChange={(e) => setDays(e.target.value)} style={{ width: 70 }} /> days</label>
            <button onClick={() => downloadCsv('price_alerts.csv', ACOLS, alerts.data || [])}>Download CSV</button>
          </div>
        </div>
        <Status loading={alerts.loading} error={alerts.error} />
        <DataTable columns={ACOLS} rows={alerts.data}
                   onRowClick={(r) => setProduct({ id: r.product_id, name: r.product, item_code: r.item_code })} />
      </div>
    </section>
  )
}


export default function ProductAnalysis({ companies, categories, product, setProduct }) {
  const [view, setView] = useState(product ? 'product' : 'category')
  return (
    <section>
      <div className="subtabs">
        <button className={view === 'category' ? 'sub active' : 'sub'} onClick={() => setView('category')}>
          By category – all products, vendors &amp; prices</button>
        <button className={view === 'product' ? 'sub active' : 'sub'} onClick={() => setView('product')}>
          Single product – detail &amp; trend</button>
      </div>
      {view === 'category'
        ? <CategoryPrices categories={categories} companies={companies}
                          onPick={(p) => { setProduct(p); setView('product') }} />
        : <ProductDetail companies={companies} product={product} setProduct={setProduct} />}
    </section>
  )
}
