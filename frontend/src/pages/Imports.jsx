import React, { useState } from 'react'
import { get, num, compact } from '../api.js'
import {
  useSteps, Kpi, Status, DataTable, CompanySelect, useCompany, PeriodSelect, periodRange, PeriodNote, downloadCsv, More,
} from '../components/ui.jsx'
import { ChartCard, BarRank } from '../components/Charts.jsx'

const d10 = (v) => (v ? String(v).slice(0, 10) : '—')
const pct = (v) => (v === null || v === undefined ? '—' : `${num(v, 2)}%`)

const POCOLS = [
  { key: 'boe_date', label: 'BOE date', render: d10 },
  { key: 'boe_reference', label: 'BOE no.' },
  { key: 'po_number', label: 'PO' },
  { key: 'supplier', label: 'Supplier' },
  { key: 'currency', label: 'Ccy' },
  { key: 'exchange_rate', label: 'Customs rate', type: 'num' },
  { key: 'fob', label: 'FOB (ccy)', type: 'num' },
  { key: 'cif', label: 'CIF (ccy)', type: 'num' },
  { key: 'assessable', label: 'Assessable (INR)', type: 'money' },
  { key: 'bcd', label: 'BCD', type: 'money' },
  { key: 'sws', label: 'SWS', type: 'money' },
  { key: 'cess', label: 'Cess', type: 'money' },
  { key: 'misc', label: 'Misc', type: 'money' },
  { key: 'other_landed', label: 'Other landed (freight/clearing)', type: 'money' },
  { key: 'landed', label: 'Landed cost', type: 'money' },
  { key: 'uplift_pct', label: 'On top of assessable', type: 'pct', render: (v) => <b>{pct(v)}</b> },
  { key: 'igst', label: 'Import IGST (credit)', type: 'money' },
  { key: 'state', label: 'State' },
]
const SCOLS = [
  { key: 'supplier', label: 'Supplier' },
  { key: 'currency', label: 'Ccy' },
  { key: 'pos', label: 'POs', type: 'num' },
  { key: 'assessable', label: 'Assessable', type: 'money' },
  { key: 'customs_duty', label: 'Customs duty', type: 'money' },
  { key: 'landed', label: 'Landed cost', type: 'money' },
  { key: 'uplift_pct', label: 'On top of assessable', type: 'pct', render: (v) => pct(v) },
  { key: 'min_rate', label: 'Min rate', type: 'num' },
  { key: 'max_rate', label: 'Max rate', type: 'num' },
]
const PRCOLS = [
  { key: 'item_code', label: 'Item code' },
  { key: 'product', label: 'Product' },
  { key: 'suppliers', label: 'Supplier' },
  { key: 'pos', label: 'POs', type: 'num' },
  { key: 'qty', label: 'Qty', type: 'num' },
  { key: 'base_unit_inr', label: 'Price / unit (INR)', type: 'money' },
  { key: 'landed_unit_inr', label: 'Landed cost / unit', type: 'money', render: (v) => <b>₹ {num(v, 2)}</b> },
  { key: 'uplift_pct', label: 'Uplift', type: 'pct', render: (v) => pct(v) },
  { key: 'last_import', label: 'Last import', render: d10 },
]

function Section({ r, title, note, cols, file }) {
  return (
    <div className="card">
      <div className="card-head">
        <h3>{title} {note && <span className="muted">{note}</span>}</h3>
        <button onClick={() => downloadCsv(file, cols, r.data || [])}>Download CSV</button>
      </div>
      <Status loading={r.loading} error={r.error} />
      {!r.loading && <DataTable columns={cols} rows={r.data} empty="No import duty entries in this period" />}
    </div>
  )
}

export default function Imports({ companies }) {
  const [company, setCompany] = useCompany()   // shared by every page
  const [period, setPeriod] = useState('fy')
  const [custom, setCustom] = useState({})
  const pr = periodRange(period, custom)
  const range = { company_id: company, date_from: pr.from, date_to: pr.to }
  const step = useSteps([
    ['summary', () => get('/imports/summary', range)],
    ['products', () => get('/imports/products', range)],
    ['suppliers', () => get('/imports/suppliers', range)],
    ['pos', () => get('/imports/pos', range)],
  ], [company, pr.from, pr.to])
  const s = step('summary'), sd = s.data

  return (
    <section>
      <div className="filters">
        <CompanySelect companies={companies} value={company} onChange={setCompany} />
        <PeriodSelect period={period} setPeriod={setPeriod} custom={custom} setCustom={setCustom} />
      </div>
      <PeriodNote from={pr.from} to={pr.to} label="Bills of Entry" />
      {sd && sd.available === false && <div className="msg">The import duty table was not found in silver.</div>}
      <div className="kpis">
        <Kpi label="Import POs" value={num(sd?.pos, 0)} loading={s.loading} sub={sd ? `${num(sd.suppliers, 0)} suppliers` : ''} />
        <Kpi label="Assessable value" value={compact(sd?.assessable)} loading={s.loading} />
        <Kpi label="Customs duty (BCD+SWS+cess)" value={compact(sd?.customs_duty)} loading={s.loading} />
        <Kpi label="Landed cost" value={compact(sd?.landed)} loading={s.loading}
             sub={sd ? `${pct(sd.uplift_pct)} on top of assessable` : ''} />
        <Kpi label="Import IGST" value={compact(sd?.igst)} loading={s.loading} sub="claimable credit – not in cost" />
      </div>
      <div className="charts two">
        <ChartCard title="How the landed cost builds up" loading={s.loading} error={s.error} empty={!sd?.pos} height={200}>
          <BarRank name="INR" data={sd ? [
            { name: 'Assessable value', value: sd.assessable },
            { name: 'Customs duty', value: sd.customs_duty },
            { name: 'Misc + other landed', value: sd.other_costs },
            { name: 'Import IGST (credit)', value: sd.igst },
          ] : []} />
        </ChartCard>
        <ChartCard title="Landed cost by supplier" loading={step('suppliers').loading} error={step('suppliers').error}
                   empty={!(step('suppliers').data || []).length} height={200}>
          <BarRank data={(step('suppliers').data || []).slice(0, 10)} nameKey="supplier" valueKey="landed" name="Landed cost" color="#6250d6" />
        </ChartCard>
      </div>
      <Section r={step('products')} title="Landed cost per unit" note="(PO price × customs rate, plus the PO's duty & charges %)"
               cols={PRCOLS} file="import_landed_cost_per_unit.csv" />
      <More title="By supplier and duty breakup per Bill of Entry">
      <Section r={step('suppliers')} title="By supplier" cols={SCOLS} file="import_by_supplier.csv" />
      <Section r={step('pos')} title="Duty breakup per import (Bill of Entry)" cols={POCOLS} file="import_duty_breakup.csv" />
      </More>
      <div className="muted small">Landed cost = assessable value + BCD + SWS + cess + misc + Odoo landed costs on the PO's receipts.
        Import IGST is shown separately because it is normally claimed as input credit.</div>
    </section>
  )
}
