import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import {
  Activity, TrendingUp, TrendingDown, Shield, Zap,
  RefreshCw, AlertCircle, CheckCircle, Clock, ChevronDown, ChevronUp
} from 'lucide-react';

const API = process.env.REACT_APP_BACKEND_URL + '/api';

const fmt = (n, dec = 0) =>
  new Intl.NumberFormat('en-IN', { maximumFractionDigits: dec }).format(n ?? 0);

const fmtRs = (n) =>
  `₹${fmt(Math.abs(n ?? 0), 0)}`;

const pct = (n) => `${(n ?? 0) > 0 ? '+' : ''}${((n ?? 0) * 100).toFixed(2)}%`;

const REGIME_COLOR = {
  fear: 'text-red-400', extreme_fear: 'text-red-600',
  neutral: 'text-yellow-400', greed: 'text-green-400',
};

const STRATEGY_LABELS = {
  iron_condor:      { label: 'Iron Condor',       color: 'bg-purple-500/20 text-purple-300 border-purple-500/40' },
  short_strangle:   { label: 'Short Strangle',    color: 'bg-orange-500/20 text-orange-300 border-orange-500/40' },
  bull_put_spread:  { label: 'Bull Put Spread',   color: 'bg-green-500/20  text-green-300  border-green-500/40' },
  bull_call_spread: { label: 'Bull Call Spread',  color: 'bg-blue-500/20   text-blue-300   border-blue-500/40' },
};

function GreeksRow({ label, value, suffix = '', colorFn }) {
  const color = colorFn ? colorFn(value) : 'text-slate-200';
  return (
    <div className="flex justify-between items-center py-1 border-b border-slate-700/40">
      <span className="text-slate-400 text-xs">{label}</span>
      <span className={`text-sm font-mono ${color}`}>
        {typeof value === 'number' ? value.toFixed(3) : value}{suffix}
      </span>
    </div>
  );
}

function StrategyCard({ strat }) {
  const meta = STRATEGY_LABELS[strat.strategy_name] || { label: strat.strategy_name, color: 'bg-slate-600/30 text-slate-300 border-slate-600' };
  const pnlColor = strat.current_pnl >= 0 ? 'text-green-400' : 'text-red-400';
  const stopped = strat.stopped;
  return (
    <div className={`rounded-xl border p-4 ${stopped ? 'opacity-50 border-slate-700' : 'border-slate-600'} bg-slate-800/60`}>
      <div className="flex items-center justify-between mb-3">
        <span className={`text-xs font-semibold px-2 py-0.5 rounded-full border ${meta.color}`}>
          {meta.label}
        </span>
        <span className={`text-xs ${stopped ? 'text-slate-500' : 'text-green-400'}`}>
          {stopped ? 'Closed' : 'Active'}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
        <div>
          <span className="text-slate-400 text-xs">Symbol</span>
          <div className="font-semibold text-white">{strat.symbol}</div>
        </div>
        <div>
          <span className="text-slate-400 text-xs">Expiry</span>
          <div className="text-slate-200 text-xs">{strat.expiry}</div>
        </div>
        <div>
          <span className="text-slate-400 text-xs">Entry Credit</span>
          <div className="text-green-400 font-semibold">{fmtRs(strat.entry_credit)}</div>
        </div>
        <div>
          <span className="text-slate-400 text-xs">Current P&L</span>
          <div className={`font-semibold ${pnlColor}`}>{fmtRs(strat.current_pnl)}</div>
        </div>
        <div>
          <span className="text-slate-400 text-xs">Max Loss</span>
          <div className="text-red-400 font-mono text-sm">{fmtRs(strat.max_loss)}</div>
        </div>
        <div>
          <span className="text-slate-400 text-xs">Θ/day</span>
          <div className="text-blue-300 font-mono text-sm">{fmtRs(strat.net_theta)}</div>
        </div>
      </div>
    </div>
  );
}

function OptionsChainTable({ chain }) {
  if (!chain) return null;
  const { calls = [], puts = [], strikes_near_atm = [], atm_strike, spot } = chain;

  const callsByStrike = Object.fromEntries(calls.map(c => [c.strike, c]));
  const putsByStrike  = Object.fromEntries(puts.map(p => [p.strike, p]));
  const strikes = strikes_near_atm.length ? strikes_near_atm : [...new Set([...calls.map(c=>c.strike),...puts.map(p=>p.strike)])].sort((a,b)=>a-b).slice(0,12);

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-slate-400 border-b border-slate-700">
            <th className="text-right py-2 pr-2">CE IV%</th>
            <th className="text-right py-2 pr-2">CE OI</th>
            <th className="text-right py-2 pr-2">CE LTP</th>
            <th className="text-center py-2 font-bold text-slate-300">Strike</th>
            <th className="text-left py-2 pl-2">PE LTP</th>
            <th className="text-left py-2 pl-2">PE OI</th>
            <th className="text-left py-2 pl-2">PE IV%</th>
          </tr>
        </thead>
        <tbody>
          {strikes.map(strike => {
            const ce = callsByStrike[strike] || {};
            const pe = putsByStrike[strike]  || {};
            const isATM = strike === atm_strike;
            return (
              <tr key={strike}
                className={`border-b border-slate-800 ${isATM ? 'bg-yellow-400/10 font-semibold' : 'hover:bg-slate-700/20'}`}>
                <td className="text-right pr-2 py-1.5 text-blue-300">{ce.iv ? ce.iv.toFixed(1) : '—'}</td>
                <td className="text-right pr-2 text-slate-400">{ce.oi ? fmt(ce.oi) : '—'}</td>
                <td className="text-right pr-2 text-green-300 font-mono">{ce.ltp ? fmt(ce.ltp, 1) : '—'}</td>
                <td className={`text-center px-2 ${isATM ? 'text-yellow-300 font-bold' : 'text-white'}`}>{fmt(strike)}</td>
                <td className="text-left pl-2 text-red-300 font-mono">{pe.ltp ? fmt(pe.ltp, 1) : '—'}</td>
                <td className="text-left pl-2 text-slate-400">{pe.oi ? fmt(pe.oi) : '—'}</td>
                <td className="text-left pl-2 text-blue-300">{pe.iv ? pe.iv.toFixed(1) : '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function Derivatives() {
  const [status, setStatus]       = useState(null);
  const [chain, setChain]         = useState(null);
  const [expiry, setExpiry]       = useState(null);
  const [events, setEvents]       = useState([]);
  const [chainSymbol, setChainSymbol] = useState('NIFTY');
  const [loadingChain, setLoadingChain] = useState(false);
  const [showChain, setShowChain] = useState(false);
  const [error, setError]         = useState(null);

  const fetchStatus = useCallback(() => {
    axios.get(`${API}/derivatives/status`)
      .then(r => setStatus(r.data))
      .catch(() => {});
  }, []);

  const fetchEvents = useCallback(() => {
    axios.get(`${API}/derivatives/events?limit=20`)
      .then(r => setEvents(r.data))
      .catch(() => {});
  }, []);

  const fetchExpiry = useCallback(() => {
    axios.get(`${API}/derivatives/expiry?symbol=${chainSymbol}`)
      .then(r => setExpiry(r.data))
      .catch(() => {});
  }, [chainSymbol]);

  const fetchChain = useCallback(() => {
    setLoadingChain(true);
    setError(null);
    axios.get(`${API}/derivatives/chain?symbol=${chainSymbol}`)
      .then(r => { setChain(r.data); setShowChain(true); })
      .catch(e => setError(e.response?.data?.detail || 'Chain fetch failed — market may be closed'))
      .finally(() => setLoadingChain(false));
  }, [chainSymbol]);

  useEffect(() => {
    fetchStatus();
    fetchEvents();
    fetchExpiry();
    const iv = setInterval(() => { fetchStatus(); fetchEvents(); }, 15000);
    return () => clearInterval(iv);
  }, [fetchStatus, fetchEvents, fetchExpiry]);

  const activeStrategies = status?.strategies?.filter(s => !s.stopped) ?? [];
  const closedStrategies = status?.strategies?.filter(s => s.stopped) ?? [];

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <Zap size={24} className="text-yellow-400" />
            Derivatives — NSE F&amp;O
          </h1>
          <p className="text-slate-400 text-sm mt-1">
            Index options engine · NIFTY/BANKNIFTY weekly · Iron Condor · Theta decay
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border ${
            status?.is_running ? 'bg-green-500/10 text-green-400 border-green-500/30' : 'bg-slate-700/40 text-slate-400 border-slate-600'
          }`}>
            <Activity size={12} className={status?.is_running ? 'animate-pulse' : ''} />
            {status?.is_running ? 'Loop Running' : 'Loop Stopped'}
          </div>
          <button
            onClick={() => { fetchStatus(); fetchEvents(); fetchExpiry(); }}
            className="p-2 rounded-lg bg-slate-700/60 hover:bg-slate-600/60 text-slate-400 hover:text-white transition-colors"
          >
            <RefreshCw size={16} />
          </button>
        </div>
      </div>

      {/* Stats Row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[
          {
            label: 'Active Strategies',
            value: status?.open_strategies ?? 0,
            icon: Shield,
            color: 'text-blue-400',
          },
          {
            label: 'Net Delta',
            value: status?.net_delta != null ? status.net_delta.toFixed(3) : '—',
            icon: Activity,
            color: Math.abs(status?.net_delta ?? 0) > 30 ? 'text-orange-400' : 'text-green-400',
          },
          {
            label: 'Daily Θ (Theta)',
            value: status?.net_theta != null ? fmtRs(status.net_theta) : '—',
            icon: Clock,
            color: (status?.net_theta ?? 0) > 0 ? 'text-green-400' : 'text-slate-400',
          },
          {
            label: 'Total P&L',
            value: status?.total_pnl != null ? fmtRs(status.total_pnl) : '—',
            icon: (status?.total_pnl ?? 0) >= 0 ? TrendingUp : TrendingDown,
            color: (status?.total_pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400',
          },
        ].map(({ label, value, icon: Icon, color }) => (
          <div key={label} className="bg-slate-800/60 rounded-xl border border-slate-700/60 p-4">
            <div className="flex items-center gap-2 mb-2">
              <Icon size={16} className={color} />
              <span className="text-slate-400 text-xs">{label}</span>
            </div>
            <div className={`text-xl font-bold ${color}`}>{value}</div>
          </div>
        ))}
      </div>

      {/* Expiry + Greeks row */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Expiry card */}
        <div className="bg-slate-800/60 rounded-xl border border-slate-700/60 p-4">
          <h3 className="text-slate-300 text-sm font-semibold mb-3 flex items-center gap-2">
            <Clock size={14} /> Expiry Calendar
          </h3>
          <div className="flex gap-2 mb-4">
            {['NIFTY', 'BANKNIFTY', 'FINNIFTY'].map(sym => (
              <button
                key={sym}
                onClick={() => setChainSymbol(sym)}
                className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${
                  chainSymbol === sym
                    ? 'bg-blue-500/20 text-blue-300 border-blue-500/40'
                    : 'bg-slate-700/40 text-slate-400 border-slate-700 hover:border-slate-500'
                }`}
              >{sym}</button>
            ))}
          </div>
          {expiry ? (
            <div className="space-y-2">
              <div className="flex justify-between text-sm">
                <span className="text-slate-400">Current Expiry</span>
                <span className="text-white font-mono">{expiry.current_expiry}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-slate-400">Next Expiry</span>
                <span className="text-slate-300 font-mono">{expiry.next_expiry}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-slate-400">Calendar DTE</span>
                <span className={`font-bold ${expiry.dte <= 2 ? 'text-red-400' : expiry.dte <= 4 ? 'text-yellow-400' : 'text-green-400'}`}>
                  {expiry.dte} days
                </span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-slate-400">Trading DTE</span>
                <span className="text-blue-300">{expiry.trading_dte} sessions</span>
              </div>
            </div>
          ) : (
            <div className="text-slate-500 text-sm">Loading…</div>
          )}
        </div>

        {/* Portfolio Greeks */}
        <div className="bg-slate-800/60 rounded-xl border border-slate-700/60 p-4">
          <h3 className="text-slate-300 text-sm font-semibold mb-3 flex items-center gap-2">
            <Activity size={14} /> Portfolio Greeks
          </h3>
          {status ? (
            <div className="space-y-1">
              <GreeksRow label="Net Δ Delta" value={status.net_delta ?? 0}
                colorFn={v => Math.abs(v) > 30 ? 'text-orange-400' : 'text-green-400'} />
              <GreeksRow label="Net Θ Theta (₹/day)" value={status.net_theta ?? 0}
                suffix="" colorFn={v => v > 0 ? 'text-green-400' : 'text-red-400'} />
              <GreeksRow label="Net ν Vega" value={status.net_vega ?? 0}
                colorFn={v => v < -3000 ? 'text-orange-400' : 'text-slate-300'} />
              <GreeksRow label="Open Strategies" value={status.open_strategies ?? 0}
                colorFn={() => 'text-blue-300'} />
              <GreeksRow label="Total P&L" value={`₹${fmt(status.total_pnl ?? 0, 0)}`}
                colorFn={() => (status.total_pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'} />
            </div>
          ) : (
            <div className="text-slate-500 text-sm">No data yet</div>
          )}
        </div>
      </div>

      {/* Active Strategies */}
      <div>
        <h2 className="text-slate-300 font-semibold mb-3 flex items-center gap-2">
          <CheckCircle size={16} className="text-green-400" />
          Active Strategies
          <span className="text-xs text-slate-500">({activeStrategies.length})</span>
        </h2>
        {activeStrategies.length === 0 ? (
          <div className="bg-slate-800/40 rounded-xl border border-slate-700/50 p-8 text-center">
            <Shield size={32} className="text-slate-600 mx-auto mb-2" />
            <div className="text-slate-400 text-sm">No active strategies</div>
            <div className="text-slate-500 text-xs mt-1">
              Loop runs every 5 min during NSE hours (09:15–14:30 IST). Strategies enter when conditions qualify.
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {activeStrategies.map((s, i) => <StrategyCard key={i} strat={s} />)}
          </div>
        )}
      </div>

      {/* Options Chain */}
      <div className="bg-slate-800/60 rounded-xl border border-slate-700/60 overflow-hidden">
        <button
          onClick={() => showChain ? setShowChain(false) : fetchChain()}
          className="w-full flex items-center justify-between p-4 hover:bg-slate-700/20 transition-colors"
        >
          <h3 className="text-slate-300 font-semibold flex items-center gap-2">
            <TrendingUp size={16} className="text-blue-400" />
            Options Chain — {chainSymbol}
            {chain && <span className="text-slate-500 text-xs ml-1">spot ₹{fmt(chain.spot)} · PCR {chain.pcr}</span>}
          </h3>
          <div className="flex items-center gap-2">
            {loadingChain && <RefreshCw size={14} className="animate-spin text-slate-400" />}
            {showChain ? <ChevronUp size={16} className="text-slate-400" /> : <ChevronDown size={16} className="text-slate-400" />}
          </div>
        </button>
        {showChain && (
          <div className="px-4 pb-4">
            {error && (
              <div className="flex items-center gap-2 text-orange-400 text-sm bg-orange-400/10 rounded-lg p-3 mb-3 border border-orange-400/20">
                <AlertCircle size={14} />
                {error}
              </div>
            )}
            {chain && <OptionsChainTable chain={chain} />}
            {!chain && !error && !loadingChain && (
              <div className="text-slate-500 text-sm text-center py-4">
                Click header to fetch live chain (requires market hours + NSE API access)
              </div>
            )}
          </div>
        )}
      </div>

      {/* Recent Events */}
      <div>
        <h2 className="text-slate-300 font-semibold mb-3 flex items-center gap-2">
          <AlertCircle size={16} className="text-yellow-400" />
          Recent Events
        </h2>
        {events.length === 0 ? (
          <div className="bg-slate-800/40 rounded-xl border border-slate-700/50 p-6 text-center text-slate-500 text-sm">
            No derivatives events yet — entries and exits will appear here
          </div>
        ) : (
          <div className="space-y-2">
            {events.slice(0, 10).map((ev, i) => (
              <div key={i} className="flex items-start gap-3 bg-slate-800/50 rounded-lg px-4 py-2.5 border border-slate-700/40">
                <div className={`mt-0.5 w-2 h-2 rounded-full flex-shrink-0 ${
                  ev.type === 'entry' ? 'bg-green-400' :
                  ev.type === 'exit'  ? 'bg-blue-400'  :
                  ev.type === 'entry_blocked' ? 'bg-yellow-400' : 'bg-slate-500'
                }`} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-slate-300 text-xs font-semibold">{ev.symbol}</span>
                    <span className="text-slate-500 text-xs">{ev.type}</span>
                    {ev.strategy && (
                      <span className="text-xs text-purple-300">{ev.strategy}</span>
                    )}
                    {ev.pnl !== 0 && (
                      <span className={`text-xs font-semibold ${ev.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {fmtRs(ev.pnl)}
                      </span>
                    )}
                  </div>
                  <div className="text-slate-500 text-xs mt-0.5 truncate">{ev.detail}</div>
                </div>
                <div className="text-slate-600 text-xs flex-shrink-0">
                  {ev.ts ? new Date(ev.ts).toLocaleTimeString('en-IN') : ''}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Closed Strategies */}
      {closedStrategies.length > 0 && (
        <div>
          <h2 className="text-slate-400 font-semibold mb-3 text-sm flex items-center gap-2">
            History ({closedStrategies.length})
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {closedStrategies.map((s, i) => <StrategyCard key={i} strat={s} />)}
          </div>
        </div>
      )}

      {/* Info footer */}
      <div className="bg-slate-900/60 rounded-xl border border-slate-800 p-4 text-xs text-slate-500 space-y-1">
        <div className="font-semibold text-slate-400 mb-2">How it works</div>
        <div>• Scans NIFTY weekly options chain every 5 minutes during 09:15–14:30 IST</div>
        <div>• Selects regime-aware strategy: Iron Condor (neutral), Short Strangle (fear/high IV), Bull Spreads (greed)</div>
        <div>• Greeks limits: |Δ| ≤ 50 · Vega ≥ −₹5,000 · Theta ≤ +₹2,000/day</div>
        <div>• Take profit at 50% of entry credit · Stop-out at 2× credit or ₹10,000 loss</div>
        <div>• Force-closes all positions at 15:00 IST (before MIS auto-squareoff at 15:20)</div>
      </div>
    </div>
  );
}
