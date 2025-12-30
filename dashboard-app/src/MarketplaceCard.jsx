import React from 'react';
import { Sparkles, Star, Clock, Zap, Key, Shield } from 'lucide-react';
import { motion } from 'framer-motion';

const MarketplaceCard = ({ item, onSelect }) => {
    const { title, description, category, price, rating, reviews, complexity, stats } = item;

    const getComplexityColor = (c) => {
        switch (c?.toLowerCase()) {
            case 'low': return 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20';
            case 'medium': return 'text-amber-400 bg-amber-400/10 border-amber-400/20';
            case 'high': return 'text-rose-400 bg-rose-400/10 border-rose-400/20';
            default: return 'text-slate-400 bg-slate-400/10 border-slate-400/20';
        }
    };

    return (
        <motion.div
            whileHover={{ y: -8, scale: 1.02 }}
            className="group relative bg-black/40 backdrop-blur-2xl border border-white/5 rounded-3xl overflow-hidden hover:border-indigo-500/30 transition-all duration-500 shadow-xl"
        >
            {/* Ambient Background Glow */}
            <div className="absolute -top-10 -right-10 w-32 h-32 bg-indigo-500/5 blur-3xl rounded-full opacity-0 group-hover:opacity-100 transition-opacity duration-700" />

            <div className="p-8 relative z-10 space-y-6">

                {/* Header */}
                <div className="flex justify-between items-start">
                    <div className="space-y-1">
                        <div className="flex items-center gap-3">
                            <span className="text-[10px] font-mono text-indigo-400 uppercase tracking-[0.2em]">{category}</span>
                            <div className={`text-[8px] px-2 py-0.5 rounded-full border font-mono uppercase tracking-tighter ${getComplexityColor(complexity)}`}>
                                {complexity} CORE
                            </div>
                        </div>
                        <h3 className="text-2xl font-light text-white group-hover:text-indigo-200 transition-colors tracking-tight">{title}</h3>
                    </div>
                </div>

                {/* Description */}
                <p className="text-sm text-slate-400/80 line-clamp-2 min-h-[40px] leading-relaxed font-light">
                    {description}
                </p>

                {/* Stats / Tickers */}
                <div className="flex items-center gap-6 py-2 border-y border-white/5">
                    <div className="flex items-center gap-2 text-[10px] font-mono text-slate-500 uppercase tracking-widest">
                        <Clock className="w-3 h-3 text-indigo-500/40" />
                        <span>{stats?.duration || 'INSTANT'}</span>
                    </div>
                    <div className="flex items-center gap-2 text-[10px] font-mono text-slate-500 uppercase tracking-widest">
                        <Zap className="w-3 h-3 text-indigo-500/40" />
                        <span>{stats?.installations || 0} SEEDS</span>
                    </div>
                </div>

                {/* Action Bar */}
                <div className="flex items-center justify-between pt-2">
                    <div className="flex flex-col">
                        <span className="text-[9px] font-mono text-slate-600 uppercase tracking-tighter">Flux Cost</span>
                        <div className="text-xl font-light text-white">
                            {price} <span className="text-[10px] text-indigo-400/60 font-mono uppercase">Credits</span>
                        </div>
                    </div>
                    <button
                        onClick={() => onSelect(item)}
                        className="px-6 py-3 bg-white/5 hover:bg-white/10 text-white text-[10px] font-mono tracking-[0.2em] uppercase rounded-2xl border border-white/5 transition-all duration-300 group-hover:border-indigo-500/20 group-hover:shadow-[0_0_20px_rgba(99,102,241,0.1)]"
                    >
                        Review DNA
                    </button>
                </div>

            </div>
        </motion.div>
    );
};

export default MarketplaceCard;
