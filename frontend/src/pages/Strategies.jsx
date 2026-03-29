import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer, ReferenceLine,
} from 'recharts';
import {
  TrendingUp, Activity, Play, Square, RefreshCw,
  AlertCircle, CheckCircle, Clock, Zap, BarChart2,
} from 'lucide-react';

const API = process.env.REACT_APP_BACKEND_URL + '/api';

const STRATEGY_CONFIG = {
  momentum: { label: 'Momentum',  color: '#3B82F6', desc: 'Riding waves — directional NIFTY trend' },
  zen:      { label: 'Zen',       color: '#10B981', desc: 'Credit spread overnight (proven 116%+)' },
  curvature:{ label: 'Curvature', color: '#F59E0B', desc: 'IV smile curvature (proxy — real signal needs chain data)' },
};

const fmt = (n) => new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(n ?? 0);
const fmtRs = (n) => `₹${fmt(Math.abs(n ?? 0))}`;
const fmtPct = (n) => `${(n ?? 0) > 0 ? '+' : ''}${(n ?? 0).toFixed(1)}%`;

function StatChip({ label, value, positive }) {
  const color = positive === undefined ? '#94A3B8'
    : positive ? '#10B981' : '#EF4444';
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{ fontSize: 10, color: '#64748B', fontFamily: 'JetBrains Mono', letterSpacing: '0.08em', marginBottom: 2 }}>
        {label}
      </div>
      <div style={{ fontSize: 13, fontFamily: 'JetBrains Mono', fontWeight: 700, color }}>
        {value}
      </div>
    </div>
  );
}

function StrategyCard({ name, data }) {
  const cfg = STRATEGY_CONFIG[name];
  const pnl = (data?.equity ?? 100000) - 100000;
  const roc = ((data?.equity ?? 100000) - 100000) / 100000 * 100;
  const isActive = data?.in_trade;
  return (
    <div style={{
      background: 'rgba(15,23,42,0.8)', border: `1px solid ${cfg.color}33`,
      borderRadius: 12, padding: '16px 20px',
      borderLeft: `3px solid ${cfg.color}`,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ width: 10, height: 10, borderRadius: '50%', background: cfg.color }} />
          <span style={{ fontFamily: 'JetBrains Mono', fontSize: 12, fontWeight: 700, color: '#F1F5F9', letterSpacing: '0.06em' }}>
            {cfg.label.toUpperCase()}
          </span>
        </div>
        <span style={{
          fontSize: 10, fontFamily: 'JetBrains Mono', letterSpacing: '0.08em',
          color: isActive ? '#10B981' : '#64748B',
          background: isActive ? 'rgba(16,185,129,0.1)' : 'rgba(100,116,139,0.1)',
          border: `1px solid ${isActive ? '#10B981' : '#334155'}`,
          padding: '2px 8px', borderRadius: 4,
        }}>
          {isActive ? '● IN TRADE' : '○ WATCHING'}
        </span>
      </div>
      <div style={{ fontSize: 10, color: '#64748B', marginBottom: 12 }}>{cfg.desc}</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 8 }}>
        <StatChip label="EQUITY" value={fmtRs(data?.equity ?? 100000)} />
        <StatChip label="P&L" value={(pnl >= 0 ? '+' : '') + fmtRs(pnl)} positive={pnl >= 0} />
        <StatChip label="ROC" value={fmtPct(roc)} positive={roc >= 0} />
        <StatChip label="WIN%" value={`${(data?.win_rate ?? 0).toFixed(1)}%`} positive={(data?.win_rate ?? 0) > 50} />
      </div>
    </div>
  );
}

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{
      background: '#0F172A', border: '1px solid #1E293B',
      borderRadius: 8, padding: '10px 14px', fontSize: 11,
      fontFamily: 'JetBrains Mono',
    }}>
      <div style={{ color: '#64748B', marginBottom: 6 }}>{label}</div>
      {payload.map((p) => (
        <div key={p.name} style={{ color: p.color, marginBottom: 3 }}>
          {STRATEGY_CONFIG[p.dataKey]?.label ?? p.name}: {fmtRs(p.value)}
          <span style={{ color: '#64748B', fontSize: 10, marginLeft: 6 }}>
            ({fmtPct((p.value - 100000) / 100000 * 100)})
          </span>
        </div>
      ))}
    </div>
  );
};

export default function Strategies() {
  const [tab, setTab] = useState('backtest');

  // ── Backtest state ────────────────────────────────────────────────────────
  const [btData,    setBtData]    = useState(null);
  const [btLoading, setBtLoading] = useState(false);
  const [btError,   setBtError]   = useState(null);
  const [lots,      setLots]      = useState(3);

  // ── Paper trading state ───────────────────────────────────────────────────
  const [ptStatus,  setPtStatus]  = useState(null);
  const [navHistory,setNavHistory]= useState([]);
  const [ptLoading, setPtLoading] = useState(false);
  const [navLoading,setNavLoading]= useState(false);

  // ── Fetch backtest ────────────────────────────────────────────────────────
  const fetchBacktest = useCallback(async () => {
    setBtLoading(true);
    setBtError(null);
    try {
      const { data } = await axios.get(`${API}/strategies/backtest/compare`, {
        params: { capital: 100000, lots },
      });
      setBtData(data);
    } catch (e) {
      setBtError(e.response?.data?.detail ?? e.message);
    } finally {
      setBtLoading(false);
    }
  }, [lots]);

  // ── Paper trading controls ────────────────────────────────────────────────
  const fetchPtStatus = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/strategies/paper-trading/status`);
      setPtStatus(data);
    } catch {}
  }, []);

  const fetchNavHistory = useCallback(async () => {
    setNavLoading(true);
    try {
      const { data } = await axios.get(`${API}/strategies/paper-trading/nav`, { params: { days: 30 } });
      // Normalise: [{ts, nav:{momentum,zen,curvature}, spot}] → recharts rows
      const rows = (data.snapshots ?? []).map((s) => ({
        date: new Date(s.ts).toLocaleDateString('en-IN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }),
        momentum: s.nav?.momentum ?? 100000,
        zen:      s.nav?.zen      ?? 100000,
        curvature:s.nav?.curvature?? 100000,
      }));
      setNavHistory(rows);
    } catch {} finally {
      setNavLoading(false);
    }
  }, []);

  const handlePaperStart = async () => {
    setPtLoading(true);
    try {
      await axios.post(`${API}/strategies/paper-trading/start`);
      await fetchPtStatus();
    } catch (e) {
      alert(e.response?.data?.detail ?? 'Failed to start paper trading');
    } finally {
      setPtLoading(false);
    }
  };

  const handlePaperStop = async () => {
    setPtLoading(true);
    try {
      await axios.post(`${API}/strategies/paper-trading/stop`);
      await fetchPtStatus();
    } catch {} finally {
      setPtLoading(false);
    }
  };

  // ── Effects ───────────────────────────────────────────────────────────────
  useEffect(() => {
    if (tab === 'backtest' && !btData) fetchBacktest();
  }, [tab, btData, fetchBacktest]);

  useEffect(() => {
    if (tab === 'live') {
      fetchPtStatus();
      fetchNavHistory();
      const iv = setInterval(() => { fetchPtStatus(); fetchNavHistory(); }, 60_000);
      return () => clearInterval(iv);
    }
  }, [tab, fetchPtStatus, fetchNavHistory]);

  // ── Derived backtest chart data ───────────────────────────────────────────
  const btSeries = btData?.series ?? [];
  const btSummary = btData?.summary ?? {};

  // ── UI ────────────────────────────────────────────────────────────────────
  const tabStyle = (t) => ({
    padding: '8px 20px',
    fontFamily: 'JetBrains Mono',
    fontSize: 11,
    fontWeight: tab === t ? 700 : 400,
    letterSpacing: '0.08em',
    color: tab === t ? '#00E5FF' : '#64748B',
    background: tab === t ? 'rgba(0,229,255,0.07)' : 'transparent',
    border: 'none',
    borderBottom: tab === t ? '2px solid #00E5FF' : '2px solid transparent',
    cursor: 'pointer',
    transition: 'all 150ms',
  });

  return (
    <div style={{ padding: '24px 28px', color: '#F1F5F9', maxWidth: 1200 }}>
      {/* Page header */}
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontFamily: 'JetBrains Mono', fontSize: 18, fontWeight: 700, color: '#F1F5F9', margin: 0, letterSpacing: '0.04em' }}>
          Strategy Lab
        </h1>
        <p style={{ color: '#64748B', fontSize: 12, marginTop: 4, fontFamily: 'JetBrains Mono' }}>
          Backtest comparison + live paper trading · Momentum / Zen / Curvature
        </p>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', borderBottom: '1px solid #1E293B', marginBottom: 28 }}>
        <button style={tabStyle('backtest')} onClick={() => setTab('backtest')}>
          <BarChart2 size={12} style={{ marginRight: 6, verticalAlign: 'middle' }} />
          BACKTEST COMPARE
        </button>
        <button style={tabStyle('live')} onClick={() => setTab('live')}>
          <Activity size={12} style={{ marginRight: 6, verticalAlign: 'middle' }} />
          LIVE PAPER TRADING
        </button>
      </div>

      {/* ── BACKTEST TAB ──────────────────────────────────────────────────── */}
      {tab === 'backtest' && (
        <div>
          {/* Controls */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 24 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 11, color: '#64748B', fontFamily: 'JetBrains Mono' }}>LOTS</span>
              {[1, 2, 3].map((l) => (
                <button key={l} onClick={() => setLots(l)} style={{
                  padding: '4px 12px', fontFamily: 'JetBrains Mono', fontSize: 11,
                  background: lots === l ? 'rgba(0,229,255,0.1)' : 'transparent',
                  border: `1px solid ${lots === l ? '#00E5FF' : '#334155'}`,
                  color: lots === l ? '#00E5FF' : '#64748B',
                  borderRadius: 4, cursor: 'pointer',
                }}>
                  {l}
                </button>
              ))}
            </div>
            <button
              onClick={fetchBacktest}
              disabled={btLoading}
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '6px 16px', background: 'rgba(0,229,255,0.1)',
                border: '1px solid #00E5FF', color: '#00E5FF',
                borderRadius: 6, cursor: btLoading ? 'not-allowed' : 'pointer',
                fontFamily: 'JetBrains Mono', fontSize: 11, fontWeight: 700,
                opacity: btLoading ? 0.6 : 1,
              }}
            >
              <RefreshCw size={11} className={btLoading ? 'animate-spin' : ''} />
              {btLoading ? 'RUNNING...' : 'RUN BACKTEST'}
            </button>
            {btData && (
              <span style={{ fontSize: 10, color: '#64748B', fontFamily: 'JetBrains Mono' }}>
                {btData.summary?.zen?.sample_trading_days ?? 0} trading days · ₹1,00,000 capital · {lots} lots
              </span>
            )}
          </div>

          {btError && (
            <div style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid #EF4444', borderRadius: 8, padding: '12px 16px', marginBottom: 20, fontSize: 12, color: '#EF4444', fontFamily: 'JetBrains Mono' }}>
              <AlertCircle size={12} style={{ marginRight: 6 }} />{btError}
            </div>
          )}

          {btLoading && !btData && (
            <div style={{ textAlign: 'center', padding: '60px 0', color: '#64748B', fontFamily: 'JetBrains Mono', fontSize: 12 }}>
              <RefreshCw size={20} style={{ marginBottom: 12, animation: 'spin 1s linear infinite' }} />
              <br />Downloading 5-min NIFTY data and running 3 strategies...
              <br /><span style={{ fontSize: 10, marginTop: 8, display: 'block' }}>This may take ~30s on first run</span>
            </div>
          )}

          {btSeries.length > 0 && (
            <>
              {/* Equity curve chart */}
              <div style={{ background: 'rgba(15,23,42,0.6)', border: '1px solid #1E293B', borderRadius: 12, padding: '20px 16px', marginBottom: 24 }}>
                <div style={{ fontFamily: 'JetBrains Mono', fontSize: 11, color: '#94A3B8', letterSpacing: '0.08em', marginBottom: 16 }}>
                  EQUITY CURVE — NAV vs Date (₹1,00,000 starting capital)
                </div>
                <ResponsiveContainer width="100%" height={320}>
                  <LineChart data={btSeries} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" />
                    <XAxis
                      dataKey="date"
                      tick={{ fill: '#64748B', fontSize: 10, fontFamily: 'JetBrains Mono' }}
                      tickLine={false}
                      interval={Math.floor(btSeries.length / 6)}
                    />
                    <YAxis
                      tick={{ fill: '#64748B', fontSize: 10, fontFamily: 'JetBrains Mono' }}
                      tickLine={false}
                      tickFormatter={(v) => `₹${(v / 1000).toFixed(0)}k`}
                      width={56}
                    />
                    <Tooltip content={<CustomTooltip />} />
                    <Legend
                      formatter={(v) => STRATEGY_CONFIG[v]?.label ?? v}
                      wrapperStyle={{ fontFamily: 'JetBrains Mono', fontSize: 11, paddingTop: 12 }}
                    />
                    <ReferenceLine y={100000} stroke="#334155" strokeDasharray="4 4" label={{ value: '₹1L', fill: '#475569', fontSize: 9, fontFamily: 'JetBrains Mono' }} />
                    {Object.entries(STRATEGY_CONFIG).map(([key, cfg]) => (
                      <Line
                        key={key}
                        type="monotone"
                        dataKey={key}
                        stroke={cfg.color}
                        strokeWidth={2}
                        dot={false}
                        activeDot={{ r: 4, strokeWidth: 0 }}
                      />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              </div>

              {/* Summary cards */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 16 }}>
                {Object.entries(STRATEGY_CONFIG).map(([key, cfg]) => {
                  const s = btSummary[key] ?? {};
                  return (
                    <div key={key} style={{
                      background: 'rgba(15,23,42,0.8)',
                      border: `1px solid ${cfg.color}33`,
                      borderLeft: `3px solid ${cfg.color}`,
                      borderRadius: 10, padding: '16px 18px',
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
                        <div style={{ width: 8, height: 8, borderRadius: '50%', background: cfg.color }} />
                        <span style={{ fontFamily: 'JetBrains Mono', fontSize: 11, fontWeight: 700, color: '#F1F5F9', letterSpacing: '0.06em' }}>
                          {cfg.label.toUpperCase()}
                        </span>
                      </div>
                      <div style={{ fontSize: 10, color: '#64748B', marginBottom: 12, lineHeight: 1.4 }}>{cfg.desc}</div>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 16px' }}>
                        {[
                          ['Ann. ROC',    `${s.annualised_roc_pct ?? 0}%`,              (s.annualised_roc_pct ?? 0) > 0],
                          ['Ann. P&L',    fmtRs(s.annualised_pnl_1L),                  (s.annualised_pnl_1L ?? 0) > 0],
                          ['Win Rate',    `${s.win_rate_pct ?? 0}%`,                    (s.win_rate_pct ?? 0) > 50],
                          ['Trades',      s.n_trades ?? 0,                              undefined],
                          ['Sharpe',      s.sharpe ?? 0,                                (s.sharpe ?? 0) > 0.3],
                          ['Max DD',      fmtRs(s.max_drawdown_sample),                 false],
                        ].map(([lbl, val, pos]) => (
                          <div key={lbl}>
                            <div style={{ fontSize: 9, color: '#475569', fontFamily: 'JetBrains Mono', letterSpacing: '0.06em' }}>{lbl}</div>
                            <div style={{
                              fontSize: 12, fontFamily: 'JetBrains Mono', fontWeight: 700, marginTop: 1,
                              color: pos === undefined ? '#94A3B8' : pos ? '#10B981' : '#EF4444',
                            }}>{val}</div>
                          </div>
                        ))}
                      </div>
                      {key === 'curvature' && (
                        <div style={{ marginTop: 10, padding: '6px 8px', background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.2)', borderRadius: 4, fontSize: 9, color: '#F59E0B', fontFamily: 'JetBrains Mono', lineHeight: 1.5 }}>
                          ⚠ Proxy signal. Real Curvature (141% ROC) needs live IV chain data from chain_collector.py
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Meta notes */}
              {btData?.meta && (
                <div style={{ marginTop: 20, padding: '12px 16px', background: 'rgba(15,23,42,0.4)', border: '1px solid #1E293B', borderRadius: 8 }}>
                  {Object.entries(btData.meta).map(([k, v]) => (
                    <div key={k} style={{ fontSize: 10, color: '#475569', fontFamily: 'JetBrains Mono', marginBottom: 4 }}>
                      <span style={{ color: STRATEGY_CONFIG[k]?.color, marginRight: 6 }}>●</span>
                      <span style={{ color: '#64748B', marginRight: 6 }}>{STRATEGY_CONFIG[k]?.label}:</span>
                      {v}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* ── LIVE PAPER TRADING TAB ────────────────────────────────────────── */}
      {tab === 'live' && (
        <div>
          {/* Controls */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 24 }}>
            <button
              onClick={handlePaperStart}
              disabled={ptLoading || ptStatus?.running}
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '8px 18px', borderRadius: 6,
                background: ptStatus?.running ? 'rgba(16,185,129,0.08)' : 'rgba(16,185,129,0.15)',
                border: `1px solid ${ptStatus?.running ? '#064E3B' : '#10B981'}`,
                color: ptStatus?.running ? '#34D399' : '#10B981',
                fontFamily: 'JetBrains Mono', fontSize: 11, fontWeight: 700,
                cursor: ptStatus?.running || ptLoading ? 'not-allowed' : 'pointer',
                opacity: ptStatus?.running || ptLoading ? 0.6 : 1,
              }}
            >
              <Play size={11} />
              {ptStatus?.running ? 'RUNNING' : 'START PAPER TRADING'}
            </button>
            <button
              onClick={handlePaperStop}
              disabled={ptLoading || !ptStatus?.running}
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '8px 18px', borderRadius: 6,
                background: 'rgba(239,68,68,0.1)', border: '1px solid #EF4444',
                color: '#EF4444', fontFamily: 'JetBrains Mono', fontSize: 11, fontWeight: 700,
                cursor: !ptStatus?.running || ptLoading ? 'not-allowed' : 'pointer',
                opacity: !ptStatus?.running || ptLoading ? 0.5 : 1,
              }}
            >
              <Square size={11} />
              STOP
            </button>
            <button
              onClick={() => { fetchPtStatus(); fetchNavHistory(); }}
              style={{
                display: 'flex', alignItems: 'center', gap: 5,
                padding: '6px 12px', background: 'transparent',
                border: '1px solid #334155', color: '#64748B',
                borderRadius: 6, cursor: 'pointer',
                fontFamily: 'JetBrains Mono', fontSize: 11,
              }}
            >
              <RefreshCw size={11} />
              REFRESH
            </button>
            {ptStatus && (
              <span style={{ fontSize: 10, color: '#64748B', fontFamily: 'JetBrains Mono' }}>
                {ptStatus.running ? `● LIVE · session ${ptStatus.session_id} · ${ptStatus.tick_count} ticks` : '○ IDLE — click Start to begin paper trading'}
              </span>
            )}
          </div>

          {/* Strategy cards */}
          {ptStatus?.strategies && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 16, marginBottom: 28 }}>
              {Object.keys(STRATEGY_CONFIG).map((name) => (
                <StrategyCard key={name} name={name} data={ptStatus.strategies[name]} />
              ))}
            </div>
          )}

          {!ptStatus?.running && navHistory.length === 0 && (
            <div style={{
              padding: '40px 24px', textAlign: 'center',
              background: 'rgba(15,23,42,0.4)', border: '1px solid #1E293B', borderRadius: 12,
              marginBottom: 28,
            }}>
              <Clock size={28} color="#334155" style={{ marginBottom: 12 }} />
              <div style={{ fontFamily: 'JetBrains Mono', fontSize: 12, color: '#475569', marginBottom: 6 }}>
                No live data yet
              </div>
              <div style={{ fontSize: 11, color: '#334155', fontFamily: 'JetBrains Mono' }}>
                Start paper trading to begin tracking NAV. Data saves every 5 min to MongoDB.<br />
                When Monday market opens, all 3 strategies run in parallel automatically.
              </div>
            </div>
          )}

          {/* Live NAV chart */}
          {navHistory.length > 0 && (
            <div style={{ background: 'rgba(15,23,42,0.6)', border: '1px solid #1E293B', borderRadius: 12, padding: '20px 16px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <span style={{ fontFamily: 'JetBrains Mono', fontSize: 11, color: '#94A3B8', letterSpacing: '0.08em' }}>
                  LIVE NAV — All 3 Strategies (updates every 5 min)
                </span>
                {navLoading && <RefreshCw size={11} color="#64748B" style={{ animation: 'spin 1s linear infinite' }} />}
              </div>
              <ResponsiveContainer width="100%" height={320}>
                <LineChart data={navHistory} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" />
                  <XAxis
                    dataKey="date"
                    tick={{ fill: '#64748B', fontSize: 9, fontFamily: 'JetBrains Mono' }}
                    tickLine={false}
                    interval={Math.max(1, Math.floor(navHistory.length / 8))}
                  />
                  <YAxis
                    tick={{ fill: '#64748B', fontSize: 10, fontFamily: 'JetBrains Mono' }}
                    tickLine={false}
                    tickFormatter={(v) => `₹${(v / 1000).toFixed(0)}k`}
                    width={56}
                  />
                  <Tooltip content={<CustomTooltip />} />
                  <Legend
                    formatter={(v) => STRATEGY_CONFIG[v]?.label ?? v}
                    wrapperStyle={{ fontFamily: 'JetBrains Mono', fontSize: 11, paddingTop: 12 }}
                  />
                  <ReferenceLine y={100000} stroke="#334155" strokeDasharray="4 4" />
                  {Object.entries(STRATEGY_CONFIG).map(([key, cfg]) => (
                    <Line
                      key={key}
                      type="monotone"
                      dataKey={key}
                      stroke={cfg.color}
                      strokeWidth={2}
                      dot={false}
                      activeDot={{ r: 4, strokeWidth: 0 }}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Info banner */}
          <div style={{
            marginTop: 20, padding: '12px 16px',
            background: 'rgba(59,130,246,0.06)', border: '1px solid rgba(59,130,246,0.2)', borderRadius: 8,
            fontSize: 10, color: '#60A5FA', fontFamily: 'JetBrains Mono', lineHeight: 1.7,
          }}>
            <Zap size={11} style={{ marginRight: 6, verticalAlign: 'middle' }} />
            Paper trading uses live yfinance 5-min ^NSEI data · No real orders placed · All NAV saved to MongoDB<br />
            <span style={{ color: '#3B82F6' }}>Momentum</span>: directional NIFTY long/short ·{' '}
            <span style={{ color: '#10B981' }}>Zen</span>: credit spread overnight ·{' '}
            <span style={{ color: '#F59E0B' }}>Curvature</span>: hv5/hv20 proxy (real IV signal unlocks after 3mo chain data)
          </div>
        </div>
      )}
    </div>
  );
}
