import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Search, ShoppingBag } from 'lucide-react';
import MarketplaceCard from './MarketplaceCard';

const Marketplace = () => {
    const [archetypes, setArchetypes] = useState([]);
    const [loading, setLoading] = useState(true);
    const [selectedItem, setSelectedItem] = useState(null);

    useEffect(() => {
        fetch('http://localhost:8000/marketplace/archetypes')
            .then(res => res.json())
            .then(data => {
                setArchetypes(data.archetypes || []);
                setLoading(false);
            })
            .catch(err => {
                console.error("Failed to load marketplace:", err);
                setLoading(false);
            });
    }, []);

    return (
        <div className="h-full flex flex-col p-6 overflow-y-auto">
            {/* Header */}
            <div className="flex items-center justify-between mb-12">
                <div className="flex items-center gap-6">
                    <div className="w-14 h-14 rounded-2xl bg-indigo-600/20 border border-indigo-500/30 flex items-center justify-center text-indigo-400 shadow-[0_0_30px_rgba(99,102,241,0.2)]">
                        <ShoppingBag className="w-7 h-7" />
                    </div>
                    <div>
                        <h2 className="text-3xl font-light text-white tracking-tight">Archetype Repository</h2>
                        <p className="text-indigo-400/60 font-mono text-[10px] uppercase tracking-[0.3em]">Neural Seeds • Version 2.0</p>
                    </div>
                </div>

                {/* Search (Premium Minimal) */}
                <div className="relative hidden md:block w-72">
                    <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-indigo-500/50" />
                    <input
                        type="text"
                        placeholder="Search collective memory..."
                        className="w-full bg-white/5 border border-white/5 rounded-2xl pl-12 pr-4 py-3 text-sm text-white focus:outline-none focus:border-indigo-500/30 transition-all placeholder:text-slate-600"
                    />
                </div>
            </div>

            {/* Grid */}
            {loading ? (
                <div className="flex items-center justify-center flex-1">
                    <span className="text-slate-500 font-mono animate-pulse">Scanning Neural Marketplace...</span>
                </div>
            ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
                    {archetypes.map(item => (
                        <MarketplaceCard
                            key={item.id}
                            item={item}
                            onSelect={setSelectedItem}
                        />
                    ))}
                </div>
            )}

            {/* Detail Modal (Lightweight) */}
            <AnimatePresence>
                {selectedItem && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
                        onClick={() => setSelectedItem(null)}
                    >
                        <motion.div
                            initial={{ scale: 0.9, y: 20 }}
                            animate={{ scale: 1, y: 0 }}
                            exit={{ scale: 0.9, y: 20 }}
                            className="bg-[#0f111a] border border-white/10 rounded-2xl max-w-lg w-full p-8 shadow-2xl overflow-hidden relative"
                            onClick={e => e.stopPropagation()}
                        >
                            {/* Background Glow */}
                            <div className="absolute -top-20 -right-20 w-64 h-64 bg-emerald-500/20 blur-3xl rounded-full" />

                            <h2 className="text-3xl font-bold text-white mb-2 relative">{selectedItem.title}</h2>
                            <div className="text-emerald-400 font-mono text-sm mb-6 relative">{selectedItem.category} Class</div>

                            <p className="text-slate-300 leading-relaxed mb-8 relative z-10">
                                {selectedItem.description}
                            </p>

                            <div className="flex justify-between items-center pt-6 border-t border-white/5">
                                <div className="text-2xl font-bold text-white">
                                    {selectedItem.price} <span className="text-sm text-slate-500 font-normal">credits</span>
                                </div>
                                <button className="px-8 py-3 bg-emerald-600 hover:bg-emerald-500 text-white font-bold rounded-xl shadow-lg shadow-emerald-600/20 transition-all hover:scale-105">
                                    UNLOCK DNA
                                </button>
                            </div>
                        </motion.div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
};

export default Marketplace;
