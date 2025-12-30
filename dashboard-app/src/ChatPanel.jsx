import React, { useState, useRef, useEffect } from 'react';
import { Send, Bot, User, Loader2, Paperclip, Mic, X, Copy, Check, ChevronDown } from 'lucide-react';

// ═══════════════════════════════════════════════════════════════════
// MODELS CONFIGURATION
// ═══════════════════════════════════════════════════════════════════

const AVAILABLE_MODELS = [
    { id: 'deepseek-chat', name: 'DeepSeek V3', provider: 'DeepSeek', icon: '🧠' },
    { id: 'deepseek-reasoner', name: 'DeepSeek R1', provider: 'DeepSeek', icon: '🔬' },
    { id: 'gpt-4', name: 'GPT-4', provider: 'OpenAI', icon: '🤖' },
    { id: 'gpt-4-turbo', name: 'GPT-4 Turbo', provider: 'OpenAI', icon: '⚡' },
    { id: 'claude-3-opus', name: 'Claude 3 Opus', provider: 'Anthropic', icon: '🎭' },
    { id: 'claude-3-sonnet', name: 'Claude 3 Sonnet', provider: 'Anthropic', icon: '📝' },
    { id: 'gemini-pro', name: 'Gemini Pro', provider: 'Google', icon: '✨' },
];

// ═══════════════════════════════════════════════════════════════════
// MODEL SELECTOR COMPONENT
// ═══════════════════════════════════════════════════════════════════

const ModelSelector = ({ selectedModel, onSelectModel }) => {
    const [isOpen, setIsOpen] = useState(false);
    const dropdownRef = useRef(null);

    useEffect(() => {
        const handleClickOutside = (e) => {
            if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
                setIsOpen(false);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    const current = AVAILABLE_MODELS.find(m => m.id === selectedModel) || AVAILABLE_MODELS[0];

    return (
        <div className="relative" ref={dropdownRef}>
            <button
                onClick={() => setIsOpen(!isOpen)}
                className="flex items-center gap-2 px-3 py-1.5 bg-white/5 hover:bg-white/10 rounded-lg border border-white/10 transition-colors"
            >
                <span className="text-base">{current.icon}</span>
                <div className="text-left">
                    <div className="text-xs font-medium text-white">{current.name}</div>
                    <div className="text-[10px] text-slate-500">{current.provider}</div>
                </div>
                <ChevronDown className={`w-4 h-4 text-slate-400 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
            </button>

            {isOpen && (
                <div className="absolute top-full mt-2 right-0 w-56 bg-[#1a1a1a] border border-white/10 rounded-lg shadow-2xl overflow-hidden z-50">
                    <div className="p-2 space-y-1 max-h-80 overflow-y-auto">
                        {AVAILABLE_MODELS.map(model => (
                            <button
                                key={model.id}
                                onClick={() => {
                                    onSelectModel(model.id);
                                    setIsOpen(false);
                                }}
                                className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors ${model.id === selectedModel
                                    ? 'bg-indigo-600 text-white'
                                    : 'hover:bg-white/5 text-slate-300'
                                    }`}
                            >
                                <span className="text-lg">{model.icon}</span>
                                <div className="flex-1 text-left">
                                    <div className="text-sm font-medium">{model.name}</div>
                                    <div className="text-[10px] opacity-60">{model.provider}</div>
                                </div>
                                {model.id === selectedModel && (
                                    <Check className="w-4 h-4" />
                                )}
                            </button>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
};

// ═══════════════════════════════════════════════════════════════════
// CODE BLOCK COMPONENT
// ═══════════════════════════════════════════════════════════════════

const CodeBlock = ({ code, language }) => {
    const [copied, setCopied] = useState(false);

    const handleCopy = () => {
        navigator.clipboard.writeText(code);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    return (
        <div className="relative group my-3 rounded-lg overflow-hidden bg-[#1e1e1e] border border-white/10">
            <div className="flex items-center justify-between px-4 py-2 bg-white/5 border-b border-white/5">
                <span className="text-xs text-slate-500 font-mono">{language || 'code'}</span>
                <button
                    onClick={handleCopy}
                    className="text-slate-500 hover:text-white transition-colors"
                >
                    {copied ? <Check className="w-4 h-4 text-emerald-400" /> : <Copy className="w-4 h-4" />}
                </button>
            </div>
            <pre className="p-4 overflow-x-auto text-sm">
                <code className="text-slate-300 font-mono">{code}</code>
            </pre>
        </div>
    );
};

// ═══════════════════════════════════════════════════════════════════
// MESSAGE CONTENT RENDERER (Markdown-like)
// ═══════════════════════════════════════════════════════════════════

const MessageContent = ({ content }) => {
    // Simple markdown-like parsing
    const parseContent = (text) => {
        const parts = [];
        let remaining = text;
        let key = 0;

        // Parse code blocks first
        const codeBlockRegex = /```(\w*)\n?([\s\S]*?)```/g;
        let lastIndex = 0;
        let match;

        while ((match = codeBlockRegex.exec(text)) !== null) {
            // Add text before code block
            if (match.index > lastIndex) {
                parts.push(
                    <span key={key++} className="whitespace-pre-wrap">
                        {parseInlineElements(text.slice(lastIndex, match.index))}
                    </span>
                );
            }

            // Add code block
            parts.push(
                <CodeBlock key={key++} language={match[1]} code={match[2].trim()} />
            );

            lastIndex = match.index + match[0].length;
        }

        // Add remaining text
        if (lastIndex < text.length) {
            parts.push(
                <span key={key++} className="whitespace-pre-wrap">
                    {parseInlineElements(text.slice(lastIndex))}
                </span>
            );
        }

        return parts.length > 0 ? parts : <span className="whitespace-pre-wrap">{text}</span>;
    };

    const parseInlineElements = (text) => {
        // Parse inline code
        return text.split(/(`[^`]+`)/).map((part, i) => {
            if (part.startsWith('`') && part.endsWith('`')) {
                return (
                    <code key={i} className="px-1.5 py-0.5 bg-white/10 rounded text-sm font-mono text-indigo-300">
                        {part.slice(1, -1)}
                    </code>
                );
            }
            // Parse bold
            return part.split(/(\*\*[^*]+\*\*)/).map((p, j) => {
                if (p.startsWith('**') && p.endsWith('**')) {
                    return <strong key={`${i}-${j}`} className="font-semibold">{p.slice(2, -2)}</strong>;
                }
                return p;
            });
        });
    };

    return <div className="leading-relaxed">{parseContent(content)}</div>;
};

// ═══════════════════════════════════════════════════════════════════
// MAIN CHAT PANEL - ChatGPT/Cursor Style
// ═══════════════════════════════════════════════════════════════════

const ChatPanel = ({ characterContext = null }) => {
    const [messages, setMessages] = useState([]);
    const [input, setInput] = useState('');
    const [loading, setLoading] = useState(false);
    const [files, setFiles] = useState([]);
    const [selectedModel, setSelectedModel] = useState('deepseek-chat');
    const messagesEndRef = useRef(null);
    const fileInputRef = useRef(null);
    const textareaRef = useRef(null);

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages, loading]);

    // Auto-resize textarea
    useEffect(() => {
        if (textareaRef.current) {
            textareaRef.current.style.height = 'auto';
            textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 200) + 'px';
        }
    }, [input]);

    const handleFileSelect = (e) => {
        const newFiles = Array.from(e.target.files).slice(0, 5 - files.length);
        setFiles(prev => [...prev, ...newFiles]);
        e.target.value = '';
    };

    const removeFile = (index) => {
        setFiles(prev => prev.filter((_, i) => i !== index));
    };

    const sendMessage = async () => {
        if ((!input.trim() && files.length === 0) || loading) return;

        const userMessage = {
            role: 'user',
            content: input,
            files: files.map(f => ({ name: f.name, type: f.type }))
        };

        setMessages(prev => [...prev, userMessage]);
        const currentInput = input;
        setInput('');
        setFiles([]);
        setLoading(true);

        try {
            const response = await fetch('http://localhost:8000/chat/deepseek', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    messages: [...messages, { role: 'user', content: currentInput }],
                    character_context: characterContext
                })
            });

            const data = await response.json();

            if (data.status === 'success') {
                setMessages(prev => [...prev, {
                    role: 'assistant',
                    content: data.response
                }]);
            } else {
                setMessages(prev => [...prev, {
                    role: 'assistant',
                    content: 'Error: ' + (data.detail || 'Unknown error')
                }]);
            }
        } catch (error) {
            setMessages(prev => [...prev, {
                role: 'assistant',
                content: 'Connection error. Make sure the backend is running on port 8000.'
            }]);
        } finally {
            setLoading(false);
        }
    };

    const handleKeyDown = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    };

    return (
        <div className="flex flex-col h-full bg-[#0d0d0d]">
            {/* Header with Model Selector */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-white/5 bg-[#1a1a1a]">
                <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center">
                        <Bot className="w-4 h-4 text-white" />
                    </div>
                    <div>
                        <h3 className="text-sm font-medium text-white">AI Chat</h3>
                        <p className="text-[10px] text-slate-500">Select a model to start</p>
                    </div>
                </div>
                <ModelSelector selectedModel={selectedModel} onSelectModel={setSelectedModel} />
            </div>

            {/* Messages Area */}
            <div className="flex-1 overflow-y-auto">
                {messages.length === 0 ? (
                    <div className="h-full flex flex-col items-center justify-center text-center px-4">
                        <div className="w-16 h-16 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center mb-4">
                            <Bot className="w-8 h-8 text-white" />
                        </div>
                        <h2 className="text-xl font-medium text-white mb-2">How can I help you today?</h2>
                        <p className="text-sm text-slate-500 max-w-md">
                            Select a model above and start chatting
                        </p>
                    </div>
                ) : (
                    <div className="max-w-3xl mx-auto px-4 py-6 space-y-6">
                        {messages.map((msg, i) => (
                            <div key={i} className={`flex gap-4 ${msg.role === 'user' ? 'justify-end' : ''}`}>
                                {msg.role === 'assistant' && (
                                    <div className="w-8 h-8 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center flex-shrink-0">
                                        <Bot className="w-4 h-4 text-white" />
                                    </div>
                                )}

                                <div className={`flex-1 max-w-[85%] ${msg.role === 'user' ? 'flex justify-end' : ''}`}>
                                    {msg.role === 'user' ? (
                                        <div className="bg-[#2f2f2f] rounded-2xl px-4 py-3 text-white text-sm">
                                            {msg.files?.length > 0 && (
                                                <div className="text-xs text-slate-400 mb-2">
                                                    📎 {msg.files.map(f => f.name).join(', ')}
                                                </div>
                                            )}
                                            {msg.content}
                                        </div>
                                    ) : (
                                        <div className="text-slate-200 text-sm">
                                            <MessageContent content={msg.content} />
                                        </div>
                                    )}
                                </div>

                                {msg.role === 'user' && (
                                    <div className="w-8 h-8 rounded-full bg-indigo-600 flex items-center justify-center flex-shrink-0">
                                        <User className="w-4 h-4 text-white" />
                                    </div>
                                )}
                            </div>
                        ))}

                        {loading && (
                            <div className="flex gap-4">
                                <div className="w-8 h-8 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center flex-shrink-0">
                                    <Bot className="w-4 h-4 text-white" />
                                </div>
                                <div className="flex items-center gap-2 text-slate-400 text-sm">
                                    <Loader2 className="w-4 h-4 animate-spin" />
                                    <span>Thinking...</span>
                                </div>
                            </div>
                        )}

                        <div ref={messagesEndRef} />
                    </div>
                )}
            </div>

            {/* Input Area - Fixed at bottom, CLEARLY MULTILINE */}
            <div className="border-t border-white/5 bg-[#0d0d0d] p-4">
                <div className="max-w-3xl mx-auto">
                    {/* File previews */}
                    {files.length > 0 && (
                        <div className="flex flex-wrap gap-2 mb-3">
                            {files.map((file, i) => (
                                <div key={i} className="flex items-center gap-2 bg-[#2f2f2f] rounded-lg px-3 py-1.5 text-sm">
                                    <span className="text-slate-300 truncate max-w-[150px]">{file.name}</span>
                                    <button onClick={() => removeFile(i)} className="text-slate-500 hover:text-white">
                                        <X className="w-3 h-3" />
                                    </button>
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Input box - MULTILINE with visible height */}
                    <div className="relative flex items-end gap-2 bg-[#2f2f2f] rounded-2xl border border-white/10 p-3 focus-within:border-indigo-500/50 transition-colors">
                        {/* Attachment button */}
                        <button
                            onClick={() => fileInputRef.current?.click()}
                            className="p-2 text-slate-400 hover:text-white rounded-lg hover:bg-white/10 transition-colors flex-shrink-0 self-end mb-1"
                            title="Attach files"
                        >
                            <Paperclip className="w-5 h-5" />
                        </button>
                        <input
                            ref={fileInputRef}
                            type="file"
                            multiple
                            onChange={handleFileSelect}
                            className="hidden"
                        />

                        {/* Text input - CLEARLY MULTILINE */}
                        <textarea
                            ref={textareaRef}
                            value={input}
                            onChange={(e) => setInput(e.target.value)}
                            onKeyDown={handleKeyDown}
                            placeholder="Ask anything... (Shift+Enter for new line)"
                            rows={3}
                            className="flex-1 bg-transparent text-white text-sm resize-none focus:outline-none placeholder-slate-500 py-2 max-h-[200px] leading-relaxed"
                            style={{ minHeight: '60px' }}
                            disabled={loading}
                        />

                        {/* Send button */}
                        <button
                            onClick={sendMessage}
                            disabled={loading || (!input.trim() && files.length === 0)}
                            className={`p-3 rounded-xl transition-all flex-shrink-0 self-end ${input.trim() || files.length > 0
                                    ? 'bg-indigo-600 hover:bg-indigo-500 text-white shadow-lg shadow-indigo-500/20'
                                    : 'bg-white/5 text-slate-600 cursor-not-allowed'
                                }`}
                        >
                            <Send className="w-5 h-5" />
                        </button>
                    </div>

                    {/* Footer hint */}
                    <p className="text-[10px] text-slate-600 text-center mt-3">
                        <span className="font-mono">{AVAILABLE_MODELS.find(m => m.id === selectedModel)?.name}</span> • Press Enter to send
                    </p>
                </div>
            </div>
        </div>
    );
};

export default ChatPanel;
