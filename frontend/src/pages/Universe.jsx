import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { RefreshCw, TrendingUp, TrendingDown, Minus } from 'lucide-react';

const API = process.env.REACT_APP_BACKEND_URL + '/api';

function ScoreBar({ value, max = 1, color = 'var(--primary)' }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{ flex: 1, height: 3, background: 'var(--border)', borderRadius: 1 }}>
        <div style={{ height: '100%', width: `${(value / max) * 100}%`, background: color, borderRadius: 1 }} />
      </div>
      <span style={{ fontSize: 11, fontFamily: 'JetBrains Mono', color: 'var(--text-secondary)', minWidth: 32, textAlign: 'right' }}>
        {(value * 100).toFixed(0)}%
      </span>
    </div>
  );
}

export default function Universe() {
  const [stocks, setStocks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sortBy, setSortBy] = useState('composite_score');

  const fetchUniverse = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/universe`);
      setStocks(r.data);
    } catch (e) {}
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchUniverse();
  }, [fetchUniverse]);

  const sorted = [...stocks].sort((a, b) => (b[sortBy] || 0) - (a[sortBy] || 0));

  const headers = [
    { key: 'rank', label: '#' },
    { key: 'ticker', label: 'TICKER' },
    { key: 'price', label: 'PRICE' },
    { key: 'momentum_5d', label: '5D MOM' },
    { key: 'volume_ratio', label: 'VOL RATIO' },
    { key: 'rsi', label: 'RSI' },
    { key: 'ema_bullish', label: 'TREND' },
    { key: 'bayesian_score', label: 'BAYESIAN' },
    { key: 'composite_score', label: 'COMPOSITE' },
  ];

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontFamily: 'JetBrains Mono', fontSize: 20, fontWeight: 700, letterSpacing: '0.08em' }}>UNIVERSE SCANNER</h1>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
            {stocks.length} stocks ranked &nbsp;•&nbsp; 4-factor composite scoring &nbsp;•&nbsp; Updated every 10 minutes
          </div>
        </div>
        <button
          onClick={fetchUniverse}
          disabled={loading}
          style={{ background: 'transparent', border: '1px solid var(--border)', color: 'var(--text-muted)', padding: '6px 12px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, fontFamily: 'JetBrains Mono' }}
        >
          <RefreshCw size={12} className={loading ? 'agent-running' : ''} />
          REFRESH
        </button>
      </div>

      {/* Score Legend */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginBottom: 16 }}>
        {[
          { label: 'MOMENTUM (25%)', desc: '5-day price momentum', color: 'var(--primary)' },
          { label: 'VOLUME (25%)', desc: 'Volume vs 20-day avg', color: 'var(--success)' },
          { label: 'RSI (30%)', desc: 'RSI in 40-65 zone', color: 'var(--warning)' },
          { label: 'TREND (20%)', desc: 'EMA9 > EMA21', color: 'rgba(255,165,0,0.8)' },
        ].map(({ label, desc, color }) => (
          <div key={label} style={{ background: 'var(--surface)', border: '1px solid var(--border)', padding: '10px 14px' }}>
            <div style={{ fontSize: 9, color, fontFamily: 'JetBrains Mono', letterSpacing: '0.1em', marginBottom: 2 }}>{label}</div>
            <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>{desc}</div>
          </div>
        ))}
      </div>

      {/* Table */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                {headers.map(h => (
                  <th
                    key={h.key}
                    onClick={() => h.key !== 'rank' && h.key !== 'ticker' && h.key !== 'ema_bullish' && setSortBy(h.key)}
                    style={{
                      padding: '10px 14px',
                      textAlign: h.key === 'ticker' || h.key === 'rank' ? 'left' : 'right',
                      fontSize: 9, color: sortBy === h.key ? 'var(--primary)' : 'var(--text-muted)',
                      fontFamily: 'JetBrains Mono', letterSpacing: '0.12em', fontWeight: 500,
                      cursor: 'pointer', whiteSpace: 'nowrap',
                    }}
                  >
                    {h.label} {sortBy === h.key ? '↓' : ''}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={9} style={{ padding: '40px 16px', textAlign: 'center', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', fontSize: 12 }}>SCANNING UNIVERSE...</td></tr>
              ) : sorted.map((stock, idx) => {
                const mom = stock.momentum_5d;
                const bayesianOk = stock.bayesian_score >= 0.45;
                return (
                  <tr
                    key={stock.ticker}
                    data-testid={`universe-row-${stock.ticker}`}
                    className="table-row-hover"
                    style={{ borderBottom: '1px solid rgba(255,255,255,0.03)' }}
                  >
                    <td style={{ padding: '10px 14px', fontFamily: 'JetBrains Mono', fontSize: 12, color: 'var(--text-muted)' }}>{idx + 1}</td>
                    <td style={{ padding: '10px 14px' }}>
                      <div style={{ fontFamily: 'JetBrains Mono', fontWeight: 600, fontSize: 13, color: bayesianOk ? 'var(--primary)' : 'var(--text-primary)' }}>
                        {stock.ticker}
                        {bayesianOk && <span style={{ marginLeft: 6, fontSize: 8, color: 'var(--success)', fontFamily: 'JetBrains Mono' }}>VIABLE</span>}
                      </div>
                    </td>
                    <td style={{ padding: '10px 14px', textAlign: 'right', fontFamily: 'JetBrains Mono', fontSize: 12 }}>₹{stock.price?.toFixed(2)}</td>
                    <td style={{ padding: '10px 14px', textAlign: 'right' }}>
                      <span style={{ fontFamily: 'JetBrains Mono', fontSize: 12, color: mom >= 0 ? 'var(--success)' : 'var(--danger)', display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 4 }}>
                        {mom >= 0 ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
                        {mom >= 0 ? '+' : ''}{mom?.toFixed(2)}%
                      </span>
                    </td>
                    <td style={{ padding: '10px 14px', textAlign: 'right', fontFamily: 'JetBrains Mono', fontSize: 12, color: stock.volume_ratio > 1.5 ? 'var(--warning)' : 'var(--text-secondary)' }}>
                      {stock.volume_ratio?.toFixed(2)}×
                    </td>
                    <td style={{ padding: '10px 14px', textAlign: 'right' }}>
                      <span style={{
                        fontFamily: 'JetBrains Mono', fontSize: 12,
                        color: stock.rsi >= 40 && stock.rsi <= 65 ? 'var(--success)' : stock.rsi > 70 ? 'var(--danger)' : 'var(--warning)',
                      }}>
                        {stock.rsi?.toFixed(1)}
                      </span>
                    </td>
                    <td style={{ padding: '10px 14px', textAlign: 'right' }}>
                      {stock.ema_bullish
                        ? <span style={{ fontSize: 10, color: 'var(--success)', fontFamily: 'JetBrains Mono' }}>BULL</span>
                        : <span style={{ fontSize: 10, color: 'var(--danger)', fontFamily: 'JetBrains Mono' }}>BEAR</span>}
                    </td>
                    <td style={{ padding: '10px 14px', minWidth: 120 }}>
                      <ScoreBar
                        value={stock.bayesian_score}
                        color={stock.bayesian_score >= 0.45 ? 'var(--success)' : stock.bayesian_score >= 0.35 ? 'var(--warning)' : 'var(--danger)'}
                      />
                    </td>
                    <td style={{ padding: '10px 14px', minWidth: 120 }}>
                      <ScoreBar value={stock.composite_score} color="var(--primary)" />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
