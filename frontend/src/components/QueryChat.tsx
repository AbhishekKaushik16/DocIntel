'use client';

import { useState, useRef, useEffect } from 'react';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface QuerySource {
  document_id: string | null;
  filename: string | null;
  relevance: number | null;
}

interface QueryStep {
  round: number;
  tool: string;
  input: Record<string, any>;
  output: Record<string, any>;
}

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  sources?: QuerySource[];
  reasoning?: string;
  steps?: QueryStep[];
  loading?: boolean;
}

const SUGGESTED_QUERIES = [
  'How many documents have I uploaded?',
  'What document types are in my collection?',
  'Show me the average confidence score',
  'What are the key findings in the financial reports?',
];

export default function QueryChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSubmit = async (question: string) => {
    if (!question.trim() || loading) return;

    const userMsg: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: question,
    };

    const loadingMsg: Message = {
      id: (Date.now() + 1).toString(),
      role: 'assistant',
      content: '',
      loading: true,
    };

    setMessages((prev) => [...prev, userMsg, loadingMsg]);
    setInput('');
    setLoading(true);

    try {
      const chatHistory = messages
        .filter(m => !m.loading && m.content)
        .map(m => ({
          role: m.role,
          content: m.content
        }));

      const res = await fetch(`${API_BASE}/api/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          question, 
          max_results: 10,
          chat_history: chatHistory
        }),
      });

      if (!res.ok || !res.body) throw new Error('Query failed');
      
      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      
      let done = false;
      let currentContent = '';
      let buffer = '';
      
      while (!done) {
        const { value, done: readerDone } = await reader.read();
        done = readerDone;
        if (value) {
          buffer += decoder.decode(value, { stream: true });
          
          let eolIndex;
          while ((eolIndex = buffer.indexOf('\n\n')) >= 0) {
            const eventString = buffer.slice(0, eolIndex).trim();
            buffer = buffer.slice(eolIndex + 2);
            
            if (eventString.startsWith('data: ')) {
              try {
                const event = JSON.parse(eventString.slice(6));
                
                setMessages((prev) => 
                  prev.map((m): Message => {
                    if (m.id === loadingMsg.id) {
                      if (event.type === 'token') {
                        currentContent += event.content;
                        let displayContent = currentContent;
                        const match = currentContent.match(/<answer>([\s\S]*?)(?:<\/answer>|$)/);
                        if (match) {
                            displayContent = match[1] || "";
                        } else if (currentContent.includes('<answer>')) {
                            displayContent = currentContent.split('<answer>')[1] || "";
                        } else if (currentContent.includes('<reasoning>') || currentContent.includes('<sources>')) {
                            displayContent = "Processing...";
                        } else {
                            displayContent = currentContent;
                        }
                        return { ...m, content: displayContent, loading: false };
                      } else if (event.type === 'step') {
                        return { ...m, steps: [...(m.steps || []), event.content] };
                      } else if (event.type === 'done') {
                        return { 
                          ...m, 
                          sources: event.sources, 
                          reasoning: event.reasoning,
                          steps: event.steps,
                          loading: false
                        };
                      }
                    }
                    return m;
                  })
                );
              } catch (e) {
                console.error("SSE parse error", e);
              }
            }
          }
        }
      }
    } catch (err) {
      setMessages((prev) =>
        prev.map((m): Message =>
          m.id === loadingMsg.id
            ? { ...m, content: 'Sorry, something went wrong. Please try again.', loading: false }
            : m
        )
      );
    } finally {
      setLoading(false);
    }
  };

  const toggleSteps = (msgId: string) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(msgId)) next.delete(msgId);
      else next.add(msgId);
      return next;
    });
  };

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <div style={styles.headerIcon}>🔍</div>
        <div>
          <h2 style={styles.headerTitle}>Document Query Agent</h2>
          <p style={styles.headerSubtitle}>
            Ask questions about your documents in natural language
          </p>
        </div>
      </div>

      <div style={styles.messagesContainer}>
        {messages.length === 0 && (
          <div style={styles.emptyState}>
            <div style={styles.emptyIcon}>💬</div>
            <p style={styles.emptyTitle}>Ask anything about your documents</p>
            <div style={styles.suggestions}>
              {SUGGESTED_QUERIES.map((q, i) => (
                <button
                  key={i}
                  style={styles.suggestionBtn}
                  onClick={() => handleSubmit(q)}
                  onMouseEnter={(e) => {
                    (e.target as HTMLElement).style.borderColor = '#6366f1';
                    (e.target as HTMLElement).style.background = 'rgba(99, 102, 241, 0.05)';
                  }}
                  onMouseLeave={(e) => {
                    (e.target as HTMLElement).style.borderColor = '#e2e8f0';
                    (e.target as HTMLElement).style.background = 'transparent';
                  }}
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <div
            key={msg.id}
            style={{
              ...styles.message,
              ...(msg.role === 'user' ? styles.userMessage : styles.assistantMessage),
            }}
          >
            <div style={styles.messageAvatar}>
              {msg.role === 'user' ? '👤' : '🤖'}
            </div>
            <div style={styles.messageContent}>
              {msg.loading ? (
                <div style={styles.loadingDots}>
                  <span style={styles.dot}>●</span>
                  <span style={{ ...styles.dot, animationDelay: '0.2s' }}>●</span>
                  <span style={{ ...styles.dot, animationDelay: '0.4s' }}>●</span>
                </div>
              ) : (
                <>
                  <p style={styles.messageText}>{msg.content}</p>

                  {/* Sources */}
                  {msg.sources && msg.sources.length > 0 && (
                    <div style={styles.sourcesSection}>
                      <p style={styles.sourcesLabel}>📄 Sources</p>
                      {msg.sources.map((src, i) => (
                        <div key={i} style={styles.sourceItem}>
                          <span style={styles.sourceFilename}>
                            {src.filename || src.document_id}
                          </span>
                          {src.relevance && (
                            <span style={styles.sourceRelevance}>
                              {(src.relevance * 100).toFixed(0)}% relevant
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Agent trace toggle */}
                  {msg.steps && msg.steps.length > 0 && (
                    <div style={styles.traceSection}>
                      <button
                        style={styles.traceToggle}
                        onClick={() => toggleSteps(msg.id)}
                      >
                        {expandedSteps.has(msg.id) ? '▼' : '▶'} Agent Trace ({msg.steps.length} steps)
                      </button>

                      {expandedSteps.has(msg.id) && (
                        <div style={styles.traceContent}>
                          {msg.reasoning && (
                            <p style={styles.reasoning}>
                              <strong>Reasoning:</strong> {msg.reasoning}
                            </p>
                          )}
                          {msg.steps.map((step, i) => (
                            <div key={i} style={styles.stepItem}>
                              <div style={styles.stepHeader}>
                                <span style={styles.stepRound}>Round {step.round}</span>
                                <span style={styles.stepTool}>{step.tool}</span>
                              </div>
                              <pre style={styles.stepCode}>
                                {JSON.stringify(step.input, null, 2)}
                              </pre>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      <form
        style={styles.inputForm}
        onSubmit={(e) => {
          e.preventDefault();
          handleSubmit(input);
        }}
      >
        <input
          style={styles.input}
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about your documents..."
          disabled={loading}
        />
        <button
          style={{
            ...styles.sendBtn,
            opacity: loading || !input.trim() ? 0.5 : 1,
          }}
          type="submit"
          disabled={loading || !input.trim()}
        >
          ↑
        </button>
      </form>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    maxHeight: 'calc(100vh - 120px)',
    background: '#ffffff',
    borderRadius: '16px',
    border: '1px solid #e2e8f0',
    overflow: 'hidden',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
    padding: '16px 20px',
    borderBottom: '1px solid #e2e8f0',
    background: 'linear-gradient(135deg, #f8fafc, #f1f5f9)',
  },
  headerIcon: { fontSize: '24px' },
  headerTitle: { margin: 0, fontSize: '16px', fontWeight: 600, color: '#1e293b' },
  headerSubtitle: { margin: 0, fontSize: '12px', color: '#64748b' },
  messagesContainer: {
    flex: 1,
    overflowY: 'auto' as const,
    padding: '20px',
    display: 'flex',
    flexDirection: 'column',
    gap: '16px',
  },
  emptyState: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '12px',
  },
  emptyIcon: { fontSize: '48px', opacity: 0.5 },
  emptyTitle: { color: '#94a3b8', fontSize: '16px', fontWeight: 500 },
  suggestions: {
    display: 'flex',
    flexWrap: 'wrap' as const,
    gap: '8px',
    justifyContent: 'center',
    maxWidth: '600px',
  },
  suggestionBtn: {
    padding: '8px 16px',
    borderRadius: '20px',
    border: '1px solid #e2e8f0',
    background: 'transparent',
    color: '#475569',
    fontSize: '13px',
    cursor: 'pointer',
    transition: 'all 0.2s',
  },
  message: {
    display: 'flex',
    gap: '12px',
    maxWidth: '85%',
  },
  userMessage: {
    alignSelf: 'flex-end',
    flexDirection: 'row-reverse' as const,
  },
  assistantMessage: {
    alignSelf: 'flex-start',
  },
  messageAvatar: {
    width: '32px',
    height: '32px',
    borderRadius: '50%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: '16px',
    flexShrink: 0,
  },
  messageContent: {
    padding: '12px 16px',
    borderRadius: '16px',
    background: '#f8fafc',
    border: '1px solid #e2e8f0',
    minWidth: '60px',
  },
  messageText: {
    margin: 0,
    fontSize: '14px',
    lineHeight: '1.6',
    color: '#1e293b',
    whiteSpace: 'pre-wrap' as const,
  },
  loadingDots: {
    display: 'flex',
    gap: '4px',
    padding: '4px 0',
  },
  dot: {
    fontSize: '14px',
    color: '#6366f1',
    animation: 'pulse 1.5s ease-in-out infinite',
  },
  sourcesSection: {
    marginTop: '12px',
    padding: '10px 12px',
    background: '#f0fdf4',
    borderRadius: '8px',
    border: '1px solid #bbf7d0',
  },
  sourcesLabel: {
    margin: '0 0 6px 0',
    fontSize: '12px',
    fontWeight: 600,
    color: '#166534',
  },
  sourceItem: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '4px 0',
    fontSize: '12px',
  },
  sourceFilename: { color: '#15803d', fontWeight: 500 },
  sourceRelevance: { color: '#86efac', fontSize: '11px' },
  traceSection: { marginTop: '10px' },
  traceToggle: {
    background: 'none',
    border: 'none',
    color: '#6366f1',
    cursor: 'pointer',
    fontSize: '12px',
    fontWeight: 500,
    padding: '4px 0',
  },
  traceContent: {
    marginTop: '8px',
    padding: '10px',
    background: '#f8fafc',
    borderRadius: '8px',
    border: '1px solid #e2e8f0',
  },
  reasoning: {
    margin: '0 0 8px 0',
    fontSize: '12px',
    color: '#475569',
    lineHeight: '1.5',
  },
  stepItem: {
    padding: '8px',
    marginBottom: '6px',
    background: '#fff',
    borderRadius: '6px',
    border: '1px solid #e2e8f0',
  },
  stepHeader: {
    display: 'flex',
    gap: '8px',
    alignItems: 'center',
    marginBottom: '4px',
  },
  stepRound: {
    fontSize: '10px',
    fontWeight: 600,
    color: '#6366f1',
    background: '#eef2ff',
    padding: '2px 6px',
    borderRadius: '4px',
  },
  stepTool: {
    fontSize: '12px',
    fontWeight: 500,
    color: '#1e293b',
    fontFamily: 'monospace',
  },
  stepCode: {
    margin: 0,
    fontSize: '11px',
    color: '#64748b',
    fontFamily: 'monospace',
    overflow: 'auto' as const,
    maxHeight: '100px',
  },
  inputForm: {
    display: 'flex',
    gap: '8px',
    padding: '16px 20px',
    borderTop: '1px solid #e2e8f0',
    background: '#fafafa',
  },
  input: {
    flex: 1,
    padding: '10px 16px',
    borderRadius: '12px',
    border: '1px solid #e2e8f0',
    fontSize: '14px',
    outline: 'none',
    background: '#fff',
    color: '#1e293b',
  },
  sendBtn: {
    width: '40px',
    height: '40px',
    borderRadius: '12px',
    border: 'none',
    background: '#6366f1',
    color: '#fff',
    fontSize: '18px',
    fontWeight: 700,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    transition: 'opacity 0.2s',
  },
};
