import React from 'react';
import { Brain, Wrench, ChevronDown, ChevronRight, CheckCircle2, AlertCircle } from 'lucide-react';

interface AgentStep {
  role: 'thought' | 'tool_call' | 'tool_result' | string;
  thought?: string;
  tool_name?: string;
  tool_args?: any;
  result?: string;
  error?: string;
}

interface AgentTraceViewerProps {
  reasoning: string | null;
  agentSteps: AgentStep[] | null;
}

export default function AgentTraceViewer({ reasoning, agentSteps }: AgentTraceViewerProps) {
  const [expandedIndices, setExpandedIndices] = React.useState<Set<number>>(new Set());

  const toggleExpand = (index: number) => {
    const newSet = new Set(expandedIndices);
    if (newSet.has(index)) {
      newSet.delete(index);
    } else {
      newSet.add(index);
    }
    setExpandedIndices(newSet);
  };

  if (!reasoning && (!agentSteps || agentSteps.length === 0)) {
    return null;
  }

  // Group tool_call and tool_result together for better UI if they are sequential
  const groupedSteps = [];
  if (agentSteps) {
    let i = 0;
    while (i < agentSteps.length) {
      const step = agentSteps[i];
      if (step.role === 'tool_call' && i + 1 < agentSteps.length && agentSteps[i+1].role === 'tool_result') {
        groupedSteps.push({
          type: 'tool_interaction',
          call: step,
          result: agentSteps[i+1],
          originalIndex: i
        });
        i += 2;
      } else {
        groupedSteps.push({
          type: 'single_step',
          step: step,
          originalIndex: i
        });
        i += 1;
      }
    }
  }

  return (
    <div className="mt-4 border border-indigo-500/30 bg-[#0f1524]/80 backdrop-blur rounded-lg overflow-hidden font-mono text-sm shadow-[0_0_15px_rgba(79,70,229,0.1)]">
      <div className="bg-indigo-900/40 px-4 py-2 border-b border-indigo-500/30 flex items-center gap-2">
        <Brain className="w-4 h-4 text-indigo-400" />
        <span className="text-indigo-300 font-semibold uppercase tracking-wider text-xs">Agentic Trace</span>
      </div>
      
      <div className="p-4 space-y-4">
        {/* Final Reasoning / Conclusion */}
        {reasoning && (
          <div className="flex gap-3 items-start">
            <div className="mt-1 bg-indigo-500/20 p-1.5 rounded-full ring-1 ring-indigo-500/40 shrink-0">
              <Brain className="w-3.5 h-3.5 text-indigo-300" />
            </div>
            <div className="flex-1 bg-indigo-950/40 border border-indigo-800/50 rounded-md p-3 text-indigo-100 shadow-inner">
              <span className="text-xs font-bold text-indigo-400 mb-1 block uppercase tracking-wide">Final Reasoning</span>
              <p className="leading-relaxed">{reasoning}</p>
            </div>
          </div>
        )}

        {/* Timeline of Steps */}
        {groupedSteps.length > 0 && (
          <div className="relative pl-6 space-y-4 before:absolute before:inset-0 before:ml-[1.4rem] before:-translate-x-px md:before:mx-auto md:before:translate-x-0 before:h-full before:w-0.5 before:bg-gradient-to-b before:from-indigo-500/20 before:via-indigo-500/10 before:to-transparent">
            {groupedSteps.map((group, idx) => {
              
              if (group.type === 'single_step' && group.step.role === 'thought') {
                return (
                  <div key={idx} className="relative flex items-start gap-4">
                    <div className="absolute left-[-24px] top-1.5 w-2 h-2 rounded-full bg-indigo-400 ring-4 ring-[#0f1524]"></div>
                    <div className="flex-1 text-gray-300 leading-relaxed bg-white/5 border border-white/10 rounded-md p-3 shadow-sm">
                      <span className="text-xs font-bold text-gray-400 mb-1 block uppercase tracking-wide">Agent Thought</span>
                      {group.step.thought}
                    </div>
                  </div>
                );
              }

              if (group.type === 'tool_interaction' || (group.type === 'single_step' && group.step.role === 'tool_call')) {
                const call = group.type === 'tool_interaction' ? group.call : group.step;
                const result = group.type === 'tool_interaction' ? group.result : null;
                const isExpanded = expandedIndices.has(group.originalIndex);
                
                return (
                  <div key={idx} className="relative flex items-start gap-4">
                    <div className="absolute left-[-26px] top-1 bg-[#0f1524] p-0.5 rounded-full ring-2 ring-emerald-500/50">
                      <Wrench className="w-3 h-3 text-emerald-400" />
                    </div>
                    
                    <div className="flex-1 border border-emerald-900/50 bg-emerald-950/20 rounded-md overflow-hidden shadow-sm transition-all hover:border-emerald-800/80">
                      <button 
                        onClick={() => toggleExpand(group.originalIndex)}
                        className="w-full text-left px-3 py-2 flex items-center justify-between bg-emerald-900/10 hover:bg-emerald-900/20 transition-colors"
                      >
                        <div className="flex items-center gap-2">
                          <span className="text-emerald-400 font-bold text-xs uppercase tracking-wide">Tool Call</span>
                          <span className="text-emerald-200 bg-emerald-900/40 px-2 py-0.5 rounded text-xs">{call.tool_name}</span>
                        </div>
                        {isExpanded ? <ChevronDown className="w-4 h-4 text-emerald-500" /> : <ChevronRight className="w-4 h-4 text-emerald-500" />}
                      </button>
                      
                      {isExpanded && (
                        <div className="p-3 border-t border-emerald-900/50 space-y-3">
                          <div>
                            <span className="text-[10px] text-emerald-500 uppercase font-bold tracking-wider mb-1 block">Arguments</span>
                            <pre className="text-xs text-gray-300 bg-black/40 p-2 rounded overflow-x-auto">
                              {JSON.stringify(call.tool_args, null, 2)}
                            </pre>
                          </div>
                          
                          {result && (
                            <div>
                              <div className="flex items-center gap-1.5 mb-1">
                                {result.error ? (
                                  <AlertCircle className="w-3.5 h-3.5 text-rose-400" />
                                ) : (
                                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                                )}
                                <span className={`text-[10px] uppercase font-bold tracking-wider ${result.error ? 'text-rose-400' : 'text-emerald-500'}`}>
                                  {result.error ? 'Error' : 'Result'}
                                </span>
                              </div>
                              <pre className={`text-xs p-2 rounded overflow-x-auto max-h-40 overflow-y-auto ${result.error ? 'text-rose-300 bg-rose-950/30' : 'text-gray-300 bg-black/40'}`}>
                                {result.error || result.result || 'Success'}
                              </pre>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                );
              }

              return null;
            })}
          </div>
        )}
      </div>
    </div>
  );
}
