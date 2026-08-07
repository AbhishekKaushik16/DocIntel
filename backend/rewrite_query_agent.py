import re

with open("app/pipeline/query_agent.py", "r") as f:
    content = f.read()

new_run_query_agent = """
async def run_query_agent(question: str) -> QueryResult:
    \"\"\"
    Run the query agent to answer a natural language question.
    Uses LangChain to iteratively query the database and synthesize an answer.
    \"\"\"
    if not settings.llm_available:
        return QueryResult(
            answer="LLM is not available. Please configure an API key.",
            sources=[],
            agent_reasoning="No LLM configured.",
        )

    from app.utils.llm import get_llm
    from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
    
    llm = get_llm(model_type="fast", temperature=0.0)
    llm_with_tools = llm.bind_tools(QUERY_TOOL_SPECS)
    
    messages = [
        SystemMessage(content=QUERY_SYSTEM_PROMPT),
        HumanMessage(content=f"User question: {question}")
    ]
    agent_steps: list[dict] = []
    
    for round_num in range(MAX_QUERY_ROUNDS):
        if settings.llm_provider.lower() == "gemini":
            from app.utils.rate_limit import throttle_gemini_request
            await throttle_gemini_request()
            
        resp = await llm_with_tools.ainvoke(messages)
        messages.append(resp)
        
        if not resp.tool_calls:
            # Done
            try:
                raw_content = resp.content
                if isinstance(raw_content, list):
                    raw_content = raw_content[0].get("text", "") if raw_content else ""
                raw = str(raw_content).strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                data = json.loads(raw.strip())
                return QueryResult(
                    answer=data.get("answer", str(raw_content)),
                    sources=data.get("sources", []),
                    agent_reasoning=data.get("reasoning", ""),
                    query_steps=agent_steps,
                )
            except (json.JSONDecodeError, KeyError):
                return QueryResult(
                    answer=str(resp.content),
                    sources=[],
                    agent_reasoning="Agent returned free-text answer.",
                    query_steps=agent_steps,
                )
                
        # Execute tools
        for tc in resp.tool_calls:
            result = await _dispatch_query_tool(tc["name"], tc.get("args", {}))
            agent_steps.append({
                "round": round_num + 1,
                "tool": tc["name"],
                "input": tc.get("args", {}),
                "output": result,
            })
            messages.append(ToolMessage(
                tool_call_id=tc["id"],
                content=json.dumps(result)
            ))
            
    return QueryResult(
        answer="I couldn't find a definitive answer. Please try rephrasing your question.",
        sources=[],
        agent_reasoning=f"Agent did not converge after {MAX_QUERY_ROUNDS} rounds.",
        query_steps=agent_steps,
    )
"""

content = re.sub(
    r'async def run_query_agent\(question: str\) -> QueryResult:.*',
    new_run_query_agent.strip() + "\n",
    content,
    flags=re.DOTALL
)

with open("app/pipeline/query_agent.py", "w") as f:
    f.write(content)
