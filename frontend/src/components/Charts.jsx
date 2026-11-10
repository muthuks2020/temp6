import React from 'react'
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid, Legend,
} from 'recharts'
import { compact, money, SERIES } from '../api.js'
import { Status } from './ui.jsx'

// recessive axes / grid, white tooltip without border
const AX = { fill: '#8a91a6', fontSize: 11 }
const TIP = {
  contentStyle: { background: '#fff', border: 0, borderRadius: 10, boxShadow: '0 8px 24px rgba(20,26,43,.14)', fontSize: 12 },
  labelStyle: { color: '#141a2b', fontWeight: 600, marginBottom: 4 },
  cursor: { fill: 'rgba(37,99,235,.06)' },
}
const short = (s, n = 22) => (s && s.length > n ? s.slice(0, n - 1) + '…' : s)
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
export const fmtPeriod = (unit) => (v) => {
  if (!v) return ''
  const [y, m, d] = String(v).slice(0, 10).split('-')
  return unit === 'day' ? `${+d} ${MON[+m - 1]}` : `${MON[+m - 1]} ${y.slice(2)}`
}

export function ChartCard({ title, note, loading, error, empty, height = 260, children }) {
  return (
    <div className="card chart-card">
      <div className="card-head"><h3>{title} {note && <span className="muted small">{note}</span>}</h3></div>
      <Status loading={loading} error={error} />
      {!loading && !error && (empty
        ? <div className="empty">No data in this period</div>
        : <div style={{ width: '100%', height }}>{children}</div>)}
    </div>
  )
}

/** One series over time / categories, vertical bars */
export function BarTrend({ data, x, y, color = SERIES[0], xFormat, name = 'Value' }) {
  return (
    <ResponsiveContainer>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid vertical={false} stroke="#f0f2f6" />
        <XAxis dataKey={x} tick={AX} tickLine={false} axisLine={false} tickFormatter={xFormat} minTickGap={12} />
        <YAxis tick={AX} tickLine={false} axisLine={false} width={56} tickFormatter={(v) => compact(v)} />
        <Tooltip {...TIP} labelFormatter={xFormat} formatter={(v) => [money(v), name]} />
        <Bar dataKey={y} fill={color} radius={[4, 4, 0, 0]} maxBarSize={34} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  )
}

/** Ranking: horizontal bars, biggest first */
export function BarRank({ data, nameKey = 'name', valueKey = 'value', color = SERIES[0], name = 'Value', format = money, axisFormat = compact }) {
  return (
    <ResponsiveContainer>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, left: 0, bottom: 0 }} barCategoryGap={6}>
        <CartesianGrid horizontal={false} stroke="#f0f2f6" />
        <XAxis type="number" tick={AX} tickLine={false} axisLine={false} tickFormatter={(v) => axisFormat(v)} allowDecimals={false} />
        <YAxis type="category" dataKey={nameKey} tick={{ ...AX, fill: '#4a5268' }} tickLine={false} axisLine={false}
               width={140} tickFormatter={(s) => short(s, 20)} interval={0} />
        <Tooltip {...TIP} formatter={(v) => [format(v), name]} />
        <Bar dataKey={valueKey} fill={color} radius={[0, 4, 4, 0]} maxBarSize={22} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  )
}

/** Two or three series side by side (same unit, one axis), with legend */
export function BarGroup({ data, x, series, xFormat, horizontal = false }) {
  const s = series.map((d, i) => ({ ...d, color: d.color || SERIES[i] }))
  return (
    <ResponsiveContainer>
      <BarChart data={data} layout={horizontal ? 'vertical' : 'horizontal'}
                margin={{ top: 8, right: 12, left: 0, bottom: 0 }} barGap={2}>
        <CartesianGrid vertical={horizontal} horizontal={!horizontal} stroke="#f0f2f6" />
        {horizontal ? (
          <>
            <XAxis type="number" tick={AX} tickLine={false} axisLine={false} tickFormatter={(v) => compact(v)} />
            <YAxis type="category" dataKey={x} tick={{ ...AX, fill: '#4a5268' }} tickLine={false} axisLine={false}
                   width={140} tickFormatter={(v) => short(v, 20)} interval={0} />
          </>
        ) : (
          <>
            <XAxis dataKey={x} tick={AX} tickLine={false} axisLine={false} tickFormatter={xFormat} minTickGap={12} />
            <YAxis tick={AX} tickLine={false} axisLine={false} width={56} tickFormatter={(v) => compact(v)} />
          </>
        )}
        <Tooltip {...TIP} labelFormatter={xFormat} formatter={(v, n) => [money(v), n]} />
        <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12, color: '#4a5268', paddingTop: 4 }} />
        {s.map((d) => (
          <Bar key={d.key} dataKey={d.key} name={d.label} fill={d.color} isAnimationActive={false}
               radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]} maxBarSize={horizontal ? 14 : 26} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}
