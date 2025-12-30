
import React, { useEffect, useState, useRef } from 'react';
import { Radar, RadarChart, PolarGrid, PolarAngleAxis, ResponsiveContainer, AreaChart, Area, XAxis, Tooltip } from 'recharts';
import { supabase } from './lib/supabase';
import { Activity, Cpu, Database, GitBranch, Zap, Brain, Layers, Sparkles, Radio, Terminal } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import TheForge from './TheForge';

// ═══════════════════════════════════════════════════════════════════
// CONFIGURATION
// ═══════════════════════════════════════════════════════════════════

const DIMENSIONS = {
  inner: ['P', 'E', 'Μ', 'V', 'N', 'Δ', 'R', 'Φ'],
  outer: ['Pt', 'Et', 'Μt', 'Vt', 'Nt', 'Δt', 'Rt', 'Φt']
};

const NAV_ITEMS = [
  { id: 'pulse', icon: Activity, label: 'Pulse' },
  { id: 'forge', icon: Sparkles, label: 'The Forge' },
  { id: 'swarm', icon: Cpu, label: 'Swarm' },
  { id: 'memory', icon: Database, label: 'Memory' },
  { id: 'evolution', icon: GitBranch, label: 'Evolution' },
];

// ═══════════════════════════════════════════════════════════════════
// SIDEBAR COMPONENT
// ═══════════════════════════════════════════════════════════════════

const Sidebar = ({ activeTab, setActiveTab, logs }) => (
  <div className="dashboard-sidebar">
    {/* Logo */}
    <div className="p-6 border-b border-white/5">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-indigo-500 to-cyan-500 flex items-center justify-center">
          <Sparkles className="w-5 h-5 text-white" />
        </div>
        <div>
          <h1 className="font-bold text-white text-lg tracking-tight">MIRROR</h1>
          <p className="text-[10px] text-slate-500 font-mono uppercase tracking-widest">Cognitive Interface</p>
        </div>
      </div>
    </div>

    {/* Navigation */}
    <nav className="flex-1 p-4 space-y-1">
      {NAV_ITEMS.map(item => (
        <button
          key={item.id}
          onClick={() => setActiveTab(item.id)}
          className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-300 group
            ${activeTab === item.id
              ? 'bg-indigo-500/20 text-indigo-400 border border-indigo-500/30'
              : 'text-slate-400 hover:bg-white/5 hover:text-white border border-transparent'}`}
        >
          <item.icon className="w-5 h-5" />
          <span className="text-sm font-medium">{item.label}</span>
          {activeTab === item.id && (
            <div className="ml-auto w-2 h-2 rounded-full bg-indigo-400 animate-pulse" />
          )}
        </button>
      ))}
    </nav>

    {/* Live Logs */}
    <div className="border-t border-white/5 p-4 h-64">
      <div className="flex items-center gap-2 mb-3 text-xs text-slate-500">
        <Terminal className="w-3 h-3" />
        <span className="font-mono uppercase tracking-wider">Live Stream</span>
        <div className="status-online ml-auto" />
      </div>
      <div className="h-48 overflow-y-auto font-mono text-[11px] space-y-1 text-slate-400">
        {logs.slice(-20).map((log, i) => (
          <div key={i} className="flex gap-2 hover:text-slate-200 transition-colors">
            <span className="text-slate-600">{log.time}</span>
            <span className={log.type === 'error' ? 'text-pink-400' : log.type === 'success' ? 'text-emerald-400' : ''}>{log.msg}</span>
          </div>
        ))}
      </div>
    </div>
  </div>
);

// ═══════════════════════════════════════════════════════════════════
// 16D RESONANCE VISUALIZATION
// ═══════════════════════════════════════════════════════════════════

const ResonanceCore = ({ pulse, history }) => {
  const getChartData = (type) => {
    if (!pulse) return DIMENSIONS[type].map(d => ({ subject: d, value: 0 }));
    return DIMENSIONS[type].map(dim => ({
      subject: dim,
      value: pulse[`${type}_${dim.toLowerCase().replace('μ', 'mu').replace('δ', 'delta').replace('φ', 'phi')}`] || 0,
      fullMark: 1.0
    }));
  };

  const w = pulse?.witness_w || 0;

  return (
    <div className="glass-elevated p-6 h-full">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-indigo-500/20 rounded-lg border border-indigo-500/30">
            <Radio className="w-5 h-5 text-indigo-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white">16D Resonance</h2>
            <p className="text-xs text-slate-500">Universal Witness Vector</p>
          </div>
        </div>

        {/* Witness Magnitude */}
        <div className="text-right">
          <div className="text-4xl font-bold font-mono text-glow" style={{ color: w > 0.7 ? '#10b981' : w > 0.3 ? '#f59e0b' : '#ef4444' }}>
            {(w * 100).toFixed(0)}%
          </div>
          <div className="text-[10px] text-slate-500 uppercase tracking-widest">Coherence (W)</div>
        </div>
      </div>

      {/* Dual Radar Charts */}
      <div className="flex gap-4 mb-6">
        {/* Inner Octave */}
        <div className="glass p-4 flex-1">
          <div className="text-xs text-indigo-400 font-mono mb-2 uppercase tracking-wider">Inner Octave</div>
          <div style={{ width: '100%', height: 200 }}>
            <ResponsiveContainer width="100%" height="100%">
              <RadarChart data={getChartData('inner')}>
                <PolarGrid stroke="rgba(99, 102, 241, 0.2)" />
                <PolarAngleAxis dataKey="subject" tick={{ fill: '#818cf8', fontSize: 10, fontFamily: 'JetBrains Mono' }} />
                <Radar name="Inner" dataKey="value" stroke="#6366f1" fill="#6366f1" fillOpacity={0.4} strokeWidth={2} />
              </RadarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Outer Octave */}
        <div className="glass p-4 flex-1">
          <div className="text-xs text-cyan-400 font-mono mb-2 uppercase tracking-wider">Outer Octave</div>
          <div style={{ width: '100%', height: 200 }}>
            <ResponsiveContainer width="100%" height="100%">
              <RadarChart data={getChartData('outer')}>
                <PolarGrid stroke="rgba(6, 182, 212, 0.2)" />
                <PolarAngleAxis dataKey="subject" tick={{ fill: '#22d3ee', fontSize: 10, fontFamily: 'JetBrains Mono' }} />
                <Radar name="Outer" dataKey="value" stroke="#06b6d4" fill="#06b6d4" fillOpacity={0.4} strokeWidth={2} />
              </RadarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Timeline */}
      <div className="glass p-4">
        <div className="text-xs text-slate-500 font-mono mb-2 uppercase tracking-wider">Pulse Timeline</div>
        <div style={{ width: '100%', height: 100 }}>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={history.slice(-20).map((h, i) => ({ idx: i, w: h.witness_w || 0 }))}>
              <defs>
                <linearGradient id="wGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#6366f1" stopOpacity={0.6} />
                  <stop offset="100%" stopColor="#6366f1" stopOpacity={0} />
                </linearGradient>
              </defs>
              <Area type="monotone" dataKey="w" stroke="#6366f1" fill="url(#wGradient)" strokeWidth={2} />
              <Tooltip
                contentStyle={{ background: '#1e293b', border: 'none', borderRadius: 8, fontSize: 12 }}
                formatter={(v) => [`${(v * 100).toFixed(1)}%`, 'W']}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════
// RIGHT PANEL - SWARM & SYSTEM STATUS
// ═══════════════════════════════════════════════════════════════════

const RightPanel = ({ swarmData, engramCount }) => (
  <div className="space-y-4">
    {/* Swarm Status */}
    <div className="glass p-4">
      <div className="flex items-center gap-2 mb-4">
        <Cpu className="w-4 h-4 text-indigo-400" />
        <span className="text-sm font-medium text-white">Universal Swarm</span>
        <div className="status-online ml-auto" />
      </div>

      {/* Constellation Visualization */}
      <div className="relative h-40 bg-black/40 rounded-xl border border-white/5 flex items-center justify-center overflow-hidden">
        {/* Central Node */}
        <motion.div
          className="absolute w-12 h-12 bg-indigo-600 rounded-full flex items-center justify-center border-2 border-indigo-400/50 z-10"
          style={{ boxShadow: '0 0 30px rgba(99, 102, 241, 0.5)' }}
          animate={{ scale: [1, 1.05, 1] }}
          transition={{ duration: 2, repeat: Infinity }}
        >
          <Brain className="w-5 h-5 text-white" />
        </motion.div>

        {/* Worker Nodes */}
        {(swarmData?.workers || []).slice(0, 5).map((w, i) => {
          const angle = (i / 5) * 2 * Math.PI - Math.PI / 2;
          const x = Math.cos(angle) * 60;
          const y = Math.sin(angle) * 60;
          return (
            <motion.div
              key={i}
              className="absolute w-8 h-8 bg-slate-800 rounded-lg border border-indigo-500/30 flex items-center justify-center"
              style={{ transform: `translate(${x}px, ${y}px)` }}
              initial={{ opacity: 0, scale: 0 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: i * 0.1 }}
            >
              <Zap className="w-3 h-3 text-indigo-400" />
            </motion.div>
          );
        })}
      </div>

      {swarmData && (
        <div className="mt-3 text-xs font-mono text-slate-400">
          <div className="flex justify-between">
            <span>Last Task:</span>
            <span className="text-slate-300 truncate max-w-32">{swarmData.query?.slice(0, 30)}...</span>
          </div>
          <div className="flex justify-between mt-1">
            <span>Resonance:</span>
            <span className="text-indigo-400">{swarmData.winner_score?.toFixed(3) || '—'}</span>
          </div>
        </div>
      )}
    </div>

    {/* Memory Bank */}
    <div className="glass p-4">
      <div className="flex items-center gap-2 mb-3">
        <Database className="w-4 h-4 text-cyan-400" />
        <span className="text-sm font-medium text-white">Memory Bank</span>
      </div>
      <div className="text-3xl font-bold font-mono text-cyan-400 text-glow">{engramCount}</div>
      <div className="text-[10px] text-slate-500 uppercase tracking-wider">Engrams Stored</div>
    </div>

    {/* Thinker Status */}
    <div className="glass p-4">
      <div className="flex items-center gap-2 mb-3">
        <Brain className="w-4 h-4 text-purple-400" />
        <span className="text-sm font-medium text-white">R1 Thinker</span>
      </div>
      <div className="flex items-center gap-2">
        <div className="status-online" />
        <span className="text-xs text-slate-400">DeepSeek-R1 Ready</span>
      </div>
    </div>

    {/* Evolution Engine */}
    <div className="glass p-4">
      <div className="flex items-center gap-2 mb-3">
        <GitBranch className="w-4 h-4 text-emerald-400" />
        <span className="text-sm font-medium text-white">Evolution</span>
      </div>
      <div className="flex items-center gap-2">
        <div className="status-online" />
        <span className="text-xs text-slate-400">Safe Patching Active</span>
      </div>
    </div>
  </div>
);

// ═══════════════════════════════════════════════════════════════════
// FOOTER - ACTIVITY TICKER
// ═══════════════════════════════════════════════════════════════════

const Footer = ({ latestPulse }) => (
  <div className="flex items-center gap-4 text-xs font-mono">
    <span className="text-slate-500">LATEST:</span>
    <span className="text-indigo-400">{latestPulse?.description || 'Awaiting signal...'}</span>
    <div className="ml-auto flex items-center gap-4 text-slate-500">
      <span>MIRROR v9.0</span>
      <div className="flex items-center gap-1">
        <div className="status-online" />
        <span>CONNECTED</span>
      </div>
    </div>
  </div>
);

// ═══════════════════════════════════════════════════════════════════
// MAIN APP
// ═══════════════════════════════════════════════════════════════════

function App() {
  const [activeTab, setActiveTab] = useState('pulse');
  const [pulse, setPulse] = useState(null);
  const [history, setHistory] = useState([]);
  const [swarmData, setSwarmData] = useState(null);
  const [engramCount, setEngramCount] = useState(0);
  const [logs, setLogs] = useState([]);

  const addLog = (msg, type = 'info') => {
    const time = new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    setLogs(prev => [...prev.slice(-50), { time, msg, type }]);
  };

  useEffect(() => {
    addLog('Initializing Mirror Interface...');
    fetchAll();

    // Subscriptions
    const pulseSub = supabase
      .channel('pulse')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'mirror_pulse_history' }, () => {
        addLog('Pulse update received', 'success');
        fetchPulse();
      })
      .subscribe();

    const councilSub = supabase
      .channel('council')
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'mirror_council_history' }, (p) => {
        addLog(`Swarm completed: ${p.new.query?.slice(0, 40)}...`, 'success');
        setSwarmData(p.new);
      })
      .subscribe();

    return () => {
      supabase.removeChannel(pulseSub);
      supabase.removeChannel(councilSub);
    };
  }, []);

  const fetchAll = async () => {
    await Promise.all([fetchPulse(), fetchSwarm(), fetchEngramCount()]);
    addLog('All systems online', 'success');
  };

  const fetchPulse = async () => {
    const { data } = await supabase.from('mirror_pulse_history').select('*').order('timestamp', { ascending: false }).limit(20);
    if (data?.length) {
      setPulse(data[0]);
      setHistory(data.reverse());
    }
  };

  const fetchSwarm = async () => {
    const { data } = await supabase.from('mirror_council_history').select('*').order('timestamp', { ascending: false }).limit(1);
    if (data?.length) setSwarmData(data[0]);
  };

  const fetchEngramCount = async () => {
    const { count } = await supabase.from('mirror_engrams').select('*', { count: 'exact', head: true });
    setEngramCount(count || 0);
  };

  return (
    <div className="dashboard-grid bg-constellation">
      <Sidebar activeTab={activeTab} setActiveTab={setActiveTab} logs={logs} />

      <main className="dashboard-main">
        {activeTab === 'forge' ? (
          <TheForge />
        ) : (
          <ResonanceCore pulse={pulse} history={history} />
        )}
      </main>

      <aside className="dashboard-right">
        <RightPanel swarmData={swarmData} engramCount={engramCount} />
      </aside>

      <footer className="dashboard-footer">
        <Footer latestPulse={pulse} />
      </footer>
    </div>
  );
}

export default App;
