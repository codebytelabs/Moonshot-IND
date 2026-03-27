import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';

const API = process.env.REACT_APP_BACKEND_URL + '/api';

const CONFIG_ITEMS = [
  { section: 'RISK MANAGEMENT', items: [
    { label: 'Daily Loss Limit', key: 'max_daily_loss_pct', type: 'pct', value: 3, desc: 'Stop new entries if daily loss exceeds this %' },
    { label: 'Max Drawdown', key: 'max_drawdown_pct', type: 'pct', value: 15, desc: 'Full system halt if portfolio drawdown exceeds this %' },
    { label: 'Max Daily Trades', key: 'max_daily_trades', type: 'num', value: 15, desc: 'Maximum new positions per trading day' },
    { label: 'Risk Per Trade', key: 'risk_per_trade_pct', type: 'pct', value: 1.5, desc: 'Maximum portfolio % risked on each trade' },
    { label: 'Max Position Size', key: 'max_single_position_pct', type: 'pct', value: 4, desc: 'Maximum single position as % of portfolio' },
  ]},
  { section: 'ENTRY CONTROLS', items: [
    { label: 'Bayesian Threshold', key: 'bayesian_threshold', type: 'score', value: 0.45, desc: 'Minimum Bayesian score to enter agent pipeline' },
    { label: 'Symbol Cooldown', key: 'symbol_cooldown_hours', type: 'hours', value: 4, desc: 'Hours before re-entering a ticker after exit' },
    { label: 'Earnings Blackout', key: 'earnings_blackout_hours', type: 'hours', value: 48, desc: 'Hours before earnings to block new entries' },
  ]},
  { section: 'LLM CONFIGURATION', items: [
    { label: 'Daily LLM Budget', key: 'llm_daily_cap_usd', type: 'usd', value: 25, desc: 'Maximum daily spend on LLM API calls' },
    { label: 'LLM Alert Threshold', key: 'llm_alert_usd', type: 'usd', value: 15, desc: 'Send alert when LLM cost exceeds this' },
    { label: 'Quick-Think Model', key: 'quick_model', type: 'text', value: '__config__', desc: 'Model for 10 quick-think agents (primary + fallback)' },
    { label: 'Deep-Think Model', key: 'deep_model', type: 'text', value: '__config__', desc: 'Model for Research Manager & Portfolio Manager (primary + fallback)' },
  ]},
  { section: 'BEAR MODE', items: [
    { label: 'Bear VIX Trigger', key: 'bear_vix_trigger', type: 'num', value: 28, desc: 'VIX level that activates bear mode consideration' },
    { label: 'Max Inverse ETF Alloc', key: 'max_inverse_etf_pct', type: 'pct', value: 15, desc: 'Maximum portfolio allocation to inverse ETFs' },
    { label: 'Confirm Loops', key: 'bear_confirm_loops', type: 'num', value: 3, desc: 'Consecutive bear regime loops before activation' },
  ]},
];

export default function Settings() {
  const [status, setStatus] = useState(null);
  const [account, setAccount] = useState(null);
  const [config, setConfig] = useState(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    axios.get(`${API}/system/status`).then(r => setStatus(r.data)).catch(() => {});
    axios.get(`${API}/account`).then(r => setAccount(r.data)).catch(() => {});
    axios.get(`${API}/config`).then(r => setConfig(r.data)).catch(() => {});
  }, []);

  const handleSave = () => {
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontFamily: 'JetBrains Mono', fontSize: 20, fontWeight: 700, letterSpacing: '0.08em' }}>SYSTEM CONFIGURATION</h1>
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
          MoonshotX-IND v1.1 — Configuration reference &nbsp;•&nbsp; Changes require system restart to take effect
        </div>
      </div>

      {/* System Info */}
      {(status || account) && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginBottom: 24 }}>
          {[
            { label: 'SYSTEM STATUS', value: status?.is_halted ? 'HALTED' : status?.is_running ? 'RUNNING' : 'IDLE', color: status?.is_running ? 'var(--success)' : status?.is_halted ? 'var(--danger)' : 'var(--text-muted)' },
            { label: 'BROKER', value: 'Zerodha Kite · NSE', color: 'var(--primary)' },
            { label: 'LLM PROVIDER', value: 'Ollama / OpenRouter', color: 'var(--primary)' },
            { label: 'ACCOUNT STATUS', value: account?.status?.toUpperCase() || '—', color: account?.status === 'ACTIVE' ? 'var(--success)' : 'var(--text-muted)' },
          ].map(({ label, value, color }) => (
            <div key={label} style={{ background: 'var(--surface)', border: '1px solid var(--border)', padding: '12px 16px' }}>
              <div style={{ fontSize: 9, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.15em', marginBottom: 6 }}>{label}</div>
              <div style={{ fontFamily: 'JetBrains Mono', fontSize: 13, color }}>{value}</div>
            </div>
          ))}
        </div>
      )}

      {/* Config Sections */}
      {CONFIG_ITEMS.map(({ section, items }) => (
        <div key={section} style={{ background: 'var(--surface)', border: '1px solid var(--border)', marginBottom: 16 }}>
          <div style={{
            padding: '12px 20px', borderBottom: '1px solid var(--border)',
            fontSize: 10, color: 'var(--primary)', fontFamily: 'JetBrains Mono', letterSpacing: '0.15em', fontWeight: 600,
          }}>
            {section}
          </div>
          <div style={{ padding: '8px 0' }}>
            {items.map(({ label, key, type, value, desc }) => (
              <div key={key} style={{
                display: 'grid', gridTemplateColumns: '1fr 200px',
                padding: '12px 20px', borderBottom: '1px solid rgba(255,255,255,0.04)',
                alignItems: 'center', gap: 16,
              }}>
                <div>
                  <div style={{ fontSize: 12, color: 'var(--text-primary)', marginBottom: 2 }}>{label}</div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{desc}</div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  {type === 'text' ? (
                    <input
                      value={
                        value === '__config__'
                          ? (key === 'quick_model'
                              ? (config ? `${config.quick_model}` : '...')
                              : (config ? `${config.deep_model}` : '...'))
                          : value
                      }
                      readOnly
                      style={{
                        background: 'var(--surface-2, #0D0D10)', border: '1px solid var(--border)',
                        color: 'var(--primary)', padding: '6px 10px', width: '100%',
                        fontFamily: 'JetBrains Mono', fontSize: 11, outline: 'none',
                      }}
                    />
                  ) : (
                    <>
                      <input
                        type="number"
                        defaultValue={value}
                        readOnly
                        style={{
                          background: 'var(--surface-2, #0D0D10)', border: '1px solid var(--border)',
                          color: 'var(--text-primary)', padding: '6px 10px', width: 80,
                          fontFamily: 'JetBrains Mono', fontSize: 12, textAlign: 'right', outline: 'none',
                        }}
                      />
                      <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                        {type === 'pct' ? '%' : type === 'hours' ? 'hrs' : type === 'usd' ? 'USD' : type === 'score' ? 'score' : ''}
                      </span>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}

      {/* Agent Roster */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', marginBottom: 16 }}>
        <div style={{ padding: '12px 20px', borderBottom: '1px solid var(--border)', fontSize: 10, color: 'var(--primary)', fontFamily: 'JetBrains Mono', letterSpacing: '0.15em', fontWeight: 600 }}>
          AGENT ROSTER (12 AGENTS)
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)' }}>
              {['AGENT', 'LLM TIER', 'ROLE', 'BLOCKING', 'TIMEOUT'].map(h => (
                <th key={h} style={{ padding: '8px 20px', textAlign: 'left', fontSize: 9, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.12em', fontWeight: 500 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {[
              ['Technical Analyst', 'QUICK', 'Analyst', 'YES', '20s'],
              ['News Analyst', 'QUICK', 'Analyst', 'YES', '20s'],
              ['Sentiment Analyst', 'QUICK', 'Analyst', 'Optional', '20s'],
              ['Fundamentals Analyst', 'QUICK', 'Analyst', 'Optional', '20s'],
              ['Bull Researcher', 'QUICK', 'Researcher', 'YES', '15s'],
              ['Bear Researcher', 'QUICK', 'Researcher', 'YES', '15s'],
              ['Research Manager', 'DEEP', 'Manager', 'YES', '25s'],
              ['Trader', 'QUICK', 'Trader', 'YES', '15s'],
              ['Aggressive Analyst', 'QUICK', 'Risk', 'YES', '12s'],
              ['Neutral Analyst', 'QUICK', 'Risk', 'YES', '12s'],
              ['Conservative Analyst', 'QUICK', 'Risk', 'YES', '12s'],
              ['Portfolio Manager', 'DEEP', 'Manager', 'YES', '25s'],
            ].map(([name, tier, role, blocking, timeout]) => (
              <tr key={name} className="table-row-hover" style={{ borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
                <td style={{ padding: '9px 20px', fontFamily: 'JetBrains Mono', fontSize: 12 }}>{name}</td>
                <td style={{ padding: '9px 20px' }}>
                  <span style={{
                    fontSize: 9, fontFamily: 'JetBrains Mono', padding: '2px 6px', border: '1px solid', letterSpacing: '0.1em',
                    color: tier === 'DEEP' ? 'var(--success)' : 'var(--primary)',
                    borderColor: tier === 'DEEP' ? 'rgba(0,255,135,0.3)' : 'rgba(0,229,255,0.3)',
                    background: tier === 'DEEP' ? 'rgba(0,255,135,0.08)' : 'rgba(0,229,255,0.05)',
                  }}>{tier}</span>
                </td>
                <td style={{ padding: '9px 20px', fontSize: 12, color: 'var(--text-secondary)' }}>{role}</td>
                <td style={{ padding: '9px 20px', fontSize: 11, color: blocking === 'YES' ? 'var(--text-secondary)' : 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>{blocking}</td>
                <td style={{ padding: '9px 20px', fontFamily: 'JetBrains Mono', fontSize: 12, color: 'var(--text-muted)' }}>{timeout}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ textAlign: 'right', paddingBottom: 8 }}>
        <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          Configuration changes require code update and system restart. This panel shows current static configuration.
        </div>
      </div>
    </div>
  );
}
