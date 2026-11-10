import React, { useMemo, useState } from 'react'
import { get, num, compact, money } from '../api.js'
import { useAsync, fiscalYear, GlSelect, PeriodNote, SourceSelect, TxnSelect, OrderTypeSelect, Kpi, Status, DataTable, CompanySelect, useCompany, downloadCsv } from '../components/ui.jsx'


export default function CategoryPrices({ categories, companies, onPick }) {
  const [gco, setGco] = useCompany()
  const [f, setF] = useState({ category: '', company_id: gco, date_from: fiscalYear(0).from, date_to: '', search: '', gl: [], source: '', txn: 'External', ot: [] })
  const [applied, setApplied] = useState(null)
  const set = (k) => (v) => setF((x) => ({ ...x, [k]: v }))

  const res = useAsync(() => (applied ? get('/category/products', applied) : Promise.resolve(null)),
    [JSON.stringify(applied)])
  const rows = res.data || []

  const k = useMemo(() => {
    const products = new Set(rows.map((r) => r.product_id))
    const vendors = new Set(rows.map((r) => r.vendor_id))
    const multi = new Set(rows.filter((r) => r.vendors_for_product > 1).map((r) => r.product_id))
    return { products: products.size, vendors: vendors.size, multi: multi.size,
             spend: rows.reduce((a, r) => a + (r.spend || 0), 0) }
  }, [rows])

  // show product name only on the first row of each product group
  const COLS = [
    { key: 'product', label: 'Product', sortable: false,
      render: (v, r, i) => (r._first ? <b>{r.item_code ? `[${r.item_code}] ` : ''}{v}</b> : '') },
    { key: 'vendors_for_product', label: 'Vendors', type: 'num', sortable: false,
      render: (v, r) => (r._first ? v : '') },
    { key: 'price_rank', label: '#', type: 'num', sortable: false,
      render: (v) => (v === 1 ? <span className="best">1 · best</span> : v) },
    { key: 'vendor', label: 'Vendor', sortable: false },
    { key: 'company', label: 'Company', sortable: false },
    { key: 'avg_price', label: 'Avg price', type: 'money', sortable: false },
    { key: 'pct_above_best', label: 'vs best', type: 'pct', sortable: false,
      render: (v) => (v ? <span className="up">+{num(v, 1)}%</span> : '—') },
    { key: 'last_price', label: 'Last price', type: 'money', sortable: false },
    { key: 'min_price', label: 'Min', type: 'money', sortable: false },
    { key: 'max_price', label: 'Max', type: 'money', sortable: false },
    { key: 'qty', label: 'Qty', type: 'num', sortable: false },
    { key: 'uoms', label: 'UoM', sortable: false },
    { key: 'bills', label: 'Bills', type: 'num', sortable: false },
    { key: 'last_bill', label: 'Last bill', sortable: false },
    { key: 'spend', label: 'Spend', type: 'money', sortable: false },
  ]
  const shown = rows.map((r, i) => ({ ...r, _first: i === 0 || rows[i - 1].product_id !== r.product_id }))

  const run = () => f.category && setApplied({ ...f, gl: (f.gl || []).join(','), ot: (f.ot || []).join(',') })

  return (
    <>
      <div className="filters">
        <select value={f.category} onChange={(e) => set('category')(e.target.value)}>
          <option value="">Choose a product category…</option>
          {(categories || []).map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
        </select>
        <CompanySelect companies={companies} value={f.company_id} onChange={(v) => { setGco(v); set('company_id')(v) }} />
        <GlSelect value={f.gl} onChange={set('gl')} />
        <SourceSelect value={f.source} onChange={set('source')} />
        <TxnSelect value={f.txn} onChange={set('txn')} />
        <OrderTypeSelect value={f.ot} onChange={set('ot')} />
        <label>Bills from <input type="date" value={f.date_from} onChange={(e) => set('date_from')(e.target.value)} /></label>
        <label>To <input type="date" value={f.date_to} onChange={(e) => set('date_to')(e.target.value)} /></label>
        <input placeholder="Narrow: product, item code, vendor…" value={f.search}
               onChange={(e) => set('search')(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && run()} />
        <button className="primary" disabled={!f.category} onClick={run}>Show products</button>
        {rows.length > 0 && (
          <button onClick={() => downloadCsv(`category_prices.csv`, COLS.map((c) => ({ key: c.key, label: c.label })), rows)}>
            Download CSV
          </button>
        )}
      </div>

      {!applied && (
        <div className="msg">Choose a category (its sub-categories are included) and click <b>Show products</b>.
          You get every product bought in that period with each vendor, their prices and who is cheapest.</div>
      )}

      {applied && (
        <>
          <PeriodNote from={applied.date_from} to={applied.date_to} gl={applied.gl ? applied.gl.split(',') : []} />
          <div className="kpis">
            <Kpi label="Products" value={num(k.products, 0)} loading={res.loading} />
            <Kpi label="Vendors" value={num(k.vendors, 0)} loading={res.loading} />
            <Kpi label="Products with 2+ vendors" value={num(k.multi, 0)} loading={res.loading}
                 sub="where you can compare prices" />
            <Kpi label="Spend in period" value={compact(k.spend)} loading={res.loading} />
          </div>
          <Status loading={res.loading} error={res.error} />
          {!res.loading && (
            <DataTable columns={COLS} rows={shown} empty="No vendor bills for this category in the period"
                       onRowClick={(r) => onPick({ id: r.product_id, name: r.product, item_code: r.item_code })} />
          )}
          <div className="muted small">Prices are from posted vendor bills, INR per unit after discount.
            Click a row for that product's full price history.</div>
        </>
      )}
    </>
  )
}
