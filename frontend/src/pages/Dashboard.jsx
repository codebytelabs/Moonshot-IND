import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { useNavigate } from 'react-router-dom';
import { useWebSocket } from '../hooks/useWebSocket';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Brush
} from 'recharts';
import { Play, Square, TrendingUp, TrendingDown } from 'lucide-react';
import { LOCAL_TZ_ABBR, IST_TZ_ABBR, nowIST, nowLocal, formatLocalDateTime } from '../utils/time';

const API = process.env.REACT_APP_BACKEND_URL + '/api';
const n = v => (v != null && v !== '' ? Number(v) : null);

function StatCard({ label, value, sub, color = 'var(--text-primary)', testId }) {
  return (
    <div
      data-testid={testId}
      style={{
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        padding: '16px 20px',
      }}
    >
      <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.15em', marginBottom: 8 }}>
        {label}
      </div>
      <div style={{ fontSize: 24, fontFamily: 'JetBrains Mono', fontWeight: 300, color, marginBottom: 2 }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{sub}</div>}
    </div>
  );
}

function RegimeBadge({ regime }) {
  const labels = {
    bull: 'BULL', neutral: 'NEUTRAL', fear: 'FEAR',
    choppy: 'CHOPPY', bear_mode: 'BEAR MODE', extreme_fear: 'EXTREME FEAR',
  };
  return (
    <span
      className={`regime-${regime}`}
      style={{
        fontFamily: 'JetBrains Mono', fontSize: 11, fontWeight: 700,
        letterSpacing: '0.12em', padding: '3px 8px', border: '1px solid',
        display: 'inline-block',
      }}
    >
      {labels[regime] || regime?.toUpperCase()}
    </span>
  );
}

const TIMEFRAMES = ['5m', '1H', '6H', '1D', '1W'];

export default function Dashboard() {
  const navigate = useNavigate();
  const { connected, lastEvent } = useWebSocket();
  const [account, setAccount] = useState(null);
  const [positions, setPositions] = useState([]);
  const [regime, setRegime] = useState(null);
  const [status, setStatus] = useState({ is_running: false, is_halted: false, loop_count: 0 });
  const [agentLogs, setAgentLogs] = useState([]);
  const [starting, setStarting] = useState(false);
  const [recentActivity, setRecentActivity] = useState([]);
  const [navTimeframe, setNavTimeframe] = useState('1D');
  const [navData, setNavData] = useState([]);
  const [countdown, setCountdown] = useState('');
  const [clocks, setClocks] = useState({ ist: '', local: '' });

  const fetchNav = useCallback(async (tf) => {
    try {
      const r = await axios.get(`${API}/nav?timeframe=${tf}`);
      const pts = (r.data.data || []).map(d => ({
        ts: d.ts,
        value: d.value,
        label: ['5m', '1H'].includes(tf)
          ? new Date(d.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
          : new Date(d.ts).toLocaleDateString([], { month: 'short', day: 'numeric' }),
      }));
      setNavData(pts);
    } catch (e) {}
  }, []);


  const fetchData = useCallback(async () => {
    try {
      const [accR, posR, regR, statusR, logsR] = await Promise.all([
        axios.get(`${API}/account`),
        axios.get(`${API}/positions`),
        axios.get(`${API}/regime`),
        axios.get(`${API}/system/status`),
        axios.get(`${API}/agent-logs?limit=50`),
      ]);
      setAccount(accR.data);
      setPositions(posR.data);
      setRegime(regR.data);
      setStatus(statusR.data);
      setAgentLogs(logsR.data);
    } catch (e) {}
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 15000);
    return () => clearInterval(interval);
  }, [fetchData]);

  useEffect(() => {
    fetchNav(navTimeframe);
    const interval = setInterval(() => fetchNav(navTimeframe), 60000);
    return () => clearInterval(interval);
  }, [navTimeframe, fetchNav]);

  useEffect(() => {
    const tick = () => {
      setClocks({ ist: nowIST(), local: nowLocal() });
      if (!account) return setCountdown('');
      const target = account.is_market_open ? account.next_close : account.next_open;
      if (!target) return setCountdown('');
      const diff = new Date(target) - Date.now();
      if (diff <= 0) return setCountdown('00:00:00');
      const h = Math.floor(diff / 3600000);
      const m = Math.floor((diff % 3600000) / 60000);
      const s = Math.floor((diff % 60000) / 1000);
      setCountdown(`${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`);
    };
    tick();
    const interval = setInterval(tick, 1000);
    return () => clearInterval(interval);
  }, [account]);

  useEffect(() => {
    if (!lastEvent) return;
    if (lastEvent.type === 'system_status') {
      setStatus(prev => ({ ...prev, is_running: lastEvent.is_running, is_halted: lastEvent.is_halted }));
    }
    if (lastEvent.type === 'loop_tick') {
      setStatus(prev => ({ ...prev, loop_count: lastEvent.loop_count }));
      if (lastEvent.regime_data) setRegime(lastEvent.regime_data);
    }
    if (lastEvent.type === 'trade_executed' || lastEvent.type === 'pipeline_complete' || lastEvent.type === 'pipeline_start') {
      setRecentActivity(prev => [{ ...lastEvent, id: Date.now() }, ...prev.slice(0, 9)]);
    }
    if (lastEvent.type === 'position_update') {
      setPositions(lastEvent.positions || []);
    }
  }, [lastEvent]);

  const toggleSystem = async () => {
    setStarting(true);
    try {
      if (status.is_running) {
        await axios.post(`${API}/system/stop`);
      } else {
        if (status.is_halted) await axios.post(`${API}/system/reset`);
        await axios.post(`${API}/system/start`);
      }
      await fetchData();
    } catch (e) {}
    setStarting(false);
  };

  const dailyPnl = account ? account.daily_pnl : 0;
  const portfolioValue = account ? account.portfolio_value : 0;
  const isPositive = dailyPnl >= 0;

  return (
    <div style={{ padding: 24 }}>
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontFamily: 'JetBrains Mono', fontSize: 20, fontWeight: 700, letterSpacing: '0.08em', color: 'var(--text-primary)' }}>
            TRADING DASHBOARD
          </h1>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span>{account?.is_market_open ? '🟢 Market Open' : '🔴 Market Closed'}</span>
            <span style={{ color: 'rgba(255,255,255,0.12)' }}>│</span>
            <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10 }}>
              {account?.is_market_open ? 'Closes in ' : 'Opens in '}
              <span style={{ color: account?.is_market_open ? 'var(--danger)' : 'var(--success)', letterSpacing: '0.05em' }}>
                {countdown || '—'}
              </span>
            </span>
            <span style={{ color: 'rgba(255,255,255,0.12)' }}>│</span>
            <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10 }}>
              <span style={{ color: 'rgba(255,255,255,0.4)' }}>IST </span>
              <span style={{ color: 'var(--text-primary)' }}>{clocks.ist}</span>
              <span style={{ color: 'rgba(255,255,255,0.4)' }}> {IST_TZ_ABBR}</span>
            </span>
            <span style={{ color: 'rgba(255,255,255,0.12)' }}>│</span>
            <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10 }}>
              <span style={{ color: 'rgba(255,255,255,0.4)' }}>Local </span>
              <span style={{ color: 'var(--text-primary)' }}>{clocks.local}</span>
              <span style={{ color: 'rgba(255,255,255,0.4)' }}> {LOCAL_TZ_ABBR}</span>
            </span>
            <span style={{ color: 'rgba(255,255,255,0.12)' }}>│</span>
            <span>Paper Trading</span>
          </div>
        </div>
        <button
          data-testid="system-toggle-btn"
          onClick={toggleSystem}
          disabled={starting || status.is_halted}
          style={{
            background: status.is_running ? 'transparent' : 'rgba(0,229,255,0.1)',
            border: `1px solid ${status.is_running ? 'rgba(255,59,48,0.5)' : 'var(--border-active)'}`,
            color: status.is_running ? '#FF3B30' : 'var(--primary)',
            padding: '8px 20px',
            fontFamily: 'JetBrains Mono',
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: '0.12em',
            cursor: starting || status.is_halted ? 'not-allowed' : 'pointer',
            display: 'flex', alignItems: 'center', gap: 6,
            opacity: starting ? 0.6 : 1,
          }}
        >
          {status.is_running ? <><Square size={12} /> PAUSE</> : <><Play size={12} /> RESUME</>}
        </button>
      </div>

      {/* Stats Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20 }}>
        <StatCard
          testId="portfolio-value-stat"
          label="PORTFOLIO VALUE"
          value={portfolioValue ? `₹${portfolioValue.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—'}
          sub="Paper account"
          color="var(--text-primary)"
        />
        <StatCard
          testId="daily-pnl-stat"
          label="DAILY P&L"
          value={dailyPnl !== undefined ? `${isPositive ? '+' : ''}₹${dailyPnl.toFixed(2)}` : '—'}
          sub={`${dailyPnl && portfolioValue ? ((dailyPnl / portfolioValue) * 100).toFixed(2) : '0.00'}% today`}
          color={isPositive ? 'var(--success)' : 'var(--danger)'}
        />
        <StatCard
          testId="open-positions-stat"
          label="OPEN POSITIONS"
          value={positions.length}
          sub={`${regime ? `Max ${[5,4,3,0,0,0][['bull','neutral','fear','choppy','bear_mode','extreme_fear'].indexOf(regime.regime)] ?? 4}` : ''} in ${regime?.regime || 'neutral'}`}
          color="var(--primary)"
        />
        <StatCard
          testId="system-loops-stat"
          label="LOOP COUNT"
          value={status.loop_count}
          sub={status.is_running ? 'Active — 60s cycle' : 'System idle'}
          color={status.is_running ? 'var(--success)' : 'var(--text-muted)'}
        />
      </div>

      {/* NAV Chart + Regime row */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 300px', gap: 16, marginBottom: 16 }}>
        {/* NAV Chart */}
        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', padding: '16px 20px 8px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
            <div>
              <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.15em' }}>PORTFOLIO NAV</div>
              {navData.length >= 2 && (() => {
                const first = navData[0].value;
                const last = navData[navData.length - 1].value;
                const diff = last - first;
                const pct = first > 0 ? (diff / first) * 100 : 0;
                const color = diff >= 0 ? 'var(--success)' : 'var(--danger)';
                return (
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 2 }}>
                    <span style={{ fontFamily: 'JetBrains Mono', fontSize: 20, color: 'var(--text-primary)' }}>
                      ₹{last?.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
                    </span>
                    <span style={{ fontFamily: 'JetBrains Mono', fontSize: 11, color }}>
                      {diff >= 0 ? '+' : ''}₹{diff.toFixed(2)} ({pct >= 0 ? '+' : ''}{pct.toFixed(3)}%)
                    </span>
                  </div>
                );
              })()}
            </div>
            <div style={{ display: 'flex', gap: 4 }}>
              {TIMEFRAMES.map(tf => (
                <button key={tf} onClick={() => setNavTimeframe(tf)} style={{
                  padding: '3px 10px', fontSize: 10, fontFamily: 'JetBrains Mono',
                  cursor: 'pointer', letterSpacing: '0.08em',
                  background: navTimeframe === tf ? 'rgba(0,229,255,0.15)' : 'transparent',
                  border: `1px solid ${navTimeframe === tf ? 'var(--border-active)' : 'var(--border)'}`,
                  color: navTimeframe === tf ? 'var(--primary)' : 'var(--text-muted)',
                }}>{tf}</button>
              ))}
            </div>
          </div>
          {navData.length >= 2 ? (
            <ResponsiveContainer width="100%" height={260}>
              <AreaChart data={navData} margin={{ top: 8, right: 12, left: 8, bottom: 0 }}>
                <defs>
                  <linearGradient id="navGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#00E5FF" stopOpacity={0.2} />
                    <stop offset="95%" stopColor="#00E5FF" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
                <XAxis
                  dataKey="label"
                  tick={{ fontFamily: 'JetBrains Mono', fontSize: 9, fill: 'var(--text-muted)' }}
                  interval="preserveStartEnd"
                  axisLine={{ stroke: 'var(--border)' }}
                  tickLine={false}
                />
                <YAxis
                  domain={['auto', 'auto']}
                  tick={{ fontFamily: 'JetBrains Mono', fontSize: 9, fill: 'var(--text-muted)' }}
                  tickFormatter={v => `₹${(v / 1000).toFixed(1)}k`}
                  axisLine={false}
                  tickLine={false}
                  width={52}
                />
                <Tooltip
                  contentStyle={{ background: '#0D0D10', border: '1px solid var(--border)', fontFamily: 'JetBrains Mono', fontSize: 11, borderRadius: 0 }}
                  formatter={v => [`₹${v?.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`, 'NAV']}
                  labelStyle={{ color: 'var(--text-muted)', fontSize: 10 }}
                  cursor={{ stroke: 'rgba(0,229,255,0.3)', strokeWidth: 1 }}
                />
                <Area type="monotone" dataKey="value" stroke="#00E5FF" fill="url(#navGrad)" strokeWidth={1.5} dot={false} activeDot={{ r: 3, fill: '#00E5FF' }} />
                <Brush
                  dataKey="label"
                  height={22}
                  stroke="var(--border)"
                  fill="rgba(0,0,0,0.3)"
                  travellerWidth={6}
                  tickFormatter={() => ''}
                  startIndex={Math.max(0, navData.length - Math.floor(navData.length * 0.4))}
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div style={{ height: 260, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', fontSize: 11, fontFamily: 'JetBrains Mono' }}>
              NO NAV DATA — WAITING FOR LOOP
            </div>
          )}
        </div>

        {/* Regime Panel */}
        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', padding: 20 }}>
          <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.15em', marginBottom: 16 }}>MARKET REGIME</div>
          {regime ? (
            <div>
              <div style={{ marginBottom: 16 }}><RegimeBadge regime={regime.regime} /></div>
              {[
                { label: 'VIX', value: regime.india_vix?.toFixed(1), bar: Math.min(100, (regime.india_vix / 50) * 100), color: regime.india_vix > 28 ? '#FF3B30' : regime.india_vix > 20 ? '#FFCC00' : '#00FF87' },
                { label: 'FEAR/GREED', value: regime.fear_greed?.toFixed(0) + '/100', bar: regime.fear_greed, color: regime.fear_greed < 30 ? '#FF3B30' : regime.fear_greed > 70 ? '#00FF87' : '#FFCC00' },
                { label: 'NIFTY 20D', value: (regime.nifty_20d_return >= 0 ? '+' : '') + regime.nifty_20d_return?.toFixed(2) + '%', bar: Math.min(100, Math.max(0, 50 + regime.nifty_20d_return * 5)), color: regime.nifty_20d_return >= 0 ? '#00FF87' : '#FF3B30' },
                { label: 'BREADTH', value: (regime.breadth * 100)?.toFixed(0) + '%', bar: regime.breadth * 100, color: regime.breadth > 0.6 ? '#00FF87' : regime.breadth < 0.3 ? '#FF3B30' : '#FFCC00' },
              ].map(({ label, value, bar, color }) => (
                <div key={label} style={{ marginBottom: 14 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <span style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.1em' }}>{label}</span>
                    <span style={{ fontSize: 11, fontFamily: 'JetBrains Mono', color }}>{value}</span>
                  </div>
                  <div style={{ height: 2, background: 'var(--border)', borderRadius: 1 }}>
                    <div style={{ height: '100%', width: `${bar}%`, background: color, borderRadius: 1, transition: 'width 0.5s ease' }} />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ color: 'var(--text-muted)', fontSize: 12, fontFamily: 'JetBrains Mono' }}>LOADING...</div>
          )}
        </div>
      </div>

      {/* Full-width Positions Table */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 20px', borderBottom: '1px solid var(--border)' }}>
          <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.15em' }}>
            OPEN POSITIONS ({positions.length})
          </div>
          <button
            onClick={() => navigate('/positions')}
            style={{ background: 'transparent', border: '1px solid var(--border)', color: 'var(--text-muted)', padding: '3px 10px', fontSize: 9, fontFamily: 'JetBrains Mono', cursor: 'pointer', letterSpacing: '0.1em' }}
          >
            FULL VIEW →
          </button>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                {['TICKER', 'SIDE', 'QTY', 'ENTRY', 'CURRENT', 'MKT VALUE', 'STOP LOSS', 'TRAIL STOP', 'T1 +5%', 'T2 +10%', 'TAKE PROFIT', 'P&L (₹)', 'RETURN'].map(h => (
                  <th key={h} style={{
                    padding: '8px 14px',
                    textAlign: h === 'TICKER' || h === 'SIDE' ? 'left' : 'right',
                    fontSize: 9, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono',
                    letterSpacing: '0.12em', fontWeight: 500, whiteSpace: 'nowrap',
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {positions.length === 0 ? (
                <tr><td colSpan={13} style={{ padding: '30px 14px', textAlign: 'center', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', fontSize: 11 }}>NO OPEN POSITIONS — WAITING FOR ENTRY SIGNALS</td></tr>
              ) : (
                positions.map(pos => {
                  const pnlPos = n(pos.unrealized_pnl) >= 0;
                  return (
                    <tr
                      key={pos.ticker}
                      onClick={() => navigate('/positions')}
                      className="table-row-hover"
                      style={{ borderBottom: '1px solid rgba(255,255,255,0.04)', cursor: 'pointer' }}
                    >
                      <td style={{ padding: '10px 14px' }}>
                        <span style={{ fontFamily: 'JetBrains Mono', fontWeight: 700, fontSize: 13 }}>{pos.ticker}</span>
                      </td>
                      <td style={{ padding: '10px 14px' }}>
                        <span style={{
                          fontSize: 9, fontFamily: 'JetBrains Mono', letterSpacing: '0.1em', padding: '2px 5px', border: '1px solid',
                          color: pos.side === 'long' ? 'var(--success)' : 'var(--danger)',
                          borderColor: pos.side === 'long' ? 'rgba(0,255,135,0.3)' : 'rgba(255,59,48,0.3)',
                        }}>{pos.side?.toUpperCase()}</span>
                      </td>
                      <td style={{ padding: '10px 14px', textAlign: 'right', fontFamily: 'JetBrains Mono', fontSize: 12 }}>{pos.qty}</td>
                      <td style={{ padding: '10px 14px', textAlign: 'right', fontFamily: 'JetBrains Mono', fontSize: 12 }}>₹{n(pos.entry_price)?.toFixed(2)}</td>
                      <td style={{ padding: '10px 14px', textAlign: 'right', fontFamily: 'JetBrains Mono', fontSize: 12, color: 'var(--primary)' }}>₹{n(pos.current_price)?.toFixed(2)}</td>
                      <td style={{ padding: '10px 14px', textAlign: 'right', fontFamily: 'JetBrains Mono', fontSize: 12 }}>₹{n(pos.market_value)?.toFixed(0)}</td>
                      <td style={{ padding: '10px 14px', textAlign: 'right', fontFamily: 'JetBrains Mono', fontSize: 12, color: n(pos.stop_loss) > 0 ? '#FF3B30' : 'var(--text-muted)' }}>
                        {n(pos.stop_loss) > 0 ? `₹${n(pos.stop_loss).toFixed(2)}` : '—'}
                      </td>
                      <td style={{ padding: '10px 14px', textAlign: 'right', fontFamily: 'JetBrains Mono', fontSize: 12 }}>
                        {pos.trailing_active ? (
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 4 }}>
                            <span style={{ fontSize: 8, color: '#FF9500', fontFamily: 'JetBrains Mono', letterSpacing: '0.08em', border: '1px solid rgba(255,149,0,0.4)', padding: '1px 3px' }}>TRAIL</span>
                            <span style={{ color: '#FF9500' }}>₹{n(pos.trailing_stop)?.toFixed(2)}</span>
                          </div>
                        ) : (
                          <span style={{ color: 'var(--text-muted)', fontSize: 10 }}>inactive</span>
                        )}
                      </td>
                      <td style={{ padding: '10px 14px', textAlign: 'right', fontFamily: 'JetBrains Mono', fontSize: 12, color: '#FFCC00' }}>
                        {n(pos.target_1) > 0 ? `₹${n(pos.target_1).toFixed(2)}` : '—'}
                      </td>
                      <td style={{ padding: '10px 14px', textAlign: 'right', fontFamily: 'JetBrains Mono', fontSize: 12, color: '#FFCC00' }}>
                        {n(pos.target_2) > 0 ? `₹${n(pos.target_2).toFixed(2)}` : '—'}
                      </td>
                      <td style={{ padding: '10px 14px', textAlign: 'right', fontFamily: 'JetBrains Mono', fontSize: 12, color: n(pos.take_profit) > 0 ? '#00FF87' : 'var(--text-muted)' }}>
                        {n(pos.take_profit) > 0 ? `₹${n(pos.take_profit).toFixed(2)}` : '—'}
                      </td>
                      <td style={{ padding: '10px 14px', textAlign: 'right' }}>
                        <div style={{ fontFamily: 'JetBrains Mono', fontSize: 12, color: pnlPos ? 'var(--success)' : 'var(--danger)' }}>
                          {pnlPos ? '+' : ''}₹{n(pos.unrealized_pnl)?.toFixed(2)}
                        </div>
                      </td>
                      <td style={{ padding: '10px 14px', textAlign: 'right' }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 4 }}>
                          {pnlPos ? <TrendingUp size={11} color="var(--success)" /> : <TrendingDown size={11} color="var(--danger)" />}
                          <span style={{ fontFamily: 'JetBrains Mono', fontSize: 11, color: pnlPos ? 'var(--success)' : 'var(--danger)' }}>
                            {n(pos.unrealized_pnl_pct) >= 0 ? '+' : ''}{n(pos.unrealized_pnl_pct)?.toFixed(2)}%
                          </span>
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Full-width Agent Activity Table */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
        <div style={{ padding: '12px 20px', borderBottom: '1px solid var(--border)' }}>
          <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.15em' }}>RECENT AGENT ACTIVITY</div>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                {['TIME', 'TICKER', 'DECISION', 'REGIME', 'BAYESIAN', 'AGENTS', 'DURATION', 'REASONING'].map(h => (
                  <th key={h} style={{
                    padding: '8px 14px',
                    textAlign: h === 'TICKER' || h === 'DECISION' || h === 'REASON' ? 'left' : 'right',
                    fontSize: 9, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono',
                    letterSpacing: '0.12em', fontWeight: 500, whiteSpace: 'nowrap',
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {agentLogs.length === 0 && recentActivity.length === 0 ? (
                <tr><td colSpan={8} style={{ padding: '30px 14px', textAlign: 'center', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', fontSize: 11 }}>NO AGENT ACTIVITY YET</td></tr>
              ) : (
                [...recentActivity.slice(0, 5), ...agentLogs].slice(0, 20).map((log, i) => {
                  const approved = log.decision === 'APPROVE';
                  const ts = log.created_at || log.timestamp;
                  return (
                    <tr key={log.decision_id || log.id || i} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                      <td style={{ padding: '8px 14px', textAlign: 'right', fontFamily: 'JetBrains Mono', fontSize: 10, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                        {ts ? formatLocalDateTime(ts) : '—'}
                      </td>
                      <td style={{ padding: '8px 14px', fontFamily: 'JetBrains Mono', fontWeight: 600, fontSize: 12 }}>{log.ticker || '—'}</td>
                      <td style={{ padding: '8px 14px' }}>
                        <span style={{
                          fontSize: 9, padding: '2px 6px', border: '1px solid', fontFamily: 'JetBrains Mono', letterSpacing: '0.08em',
                          color: approved ? 'var(--success)' : 'var(--danger)',
                          borderColor: approved ? 'rgba(0,255,135,0.3)' : 'rgba(255,59,48,0.3)',
                          background: approved ? 'rgba(0,255,135,0.06)' : 'rgba(255,59,48,0.06)',
                        }}>{log.decision || log.type?.toUpperCase() || '—'}</span>
                      </td>
                      <td style={{ padding: '8px 14px', textAlign: 'right', fontFamily: 'JetBrains Mono', fontSize: 10, color: 'var(--text-muted)' }}>{log.regime || '—'}</td>
                      <td style={{ padding: '8px 14px', textAlign: 'right', fontFamily: 'JetBrains Mono', fontSize: 11, color: 'var(--text-primary)' }}>
                        {log.bayesian_score != null ? log.bayesian_score.toFixed(3) : '—'}
                      </td>
                      <td style={{ padding: '8px 14px', textAlign: 'right', fontFamily: 'JetBrains Mono', fontSize: 11, color: 'var(--text-muted)' }}>
                        {log.agent_count != null ? log.agent_count : '—'}
                      </td>
                      <td style={{ padding: '8px 14px', textAlign: 'right', fontFamily: 'JetBrains Mono', fontSize: 10, color: 'var(--text-muted)' }}>
                        {log.duration_s != null ? `${log.duration_s}s` : '—'}
                      </td>
                      <td style={{ padding: '8px 14px', fontFamily: 'JetBrains Mono', fontSize: 10, color: 'var(--text-muted)', maxWidth: 300 }}>
                        <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {log.reasoning || '—'}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
