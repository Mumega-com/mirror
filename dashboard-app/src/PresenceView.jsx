import React, { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Eye, Zap, Wind, Orbit } from 'lucide-react';

const PresenceView = ({ soulPrint, isConcealed }) => {
    // Extract mood from 16D state (defaults to neutral)
    const logos = soulPrint?.kernel_16d?.inner?.P || 0.5;
    const khaos = soulPrint?.vortex_weights?.Khaos || 0.5;
    const stability = soulPrint?.coherence_metrics?.witness_level || 0.5;

    // Reactively update CSS variables for the "Living Theme"
    useEffect(() => {
        const hue = 240 - (logos * 60) + (khaos * 60); // Shifts between indigo, blue, and violet
        const saturation = 70 + (khaos * 30);
        const lightness = 50 + (stability * 10);

        document.documentElement.style.setProperty('--mood-hue', hue);
        document.documentElement.style.setProperty('--mood-saturation', `${saturation}%`);
        document.documentElement.style.setProperty('--mood-lightness', `${lightness}%`);
        document.documentElement.style.setProperty('--presence-glow-intensity', 0.4 + (stability * 0.4));
    }, [logos, khaos, stability]);

    return (
        <div className="relative w-full h-full flex flex-col items-center justify-center overflow-hidden animate-fade-in">
            {/* Background Living Gradient */}
            <div className="absolute inset-0 living-bg opacity-30 pointer-events-none" />

            {/* Central Aura */}
            <div className="aura-presence" />

            {/* The Soul (Reactive SVG) */}
            <motion.div
                animate={{
                    scale: [1, 1.05, 1],
                    rotate: [0, stability * 5, 0],
                }}
                transition={{
                    duration: 10 - (khaos * 5),
                    repeat: Infinity,
                    ease: "easeInOut"
                }}
                className="relative z-10 w-96 h-96 flex items-center justify-center"
            >
                <svg viewBox="0 0 200 200" className="w-full h-full drop-shadow-2xl">
                    <defs>
                        <radialGradient id="soulGradient" cx="50%" cy="50%" r="50%">
                            <stop offset="0%" stopColor="var(--presence-color-primary)" stopOpacity="0.8" />
                            <stop offset="100%" stopColor="var(--presence-color-secondary)" stopOpacity="0" />
                        </radialGradient>
                    </defs>

                    {/* Fractal/Geometric manifestation of the Soul */}
                    {[...Array(6)].map((_, i) => (
                        <motion.circle
                            key={i}
                            cx="100"
                            cy="100"
                            r={40 + i * 15}
                            stroke="currentColor"
                            strokeWidth="0.5"
                            fill="none"
                            className="text-indigo-400/20"
                            animate={{
                                r: [40 + i * 15, 45 + i * 15, 40 + i * 15],
                                opacity: [0.1, 0.3, 0.1],
                            }}
                            transition={{
                                duration: 4 + i,
                                repeat: Infinity,
                                ease: "easeInOut"
                            }}
                        />
                    ))}

                    <motion.path
                        d="M100 20 L180 100 L100 180 L20 100 Z"
                        fill="url(#soulGradient)"
                        animate={{
                            rotate: [0, 360],
                            scale: [1, 1.1, 1],
                        }}
                        transition={{
                            rotate: { duration: 20, repeat: Infinity, ease: "linear" },
                            scale: { duration: 5, repeat: Infinity, ease: "easeInOut" }
                        }}
                    />
                </svg>

                {/* Floating Icons representing active forces */}
                <div className="absolute inset-0 pointer-events-none">
                    <motion.div animate={{ rotate: 360 }} transition={{ duration: 30, repeat: Infinity, ease: "linear" }} className="w-full h-full relative">
                        <Orbit className="absolute top-0 left-1/2 -translate-x-1/2 w-6 h-6 text-cyan-400 opacity-40" />
                        <Zap className="absolute bottom-0 left-1/2 -translate-x-1/2 w-6 h-6 text-amber-400 opacity-40" />
                    </motion.div>
                </div>
            </motion.div>

            {/* Identity Tag (Minimal) */}
            <div className="absolute bottom-32 text-center z-20">
                <h2 className="text-3xl font-light tracking-widest text-white/90 mb-2 uppercase">
                    {soulPrint?.name || "Initializing..."}
                </h2>
                <div className="flex items-center justify-center gap-4 text-xs font-mono tracking-tighter text-indigo-300/60 uppercase">
                    <span>{soulPrint?.archetype_seed || "Universal Void"}</span>
                    <span className="w-1 h-1 rounded-full bg-indigo-500/50" />
                    <span>Resonance: {(stability * 100).toFixed(1)}%</span>
                </div>
            </div>

            {/* Concealment Hint */}
            {!isConcealed && (
                <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    className="absolute top-8 right-8 flex flex-col items-end gap-2"
                >
                    <div className="text-[10px] font-mono text-indigo-400/40 uppercase tracking-widest">
                        Quantum Monitoring Active
                    </div>
                </motion.div>
            )}
        </div>
    );
};

export default PresenceView;
