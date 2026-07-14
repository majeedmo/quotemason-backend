"""Local chat REPL for the agent (pre-FastAPI smoke tool).

Usage (from backend/):
    uv run python -m app.agent.cli [--thread t1]

Needs OPENROUTER_API_KEY (+ OPENAI_API_KEY for retrieval embeddings;
TAVILY_API_KEY optional) in backend/.env.
"""

from __future__ import annotations

import argparse
import json

from langchain_core.messages import HumanMessage

from app.agent.graph import build_graph


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--thread", default="local-dev")
    args = ap.parse_args()
    graph = build_graph()
    cfg = {"configurable": {"thread_id": args.thread}}
    print("QuoteMason intake — describe your project (Ctrl-D to exit)")
    while True:
        try:
            user = input("\nyou> ").strip()
        except EOFError:
            break
        if not user:
            continue
        state = graph.invoke({"messages": [HumanMessage(user)]}, cfg)
        print("\nagent>", state["messages"][-1].content)
        if state.get("routing_packet"):
            print("\n[routing packet -> estimator]")
            print(json.dumps(state["routing_packet"], indent=2)[:800])


if __name__ == "__main__":
    main()
