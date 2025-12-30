import React, { useState, useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Send, Bot, User, Loader2, Sparkles } from 'lucide-react';

const ChatPanel = ({ characterContext = null }) => {
    const [messages, setMessages] = useState([]);
    const [input, setInput] = useState('');
    const [loading, setLoading] = useState(false);
    const messagesEndRef = useRef(null);

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages]);

    const sendMessage = async () => {
        if (!input.trim() || loading) return;

        const userMessage = { role: 'user', content: input };
        setMessages(prev => [...prev, userMessage]);
        setInput('');
        setLoading(true);

        try {
            const response = await fetch('http://localhost:8000/chat/deepseek', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    messages: [...messages, userMessage],
                    character_context: characterContext
                })
            });

            const data = await response.json();

            if (data.status === 'success') {
                setMessages(prev => [...prev, { role: 'assistant', content: data.response }]);
            } else {
                setMessages(prev => [...prev, { role: 'assistant', content: 'Error: ' + (data.detail || 'Unknown error') }]);
            }
        } catch (error) {
            setMessages(prev => [...prev, { role: 'assistant', content: 'Connection error. Is the backend running?' }]);
        } finally {
            setLoading(false);
        }
    };

    const handleKeyPress = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    };

    return (
        <div className="flex flex-col h-full bg-black/40 backdrop-blur-xl rounded-2xl border border-white/5 overflow-hidden">
            {/* Header */}
            <div className="flex items-center gap-3 p-4 border-b border-white/5">
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center">
                    <Bot className="w-4 h-4 text-white" />
                </div>
                <div>
                    <h3 className="text-sm font-bold text-white">DeepSeek V3</h3>
                    <p className="text-[10px] text-slate-500 font-mono">Direct API Connection</p>
                </div>
                {characterContext && (
                    <div className="ml-auto flex items-center gap-1 text-xs text-indigo-400">
                        <Sparkles className="w-3 h-3" />
                        <span>In Character</span>
                    </div>
                )}
            </div>

            {/* Messages */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
                {messages.length === 0 && (
                    <div className="text-center text-slate-500 text-sm py-8">
                        <Bot className="w-12 h-12 mx-auto mb-3 opacity-20" />
                        <p>Start a conversation with DeepSeek V3</p>
                        {characterContext && (
                            <p className="text-xs text-indigo-400 mt-2">Character context loaded</p>
                        )}
                    </div>
                )}

                <AnimatePresence>
                    {messages.map((msg, i) => (
                        <motion.div
                            key={i}
                            initial={{ opacity: 0, y: 10 }}
                            animate={{ opacity: 1, y: 0 }}
                            className={`flex gap-3 ${msg.role === 'user' ? 'justify-end' : ''}`}
                        >
                            {msg.role === 'assistant' && (
                                <div className="w-6 h-6 rounded-lg bg-indigo-500/20 flex items-center justify-center flex-shrink-0">
                                    <Bot className="w-3 h-3 text-indigo-400" />
                                </div>
                            )}
                            <div className={`max-w-[80%] rounded-xl px-4 py-2 text-sm ${msg.role === 'user'
                                    ? 'bg-indigo-600 text-white'
                                    : 'bg-white/5 text-slate-200'
                                }`}>
                                {msg.content}
                                {msg.role === 'assistant' && (
                                    <div className="mt-1 text-[8px] opacity-40 font-mono text-indigo-400">
                                        MEMORY_ARCHIVE_SUCCESS
                                    </div>
                                )}
                            </div>
                            {msg.role === 'user' && (
                                <div className="w-6 h-6 rounded-lg bg-indigo-600 flex items-center justify-center flex-shrink-0">
                                    <User className="w-3 h-3 text-white" />
                                </div>
                            )}
                        </motion.div>
                    ))}
                </AnimatePresence>

                {loading && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        className="flex gap-3"
                    >
                        <div className="w-6 h-6 rounded-lg bg-indigo-500/20 flex items-center justify-center">
                            <Loader2 className="w-3 h-3 text-indigo-400 animate-spin" />
                        </div>
                        <div className="bg-white/5 rounded-xl px-4 py-2 text-sm text-slate-400">
                            Thinking...
                        </div>
                    </motion.div>
                )}

                <div ref={messagesEndRef} />
            </div>

            {/* Input */}
            <div className="p-4 border-t border-white/5">
                <div className="flex gap-2">
                    <input
                        type="text"
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        onKeyPress={handleKeyPress}
                        placeholder="Message DeepSeek..."
                        className="flex-1 bg-white/5 border border-white/10 rounded-xl px-4 py-3 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500/50"
                        disabled={loading}
                    />
                    <button
                        onClick={sendMessage}
                        disabled={loading || !input.trim()}
                        className="px-4 py-3 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-xl transition-all"
                    >
                        <Send className="w-4 h-4" />
                    </button>
                </div>
            </div>
        </div>
    );
};

export default ChatPanel;
