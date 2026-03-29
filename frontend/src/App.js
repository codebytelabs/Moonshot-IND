import React from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import './App.css';
import Layout from './components/Layout';
import Dashboard from './pages/Dashboard';
import Positions from './pages/Positions';
import AgentBrain from './pages/AgentBrain';
import Performance from './pages/Performance';
import Universe from './pages/Universe';
import Settings from './pages/Settings';
import Derivatives from './pages/Derivatives';
import Strategies from './pages/Strategies';

function App() {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/positions" element={<Positions />} />
          <Route path="/brain" element={<AgentBrain />} />
          <Route path="/performance" element={<Performance />} />
          <Route path="/universe" element={<Universe />} />
          <Route path="/derivatives" element={<Derivatives />} />
          <Route path="/strategies" element={<Strategies />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}

export default App;
