import logo from './assets/appasamy-logo.png'
import React, { useState } from 'react'
import { get, post } from './api.js'
import { useAsync, CompanyContext } from './components/ui.jsx'
import Register from './pages/Register.jsx'
import ProductAnalysis from './pages/ProductAnalysis.jsx'
import CostingHome from './pages/CostingHome.jsx'
import Bills from './pages/Bills.jsx'
import Freight from './pages/Freight.jsx'
import Returns from './pages/Returns.jsx'
import Imports from './pages/Imports.jsx'
import Audit from './pages/Audit.jsx'
import JobWork from './pages/JobWork.jsx'
import Transit from './pages/Transit.jsx'
import Insights from './pages/Insights.jsx'
import P2P from './pages/P2P.jsx'
import Home from './pages/Home.jsx'
import PriceImpact from './pages/PriceImpact.jsx'
import PurchaseHead from './pages/PurchaseHead.jsx'
import Explore from './pages/Explore.jsx'

// Finance edition: 7 headings; each opens its first page, the others sit in a small sub-menu.
const GROUPS = [
  { id: 'home', label: 'Home', subs: [['home', 'What needs attention']] },
  { id: 'head', label: 'Procurement Brief', subs: [['head', 'Procurement brief']] },
  { id: 'explore', label: 'Product & Vendor', subs: [['explore', 'Product & vendor analysis']] },
  { id: 'impact', label: 'Price Impact', subs: [['impact', 'Base vs current period']] },
  { id: 'spend', label: 'Spend', subs: [['register', 'Purchase register'], ['bills', 'Bills / GST']] },
  { id: 'cost', label: 'Price & Cost', subs: [['product', 'Product prices'], ['costing', 'Costing'], ['freight', 'Freight'], ['imports', 'Imports']] },
  { id: 'vendors', label: 'Vendors', subs: [['insights', 'Price & vendor watch'], ['returns', 'Returns']] },
  { id: 'p2p', label: 'Order to Pay', subs: [['p2p', 'PO → GRN → bill → payment']] },
  { id: 'control', label: 'Control', subs: [['audit', 'Audit & tax'], ['jobwork', 'Job work & DC'], ['transit', 'Stock in transit']] },
]

export default function App() {
  const [company, setCompanyRaw] = useState(() => { try { return localStorage.getItem('pa_company') || null } catch { return null } })
  const setCompany = (v) => { setCompanyRaw(v || null); try { v ? localStorage.setItem('pa_company', v) : localStorage.removeItem('pa_company') } catch {} }
  const [group, setGroup] = useState('home')
  const [tab, setTab] = useState('home')
  const [focus, setFocus] = useState('')       // item handed from Price Impact to Item x vendor
  const go = (g, t) => { const G = GROUPS.find((x) => x.id === g) || GROUPS[0]; setGroup(G.id); setTab(t || G.subs[0][0]) }
  const subs = (GROUPS.find((x) => x.id === group) || GROUPS[0]).subs
  const [product, setProduct] = useState(null)   // shared between tabs
  const meta = useAsync(() => get('/meta'), [])
  const health = useAsync(() => get('/health'), [])
  const fast = (health.data?.mart?.views_in_use || []).length
  const companies = meta.data?.companies || []

  const openProduct = (p) => { setProduct(p); go('cost', 'product') }
  const openVendors = (item) => { setFocus(item); go('vendors', 'insights') }
  const [reloadKey, setReloadKey] = useState(0)
  const [refreshing, setRefreshing] = useState(false)
  const refresh = async () => {
    setRefreshing(true)
    try { await post('/cache/clear', {}) } finally { setRefreshing(false); setReloadKey((k) => k + 1) }
  }

  return (
    <div className="app">
      <header>
        <div className="topbar">
          <div className="brand">
            <span className="logo-chip"><img src={logo} alt="Appasamy Associates" /></span>
            <span className="brand-name">Procurement Analytics</span>
          </div>
          <span className="muted small source-badge" title={fast ? 'Reading the daily reporting views (procurement_mart)' : 'Reading live from the silver tables'}>
            {health.data ? (fast ? `Data: reporting views (${fast}/12)` : 'Data: live silver') : ''}
          </span>
          <button className="refresh" onClick={refresh} disabled={refreshing}
                  title="Data is kept 30 minutes for speed. Click to fetch fresh data now.">
            ↻<span className="refresh-label"> {refreshing ? 'Refreshing…' : 'Refresh data'}</span>
          </button>
        </div>
        <nav className="groups">
          {GROUPS.map((g) => (
            <button key={g.id} className={group === g.id ? 'tab active' : 'tab'} onClick={() => go(g.id)}>
              {g.label}
            </button>
          ))}
        </nav>
        {subs.length > 1 && (
          <div className="subnav">
            {subs.map(([id, label]) => (
              <button key={id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>{label}</button>
            ))}
          </div>
        )}
      </header>
      {meta.error && !['bills', 'home'].includes(tab) && <div className="msg error">⚠ {meta.error}</div>}
      <CompanyContext.Provider value={[company, setCompany]}>
      <main key={reloadKey}>
        {tab === 'home' && <Home companies={companies} go={go} />}
        {tab === 'head' && <PurchaseHead companies={companies} go={go} />}
        {tab === 'explore' && <Explore companies={companies} />}
        {tab === 'impact' && <PriceImpact companies={companies} openVendors={openVendors} />}
        {tab === 'register' && <Register meta={meta.data} companies={companies} openProduct={openProduct} />}
        {tab === 'bills' && <Bills />}
        {tab === 'p2p' && <P2P companies={companies} />}
        {tab === 'product' && <ProductAnalysis companies={companies} categories={meta.data?.categories} product={product} setProduct={setProduct} />}
        {tab === 'freight' && <Freight companies={companies} />}
        {tab === 'imports' && <Imports companies={companies} />}
        {tab === 'returns' && <Returns companies={companies} />}
        {tab === 'insights' && <Insights companies={companies} categories={meta.data?.categories} focus={focus} />}
        {tab === 'jobwork' && <JobWork companies={companies} />}
        {tab === 'transit' && <Transit companies={companies} />}
        {tab === 'audit' && <Audit companies={companies} />}
        {tab === 'costing' && <CostingHome companies={companies} openProduct={openProduct} />}
      </main>
      </CompanyContext.Provider>
    </div>
  )
}
