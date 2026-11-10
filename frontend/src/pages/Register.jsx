import React, { useState } from 'react'
import { get, qs, num, compact } from '../api.js'
import { ChartCard, BarTrend, BarRank, fmtPeriod } from '../components/Charts.jsx'
import { useTiles, monthStart, GlSelect, PeriodNote, SourceSelect, TxnSelect, OrderTypeSelect, Kpi, Status, DataTable, Picker, CompanySelect, useCompany } from '../components/ui.jsx'

const COLS = [
  { key: 'bill_number', label: 'Bill no.' },
  { key: 'bill_date', label: 'Bill date' },
  { key: 'doc_type', label: 'Type' },
  { key: 'company', label: 'Company' },
  { key: 'vendor', label: 'Vendor' },
  { key: 'vendor_gstin', label: 'GSTIN' },
  { key: 'po_number', label: 'PO' },
  { key: 'item_code', label: 'Item code' },
  { key: 'product', label: 'Product' },
  { key: 'hsn_code', label: 'HSN' },
  { key: 'quantity', label: 'Qty', type: 'num' },
  { key: 'uom', label: 'UoM' },
  { key: 'price_unit', label: 'Rate', type: 'num' },
  { key: 'discount', label: 'Disc %', type: 'num' },
  { key: 'net_unit_price', label: 'Net price (INR)', type: 'money' },
  { key: 'taxable_value', label: 'Taxable (INR)', type: 'money' },
  { key: 'source', label: 'Local / Import' },
  { key: 'txn_class', label: 'Transaction' },
  { key: 'order_type', label: 'Order type' },
  { key: 'currency', label: 'Bill ccy' },
  { key: 'amount_fc', label: 'Amount (bill ccy)', type: 'num' },
  { key: 'tax_names', label: 'Taxes' },
  { key: 'cgst', label: 'CGST', type: 'money' },
  { key: 'sgst', label: 'SGST', type: 'money' },
  { key: 'igst', label: 'IGST', type: 'money' },
  { key: 'gl_account_code', label: 'GL code' },
  { key: 'gl_account', label: 'GL account' },
  { key: 'vendor_bill_ref', label: 'Vendor ref' },
]

export default function Register({ meta, companies, openProduct }) {
  const [gco, setGco] = useCompany()
  const [f, setF] = useState({ company_id: gco, vendor: null, product: null, category: '', date_from: monthStart(), date_to: '', search: '', gl: [], source: '', txn: '', ot: [] })
  const [applied, setApplied] = useState(f)
  const [page, setPage] = useState(1)
  const [sort, setSort] = useState({ sort: 'bill_date', order: 'desc' })
  const pageSize = 50

  const params = {
    company_id: applied.company_id, vendor_id: applied.vendor?.id, product_id: applied.product?.id,
    category: applied.category, date_from: applied.date_from, date_to: applied.date_to, search: applied.search,
    gl: (applied.gl || []).join(','), source: applied.source, txn: applied.txn,
    ot: (applied.ot || []).join(','),
  }
  // table first, then the 5 tiles one after another
  const TILES = ['spend', 'bills', 'lines', 'vendors', 'products', 'c_trend', 'c_vendors', 'c_categories']
  const step = useTiles(() => get('/register', { ...params, page, page_size: pageSize, ...sort }),
    TILES, (t) => (t.startsWith('c_') ? get(`/register/chart/${t.slice(2)}`, params) : get(`/register/tile/${t}`, params)),
    params, [page, sort.sort, sort.order])
  const res = step('rows')
  const tile = (t) => step(t)
  const lines = tile('lines').data?.value
  const anyTileLoading = TILES.some((t) => tile(t).loading)
  const tr = tile('c_trend'), tv = tile('c_vendors'), tc = tile('c_categories')
  const set = (k) => (v) => setF((x) => ({ ...x, [k]: v }))
  const apply = () => { setApplied(f); setPage(1) }
  const reset = () => {
    const e = { company_id: gco, vendor: null, product: null, category: '', date_from: monthStart(), date_to: '', search: '', gl: [], source: '', txn: '', ot: [] }
    setF(e); setApplied(e); setPage(1)
  }

  return (
    <section>
      <div className="filters">
        <CompanySelect companies={companies} value={f.company_id}
                       onChange={(v) => { setGco(v); setF((x) => ({ ...x, company_id: v })); setApplied((x) => ({ ...x, company_id: v })); setPage(1) }} />
        <Picker endpoint="/vendors" value={f.vendor} onChange={set('vendor')} placeholder="Vendor…" />
        <Picker endpoint="/products" value={f.product} onChange={set('product')} placeholder="Product / item code…" />
        <select value={f.category} onChange={(e) => set('category')(e.target.value)}>
          <option value="">All categories</option>
          {(meta?.categories || []).map((c) => <option key={c.name}>{c.name}</option>)}
        </select>
        <GlSelect value={f.gl} onChange={set('gl')} />
        <SourceSelect value={f.source} onChange={set('source')} />
        <TxnSelect value={f.txn} onChange={set('txn')} />
        <OrderTypeSelect value={f.ot} onChange={set('ot')} />
        <label>From <input type="date" value={f.date_from} onChange={(e) => set('date_from')(e.target.value)} /></label>
        <label>To <input type="date" value={f.date_to} onChange={(e) => set('date_to')(e.target.value)} /></label>
        <input placeholder="Bill, PO, product, HSN…" value={f.search}
               onChange={(e) => set('search')(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && apply()} />
        <button className="primary" onClick={apply}>Apply</button>
        <button onClick={reset}>Reset</button>
        <a className="btn" href={`/api/register/export?${qs(params)}`}>Export Excel</a>
      </div>

      <PeriodNote from={applied.date_from} to={applied.date_to} gl={applied.gl}
                  extra={applied.source ? <> · <b>{applied.source} only</b></> : null} />
      <div className="kpis">
        <Kpi label="Taxable value (INR)" value={compact(tile('spend').data?.value)} {...tile('spend')} />
        <Kpi label="Bills" value={num(tile('bills').data?.value, 0)} {...tile('bills')} />
        <Kpi label="Bill lines" value={num(lines, 0)} {...tile('lines')} />
        <Kpi label="Vendors" value={num(tile('vendors').data?.value, 0)} {...tile('vendors')} />
        <Kpi label="Products" value={num(tile('products').data?.value, 0)} {...tile('products')} />
      </div>

      <div className="charts">
        <ChartCard title="Spend over time" note={tr.data?.unit === 'day' ? '(by day)' : '(by month)'}
                   loading={tr.loading} error={tr.error} empty={!(tr.data?.rows || []).length}>
          <BarTrend data={tr.data?.rows} x="period" y="spend" name="Taxable value" xFormat={fmtPeriod(tr.data?.unit)} />
        </ChartCard>
        <ChartCard title="Top 10 vendors" loading={tv.loading} error={tv.error} empty={!(tv.data || []).length}>
          <BarRank data={tv.data} valueKey="spend" name="Taxable value" color="#6250d6" />
        </ChartCard>
        <ChartCard title="Spend by category" loading={tc.loading} error={tc.error} empty={!(tc.data || []).length}>
          <BarRank data={tc.data} valueKey="spend" name="Taxable value" color="#1baf7a" />
        </ChartCard>
      </div>

      <Status loading={res.loading} error={res.error} />
      <DataTable columns={COLS} rows={res.data?.rows} sort={sort.sort} order={sort.order}
                 onSort={(k, d) => setSort({ sort: k, order: d })}
                 onRowClick={(r) => openProduct({ id: r.product_id, name: r.product })} />
      <div className="pager">
        <button disabled={page === 1} onClick={() => setPage(page - 1)}>‹ Prev</button>
        <span>Page {page}{lines !== undefined ? ` of ${Math.max(1, Math.ceil((lines || 0) / pageSize))}` : ''}</span>
        <button disabled={(res.data?.rows || []).length < pageSize} onClick={() => setPage(page + 1)}>Next ›</button>
        <span className="muted">Click a row to analyse that product</span>
      </div>
    </section>
  )
}
