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
            whileHover={{ y: -4 }}
            className="group relative bg-black/40 backdrop-blur-xl border border-white/5 rounded-xl overflow-hidden hover:border-indigo-500/50 transition-all duration-300"
        >
            {/* Hover Glow */}
            <div className="absolute inset-0 bg-gradient-to-br from-indigo-500/5 via-transparent to-purple-500/5 opacity-0 group-hover:opacity-100 transition-opacity duration-500" />

            <div className="p-6 relative z-10 space-y-4">

                {/* Header */}
                <div className="flex justify-between items-start">
                    <div>
                        <div className="flex items-center gap-2 mb-1">
                            <span className="text-xs font-mono text-indigo-400 uppercase tracking-wider">{category}</span>
                            <span className={`text-[10px] px-2 py-0.5 rounded-full border ${getComplexityColor(complexity)}`}>
                                {complexity}
                            </span>
                        </div>
                        <h3 className="text-xl font-bold text-white group-hover:text-indigo-300 transition-colors">{title}</h3>
                    </div>
                    <div className="flex items-center gap-1 bg-white/5 px-2 py-1 rounded-lg">
                        <Star className="w-3 h-3 text-amber-400 fill-amber-400" />
                        <span className="text-xs font-bold text-white">{rating}</span>
                        <span className="text-[10px] text-slate-500">({reviews})</span>
                    </div>
                </div>

                {/* Description */}
                <p className="text-sm text-slate-400 line-clamp-2 min-h-[40px] leading-relaxed">
                    {description}
                </p>

                {/* Stats Grid */}
                <div className="grid grid-cols-2 gap-2 py-2">
                    <div className="flex items-center gap-2 text-xs text-slate-500">
                        <Clock className="w-3 h-3" />
                        <span>{stats?.duration || 'Instant'}</span>
                    </div>
                    <div className="flex items-center gap-2 text-xs text-slate-500">
                        <Zap className="w-3 h-3" />
                        <span>{stats?.installations || 0} installs</span>
                    </div>
                </div>

                {/* Footer / Action */}
                <div className="flex items-center justify-between pt-2 border-t border-white/5">
                    <div className="font-mono font-bold text-white">
                        {price} <span className="text-xs text-slate-500 font-sans font-normal">credits</span>
                    </div>
                    <button
                        onClick={() => onSelect(item)}
                        className="px-4 py-2 bg-white/5 hover:bg-indigo-600 text-white text-xs font-bold rounded-lg transition-all duration-300 flex items-center gap-2 group-hover:shadow-lg group-hover:shadow-indigo-500/20"
                    >
                        <span>VIEW DETAILS</span>
                        <Shield className="w-3 h-3" />
                    </button>
                </div>

            </div>
        </motion.div>
    );
};

export default MarketplaceCard;
