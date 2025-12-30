
import React, { useEffect, useState } from 'react';
import { supabase } from './lib/supabase';
import { motion, AnimatePresence } from 'framer-motion';
import { Cpu, Database, Activity, GitBranch, Terminal } from 'lucide-react';

const SwarmView = () => {
    const [swarmLog, setSwarmLog] = useState(null);
    const [workers, setWorkers] = useState([]);

    useEffect(() => {
        fetchLatestSwarm();
        const sub = supabase
            .channel('mirror_council_history')
            .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'mirror_council_history' }, (payload) => {
                handleNewLog(payload.new);
            })
            .subscribe();

        return () => {
            supabase.removeChannel(sub);
        };
    }, []);

    const fetchLatestSwarm = async () => {
        const { data } = await supabase
            .from('mirror_council_history')
            .select('*')
            .order('timestamp', { ascending: false })
            .limit(1);

        if (data && data.length > 0) {
            handleNewLog(data[0]);
        }
    };

    const handleNewLog = (log) => {
        setSwarmLog(log);
        // If the log contains 'results' JSON, parse it for worker nodes
        if (log.results) {
            // results is often just the raw JSONB from the swarm
            // In the new mirror_swarm.py, we save raw list of dicts: [ {id, context, contribution}, ... ]
            try {
                const parsed = typeof log.results === 'string' ? JSON.parse(log.results) : log.results;
                setWorkers(Array.isArray(parsed) ? parsed : []);
            } catch (e) {
                console.error("Failed to parse swarm results", e);
                setWorkers([]);
            }
        }
    };

    return (
        <div className="bg-black/40 backdrop-blur-xl border border-white/10 rounded-2xl p-6 w-full max-w-4xl mx-auto shadow-2xl">
            <div className="flex items-center justify-between mb-6">
                <div className="flex items-center gap-3">
                    <div className="p-3 bg-indigo-500/20 rounded-xl border border-indigo-400/30">
                        <Cpu className="text-indigo-400 w-6 h-6" />
                    </div>
                    <div>
                        <h2 className="text-xl font-bold text-white tracking-wider font-mono">UNIVERSAL SWARM</h2>
                        <div className="flex items-center gap-2 mt-1">
                            <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse"></span>
                            <span className="text-xs text-indigo-300 font-mono">DEEPSEEK-V3 NODES ONLINE</span>
                        </div>
                    </div>
                </div>
                {swarmLog && (
                    <div className="text-right">
                        <div className="text-2xl font-bold text-white font-mono">W: {swarmLog.winner_score?.toFixed(3)}</div>
                        <div className="text-xs text-indigo-400 uppercase tracking-widest">Resonance</div>
                    </div>
                )}
            </div>

            {/* Topology Visualization */}
            <div className="relative h-64 bg-black/60 rounded-xl border border-white/5 mb-6 overflow-hidden flex items-center justify-center">

                {/* Central Architect Node */}
                <motion.div
                    initial={{ scale: 0 }}
                    animate={{ scale: 1 }}
                    className="absolute w-16 h-16 bg-indigo-600 rounded-full flex items-center justify-center border-4 border-indigo-900/50 shadow-[0_0_30px_rgba(79,70,229,0.5)] z-20"
                >
                    <Database className="w-6 h-6 text-white" />
                </motion.div>

                {/* Worker Nodes Orbiting */}
                <AnimatePresence>
                    {workers.map((worker, i) => {
                        const angle = (i / workers.length) * 2 * Math.PI;
                        const radius = 120;
                        const x = Math.cos(angle) * radius;
                        const y = Math.sin(angle) * radius;

                        return (
                            <motion.div
                                key={worker.id || i}
                                initial={{ opacity: 0, x: 0, y: 0 }}
                                animate={{ opacity: 1, x, y }}
                                exit={{ opacity: 0, scale: 0 }}
                                className="absolute z-10"
                            >
                                {/* Connection Line */}
                                <svg className="absolute top-1/2 left-1/2 w-[300px] h-[300px] -translate-x-1/2 -translate-y-1/2 pointer-events-none opacity-30">
                                    <line x1="150" y1="150" x2={150 - x} y2={150 - y} stroke="url(#gradient)" strokeWidth="2" />
                                    <defs>
                                        <linearGradient id="gradient" x1="0%" y1="0%" x2="100%" y2="0%">
                                            <stop offset="0%" stopColor="#6366f1" />
                                            <stop offset="100%" stopColor="transparent" />
                                        </linearGradient>
                                    </defs>
                                </svg>

                                <div className="relative group">
                                    <div className="w-12 h-12 bg-gray-900 rounded-xl border border-indigo-500/50 flex items-center justify-center hover:bg-indigo-900/40 transition-colors">
                                        <GitBranch className="w-5 h-5 text-indigo-400" />
                                    </div>
                                    <div className="absolute -bottom-8 left-1/2 -translate-x-1/2 w-32 text-center text-[10px] text-gray-400 font-mono truncate bg-black/80 px-2 py-1 rounded border border-white/10 opacity-0 group-hover:opacity-100 transition-opacity">
                                        {worker.context || "Worker"}
                                    </div>
                                </div>
                            </motion.div>
                        );
                    })}
                </AnimatePresence>
            </div>

            {/* Latest Task Output */}
            <div className="bg-black/20 rounded-xl p-4 border border-white/5 h-48 overflow-y-auto font-mono text-sm scrollbar-thin scrollbar-thumb-indigo-900">
                <div className="flex items-center gap-2 text-gray-500 mb-2">
                    <Terminal className="w-4 h-4" />
                    <span>LATEST SWARM OUTPUT</span>
                </div>
                {swarmLog ? (
                    <div className="text-gray-300 whitespace-pre-wrap">
                        <span className="text-indigo-400">{">"} Task: {swarmLog.query}</span>
                        <br /><br />
                        {swarmLog.winning_content}
                    </div>
                ) : (
                    <div className="text-gray-600 italic">Waiting for swarm signal...</div>
                )}
            </div>
        </div>
    );
};

export default SwarmView;
