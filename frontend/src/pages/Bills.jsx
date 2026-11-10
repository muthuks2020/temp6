import React, { useState } from 'react'
import { get, qs, num, compact } from '../api.js'
import { useAsync, useTiles, monthStart, PeriodNote, TxnSelect, Kpi, Status, DataTable, Picker, CompanySelect, useCompany } from '../components/ui.jsx'

const PAY_LABEL = { paid: ['Paid', 'pill-good'], in_payment: ['In payment', 'pill-good'], partial: ['Partly paid', 'pill-warn'],
  not_paid: ['Not paid', 'pill-bad'], reversed: ['Reversed', ''], invoicing_legacy: ['Legacy', ''], blocked: ['Blocked', 'pill-bad'] }
const payPill = (v) => { const [l, c] = PAY_LABEL[v] || [v || '—', '']; return <span className={`pill ${c}`}>{l}</span> }

const COLS = [
  { key: 'bill_number', label: 'Bill no.' },
  { key: 'bill_date', label: 'Bill date' },
  { key: 'doc_type', label: 'Type' },
  { key: 'company', label: 'Company' },
  { key: 'journal', label: 'Journal' },
  { key: 'vendor', label: 'Vendor' },
  { key: 'vendor_gstin', label: 'GSTIN' },
  { key: 'vendor_bill_ref', label: 'Vendor ref' },
  { key: 'vendor_state', label: 'Vendor state' },
  { key: 'place_of_supply', label: 'Place of supply' },
  { key: 'gst_treatment', label: 'GST treatment' },
  { key: 'txn_class', label: 'Transaction' },
  { key: 'order_type', label: 'Order type' },
  { key: 'company_gstin', label: 'Our GSTIN' },
  { key: 'po_numbers', label: 'PO' },
  { key: 'currency', label: 'Ccy' },
  { key: 'taxable_value', label: 'Taxable (INR)', type: 'money' },
  { key: 'cgst', label: 'CGST', type: 'money' },
  { key: 'sgst', label: 'SGST', type: 'money' },
  { key: 'igst', label: 'IGST', type: 'money' },
  { key: 'cess', label: 'Cess', type: 'money' },
  { key: 'tds', label: 'TDS', type: 'money' },
  { key: 'invoice_total', label: 'Total', type: 'money' },
  { key: 'due_date', label: 'Due date' },
  { key: 'payment_state', label: 'Payment', render: payPill },
]

const EMPTY = { company_id: '', vendor: null, doc_type: '', date_from: monthStart(), date_to: '', search: '', txn: '', pay: '' }

export default function Bills() {
  const [gco, setGco] = useCompany()
  const [f, setF] = useState({ ...EMPTY, company_id: gco || '' })
  const [applied, setApplied] = useState({ ...EMPTY, company_id: gco || '' })
  const [page, setPage] = useState(1)
  const [sort, setSort] = useState({ sort: 'bill_date', order: 'desc' })
  const pageSize = 50
  const companies = useAsync(() => get('/bills/companies'), [])

  const params = {
    company_id: applied.company_id, vendor_id: applied.vendor?.id, doc_type: applied.doc_type,
    date_from: applied.date_from, date_to: applied.date_to, search: applied.search, txn: applied.txn, pay: applied.pay,
  }
  // table first, then the 6 tiles one after another
  const TILES = ['bills', 'taxable', 'gst_cs', 'igst', 'tds', 'total']
  const step = useTiles(() => get('/bills', { ...params, page, page_size: pageSize, ...sort }),
    TILES, (t) => get(`/bills/tile/${t}`, params), params, [page, sort.sort, sort.order])
  const res = step('rows')
  const tile = (t) => step(t)
  const count = tile('bills').data
  const set = (k) => (v) => setF((x) => ({ ...x, [k]: v }))
  const apply = () => { setApplied(f); setPage(1) }

  return (
    <section>
      <div className="filters">
        <CompanySelect companies={companies.data} value={f.company_id}
                       onChange={(v) => { setGco(v); setF((x) => ({ ...x, company_id: v })); setApplied((x) => ({ ...x, company_id: v })); setPage(1) }} />
        <Picker endpoint="/bills/vendors" value={f.vendor} onChange={set('vendor')} placeholder="Vendor / GSTIN…" />
        <select value={f.doc_type} onChange={(e) => set('doc_type')(e.target.value)}>
          <option value="">Bills + debit notes</option>
          <option value="bill">Bills only</option>
          <option value="debit_note">Debit notes only</option>
        </select>
        <TxnSelect value={f.txn} onChange={(v) => set('txn')(v)} />
        <select value={f.pay} onChange={(e) => set('pay')(e.target.value)}>
          <option value="">Paid + not paid</option>
          <option value="paid">Paid</option>
          <option value="open">Not fully paid (not paid + partly)</option>
          <option value="not_paid">Not paid</option>
          <option value="partial">Partly paid</option>
          <option value="reversed">Reversed</option>
        </select>
        <label>From <input type="date" value={f.date_from} onChange={(e) => set('date_from')(e.target.value)} /></label>
        <label>To <input type="date" value={f.date_to} onChange={(e) => set('date_to')(e.target.value)} /></label>
        <input placeholder="Bill no, vendor ref, PO, GSTIN…" value={f.search}
               onChange={(e) => set('search')(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && apply()} />
        <button className="primary" onClick={apply}>Apply</button>
        <button onClick={() => { const e = { ...EMPTY, company_id: gco || '' }; setF(e); setApplied(e); setPage(1) }}>Reset</button>
        <a className="btn" href={`/api/bills/export?${qs(params)}`}>Export Excel</a>
      </div>

      <PeriodNote from={applied.date_from} to={applied.date_to}
                  extra={<span className="muted"> (GL filter not used here – one row per whole bill)</span>} />
      <div className="kpis">
        <Kpi label="Bills" value={num(count?.bills, 0)} sub={`${num(count?.vendors, 0)} vendors`} {...tile('bills')} />
        <Kpi label="Taxable value" value={compact(tile('taxable').data?.taxable)} {...tile('taxable')} />
        <Kpi label="CGST + SGST" {...tile('gst_cs')} value={compact((tile('gst_cs').data?.cgst || 0) + (tile('gst_cs').data?.sgst || 0))}
             sub={`CGST ${compact(tile('gst_cs').data?.cgst)} · SGST ${compact(tile('gst_cs').data?.sgst)}`} />
        <Kpi label="IGST" value={compact(tile('igst').data?.igst)} sub={`Cess ${compact(tile('igst').data?.cess)}`} {...tile('igst')} />
        <Kpi label="TDS" value={compact(tile('tds').data?.tds)} {...tile('tds')} />
        <Kpi label="Invoice total" value={compact(tile('total').data?.total)} {...tile('total')} />
      </div>

      <Status loading={res.loading} error={res.error} />
      <DataTable columns={COLS} rows={res.data?.rows} sort={sort.sort} order={sort.order}
                 onSort={(k, d) => setSort({ sort: k, order: d })} />
      <div className="pager">
        <button disabled={page === 1} onClick={() => setPage(page - 1)}>‹ Prev</button>
        <span>Page {page}{count ? ` of ${Math.max(1, Math.ceil((count.bills || 0) / pageSize))}` : ''}</span>
        <button disabled={(res.data?.rows || []).length < pageSize} onClick={() => setPage(page + 1)}>Next ›</button>
        <span className="muted">Live from the silver tables · debit notes show as negative</span>
      </div>
    </section>
  )
}
