import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { useWebSocket } from '../hooks/useWebSocket';
import { TrendingUp, TrendingDown, RefreshCw, Target, Shield } from 'lucide-react';

const API = process.env.REACT_APP_BACKEND_URL + '/api';

function fmt(v, decimals = 2) {
  return v != null && v !== 0 ? `₹${Number(v).toFixed(decimals)}` : '—';
}

function PriceLevelRow({ label, price, color, isCurrent, currentPrice }) {
  const isAbove = currentPrice > 0 && price > 0 && price > currentPrice;
  const isBelow = currentPrice > 0 && price > 0 && price < currentPrice;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '5px 0',
      borderBottom: '1px solid rgba(255,255,255,0.04)',
      opacity: price > 0 ? 1 : 0.35,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{
          width: 6, height: 6, borderRadius: '50%',
          background: isCurrent ? '#00E5FF' : (price > 0 ? color : 'var(--border)'),
          boxShadow: isCurrent ? '0 0 6px #00E5FF' : 'none',
        }} />
        <span style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.1em', width: 52 }}>{label}</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        {price > 0 && !isCurrent && (
          <span style={{ fontSize: 9, color: isAbove ? 'rgba(0,255,135,0.5)' : isBelow ? 'rgba(255,59,48,0.5)' : 'transparent', fontFamily: 'JetBrains Mono' }}>
            {isAbove ? '▲' : isBelow ? '▼' : ''}
          </span>
        )}
        <span style={{ fontFamily: 'JetBrains Mono', fontSize: 12, color: isCurrent ? '#00E5FF' : (price > 0 ? color : 'var(--text-muted)') }}>
          {price > 0 ? `₹${Number(price).toFixed(2)}` : '—'}
        </span>
      </div>
    </div>
  );
}

function PositionCard({ pos }) {
  const positive = pos.unrealized_pnl >= 0;
  const pnlColor = positive ? 'var(--success)' : 'var(--danger)';

  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--border)',
      marginBottom: 12, overflow: 'hidden',
    }}>
      {/* Header row */}
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr',
        padding: '14px 20px', borderBottom: '1px solid var(--border)',
        gap: 16, alignItems: 'center',
      }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontFamily: 'JetBrains Mono', fontWeight: 700, fontSize: 16 }}>{pos.ticker}</span>
            <span style={{
              fontSize: 9, fontFamily: 'JetBrains Mono', letterSpacing: '0.1em',
              padding: '2px 6px', border: '1px solid',
              color: pos.side === 'long' ? 'var(--success)' : 'var(--danger)',
              borderColor: pos.side === 'long' ? 'rgba(0,255,135,0.3)' : 'rgba(255,59,48,0.3)',
            }}>{pos.side?.toUpperCase()}</span>
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 3 }}>
            {pos.qty} shares &nbsp;•&nbsp; Mkt value: ₹{pos.market_value?.toFixed(2)}
          </div>
        </div>

        <div>
          <div style={{ fontSize: 9, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.1em', marginBottom: 3 }}>CURRENT</div>
          <div style={{ fontFamily: 'JetBrains Mono', fontSize: 15, color: 'var(--primary)' }}>₹{pos.current_price?.toFixed(2)}</div>
        </div>

        <div>
          <div style={{ fontSize: 9, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.1em', marginBottom: 3 }}>UNREALIZED P&L</div>
          <div style={{ fontFamily: 'JetBrains Mono', fontSize: 15, color: pnlColor }}>
            {positive ? '+' : ''}₹{pos.unrealized_pnl?.toFixed(2)}
          </div>
          <div style={{ fontSize: 10, color: pnlColor, opacity: 0.7 }}>
            {pos.unrealized_pnl_pct >= 0 ? '+' : ''}{pos.unrealized_pnl_pct?.toFixed(2)}%
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end' }}>
          {positive
            ? <TrendingUp size={28} color="var(--success)" strokeWidth={1.5} />
            : <TrendingDown size={28} color="var(--danger)" strokeWidth={1.5} />
          }
        </div>
      </div>

      {/* Price Ladder */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 0 }}>
        {/* Left: price levels */}
        <div style={{ padding: '12px 20px', borderRight: '1px solid var(--border)' }}>
          <div style={{ fontSize: 9, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.15em', marginBottom: 8 }}>
            PRICE LEVELS
          </div>
          <PriceLevelRow label="TP" price={pos.take_profit} color="#00FF87" currentPrice={pos.current_price} />
          <PriceLevelRow label="T2 +10%" price={pos.target_2} color="#FFCC00" currentPrice={pos.current_price} />
          <PriceLevelRow label="T1 +5%" price={pos.target_1} color="#FFCC00" currentPrice={pos.current_price} />
          <PriceLevelRow label="CURRENT" price={pos.current_price} color="#00E5FF" isCurrent currentPrice={pos.current_price} />
          <PriceLevelRow label="ENTRY" price={pos.entry_price} color="var(--text-secondary)" currentPrice={pos.current_price} />
          <PriceLevelRow label="SL" price={pos.stop_loss} color="#FF3B30" currentPrice={pos.current_price} />
        </div>

        {/* Right: trade details */}
        <div style={{ padding: '12px 20px' }}>
          <div style={{ fontSize: 9, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.15em', marginBottom: 8 }}>
            TRADE DETAILS
          </div>
          {[
            { label: 'Entry Price', value: fmt(pos.entry_price) },
            { label: 'Stop Loss', value: fmt(pos.stop_loss), color: pos.stop_loss > 0 ? '#FF3B30' : undefined },
            { label: 'Take Profit', value: fmt(pos.take_profit), color: pos.take_profit > 0 ? '#00FF87' : undefined },
            { label: 'Target 1 (+5%)', value: fmt(pos.target_1), color: pos.target_1 > 0 ? '#FFCC00' : undefined },
            { label: 'Target 2 (+10%)', value: fmt(pos.target_2), color: pos.target_2 > 0 ? '#FFCC00' : undefined },
            { label: 'Cost Basis', value: fmt(pos.cost_basis) },
          ].map(({ label, value, color }) => (
            <div key={label} style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: '5px 0', borderBottom: '1px solid rgba(255,255,255,0.04)',
            }}>
              <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>{label}</span>
              <span style={{ fontFamily: 'JetBrains Mono', fontSize: 12, color: color || 'var(--text-primary)' }}>{value}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function Positions() {
  const { lastEvent } = useWebSocket();
  const [positions, setPositions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [account, setAccount] = useState(null);

  const fetchPositions = useCallback(async () => {
    setLoading(true);
    try {
      const [posR, accR] = await Promise.all([
        axios.get(`${API}/positions`),
        axios.get(`${API}/account`),
      ]);
      setPositions(posR.data);
      setAccount(accR.data);
    } catch (e) {}
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchPositions();
    const interval = setInterval(fetchPositions, 10000);
    return () => clearInterval(interval);
  }, [fetchPositions]);

  useEffect(() => {
    if (lastEvent?.type === 'position_update') {
      fetchPositions();
    }
  }, [lastEvent, fetchPositions]);

  const totalPnl = positions.reduce((s, p) => s + (p.unrealized_pnl || 0), 0);

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontFamily: 'JetBrains Mono', fontSize: 20, fontWeight: 700, letterSpacing: '0.08em' }}>
            OPEN POSITIONS
          </h1>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
            {positions.length} active position{positions.length !== 1 ? 's' : ''} &nbsp;•&nbsp; Paper trading account
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.1em' }}>UNREALIZED P&L</div>
            <div style={{ fontFamily: 'JetBrains Mono', fontSize: 20, color: totalPnl >= 0 ? 'var(--success)' : 'var(--danger)' }}>
              {totalPnl >= 0 ? '+' : ''}₹{totalPnl.toFixed(2)}
            </div>
          </div>
          <button onClick={fetchPositions} style={{ background: 'transparent', border: '1px solid var(--border)', color: 'var(--text-muted)', padding: '6px 10px', cursor: 'pointer' }}>
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {/* Account Summary */}
      {account && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginBottom: 20 }}>
          {[
            { label: 'PORTFOLIO VALUE', value: `₹${account.portfolio_value?.toLocaleString('en-IN', { minimumFractionDigits: 2 })}` },
            { label: 'CASH', value: `₹${account.cash?.toLocaleString('en-IN', { minimumFractionDigits: 2 })}` },
            { label: 'BUYING POWER', value: `₹${account.buying_power?.toLocaleString('en-IN', { minimumFractionDigits: 2 })}` },
            { label: 'DAILY P&L', value: `${account.daily_pnl >= 0 ? '+' : ''}₹${account.daily_pnl?.toFixed(2)}`, color: account.daily_pnl >= 0 ? 'var(--success)' : 'var(--danger)' },
          ].map(({ label, value, color }) => (
            <div key={label} style={{ background: 'var(--surface)', border: '1px solid var(--border)', padding: '12px 16px' }}>
              <div style={{ fontSize: 9, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.15em', marginBottom: 6 }}>{label}</div>
              <div style={{ fontFamily: 'JetBrains Mono', fontSize: 14, color: color || 'var(--text-primary)' }}>{value}</div>
            </div>
          ))}
        </div>
      )}

      {/* Position Cards */}
      {loading ? (
        <div style={{ padding: '40px 0', textAlign: 'center', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', fontSize: 12 }}>LOADING...</div>
      ) : positions.length === 0 ? (
        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', padding: '40px 0', textAlign: 'center', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', fontSize: 12 }}>
          NO OPEN POSITIONS — SYSTEM WAITING FOR ENTRY SIGNALS
        </div>
      ) : (
        positions.map(pos => <PositionCard key={pos.ticker} pos={pos} />)
      )}
    </div>
  );
}
