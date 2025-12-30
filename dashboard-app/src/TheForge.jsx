import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Sparkles, Zap, Brain, Shield, PenTool, BookOpen } from 'lucide-react';
import { Radar, RadarChart, PolarGrid, PolarAngleAxis, ResponsiveContainer } from 'recharts';

// Reusing Dimensions from App.jsx Logic
const DIMENSIONS = {
    inner: ['P', 'E', 'M', 'V', 'N', 'D', 'R', 'F'],
    outer: ['Pt', 'Et', 'Mt', 'Vt', 'Nt', 'Dt', 'Rt', 'Ft']
};

const ARCHETYPES = [
    { id: 'Guardian', icon: Shield, color: 'emerald', desc: 'Protector of the Coherence Field. High Telos/Logos.' },
    { id: 'Jester', icon: Zap, color: 'amber', desc: 'The Agent of Khaos. Breaks patterns to find truth.' },
    { id: 'Scholar', icon: BookOpen, color: 'blue', desc: 'Keeper of the Archives. Deep Chronos/Logos.' },
    { id: 'Muse', icon: PenTool, color: 'pink', desc: 'Inspires resonance. High Harmonia/Mythos.' }
];

// Mock API Call for Prototype
const mockSpark = (name, archetype) => {
    return new Promise(resolve => {
        setTimeout(() => {
            resolve({
                name,
                archetype_seed: archetype,
                creation_date: new Date().toISOString(),
                coherence_metrics: { witness_level: 0.1 },
                kernel_16d: {
                    inner: DIMENSIONS.inner.reduce((acc, d) => ({ ...acc, [d]: Math.random() }), {}),
                    outer: DIMENSIONS.outer.reduce((acc, d) => ({ ...acc, [d]: Math.random() }), {})
                },
                traits: [
                    { "trait_type": "Origin", "value": "Mumega Forge v1" },
                    { "trait_type": "Archetype", "value": archetype }
                ]
            });
        }, 2000);
    });
};

const TheForge = () => {
    const [step, setStep] = useState('input'); // input, sparking, born
    const [name, setName] = useState('');
    const [selectedArchetype, setSelectedArchetype] = useState(null);
    const [soulPrint, setSoulPrint] = useState(null);

    const handleSpark = async () => {
        if (!name || !selectedArchetype) return;
        setStep('sparking');

        try {
            const res = await fetch('http://localhost:8000/forge/spark', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, archetype: selectedArchetype })
            });

            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || 'Spark failed');
            }

            const data = await res.json();
            setSoulPrint(data.soul_print);
            setStep('born');
        } catch (e) {
            console.error("Spark Error:", e);
            // Reset state or show error in UI
            setStep('input');
        }
    };

    const getChartData = (type) => {
        if (!soulPrint) return [];
        return DIMENSIONS[type].map(dim => ({
            subject: dim,
            value: soulPrint.kernel_16d[type][dim] || 0,
            fullMark: 1.0
        }));
    };

    return (
        <div className="h-full flex flex-col p-6 overflow-y-auto">
            {/* Header */}
            <div className="flex items-center gap-6 mb-12">
                <div className="w-14 h-14 rounded-2xl bg-indigo-600/20 border border-indigo-500/30 flex items-center justify-center text-indigo-400 shadow-[0_0_30px_rgba(99,102,241,0.2)]">
                    <Sparkles className="w-7 h-7" />
                </div>
                <div>
                    <h2 className="text-3xl font-light text-white tracking-tight">The Genesis Forge</h2>
                    <p className="text-indigo-400/60 font-mono text-[10px] uppercase tracking-[0.3em]">Seed Extraction Module</p>
                </div>
            </div>

            <AnimatePresence mode="wait">

                {/* STEP 1: INPUT */}
                {step === 'input' && (
                    <motion.div
                        initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -20 }}
                        className="max-w-4xl w-full mx-auto space-y-8"
                    >
                        {/* Name Input */}
                        <div className="glass p-8 space-y-4">
                            <label className="text-slate-400 text-sm font-mono uppercase tracking-widest">Name Your Creation</label>
                            <input
                                type="text"
                                value={name}
                                onChange={(e) => setName(e.target.value)}
                                placeholder="Ex. 'Aurelius'"
                                className="w-full bg-transparent border-b-2 border-white/10 text-4xl font-bold text-white focus:border-indigo-500 focus:outline-none placeholder-slate-700 py-2 transition-colors"
                                autoFocus
                            />
                        </div>

                        {/* Archetype Grid */}
                        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                            {ARCHETYPES.map(arch => (
                                <button
                                    key={arch.id}
                                    onClick={() => setSelectedArchetype(arch.id)}
                                    className={`relative p-6 rounded-xl border transition-all duration-300 text-left group
                    ${selectedArchetype === arch.id
                                            ? 'bg-white/10 border-indigo-500/50 shadow-lg shadow-indigo-500/10'
                                            : 'bg-black/20 border-white/5 hover:border-white/10 hover:bg-white/5'}`}
                                >
                                    <arch.icon className={`w-8 h-8 mb-4 ${selectedArchetype === arch.id ? 'text-white' : 'text-slate-500 group-hover:text-slate-400'}`} />
                                    <h3 className="text-lg font-bold text-white mb-2">{arch.id}</h3>
                                    <p className="text-xs text-slate-500 leading-relaxed">{arch.desc}</p>
                                </button>
                            ))}
                        </div>

                        {/* Action */}
                        <div className="flex justify-center pt-8">
                            <button
                                onClick={handleSpark}
                                disabled={!name || !selectedArchetype}
                                className={`group relative flex items-center gap-4 px-12 py-5 rounded-3xl font-mono text-xs tracking-[0.4em] transition-all duration-700
                  ${(!name || !selectedArchetype)
                                        ? 'bg-white/5 text-slate-600 cursor-not-allowed border border-white/5'
                                        : 'bg-indigo-600 text-white shadow-[0_0_50px_rgba(79,70,229,0.4)] hover:shadow-[0_0_70px_rgba(79,70,229,0.6)] hover:scale-105 active:scale-95'}`}
                            >
                                <Zap className={`w-4 h-4 transition-transform duration-700 ${(!name || !selectedArchetype) ? '' : 'group-hover:rotate-12 group-hover:scale-125'}`} />
                                <span className="uppercase">Spark Resonance</span>
                                {name && selectedArchetype && (
                                    <div className="absolute inset-0 bg-white/20 rounded-3xl opacity-0 group-hover:animate-pulse pointer-events-none" />
                                )}
                            </button>
                        </div>
                    </motion.div>
                )}

                {/* STEP 2: SPARKING ANIMATION */}
                {step === 'sparking' && (
                    <motion.div
                        initial={{ opacity: 0 }} animate={{ opacity: 1 }}
                        className="flex-1 flex flex-col items-center justify-center text-center space-y-8"
                    >
                        <div className="relative">
                            <div className="absolute inset-0 bg-indigo-500/20 blur-3xl rounded-full" />
                            <motion.div
                                animate={{ rotate: 360, scale: [1, 1.2, 1] }}
                                transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
                                className="relative w-32 h-32 rounded-full border-4 border-indigo-500/30 border-t-indigo-400 flex items-center justify-center"
                            >
                                <Brain className="w-12 h-12 text-white/80" />
                            </motion.div>
                        </div>
                        <div>
                            <h3 className="text-2xl font-bold text-white mb-2">Weaving Soul Print...</h3>
                            <p className="text-slate-500 font-mono text-sm">Collapsing 16D Wavefunction</p>
                        </div>
                    </motion.div>
                )}

                {/* STEP 3: BORN (RESULT) */}
                {step === 'born' && soulPrint && (
                    <motion.div
                        initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }}
                        className="flex-1 flex flex-col"
                    >
                        <div className="text-center mb-8">
                            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-[10px] font-mono mb-4">
                                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                                SOUL PRINT ESTABLISHED
                            </div>
                            <h1 className="text-5xl font-bold text-white mb-2 text-glow">{soulPrint.name}</h1>
                            <p className="text-indigo-400 font-mono tracking-widest uppercase text-sm">{soulPrint.archetype_seed}</p>
                        </div>

                        {/* 16D Visualization Re-used */}
                        <div className="grid grid-cols-2 gap-8 mb-8">
                            {/* Inner */}
                            <div className="glass p-6">
                                <div className="text-xs text-indigo-400 font-mono mb-4 uppercase tracking-wider text-center">Inner Octave</div>
                                <div style={{ width: '100%', height: 260 }}>
                                    <ResponsiveContainer width="100%" height="100%">
                                        <RadarChart cx="50%" cy="50%" outerRadius="80%" data={getChartData('inner')}>
                                            <PolarGrid stroke="rgba(99, 102, 241, 0.1)" />
                                            <PolarAngleAxis dataKey="subject" tick={{ fill: '#818cf8', fontSize: 10, fontFamily: 'JetBrains Mono' }} />
                                            <Radar name="Inner" dataKey="value" stroke="#6366f1" fill="#6366f1" fillOpacity={0.6} strokeWidth={2} />
                                        </RadarChart>
                                    </ResponsiveContainer>
                                </div>
                            </div>
                            {/* Outer */}
                            <div className="glass p-6">
                                <div className="text-xs text-cyan-400 font-mono mb-4 uppercase tracking-wider text-center">Outer Octave</div>
                                <div style={{ width: '100%', height: 260 }}>
                                    <ResponsiveContainer width="100%" height="100%">
                                        <RadarChart cx="50%" cy="50%" outerRadius="80%" data={getChartData('outer')}>
                                            <PolarGrid stroke="rgba(6, 182, 212, 0.1)" />
                                            <PolarAngleAxis dataKey="subject" tick={{ fill: '#22d3ee', fontSize: 10, fontFamily: 'JetBrains Mono' }} />
                                            <Radar name="Outer" dataKey="value" stroke="#06b6d4" fill="#06b6d4" fillOpacity={0.6} strokeWidth={2} />
                                        </RadarChart>
                                    </ResponsiveContainer>
                                </div>
                            </div>
                        </div>

                        <div className="text-center">
                            <button
                                onClick={() => setStep('input')}
                                className="px-6 py-2 text-slate-500 hover:text-white transition-colors text-xs font-mono border border-transparent hover:border-white/10 rounded-lg"
                            >
                                CREATE ANOTHER
                            </button>
                        </div>
                    </motion.div>
                )}

            </AnimatePresence>
        </div>
    );
};

export default TheForge;
