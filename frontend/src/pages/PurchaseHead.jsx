import React, { useState } from 'react'
import { get, num, compact, money } from '../api.js'
import { useSteps, Kpi, Status, DataTable, CompanySelect, useCompany, PeriodSelect, periodRange, TxnSelect, downloadCsv, More } from '../components/ui.jsx'
import { ChartCard, BarRank } from '../components/Charts.jsx'

const pctv = (v) => (v == null ? '—' : `${num(v, 1)}%`)
const amt = (v) => (v > 0.5 ? <b className="up">{compact(v)}</b> : '—')

const SCORE = [
  { key: 'vendor', label: 'Vendor' },
  { key: 'source', label: 'Local / Import' },
  { key: 'spend', label: 'Spend', type: 'money', render: (v) => <b>{compact(v)}</b> },
  { key: 'share_pct', label: 'Share', type: 'num', render: pctv },
  { key: 'items', label: 'Items', type: 'num' },
  { key: 'price_vs_best_pct', label: 'Price vs cheapest', type: 'num',
    render: (v) => (v == null ? '—' : <span className={`pill ${v >= 10 ? 'pill-bad' : v >= 5 ? 'pill-warn' : ''}`}>{num(v, 1)}%</span>) },
  { key: 'avg_lead', label: 'Lead (days)', type: 'num' },
  { key: 'on_time_pct', label: 'On time', type: 'num', render: pctv },
  { key: 'return_pct', label: 'Returns', type: 'num', render: (v) => (v ? <span className={v >= 2 ? 'up' : ''}>{num(v, 2)}%</span> : '—') },
  { key: 'to_recover', label: 'Debit note due', type: 'money', render: amt },
  { key: 'open_pos', label: 'Open POs', type: 'num' },
  { key: 'grn_pending', label: 'Still to receive', type: 'money', render: (v) => (v ? compact(v) : '—') },
  { key: 'oldest_open_days', label: 'Oldest open (days)', type: 'num' },
  { key: 'overdue_to_vendor', label: 'We owe (overdue)', type: 'money', render: amt },
  { key: 'watch', label: 'Watch', render: (v) => (v ? <span className="small">{v}</span> : <span className="pill pill-good">ok</span>) },
]
const SAVE = [
  { key: 'item_code', label: 'Item' },
  { key: 'product', label: 'Product' },
  { key: 'uom', label: 'UoM' },
  { key: 'vendors', label: 'Vendors', type: 'num' },
  { key: 'best_price', label: 'Cheapest avg', type: 'money' },
  { key: 'worst_price', label: 'Costliest avg', type: 'money' },
  { key: 'spread_pct', label: 'Gap', type: 'num', render: (v) => <span className="pill pill-warn">{num(v, 1)}%</span> },
  { key: 'extra_paid', label: 'Paid above cheapest', type: 'money', render: (v) => <b className="up">{money(v)}</b> },
  { key: 'cheapest_vendor', label: 'Cheapest vendor' },
  { key: 'dearest_vendor', label: 'Costliest vendor' },
]
const CAT = [
  { key: 'name', label: 'Category' },
  { key: 'spend', label: 'Spend', type: 'money', render: (v) => compact(v) },
  { key: 'share_pct', label: 'Share', type: 'num', render: pctv },
  { key: 'items', label: 'Items', type: 'num' },
  { key: 'bills', label: 'Bills', type: 'num' },
]

function firstLook(d) {
  const out = []
  if (d.top10_share_pct != null)
    out.push(`${num(d.vendors_for_80pct, 0)} of ${num(d.vendors, 0)} vendors make 80% of spend; the top 10 take ${num(d.top10_share_pct, 0)}%. Meet these vendors first.`)
  if (d.extra_paid > 0.5)
    out.push(`${compact(d.extra_paid)} was paid above the cheapest vendor on ${num(d.gap_items, 0)} items bought from 2+ vendors – the quickest savings (table below).`)
  const pricey = (d.scorecard || []).filter((r) => (r.price_vs_best_pct || 0) >= 5).map((r) => r.vendor)
  if (pricey.length) out.push(`Priced above other vendors for the same items: ${pricey.slice(0, 4).join(', ')}${pricey.length > 4 ? '…' : ''}.`)
  if (d.single_source_items) out.push(`${num(d.single_source_items, 0)} items (${compact(d.single_source_spend)}) have only one vendor – no price check and a supply risk; find a second source.`)
  if (d.open_po_value > 0.5) out.push(`${compact(d.open_po_value)} is still to be received on ${num(d.open_pos, 0)} open POs – review the oldest ones with stores.`)
  if (d.overdue_to_vendors > 0.5) out.push(`${compact(d.overdue_to_vendors)} of vendor bills are past due – this weakens negotiation; align with accounts.`)
  if (d.returns_to_recover > 0.5) out.push(`${compact(d.returns_to_recover)} of goods returned to vendors still has no debit note – follow up.`)
  const p = d.process || {}
  if ((p.bills_without_po?.n || 0) + (p.split_billing?.n || 0) + (p.rate_above_po?.n || 0) > 0)
    out.push(`Process: ${num(p.bills_without_po?.n || 0, 0)} bills above ₹50,000 without a PO, ${num(p.split_billing?.n || 0, 0)} split-billing cases, ${num(p.rate_above_po?.n || 0, 0)} bills above the PO rate.`)
  return out
}

export default function PurchaseHead({ companies, go }) {
  const [company, setCompany] = useCompany()   // shared by every page
  const [period, setPeriod] = useState('fy')
  const [custom, setCustom] = useState({})
  const [txn, setTxn] = useState('External')
  const pr = periodRange(period, custom)
  const prm = { company_id: company, date_from: pr.from || '2000-01-01', date_to: pr.to, txn: txn || 'all' }
  // four parts, one after another (one database connection at a time); each shows as soon as it is ready
  const step = useSteps([
    ['brief', () => get('/purchase-head/brief', prm)],
    ['savings', () => get('/purchase-head/savings', prm)],
    ['scorecard', () => get('/purchase-head/scorecard', prm)],
    ['process', () => get('/purchase-head/process', prm)],
  ], [company, pr.from, pr.to, txn])
  const r = step('brief'), sv = step('savings'), sc = step('scorecard'), pc = step('process')
  const d = r.data ? { ...r.data, ...(sv.data || {}), ...(sc.data || {}), process: pc.data || {} } : null
  const ready = sv.data && sc.data && pc.data
  const P = d?.process || {}
  const proc = pc.data ? [
    ['Bills above ₹50,000 without a PO', P.bills_without_po, 'control'],
    ['Billed above the PO rate (> 1%)', P.rate_above_po, 'control'],
    ['Split billing (3+ bills, same item, one week)', P.split_billing, 'vendors'],
    ['Possible duplicate bills', P.duplicates, 'control'],
    ['New vendors this period', P.new_vendors, 'vendors'],
    ['Single-source items', P.single_source, 'vendors'],
    ['External bills unpaid after 90 days', P.unpaid_90, 'p2p'],
  ] : []

  return (
    <section>
      <div className="filters">
        <CompanySelect companies={companies} value={company} onChange={setCompany} />
        <PeriodSelect period={period} setPeriod={setPeriod} custom={custom} setCustom={setCustom} />
        <TxnSelect value={txn} onChange={setTxn} />
      </div>
      {d && <div className="period-note">📅 Bills dated <b>{String(d.period.from).startsWith('2000') ? 'the first bill' : d.period.from}</b> to <b>{d.period.to}</b> · open POs and overdue bills of any date</div>}
      <Status loading={r.loading} error={r.error} />
      {d && (
        <>
          <div className="kpis">
            <Kpi label="Purchase spend" value={compact(d.spend)} sub={`${num(d.vendors, 0)} vendors · ${num(d.bills, 0)} bills`} />
            <Kpi label="Vendor concentration" value={pctv(d.top10_share_pct)} sub={`top 10 vendors · ${num(d.vendors_for_80pct, 0)} vendors = 80% of spend`} />
            <Kpi label="Savings on the table" loading={!sv.data} value={compact(d.extra_paid)} sub={`${num(d.gap_items, 0)} items cheaper elsewhere`} />
            <Kpi label="Still to receive" loading={!sc.data} value={compact(d.open_po_value)} sub={`${num(d.open_pos, 0)} open POs`} />
            <Kpi label="We owe vendors (overdue)" loading={!sc.data} value={compact(d.overdue_to_vendors)} sub="past due date" />
            <Kpi label="Debit notes to recover" loading={!sc.data} value={compact(d.returns_to_recover)} sub="returns not credited" />
          </div>

          <div className="card takeaways">
            <h3>What to look at first <span className="muted small">– written from the numbers on this page</span></h3>
            {ready ? <ul>{firstLook(d).map((t, i) => <li key={i}>{t}</li>)}</ul>
              : <div className="muted small">Written once all parts below have loaded…</div>}
          </div>

          <div className="charts">
            <ChartCard title="Where the money goes" note="(top categories)" empty={!d.top_categories.length}>
              <BarRank data={d.top_categories.slice(0, 8)} valueKey="spend" name="Spend" />
            </ChartCard>
            <ChartCard title="Top 10 vendors" empty={!d.top_vendors.length}>
              <BarRank data={d.top_vendors} valueKey="spend" name="Spend" color="#6250d6" />
            </ChartCard>
            <ChartCard title="Local vs Import" empty={!d.by_source.length}>
              <BarRank data={d.by_source} valueKey="spend" name="Spend" color="#1baf7a" />
            </ChartCard>
          </div>

          <div className="card">
            <div className="card-head">
              <h3>Vendor scorecard <span className="muted small">– top 25 vendors by spend: price, delivery, quality, open orders, payments</span></h3>
              <button onClick={() => downloadCsv('vendor_scorecard.csv', SCORE, d.scorecard)}>Download CSV</button>
            </div>
            <Status loading={sc.loading || (!sc.data && !sc.error)} error={sc.error} />
            {sc.data && <DataTable columns={SCORE} rows={d.scorecard} />}
            <div className="muted small">Price vs cheapest = extra paid ÷ spend on items this vendor shares with others. Returns = returned
              value ÷ spend. On time uses the PO's scheduled date where Odoo has it.</div>
          </div>

          <div className="card">
            <div className="card-head">
              <h3>Quick savings <span className="muted small">– same item & UoM, bought from 2+ vendors at different prices</span></h3>
              <button onClick={() => downloadCsv('savings_price_gaps.csv', SAVE, d.savings)}>Download CSV</button>
            </div>
            <Status loading={sv.loading || (!sv.data && !sv.error)} error={sv.error} />
            {sv.data && <DataTable columns={SAVE} rows={d.savings} empty="No price gaps of 10% or more" />}
            <div className="muted small">Full list with lead time per vendor: Vendors → Price & vendor watch. Price rise vs last year: Price Impact.</div>
          </div>

          <div className="card">
            <div className="card-head"><h3>Buying discipline <span className="muted small">– click to open the detail</span></h3></div>
            <Status loading={pc.loading || (!pc.data && !pc.error)} error={pc.error} />
            {pc.data && <div className="home-tiles">
              {proc.map(([label, x, page]) => (
                <button key={label} className={`home-tile ${(x?.n || 0) > 0 ? 'warn' : 'good'}`} onClick={() => go && go(page)}>
                  <div className="t-label">{label}</div>
                  <div className="t-value">{num(x?.n || 0, 0)}</div>
                  <div className="t-sub">{x?.value ? compact(x.value) : '—'}</div>
                </button>
              ))}
            </div>}
          </div>

          <More title="Spend by category – full list">
            <div className="card">
              <div className="card-head"><h3>Categories</h3>
                <button onClick={() => downloadCsv('spend_by_category.csv', CAT, d.top_categories)}>Download CSV</button></div>
              <DataTable columns={CAT} rows={d.top_categories} />
            </div>
          </More>
        </>
      )}
      <div className="muted small">Spend = posted vendor bills less debit notes, INR excl. GST (same as the purchase register). All
        figures use the same rules as their own pages in this dashboard.</div>
    </section>
  )
}
