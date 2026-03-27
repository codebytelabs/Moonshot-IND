import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine
} from 'recharts';

const API = process.env.REACT_APP_BACKEND_URL + '/api';

function MetricCard({ label, value, sub, color = 'var(--text-primary)', testId }) {
  return (
    <div data-testid={testId} style={{ background: 'var(--surface)', border: '1px solid var(--border)', padding: '16px 20px' }}>
      <div style={{ fontSize: 9, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.15em', marginBottom: 8 }}>{label}</div>
      <div style={{ fontSize: 26, fontFamily: 'JetBrains Mono', fontWeight: 300, color }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

export default function Performance() {
  const [perf, setPerf] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchPerf = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/performance`);
      setPerf(r.data);
    } catch (e) {}
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchPerf();
    const interval = setInterval(fetchPerf, 30000);
    return () => clearInterval(interval);
  }, [fetchPerf]);

  const equityCurve = perf?.equity_curve?.map((v, i) => ({ i, value: v })) || [];
  const drawdownCurve = equityCurve.map((pt, i) => {
    const peak = Math.max(...equityCurve.slice(0, i + 1).map(p => p.value));
    const dd = peak > 0 ? ((pt.value - peak) / peak) * 100 : 0;
    return { i: pt.i, drawdown: Math.min(0, dd) };
  });

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontFamily: 'JetBrains Mono', fontSize: 20, fontWeight: 700, letterSpacing: '0.08em' }}>PERFORMANCE</h1>
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>Historical trade analytics — paper trading account</div>
      </div>

      {loading ? (
        <div style={{ color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', fontSize: 12 }}>LOADING...</div>
      ) : !perf || perf.total_trades === 0 ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', fontSize: 12 }}>
          NO TRADE HISTORY YET — START TRADING TO SEE PERFORMANCE METRICS
        </div>
      ) : (
        <>
          {/* Key Metrics */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 10, marginBottom: 20 }}>
            <MetricCard testId="total-trades-stat" label="TOTAL TRADES" value={perf.total_trades} />
            <MetricCard
              testId="win-rate-stat"
              label="WIN RATE"
              value={`${(perf.win_rate * 100).toFixed(1)}%`}
              color={perf.win_rate >= 0.5 ? 'var(--success)' : 'var(--danger)'}
              sub={`Target: ≥50%`}
            />
            <MetricCard
              testId="profit-factor-stat"
              label="PROFIT FACTOR"
              value={perf.profit_factor === Infinity ? '∞' : perf.profit_factor?.toFixed(2)}
              color={perf.profit_factor >= 1.8 ? 'var(--success)' : perf.profit_factor >= 1.2 ? 'var(--warning)' : 'var(--danger)'}
              sub="Target: ≥1.8"
            />
            <MetricCard
              testId="total-pnl-stat"
              label="TOTAL P&L"
              value={`${perf.total_pnl >= 0 ? '+' : ''}₹${perf.total_pnl?.toFixed(2)}`}
              color={perf.total_pnl >= 0 ? 'var(--success)' : 'var(--danger)'}
            />
            <MetricCard
              testId="max-drawdown-stat"
              label="MAX DRAWDOWN"
              value={`${perf.max_drawdown?.toFixed(1)}%`}
              color={Math.abs(perf.max_drawdown) <= 12 ? 'var(--success)' : Math.abs(perf.max_drawdown) <= 15 ? 'var(--warning)' : 'var(--danger)'}
              sub="Target: <12%"
            />
            <MetricCard
              testId="avg-win-stat"
              label="AVG WIN / LOSS"
              value={`₹${perf.avg_win?.toFixed(0)} / ₹${Math.abs(perf.avg_loss)?.toFixed(0)}`}
              color="var(--text-primary)"
            />
          </div>

          {/* Charts */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            {/* Equity Curve */}
            <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', padding: '20px 20px 12px' }}>
              <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.15em', marginBottom: 16 }}>
                EQUITY CURVE
              </div>
              <ResponsiveContainer width="100%" height={200}>
                <AreaChart data={equityCurve}>
                  <defs>
                    <linearGradient id="eq2" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#00E5FF" stopOpacity={0.2} />
                      <stop offset="95%" stopColor="#00E5FF" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                  <XAxis dataKey="i" hide />
                  <YAxis hide domain={['auto', 'auto']} />
                  <Tooltip
                    contentStyle={{ background: '#0D0D10', border: '1px solid var(--border)', fontFamily: 'JetBrains Mono', fontSize: 11 }}
                    formatter={(v) => [`₹${v?.toLocaleString('en-IN')}`, 'Portfolio']}
                  />
                  <Area type="monotone" dataKey="value" stroke="#00E5FF" fill="url(#eq2)" strokeWidth={1.5} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>

            {/* Drawdown Chart */}
            <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', padding: '20px 20px 12px' }}>
              <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.15em', marginBottom: 16 }}>
                DRAWDOWN
              </div>
              <ResponsiveContainer width="100%" height={200}>
                <AreaChart data={drawdownCurve}>
                  <defs>
                    <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#FF3B30" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#FF3B30" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                  <XAxis dataKey="i" hide />
                  <YAxis hide domain={['auto', 0]} />
                  <ReferenceLine y={-12} stroke="rgba(255,204,0,0.3)" strokeDasharray="4 4" />
                  <ReferenceLine y={-15} stroke="rgba(255,59,48,0.4)" strokeDasharray="4 4" />
                  <Tooltip
                    contentStyle={{ background: '#0D0D10', border: '1px solid var(--border)', fontFamily: 'JetBrains Mono', fontSize: 11 }}
                    formatter={(v) => [`${v?.toFixed(2)}%`, 'Drawdown']}
                  />
                  <Area type="monotone" dataKey="drawdown" stroke="#FF3B30" fill="url(#ddGrad)" strokeWidth={1.5} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Win/Loss stats */}
          <div style={{ marginTop: 16, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', padding: 20 }}>
              <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.15em', marginBottom: 16 }}>
                WIN / LOSS DISTRIBUTION
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                {[
                  { label: 'AVG WIN', value: `₹${perf.avg_win?.toFixed(2)}`, color: 'var(--success)' },
                  { label: 'AVG LOSS', value: `₹${perf.avg_loss?.toFixed(2)}`, color: 'var(--danger)' },
                  { label: 'WINS', value: Math.round(perf.win_rate * perf.total_trades), color: 'var(--success)' },
                  { label: 'LOSSES', value: Math.round((1 - perf.win_rate) * perf.total_trades), color: 'var(--danger)' },
                ].map(({ label, value, color }) => (
                  <div key={label} style={{ padding: '10px 14px', border: '1px solid var(--border)', background: 'rgba(255,255,255,0.02)' }}>
                    <div style={{ fontSize: 9, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.15em', marginBottom: 4 }}>{label}</div>
                    <div style={{ fontFamily: 'JetBrains Mono', fontSize: 18, color }}>{value}</div>
                  </div>
                ))}
              </div>
            </div>
            <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', padding: 20 }}>
              <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.15em', marginBottom: 16 }}>
                PERFORMANCE TARGETS
              </div>
              {[
                { label: 'WIN RATE', current: (perf.win_rate * 100).toFixed(1) + '%', target: '≥50%', ok: perf.win_rate >= 0.50 },
                { label: 'PROFIT FACTOR', current: perf.profit_factor === Infinity ? '∞' : perf.profit_factor?.toFixed(2), target: '≥1.8', ok: perf.profit_factor >= 1.8 },
                { label: 'MAX DRAWDOWN', current: Math.abs(perf.max_drawdown)?.toFixed(1) + '%', target: '<12%', ok: Math.abs(perf.max_drawdown) <= 12 },
              ].map(({ label, current, target, ok }) => (
                <div key={label} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 0', borderBottom: '1px solid var(--border)' }}>
                  <div style={{ fontSize: 11, color: 'var(--text-secondary)', fontFamily: 'JetBrains Mono' }}>{label}</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span style={{ fontFamily: 'JetBrains Mono', fontSize: 13, color: ok ? 'var(--success)' : 'var(--danger)' }}>{current}</span>
                    <span style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>target {target}</span>
                    <span style={{ fontSize: 10, color: ok ? 'var(--success)' : 'var(--danger)' }}>{ok ? '✓' : '✗'}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
