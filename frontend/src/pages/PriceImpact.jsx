import React, { useState } from 'react'
import { get, qs, num, compact, money } from '../api.js'
import { useSteps, useAsync, Kpi, Status, DataTable, CompanySelect, useCompany, TxnSelect, Picker, downloadCsv, More } from '../components/ui.jsx'
import { ChartCard, BarRank, BarGroup } from '../components/Charts.jsx'

// ---------------------------------------------------------------- periods
const iso = (d) => d.toISOString().slice(0, 10)
const fyStart = (off = 0) => {
  const d = new Date(); const y = (d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1) + off
  return y
}
const fyLabel = (y) => `FY${String(y + 1).slice(2)}`          // FY starting Apr 2025 = FY26
const BASES = (() => {
  const y = fyStart()
  return [
    ['lfy', `${fyLabel(y - 1)} (Apr ${y - 1} – Mar ${y})`, `${y - 1}-04-01`, `${y}-03-31`],
    ['pfy', `${fyLabel(y - 2)} (Apr ${y - 2} – Mar ${y - 1})`, `${y - 2}-04-01`, `${y - 1}-03-31`],
    ['lh2', `${fyLabel(y - 1)} H2 (Oct ${y - 1} – Mar ${y})`, `${y - 1}-10-01`, `${y}-03-31`],
    ['custom', 'Custom dates…'],
  ]
})()
const CURRENTS = (() => {
  const y = fyStart()
  return [
    ['ytd', `${fyLabel(y)} to date`, `${y}-04-01`, iso(new Date())],
    ['h1', `${fyLabel(y)} H1 (Apr – Sep ${y})`, `${y}-04-01`, `${y}-09-30`],
    ['q1', `${fyLabel(y)} Q1 (Apr – Jun)`, `${y}-04-01`, `${y}-06-30`],
    ['q2', `${fyLabel(y)} Q2 (Jul – Sep)`, `${y}-07-01`, `${y}-09-30`],
    ['custom', 'Custom dates…'],
  ]
})()
function PeriodPick({ label, options, value, setValue, custom, setCustom }) {
  return (
    <span className="inline">
      <span className="muted small">{label}</span>
      <select value={value} onChange={(e) => setValue(e.target.value)}>
        {options.map((o) => <option key={o[0]} value={o[0]}>{o[1]}</option>)}
      </select>
      {value === 'custom' && <>
        <input type="date" value={custom.from || ''} onChange={(e) => setCustom({ ...custom, from: e.target.value })} />
        <input type="date" value={custom.to || ''} onChange={(e) => setCustom({ ...custom, to: e.target.value })} />
      </>}
    </span>
  )
}
const pick = (options, v, custom) => {
  if (v === 'custom') return { from: custom.from, to: custom.to }
  const o = options.find((x) => x[0] === v); return { from: o[2], to: o[3] }
}

// ---------------------------------------------------------------- columns
const cr = (v) => (v === null || v === undefined ? '—' : compact(v))
const signed = (v) => (v === null || v === undefined ? '—'
  : <span className={v > 0.5 ? 'up' : v < -0.5 ? 'down' : ''}>{money(Math.round(v))}</span>)
const pct = (v) => (v === null || v === undefined ? '—' : <span className={v > 0 ? 'up' : v < 0 ? 'down' : ''}>{v > 0 ? '+' : ''}{num(v, 1)}%</span>)
const rate = (v) => (v === null || v === undefined ? '—' : money(v))

const CLS = [
  { key: 'classification', label: 'Classification' },
  { key: 'products', label: 'Products', type: 'num' },
  { key: 'b_value', label: 'Base purchase', type: 'money', render: cr },
  { key: 'c_value', label: 'Current purchase', type: 'money', render: cr },
  { key: 'price_impact', label: 'Price impact', type: 'money', render: signed },
  { key: 'pl_hit', label: 'P&L hit', type: 'money', render: signed },
]
const SPLIT = (label) => [
  { key: 'name', label },
  { key: 'products', label: 'Products', type: 'num' },
  { key: 'b_value', label: 'Base purchase', type: 'money', render: cr },
  { key: 'c_value', label: 'Current purchase', type: 'money', render: cr },
  { key: 'price_impact', label: 'Price impact', type: 'money', render: signed },
  { key: 'pl_hit', label: 'P&L hit', type: 'money', render: signed },
  { key: 'in_stock', label: 'Still in stock', type: 'money', render: signed },
  { key: 'realisation_pct', label: 'Realisation', type: 'num', render: (v) => (v == null ? '—' : `${num(v, 0)}%`) },
]
const PROD = [
  { key: 'item_code', label: 'Product ref.' },
  { key: 'product', label: 'Product' },
  { key: 'segment', label: 'Segment' },
  { key: 'source', label: 'Dom / Imp' },
  { key: 'uom', label: 'UoM' },
  { key: 'b_qty', label: 'Base qty', type: 'num' },
  { key: 'b_rate', label: 'Base rate', type: 'money', render: rate },
  { key: 'c_qty', label: 'Current qty', type: 'num' },
  { key: 'c_rate', label: 'Current rate', type: 'money', render: rate },
  { key: 'rate_change_pct', label: 'Rate change', type: 'num', render: pct },
  { key: 'price_impact', label: 'Price impact', type: 'money', render: signed },
  { key: 'closing_stock', label: 'Closing stock', type: 'num' },
  { key: 'pl_hit', label: 'P&L hit', type: 'money', render: signed },
  { key: 'realisation_pct', label: 'Realised', type: 'num', render: (v) => (v == null ? '—' : `${num(v, 0)}%`) },
  { key: 'classification', label: 'Classification', render: (v, r) => r.manual_tag || v },
]
const IMPORT_EXTRA = [
  { key: 'fc_currency', label: 'Currency' },
  { key: 'price_effect', label: 'Supplier price effect', type: 'money', render: signed },
  { key: 'forex_effect', label: 'Forex effect', type: 'money', render: signed },
]

export default function PriceImpact({ companies, openVendors }) {
  const [company, setCompany] = useCompany()   // shared by every page
  const [base, setBase] = useState('lfy'), [bc, setBc] = useState({})
  const [cur, setCur] = useState('ytd'), [cc, setCc] = useState({})
  const [txn, setTxn] = useState('External')
  const [vendor, setVendor] = useState(null)
  const [product, setProduct] = useState(null)
  const [by, setBy] = useState('segment')
  const [src, setSrc] = useState('')
  const [rank, setRank] = useState('price_impact')
  const [dir, setDir] = useState('increase')
  const [cls, setCls] = useState('')
  const [text, setText] = useState('')
  const b = pick(BASES, base, bc), c = pick(CURRENTS, cur, cc)
  const p = { company_id: company, b_from: b.from, b_to: b.to, c_from: c.from, c_to: c.to, txn: txn || 'all', vendor_id: vendor?.id, product_id: product?.id }
  const ready = b.from && b.to && c.from && c.to
  const dep = [company, b.from, b.to, c.from, c.to, txn, vendor?.id, product?.id]
  const step = useSteps(ready ? [
    ['sum', () => get('/price-impact/summary', p)],
    ['seg', () => get('/price-impact/split', { ...p, by: 'segment' })],
    ['src', () => get('/price-impact/split', { ...p, by: 'source' })],
    ['top10', () => get('/price-impact/top', { ...p, n: 10 })],
  ] : [], dep)
  const split = useAsync(() => (ready ? get('/price-impact/split', { ...p, by }) : Promise.resolve([])), [...dep, by])
  const top = useAsync(() => (ready ? get('/price-impact/top', { ...p, n: 30, rank, source: src, direction: dir })
    : Promise.resolve([])), [...dep, rank, src, dir])
  const s = step('sum'), sd = s.data
  const topCols = src === 'Import' ? [...PROD.slice(0, 11), ...IMPORT_EXTRA, ...PROD.slice(11)] : PROD

  return (
    <section>
      <div className="filters">
        <CompanySelect companies={companies} value={company} onChange={setCompany} />
        <PeriodPick label="Base" options={BASES} value={base} setValue={setBase} custom={bc} setCustom={setBc} />
        <PeriodPick label="vs current" options={CURRENTS} value={cur} setValue={setCur} custom={cc} setCustom={setCc} />
        <TxnSelect value={txn} onChange={setTxn} />
        <Picker endpoint="/products" value={product} onChange={setProduct} placeholder="Product (all)…" />
        <Picker endpoint="/vendors" value={vendor} onChange={setVendor} placeholder="Vendor (all)…" />
        <a className="btn" href={`/api/price-impact/export?${qs(p)}`}>Export finance register (Excel)</a>
      </div>
      <div className="period-note muted small">
        📅 Base <b>{b.from}</b> to <b>{b.to}</b> vs current <b>{c.from}</b> to <b>{c.to}</b> · posted vendor bills less
        debit notes, INR excl. GST · per product, {vendor ? <b>vendor {vendor.name} only</b> : 'all vendors'}{product ? <> · <b>{product.name}</b></> : ''} · closing stock on <b>{c.to}</b>
      </div>
      {s.error && <div className="msg error">⚠ {s.error}</div>}

      <div className="kpis">
        <Kpi label="Base purchase" value={cr(sd?.b_value)} loading={s.loading} sub={sd ? `${num(sd.products, 0)} products` : ''} />
        <Kpi label="Current purchase" value={cr(sd?.c_value)} loading={s.loading}
             sub={sd?.c_vs_b_pct != null ? `${num(sd.c_vs_b_pct, 1)}% of base already` : ''} />
        <Kpi label="Price impact" value={cr(sd?.price_impact)} loading={s.loading}
             sub={sd ? `${num(sd.increase_products, 0)} products costlier` : ''} />
        <Kpi label="Still in closing stock" value={cr(sd?.in_stock)} loading={s.loading} sub="reaches P&L when consumed" />
        <Kpi label="P&L hit" value={cr(sd?.pl_hit)} loading={s.loading}
             sub={sd?.realisation_pct != null ? `${num(sd.realisation_pct, 1)}% of impact realised` : ''} />
        <Kpi label="Import share · top 5" loading={s.loading}
             value={sd?.import_share_pct != null ? `${num(sd.import_share_pct, 0)}%` : '—'}
             sub={sd?.top5_share_pct != null ? `top 5 products = ${num(sd.top5_share_pct, 0)}% of the hit` : ''} />
      </div>

      {sd?.takeaways?.length > 0 && (
        <div className="card takeaways">
          <h3>Management takeaways <span className="muted small">– written from the numbers above</span></h3>
          <ul>{sd.takeaways.map((t, i) => <li key={i}>{t}</li>)}</ul>
        </div>
      )}

      <div className="charts">
        <ChartCard title="Price impact vs P&L hit" note="(by segment)" loading={step('seg').loading} error={step('seg').error}
                   empty={!(step('seg').data || []).length}>
          <BarGroup horizontal data={(step('seg').data || []).slice(0, 8)} x="name"
                    series={[{ key: 'price_impact', label: 'Price impact' }, { key: 'pl_hit', label: 'P&L hit' }]} />
        </ChartCard>
        <ChartCard title="Domestic vs Import" note="(import: supplier price vs forex)" loading={step('src').loading}
                   error={step('src').error} empty={!(step('src').data || []).length}>
          <BarGroup data={step('src').data || []} x="name"
                    series={[{ key: 'pl_hit', label: 'P&L hit' }, { key: 'price_effect', label: 'Supplier price' },
                      { key: 'forex_effect', label: 'Forex' }]} />
        </ChartCard>
        <ChartCard title="Top 10 P&L exposures" loading={step('top10').loading} error={step('top10').error}
                   empty={!(step('top10').data || []).length}>
          <BarRank data={(step('top10').data || []).map((r) => ({ name: r.item_code || r.product, value: r.pl_hit }))}
                   name="P&L hit" color="#e34948" />
        </ChartCard>
      </div>

      <div className="card">
        <div className="card-head">
          <h3>Top 30 <span className="muted small">– the products to negotiate first</span></h3>
          <div className="inline">
            <select value={dir} onChange={(e) => setDir(e.target.value)}>
              <option value="increase">Price increases</option><option value="decrease">Price decreases</option>
            </select>
            <select value={src} onChange={(e) => setSrc(e.target.value)}>
              <option value="">Domestic + Import</option><option value="Domestic">Domestic</option><option value="Import">Import (with forex split)</option>
            </select>
            <select value={rank} onChange={(e) => setRank(e.target.value)}>
              <option value="price_impact">Rank by price impact</option><option value="pl_hit">Rank by P&L hit</option>
            </select>
            <button onClick={() => downloadCsv('price_impact_top30.csv', topCols, top.data || [])}>Download CSV</button>
          </div>
        </div>
        <Status loading={top.loading} error={top.error} />
        {!top.loading && <DataTable columns={topCols} rows={top.data} empty="Nothing in this period"
                                    onRowClick={openVendors ? (r) => openVendors(r.item_code || r.product) : undefined} />}
        {openVendors && <div className="muted small">Click a product to see each vendor's price and lead time.</div>}
      </div>

      <More title="By classification, segment, category or product line">
        <div className="card">
          <div className="card-head"><h3>By classification</h3>
            <button onClick={() => downloadCsv('price_impact_classes.csv', CLS, sd?.by_classification || [])}>Download CSV</button></div>
          <DataTable columns={CLS} rows={sd?.by_classification} />
          <div className="muted small">
            {sd ? `${num(sd.stock_above_products, 0)} products have closing stock above the current purchase: their impact (${cr(sd.stock_above_impact)}) is all still in stock. ` : ''}
            Manual tags come from config\price_impact_overrides.csv{sd && !sd.overrides_file ? ' (no file yet)' : ''}.
          </div>
        </div>
        <div className="card">
          <div className="card-head">
            <h3>Split by</h3>
            <div className="inline">
              <select value={by} onChange={(e) => setBy(e.target.value)}>
                <option value="segment">Business segment</option><option value="category">Category</option>
                <option value="line">Product line</option><option value="source">Domestic / Import</option>
              </select>
              <button onClick={() => downloadCsv(`price_impact_by_${by}.csv`, SPLIT('Group'), split.data || [])}>Download CSV</button>
            </div>
          </div>
          <Status loading={split.loading} error={split.error} />
          {!split.loading && <DataTable columns={SPLIT({ segment: 'Segment', category: 'Category', line: 'Product line', source: 'Source' }[by])} rows={split.data} />}
          {sd && !sd.segment_field && <div className="muted small">Segment = second level of the product category until the
            business-segment field is set in database.ini [price_impact].</div>}
        </div>
      </More>

      <More title="All products – the full register (replaces the manual sheet)">
        <AllProducts p={p} ready={ready} dep={dep} cls={cls} setCls={setCls} text={text} setText={setText}
                     classes={(sd?.by_classification || []).map((x) => x.classification)} />
      </More>

      <div className="muted small">
        Base / current rate = value ÷ qty for the period (weighted, product UoM). Price impact = (current − base rate) × current
        qty. P&L hit = (current − base rate) × (current qty − closing stock): with FIFO the stock left at the period end is the
        latest purchases, so this is the part already consumed. Import split: supplier price effect = change in the
        foreign-currency rate × current qty × current exchange rate; forex effect = the rest.
      </div>
    </section>
  )
}

function AllProducts({ p, ready, dep, cls, setCls, text, setText, classes }) {
  const [q, setQ] = useState('')
  const r = useAsync(() => (ready ? get('/price-impact/products', { ...p, classification: cls, search: q, limit: 3000 })
    : Promise.resolve([])), [...dep, cls, q])
  return (
    <div className="card">
      <div className="card-head">
        <h3>All products <span className="muted small">– sorted by size of price impact; Excel export has every row</span></h3>
        <div className="inline">
          <input placeholder="Product ref. or name…" value={text} onChange={(e) => setText(e.target.value)}
                 onKeyDown={(e) => e.key === 'Enter' && setQ(text)} />
          <select value={cls} onChange={(e) => setCls(e.target.value)}>
            <option value="">All classifications</option>
            {classes.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <a className="btn" href={`/api/price-impact/export?${qs(p)}`}>Excel</a>
        </div>
      </div>
      <Status loading={r.loading} error={r.error} />
      {!r.loading && <DataTable columns={[...PROD, ...IMPORT_EXTRA]} rows={r.data} />}
    </div>
  )
}
