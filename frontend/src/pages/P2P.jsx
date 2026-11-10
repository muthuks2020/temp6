import React, { useState } from 'react'
import { get, num, compact, money } from '../api.js'
import {
  useAsync, useSteps, Kpi, Status, DataTable, CompanySelect, useCompany, PeriodSelect, periodRange, PeriodNote, Picker, downloadCsv, More,
} from '../components/ui.jsx'
import { ChartCard, BarRank } from '../components/Charts.jsx'

const d10 = (v) => (v ? String(v).slice(0, 10) : '—')
const TONE = {
  'GRN pending': 'pill-bad', 'Partly received': 'pill-warn', Received: 'pill-good',
  'Not billed': 'pill-bad', 'Partly billed': 'pill-warn', Billed: 'pill-good',
  'No bill': '', Unpaid: 'pill-bad', 'Partly paid': 'pill-warn', Paid: 'pill-good',
}
const pill = (v) => (v ? <span className={`pill ${TONE[v] || ''}`}>{v}</span> : '—')
const amt = (v) => (v > 0.5 ? <b className="up">{money(v)}</b> : '—')

const STAGES = [
  ['all', 'All POs'], ['grn_pending', 'GRN pending'], ['to_bill', 'Received, not billed'],
  ['billed_not_received', 'Billed, not received'], ['unpaid', 'Payment pending'], ['overdue', 'Payment overdue'],
  ['paid_not_received', 'Paid, not fully received'], ['closed', 'Fully closed'],
]
const DIMS = [['company', 'Company'], ['vendor', 'Vendor'], ['order_type', 'Order type'], ['month', 'PO month'],
  ['status', 'Status combination']]

const COMBO = [
  { key: 'grn_status', label: 'GRN', render: pill },
  { key: 'bill_status', label: 'Bill', render: pill },
  { key: 'pay_status', label: 'Payment', render: pill },
  { key: 'pos', label: 'POs', type: 'num' },
  { key: 'ordered_value', label: 'PO value', type: 'money' },
  { key: 'grn_pending_value', label: 'Not yet received', type: 'money' },
  { key: 'to_bill_value', label: 'Received, not billed', type: 'money' },
  { key: 'billed_not_received_value', label: 'Billed, not received', type: 'money' },
  { key: 'payment_pending', label: 'Unpaid bills', type: 'money' },
  { key: 'oldest_days', label: 'Oldest PO (days)', type: 'num' },
]
const pivotCols = (label) => [
  { key: 'name', label },
  { key: 'pos', label: 'POs', type: 'num' },
  { key: 'ordered_value', label: 'PO value', type: 'money' },
  { key: 'received_value', label: 'Received', type: 'money' },
  { key: 'billed_value', label: 'Billed', type: 'money' },
  { key: 'grn_pending_pos', label: 'GRN pending POs', type: 'num' },
  { key: 'grn_pending_value', label: 'GRN pending value', type: 'money' },
  { key: 'to_bill_pos', label: 'To bill POs', type: 'num' },
  { key: 'to_bill_value', label: 'Received, not billed', type: 'money' },
  { key: 'billed_not_received_value', label: 'Billed, not received', type: 'money' },
  { key: 'unpaid_bills', label: 'Unpaid bills', type: 'num' },
  { key: 'payment_pending', label: 'Payment pending', type: 'money' },
  { key: 'overdue_amount', label: 'Overdue', type: 'money', render: amt },
  { key: 'closed_pos', label: 'Fully closed POs', type: 'num' },
]
const LIST = [
  { key: 'po_number', label: 'PO' },
  { key: 'po_date', label: 'PO date', render: d10 },
  { key: 'age_days', label: 'Age (days)', type: 'num' },
  { key: 'vendor', label: 'Vendor' },
  { key: 'grn_status', label: 'GRN', render: pill },
  { key: 'bill_status', label: 'Bill', render: pill },
  { key: 'pay_status', label: 'Payment', render: pill },
  { key: 'ordered_value', label: 'PO value', type: 'money' },
  { key: 'grn_pending_value', label: 'Not yet received', type: 'money' },
  { key: 'to_bill_value', label: 'Received, not billed', type: 'money' },
  { key: 'billed_not_received_value', label: 'Billed, not received', type: 'money', render: amt },
  { key: 'bill_balance', label: 'Unpaid on linked bills', type: 'money' },
  { key: 'oldest_due', label: 'Oldest due date', render: d10 },
  { key: 'grns', label: 'GRN / receipts' },
  { key: 'last_grn', label: 'Last GRN', render: d10 },
  { key: 'bills', label: 'Bills / debit notes' },
  { key: 'last_bill', label: 'Last bill', render: d10 },
  { key: 'payments', label: 'Payments' },
  { key: 'last_payment', label: 'Last payment', render: d10 },
  { key: 'order_type', label: 'Order type' },
  { key: 'company', label: 'Company' },
]

export default function P2P({ companies }) {
  const [company, setCompany] = useCompany()   // shared by every page
  const [vendor, setVendor] = useState(null)
  const [period, setPeriod] = useState('fy')
  const [custom, setCustom] = useState({})
  const [by, setBy] = useState('company')
  const [stage, setStage] = useState('grn_pending')
  const [combo, setCombo] = useState(null)
  const pr = periodRange(period, custom)
  const range = { company_id: company, vendor_id: vendor?.id, date_from: pr.from, date_to: pr.to }
  const step = useSteps([
    ['summary', () => get('/p2p/summary', range)],
    ['combos', () => get('/p2p/combos', range)],
  ], [company, vendor?.id, pr.from, pr.to])
  const pivot = useAsync(() => get('/p2p/pivot', { ...range, by }), [company, vendor?.id, pr.from, pr.to, by])
  const list = useAsync(() => get('/p2p/list', combo
    ? { ...range, stage: 'all', grn: combo.grn_status, bill: combo.bill_status, pay: combo.pay_status }
    : { ...range, stage }), [company, vendor?.id, pr.from, pr.to, stage, combo && JSON.stringify(combo)])
  const s = step('summary'), sd = s.data
  const dimLabel = DIMS.find((d) => d[0] === by)[1]

  return (
    <section>
      <div className="filters">
        <CompanySelect companies={companies} value={company} onChange={setCompany} />
        <Picker endpoint="/vendors" value={vendor} onChange={setVendor} placeholder="Vendor…" />
        <PeriodSelect period={period} setPeriod={setPeriod} custom={custom} setCustom={setCustom} />
      </div>
      <PeriodNote from={pr.from} to={pr.to} label="POs" />

      <div className="kpis">
        <Kpi label="POs" value={num(sd?.pos, 0)} loading={s.loading} sub={sd ? `${compact(sd.ordered_value)} ordered` : ''} />
        <Kpi label="GRN pending" value={compact(sd?.grn_pending_value)} loading={s.loading}
             sub={sd ? `${num(sd.grn_pending_pos, 0)} POs not fully received` : ''} />
        <Kpi label="Received, not billed" value={compact(sd?.to_bill_value)} loading={s.loading}
             sub={sd ? `${num(sd.to_bill_pos, 0)} POs – bills to come` : ''} />
        <Kpi label="Payment pending" value={compact(sd?.payment_pending)} loading={s.loading}
             sub={sd ? `${num(sd.unpaid_bills, 0)} bills · ${compact(sd.overdue_amount)} overdue` : ''} />
        <Kpi label="Billed, not received" value={compact(sd?.billed_not_received_value)} loading={s.loading}
             sub={sd ? `${num(sd.billed_not_received_pos, 0)} POs – check` : ''} />
      </div>

      <div className="charts two">
        <ChartCard title="Order to payment" note="(value at each step)" loading={s.loading} error={s.error} empty={!sd?.pos}
                   height={220}>
          <BarRank name="INR" data={sd ? [
            { name: 'Ordered', value: sd.ordered_value }, { name: 'Received (GRN)', value: sd.received_value },
            { name: 'Billed', value: sd.billed_value }, { name: 'Bills still unpaid', value: sd.payment_pending },
          ] : []} />
        </ChartCard>
        <ChartCard title="POs by status" loading={step('combos').loading} error={step('combos').error}
                   empty={!(step('combos').data || []).length} height={220}>
          <BarRank data={(step('combos').data || []).map((r) => ({ name: `${r.grn_status} · ${r.bill_status} · ${r.pay_status}`, value: r.pos }))}
                   name="POs" color="#6250d6" format={(v) => `${v} POs`} axisFormat={(v) => String(v)} />
        </ChartCard>
      </div>

      <div className="card">
        <div className="card-head">
          <h3>PO list with every reference – PO → GRN → bill → payment</h3>
          <button onClick={() => downloadCsv(`po_to_payment_${combo ? 'selected' : stage}.csv`, LIST, list.data || [])}>Download CSV</button>
        </div>
        <div className="subtabs">
          {STAGES.map(([k, l]) => (
            <button key={k} className={!combo && stage === k ? 'sub active' : 'sub'}
                    onClick={() => { setCombo(null); setStage(k) }}>{l}</button>
          ))}
          {combo && <button className="sub active" onClick={() => setCombo(null)}>
            {combo.grn_status} · {combo.bill_status} · {combo.pay_status} ✕</button>}
        </div>
        <Status loading={list.loading} error={list.error} />
        {!list.loading && <DataTable columns={LIST} rows={list.data} empty="No POs in this stage" />}
      </div>

      <More title="Every status combination and the consolidated pivot">
      <div className="card">
        <div className="card-head">
          <h3>Every status combination <span className="muted small">– click a row to list those POs</span></h3>
          <button onClick={() => downloadCsv('po_status_combinations.csv', COMBO, step('combos').data || [])}>Download CSV</button>
        </div>
        <Status loading={step('combos').loading} error={step('combos').error} />
        {!step('combos').loading && <DataTable columns={COMBO} rows={step('combos').data} onRowClick={(r) => setCombo(r)} />}
      </div>

      <div className="card">
        <div className="card-head">
          <h3>Consolidated report <span className="muted small">– pivot by</span></h3>
          <div className="inline">
            <select value={by} onChange={(e) => setBy(e.target.value)}>
              {DIMS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
            <button onClick={() => downloadCsv(`po_to_payment_by_${by}.csv`, pivotCols(dimLabel), pivot.data || [])}>Download CSV</button>
          </div>
        </div>
        <Status loading={pivot.loading} error={pivot.error} />
        {!pivot.loading && <DataTable columns={pivotCols(dimLabel)} rows={pivot.data} />}
        <div className="muted small">Each unpaid bill is counted once per row, even when it covers several POs.</div>
      </div>

      </More>
      <div className="muted small">
        GRN = done receipts from the vendor against the PO lines. Received / billed quantities are Odoo's own figures on the
        PO line. Payment status and unpaid amounts come from the posted vendor bills linked to the PO lines; payments are the
        entries reconciled with those bills. "Unpaid on linked bills" is the whole bill's balance (a bill can cover more than one PO).
      </div>
    </section>
  )
}
