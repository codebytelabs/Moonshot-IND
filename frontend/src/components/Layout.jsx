import React, { useState, useEffect } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import axios from 'axios';
import { useWebSocket } from '../hooks/useWebSocket';
import {
  LayoutDashboard, TrendingUp, Brain, BarChart2, List, Settings,
  Radio, AlertTriangle, Wifi, WifiOff, Activity, Zap
} from 'lucide-react';

const API = process.env.REACT_APP_BACKEND_URL + '/api';

const NAV_ITEMS = [
  { path: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { path: '/positions', icon: TrendingUp, label: 'Positions' },
  { path: '/derivatives', icon: Zap, label: 'Derivatives' },
  { path: '/brain', icon: Brain, label: 'Agent Brain' },
  { path: '/performance', icon: BarChart2, label: 'Performance' },
  { path: '/universe', icon: List, label: 'Universe' },
  { path: '/settings', icon: Settings, label: 'Settings' },
];

export default function Layout({ children }) {
  const location = useLocation();
  const { connected, lastEvent } = useWebSocket();
  const [systemStatus, setSystemStatus] = useState({
    is_running: false, is_halted: false, regime: 'neutral',
    daily_pnl: 0, llm_cost_today: 0, loop_count: 0,
  });
  const [halting, setHalting] = useState(false);

  useEffect(() => {
    const fetch = () => axios.get(`${API}/system/status`)
      .then(r => setSystemStatus(r.data)).catch(() => {});
    fetch();
    const interval = setInterval(fetch, 10000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!lastEvent) return;
    if (lastEvent.type === 'system_status') {
      setSystemStatus(prev => ({
        ...prev,
        is_running: lastEvent.is_running,
        is_halted: lastEvent.is_halted,
      }));
    }
    if (lastEvent.type === 'loop_tick') {
      setSystemStatus(prev => ({
        ...prev,
        regime: lastEvent.regime,
        loop_count: lastEvent.loop_count,
        llm_cost_today: lastEvent.llm_cost_today,
      }));
    }
  }, [lastEvent]);

  const handleEmergencyHalt = async () => {
    if (!window.confirm('EMERGENCY HALT: Close all positions and stop all trading?')) return;
    setHalting(true);
    try {
      await axios.post(`${API}/system/emergency-halt`);
    } catch (e) {}
    setHalting(false);
  };

  const regimeClass = `regime-${systemStatus.regime.replace('_', '-')}` ;
  const regimeLabel = systemStatus.regime.replace('_', ' ').toUpperCase();

  return (
    <div style={{ background: 'var(--bg)', height: '100vh', display: 'flex', flexDirection: 'column' }}>
      {/* Top Header */}
      <header
        style={{
          borderBottom: '1px solid var(--border)',
          background: 'rgba(8,8,10,0.95)',
          backdropFilter: 'blur(12px)',
          padding: '0 20px',
          height: '52px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexShrink: 0,
          zIndex: 100,
        }}
      >
        {/* Logo */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Activity size={18} color="var(--primary)" />
            <span style={{ fontFamily: 'JetBrains Mono', fontWeight: 700, fontSize: 15, color: 'var(--primary)', letterSpacing: '0.05em' }}>
              MOONSHOTX
            </span>
            <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: 'var(--text-muted)', letterSpacing: '0.1em' }}>v1.1</span>
          </div>
          {/* Status indicator */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginLeft: 16 }}>
            <div style={{
              width: 7, height: 7, borderRadius: '50%',
              background: systemStatus.is_halted ? '#FF3B30' : systemStatus.is_running ? '#00FF87' : '#666',
              boxShadow: systemStatus.is_running ? '0 0 6px rgba(0,255,135,0.5)' : 'none',
            }} />
            <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: 'var(--text-muted)', letterSpacing: '0.12em' }}>
              {systemStatus.is_halted ? 'HALTED' : systemStatus.is_running ? 'RUNNING' : 'IDLE'}
            </span>
          </div>
        </div>

        {/* Center stats */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 24 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.1em' }}>REGIME</span>
            <span className={`${regimeClass}`} style={{
              fontFamily: 'JetBrains Mono', fontSize: 10, fontWeight: 700,
              letterSpacing: '0.12em', padding: '2px 6px', border: '1px solid',
            }}>
              {regimeLabel}
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.1em' }}>LOOPS</span>
            <span style={{ fontFamily: 'JetBrains Mono', fontSize: 11, color: 'var(--primary)' }}>{systemStatus.loop_count}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.1em' }}>LLM $</span>
            <span style={{ fontFamily: 'JetBrains Mono', fontSize: 11, color: systemStatus.llm_cost_today > 20 ? '#FF3B30' : 'var(--text-secondary)' }}>
              ${systemStatus.llm_cost_today.toFixed(2)}
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            {connected ? <Wifi size={12} color="var(--success)" /> : <WifiOff size={12} color="var(--text-muted)" />}
            <span style={{ fontSize: 10, color: connected ? 'var(--success)' : 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
              {connected ? 'LIVE' : 'OFFLINE'}
            </span>
          </div>
        </div>

        {/* Emergency Halt */}
        <button
          data-testid="emergency-halt-btn"
          onClick={handleEmergencyHalt}
          disabled={halting}
          className="halt-pulse"
          style={{
            background: '#FF3B30',
            color: '#FFFFFF',
            border: 'none',
            padding: '6px 16px',
            fontFamily: 'JetBrains Mono',
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: '0.12em',
            cursor: 'pointer',
            display: 'flex', alignItems: 'center', gap: 6,
            opacity: halting ? 0.7 : 1,
          }}
        >
          <AlertTriangle size={12} />
          {halting ? 'HALTING...' : 'EMERGENCY HALT'}
        </button>
      </header>

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Sidebar */}
        <nav style={{
          width: 200,
          borderRight: '1px solid var(--border)',
          background: 'var(--surface)',
          display: 'flex',
          flexDirection: 'column',
          flexShrink: 0,
          paddingTop: 8,
        }}>
          {NAV_ITEMS.map(({ path, icon: Icon, label }) => {
            const isActive = path === '/'
              ? location.pathname === '/'
              : location.pathname.startsWith(path);
            return (
              <NavLink
                key={path}
                to={path}
                data-testid={`nav-${label.toLowerCase().replace(' ', '-')}-link`}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  padding: '10px 16px',
                  textDecoration: 'none',
                  color: isActive ? 'var(--primary)' : 'var(--text-secondary)',
                  borderLeft: isActive ? '2px solid var(--primary)' : '2px solid transparent',
                  background: isActive ? 'rgba(0,229,255,0.05)' : 'transparent',
                  fontSize: 12,
                  fontWeight: isActive ? 600 : 400,
                  letterSpacing: '0.06em',
                  transition: 'all 150ms ease',
                }}
              >
                <Icon size={14} />
                {label}
              </NavLink>
            );
          })}

          {/* System status in sidebar bottom */}
          <div style={{ marginTop: 'auto', padding: '12px 16px', borderTop: '1px solid var(--border)' }}>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', letterSpacing: '0.1em', marginBottom: 4 }}>
              PAPER TRADING
            </div>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
              Zerodha Kite · NSE
            </div>
          </div>
        </nav>

        {/* Main Content */}
        <main style={{ flex: 1, overflow: 'auto', background: 'var(--bg)' }}>
          {children}
        </main>
      </div>
    </div>
  );
}
