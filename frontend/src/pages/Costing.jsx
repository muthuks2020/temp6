import React, { useState } from 'react'
import { get, num, compact, money } from '../api.js'
import { useAsync, Kpi, Status, DataTable, Picker, CompanySelect, useCompany, downloadCsv } from '../components/ui.jsx'

const SUM_COLS = [
  { key: 'company', label: 'Company' },
  { key: 'on_hand_qty', label: 'On hand', type: 'num' },
  { key: 'wac_unit', label: 'WAC / unit', type: 'money' },
  { key: 'wac_value', label: 'WAC value', type: 'money' },
  { key: 'fifo_unit', label: 'FIFO / unit', type: 'money' },
  { key: 'fifo_value', label: 'FIFO value', type: 'money' },
  { key: 'batch_value', label: 'Batch value', type: 'money' },
  { key: 'last_price', label: 'Last purchase price', type: 'money' },
  { key: 'last_vendor', label: 'Last vendor' },
  { key: 'fifo_minus_wac', label: 'FIFO − WAC', type: 'money',
    render: (v) => <span className={v > 0 ? 'up' : v < 0 ? 'down' : ''}>{money(v)}</span> },
]
const d10 = (v) => (v ? String(v).slice(0, 10) : '—')
const LOT_COLS = [
  { key: 'company', label: 'Company' },
  { key: 'lot_number', label: 'Batch' },
  { key: 'lot_expiry', label: 'Expiry', render: d10 },
  { key: 'vendors', label: 'Vendor' },
  { key: 'po_numbers', label: 'PO' },
  { key: 'first_receipt_date', label: 'Received on', render: d10 },
  { key: 'received_qty', label: 'Received', type: 'num' },
  { key: 'returned_qty', label: 'Returned', type: 'num' },
  { key: 'on_hand_qty', label: 'On hand', type: 'num' },
  { key: 'batch_unit_cost', label: 'Batch cost / unit', type: 'money' },
  { key: 'batch_stock_value', label: 'Stock value', type: 'money' },
]
const FIFO_COLS = [
  { key: 'company', label: 'Company' },
  { key: 'lot_number', label: 'Batch' },
  { key: 'receipt_date', label: 'Received on', render: d10 },
  { key: 'vendor', label: 'Vendor' },
  { key: 'po_number', label: 'PO' },
  { key: 'remaining_qty', label: 'Qty left', type: 'num' },
  { key: 'unit_cost', label: 'Cost / unit', type: 'money' },
  { key: 'fifo_value', label: 'FIFO value', type: 'money' },
]
const CARD_COLS = [
  { key: 'move_datetime', label: 'Date', type: 'datetime' },
  { key: 'company', label: 'Company' },
  { key: 'reference', label: 'Reference' },
  { key: 'movement_type', label: 'Type' },
  { key: 'lot_number', label: 'Batch' },
  { key: 'signed_qty', label: 'Qty ±', type: 'num' },
  { key: 'in_unit_cost', label: 'In cost / unit', type: 'money' },
  { key: 'issue_cost', label: 'Issue cost', type: 'money' },
  { key: 'running_qty', label: 'Balance qty', type: 'num' },
  { key: 'running_value', label: 'Balance value', type: 'money' },
  { key: 'avg_cost', label: 'Avg cost', type: 'money' },
]
const SUB = [
  { id: 'summary', label: 'WAC vs FIFO vs Batch' },
  { id: 'lots', label: 'Batch costing' },
  { id: 'fifo', label: 'FIFO layers in stock' },
  { id: 'card', label: 'Stock card (WAC)' },
]
const sum = (rows, k) => (rows || []).reduce((a, r) => a + (r[k] || 0), 0)

export default function Costing({ companies, openProduct, product, setProduct }) {
  const [company, setCompany] = useCompany()   // shared by every page
  const [asOf, setAsOf] = useState(new Date().toISOString().slice(0, 10))
  const [sub, setSub] = useState('summary')

  const res = useAsync(() => product
    ? get(`/costing/product/${product.id}`, { company_id: company, as_of: asOf })
    : Promise.resolve(null), [product?.id, company, asOf])
  const d = res.data

  const table = { summary: [SUM_COLS, d?.summary], lots: [LOT_COLS, d?.lots],
                  fifo: [FIFO_COLS, d?.fifo_layers], card: [CARD_COLS, d?.card] }[sub]

  return (
    <section>
      <div className="filters">
        <Picker endpoint="/products" value={product} onChange={setProduct} placeholder="Pick a product / item code…" />
        <CompanySelect companies={companies} value={company} onChange={setCompany} />
        <label>As on <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} /></label>
      </div>

      {!product && (
        <div className="msg">Pick a product. Weighted average, FIFO and batch cost are calculated live from the
          stock moves and purchase prices in the silver tables (nothing is stored).</div>
      )}

      {product && (
        <>
          <div className="card-head">
            <h2>{product.item_code ? `[${product.item_code}] ` : ''}{product.name}</h2>
            <button onClick={() => openProduct(product)}>Purchase price analysis →</button>
          </div>
          <Status loading={res.loading} error={res.error} />
          {d && (
            <div className="kpis">
              <Kpi label="On hand" value={num(sum(d.summary, 'on_hand_qty'), 2)} sub={`as on ${d.as_of}`} />
              <Kpi label="Stock value – WAC" value={compact(sum(d.summary, 'wac_value'))} />
              <Kpi label="Stock value – FIFO" value={compact(sum(d.summary, 'fifo_value'))} />
              <Kpi label="Stock value – Batch" value={compact(sum(d.summary, 'batch_value'))} />
              <Kpi label="FIFO − WAC" value={compact(sum(d.summary, 'fifo_minus_wac'))} sub="positive = FIFO higher" />
            </div>
          )}
          <div className="subtabs">
            {SUB.map((s) => (
              <button key={s.id} className={sub === s.id ? 'sub active' : 'sub'} onClick={() => setSub(s.id)}>{s.label}</button>
            ))}
          </div>
          <div className="card-head right">
            <button onClick={() => downloadCsv(`costing_${sub}.csv`, table[0], table[1] || [])}>Download CSV</button>
          </div>
          <DataTable columns={table[0]} rows={table[1]} empty="No stock movements for this product" />
        </>
      )}
    </section>
  )
}
