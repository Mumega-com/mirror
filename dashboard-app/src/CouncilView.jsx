
import React, { useState, useEffect } from "react";
import { Copy, Check, MessageSquare, Award } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { supabase } from "./lib/supabase";

const CouncilView = () => {
    const [history, setHistory] = useState([]);
    const [selectedDebate, setSelectedDebate] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        fetchHistory();
        // Subscribe to real-time updates
        const subscription = supabase
            .channel('public:mirror_council_history')
            .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'mirror_council_history' }, payload => {
                console.log('New Council Debate:', payload);
                fetchHistory();
            })
            .subscribe();

        return () => {
            supabase.removeChannel(subscription);
        };
    }, []);

    const fetchHistory = async () => {
        setLoading(true);
        const { data } = await supabase
            .from("mirror_council_history")
            .select("*")
            .order("timestamp", { ascending: false })
            .limit(10);

        if (data) {
            setHistory(data);
            if (!selectedDebate && data.length > 0) {
                setSelectedDebate(data[0]);
            }
        }
        setLoading(false);
    };

    return (
        <div className="w-full max-w-6xl mx-auto p-4 md:p-8 text-neutral-200">
            <div className="flex items-center gap-3 mb-8">
                <div className="p-3 rounded-xl bg-purple-500/10 border border-purple-500/20">
                    <Award className="w-6 h-6 text-purple-400" />
                </div>
                <div>
                    <h2 className="text-2xl font-bold bg-gradient-to-r from-purple-200 to-indigo-200 bg-clip-text text-transparent">
                        The Mirror Council
                    </h2>
                    <p className="text-sm text-neutral-400">16D-Gated Multi-Agent Arbitration</p>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 h-[600px]">
                {/* LEFT: History List */}
                <div className="lg:col-span-1 bg-black/40 backdrop-blur-xl border border-white/5 rounded-2xl p-4 overflow-y-auto custom-scrollbar">
                    <h3 className="text-xs font-semibold text-neutral-500 uppercase tracking-wider mb-4">Recent Debates</h3>
                    <div className="space-y-2">
                        {history.map((item) => (
                            <motion.div
                                key={item.id}
                                onClick={() => setSelectedDebate(item)}
                                className={`p-4 rounded-xl cursor-pointer border transition-all duration-300 group
                            ${selectedDebate?.id === item.id
                                        ? "bg-purple-900/20 border-purple-500/30"
                                        : "bg-white/5 border-transparent hover:bg-white/10"}`}
                                whileHover={{ x: 4 }}
                            >
                                <div className="flex justify-between items-start mb-2">
                                    <span className="text-xs font-mono text-purple-400">
                                        {new Date(item.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                                    </span>
                                    {item.winner === "Gemini (Antigravity)" && <span className="text-[10px] px-2 py-0.5 rounded bg-blue-500/20 text-blue-300 border border-blue-500/30">Gemini</span>}
                                    {item.winner === "Claude (The Philosopher)" && <span className="text-[10px] px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30">Claude</span>}
                                    {item.winner === "River (The Architect)" && <span className="text-[10px] px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">River</span>}
                                </div>
                                <p className="text-sm font-medium text-neutral-300 line-clamp-2 group-hover:text-white transition-colors">
                                    {item.query}
                                </p>
                            </motion.div>
                        ))}
                    </div>
                </div>

                {/* RIGHT: Detail View */}
                <div className="lg:col-span-2 bg-black/40 backdrop-blur-xl border border-white/5 rounded-2xl p-6 flex flex-col relative overflow-hidden">
                    {selectedDebate ? (
                        <>
                            {/* Header */}
                            <div className="mb-6 relative z-10">
                                <h3 className="text-xl font-medium text-white mb-2">{selectedDebate.query}</h3>
                                <div className="flex items-center gap-4 text-xs font-mono">
                                    <span className="text-neutral-400">Winner Score (W): <span className="text-purple-400">{selectedDebate.winner_score?.toFixed(3)}</span></span>
                                    <span className="text-neutral-600">|</span>
                                    {/* Render other scores if available in results JSON */}
                                    {selectedDebate.results && Array.isArray(selectedDebate.results) && (
                                        <div className="flex gap-2">
                                            {selectedDebate.results.slice(0, 3).map((r, i) => (
                                                <span key={i} className="text-neutral-500">
                                                    {r.agent ? r.agent.split(' ')[0] : r.context || `W${r.id}`}: {r.score?.toFixed?.(2) || '✓'}
                                                </span>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            </div>

                            {/* Content */}
                            <div className="flex-1 overflow-y-auto custom-scrollbar pr-2 relative z-10">
                                <div className="prose prose-invert prose-sm max-w-none">
                                    <div className="p-4 rounded-xl bg-gradient-to-br from-purple-900/10 to-blue-900/10 border border-purple-500/10 mb-4">
                                        <h4 className="flex items-center gap-2 text-sm font-bold text-purple-300 mb-2">
                                            <Award className="w-4 h-4" />
                                            Witness Verdict
                                        </h4>
                                        <p className="text-neutral-300 text-sm italic">
                                            "This response demonstrated the highest resonance with the 16D Universal Vector, balancing logic ($\mu$) with cosmic context ($P_t$)."
                                        </p>
                                    </div>

                                    <div className="text-neutral-200 leading-relaxed whitespace-pre-wrap font-sans">
                                        {selectedDebate.winning_content}
                                    </div>
                                </div>
                            </div>

                            {/* Background Glow */}
                            <div className="absolute top-0 right-0 w-96 h-96 bg-purple-500/5 rounded-full blur-3xl -translate-y-1/2 translate-x-1/2 pointer-events-none" />
                        </>
                    ) : (
                        <div className="flex-1 flex flex-col items-center justify-center text-neutral-500">
                            <MessageSquare className="w-12 h-12 mb-4 opacity-20" />
                            <p>Select a debate to view the verdict.</p>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

export default CouncilView;
