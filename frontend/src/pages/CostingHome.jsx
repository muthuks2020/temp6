import React, { useMemo, useState } from 'react'
import { get, num, compact, money } from '../api.js'
import {
  useAsync, Kpi, Status, DataTable, CompanySelect, useCompany, PeriodSelect, periodRange, downloadCsv, GlSelect, PeriodNote, SourceSelect, TxnSelect, OrderTypeSelect,
} from '../components/ui.jsx'
import Costing from './Costing.jsx'

const fyLabel = (y) => `FY ${String(y).slice(2)}-${String(y + 1).slice(2)}`

/** Costing tab: All materials (by category) -> one category's products -> one product */
export default function CostingHome({ companies, openProduct }) {
  const [level, setLevel] = useState('all')          // all | category | product
  const [category, setCategory] = useState(null)
  const [product, setProduct] = useState(null)
  const [company, setCompany] = useCompany()   // shared by every page
  const [period, setPeriod] = useState('5fy')        // default: last 5 fiscal years
  const [custom, setCustom] = useState({})
  const [search, setSearch] = useState('')
  const [gl, setGl] = useState([])
  const [source, setSource] = useState('')
  const [txn, setTxn] = useState('External')
  const [ot, setOt] = useState([])
  const pr = periodRange(period, custom)
  const range = { company_id: company, date_from: pr.from, date_to: pr.to, gl: gl.join(','), source, txn, ot: ot.join(',') }

  const openCategory = (c) => { setCategory(c); setSearch(''); setLevel('category') }
  const openProductCost = (p) => { setProduct(p); setLevel('product') }

  return (
    <section>
      <div className="crumbs">
        <button className={level === 'all' ? 'crumb active' : 'crumb'} onClick={() => setLevel('all')}>All materials</button>
        {category && <>
          <span>›</span>
          <button className={level === 'category' ? 'crumb active' : 'crumb'} onClick={() => setLevel('category')}>{category}</button>
        </>}
        {level === 'product' && <>
          <span>›</span><span className="crumb active">{product?.name || 'Product'}</span>
        </>}
        {level !== 'product' && (
          <button className="crumb-right" onClick={() => { setProduct(null); setLevel('product') }}>Find a product →</button>
        )}
      </div>

      {level !== 'product' && (
        <div className="filters">
          <CompanySelect companies={companies} value={company} onChange={setCompany} />
          <PeriodSelect period={period} setPeriod={setPeriod} custom={custom} setCustom={setCustom} />
          <GlSelect value={gl} onChange={setGl} />
          <SourceSelect value={source} onChange={setSource} />
          <TxnSelect value={txn} onChange={setTxn} />
          <OrderTypeSelect value={ot} onChange={setOt} />
          <input placeholder={level === 'all' ? 'Filter categories…' : 'Filter products…'} value={search}
                 onChange={(e) => setSearch(e.target.value)} />
        </div>
      )}

      {level !== 'product' && <PeriodNote from={pr.from} to={pr.to} gl={gl} extra={source ? <> · <b>{source} only</b></> : null} />}
      {level === 'all' && <AllCategories range={range} label={pr.label} search={search} onOpen={openCategory} />}
      {level === 'category' && (
        <CategoryProducts category={category} range={range} label={pr.label} search={search} onOpen={openProductCost} />
      )}
      {level === 'product' && (
        <Costing companies={companies} openProduct={openProduct} product={product} setProduct={setProduct} />
      )}
    </section>
  )
}

function fyList(byFy) {
  return [...new Set((byFy || []).map((r) => r.fy))].sort()
}

function AllCategories({ range, label, search, onOpen }) {
  const res = useAsync(() => get('/costing/categories', range), [JSON.stringify(range)])
  const fys = fyList(res.data?.by_fy)

  const rows = useMemo(() => {
    const idx = {}
    ;(res.data?.by_fy || []).forEach((r) => { idx[`${r.category}|${r.fy}`] = r })
    return (res.data?.totals || [])
      .filter((t) => !search || t.category.toLowerCase().includes(search.toLowerCase()))
      .map((t) => {
        const o = { ...t }
        fys.forEach((y) => {
          const c = idx[`${t.category}|${y}`]
          o[`p_${y}`] = c?.products || 0
          o[`s_${y}`] = c?.spend || 0
        })
        return o
      })
  }, [res.data, search])

  const tot = (k) => rows.reduce((a, r) => a + (r[k] || 0), 0)
  const cols = [
    { key: 'category', label: 'Category', render: (v) => <b>{v}</b> },
    { key: 'products', label: 'Products bought', type: 'num' },
    { key: 'vendors', label: 'Vendors', type: 'num' },
    { key: 'bills', label: 'Bills', type: 'num' },
    { key: 'spend', label: 'Spend', type: 'money', render: (v) => compact(v) },
    ...fys.map((y) => ({
      key: `p_${y}`, label: fyLabel(y), type: 'num',
      render: (v, r) => (v ? <span>{num(v, 0)}<span className="cell-sub">{compact(r[`s_${y}`])}</span></span> : '—'),
    })),
  ]

  return (
    <>
      <div className="kpis">
        <Kpi label={`Products purchased – ${label}`} value={num(tot('products'), 0)} loading={res.loading} />
        <Kpi label="Categories" value={num(rows.length, 0)} loading={res.loading} />
        <Kpi label="Spend" value={compact(tot('spend'))} loading={res.loading} />
        {fys.length > 0 && (
          <Kpi label={`Products bought in ${fyLabel(fys[fys.length - 1])}`} loading={res.loading}
               value={num(rows.reduce((a, r) => a + (r[`p_${fys[fys.length - 1]}`] || 0), 0), 0)} />
        )}
      </div>
      <Status loading={res.loading} error={res.error} />
      {!res.loading && (
        <>
          <div className="card-head">
            <span className="muted small">Each fiscal-year column: number of different products bought (spend below).
              Click a category for its products' costing.</span>
            <button onClick={() => downloadCsv('costing_by_category.csv', cols, rows)}>Download CSV</button>
          </div>
          <DataTable columns={cols} rows={rows} onRowClick={(r) => onOpen(r.category)}
                     empty="No purchases in this period" />
        </>
      )}
    </>
  )
}

function CategoryProducts({ category, range, label, search, onOpen }) {
  const res = useAsync(() => get('/costing/category-products', { ...range, category }),
    [category, JSON.stringify(range)])
  const fys = fyList(res.data?.by_fy)

  const rows = useMemo(() => {
    const idx = {}
    ;(res.data?.by_fy || []).forEach((r) => { idx[`${r.product_id}|${r.fy}`] = r })
    return (res.data?.products || [])
      .filter((p) => !search || `${p.item_code || ''} ${p.product}`.toLowerCase().includes(search.toLowerCase()))
      .map((p) => {
        const o = { ...p }
        const seen = []
        fys.forEach((y) => {
          const c = idx[`${p.product_id}|${y}`]
          o[`c_${y}`] = c?.avg_cost ?? null
          o[`q_${y}`] = c?.qty ?? null
          if (c?.avg_cost) seen.push(c.avg_cost)
        })
        o.change = seen.length > 1 ? ((seen[seen.length - 1] - seen[0]) / seen[0]) * 100 : null
        return o
      })
  }, [res.data, search])

  const cols = [
    { key: 'product', label: 'Product', render: (v, r) => <b>{r.item_code ? `[${r.item_code}] ` : ''}{v}</b> },
    { key: 'uoms', label: 'UoM' },
    { key: 'vendors', label: 'Vendors', type: 'num' },
    { key: 'qty', label: 'Qty bought', type: 'num' },
    { key: 'avg_cost', label: 'Avg cost (period)', type: 'money' },
    { key: 'cost_incl_freight', label: 'Avg cost incl. linked freight', type: 'money',
      render: (v, r) => (r.freight ? <b>{money(v)}</b> : <span className="muted">{money(v)}</span>) },
    { key: 'freight', label: 'Linked freight', type: 'money' },
    { key: 'freight_per_unit', label: 'Freight / unit (on linked buys)', type: 'money',
      render: (v, r) => (v == null ? '—' : <span>{money(v)}<span className="cell-sub">{num(r.freight_pct, 2)}% of value</span></span>) },
    { key: 'freight_linked_qty_pct', label: 'Qty with linked freight', type: 'pct',
      render: (v) => (v == null ? '—' : `${num(v, 1)}%`) },
    { key: 'last_cost', label: 'Last cost', type: 'money' },
    { key: 'last_vendor', label: 'Last vendor' },
    { key: 'last_bill', label: 'Last bill' },
    { key: 'min_cost', label: 'Min', type: 'money' },
    { key: 'max_cost', label: 'Max', type: 'money' },
    ...fys.map((y) => ({
      key: `c_${y}`, label: `${fyLabel(y)} avg`, type: 'money',
      render: (v, r) => (v ? <span>{money(v)}<span className="cell-sub">{num(r[`q_${y}`], 2)} qty</span></span> : '—'),
    })),
    { key: 'change', label: 'Change first→last FY', type: 'pct',
      render: (v) => (v === null ? '—' : <span className={v > 0 ? 'up' : 'down'}>{v > 0 ? '▲' : '▼'} {num(Math.abs(v), 1)}%</span>) },
    { key: 'spend', label: 'Spend', type: 'money' },
  ]

  const spend = rows.reduce((a, r) => a + (r.spend || 0), 0)
  const up = rows.filter((r) => r.change > 0).length

  return (
    <>
      <h2>{category}</h2>
      <div className="kpis">
        <Kpi label={`Products purchased – ${label}`} value={num(rows.length, 0)} loading={res.loading} />
        <Kpi label="Spend" value={compact(spend)} loading={res.loading} />
        <Kpi label="Cost went up" value={num(up, 0)} sub="products, first vs last FY" loading={res.loading} />
        <Kpi label="Single-vendor products" value={num(rows.filter((r) => r.vendors === 1).length, 0)}
             sub="no price comparison possible" loading={res.loading} />
      </div>
      <Status loading={res.loading} error={res.error} />
      {!res.loading && (
        <>
          <div className="card-head">
            <span className="muted small">Cost = posted vendor bills, INR per unit after discount, weighted by quantity.
              Incl. freight = (material spend + freight linked exactly: same bill, PO line on the freight bill, or Odoo landed
              cost) ÷ qty bought. Freight found only by reference text is NOT added (see Freight tab).
              Click a product for weighted-average, FIFO and batch costing.</span>
            <button onClick={() => downloadCsv(`costing_${category}.csv`, cols, rows)}>Download CSV</button>
          </div>
          <DataTable columns={cols} rows={rows}
                     onRowClick={(r) => onOpen({ id: r.product_id, name: r.product, item_code: r.item_code })}
                     empty="No purchases for this category in the period" />
        </>
      )}
    </>
  )
}
