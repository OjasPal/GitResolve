"""Final LangGraph stage, invoked only by the backend's approved PR dispatch.

The injected publisher independently checks exact-diff approval and actual
sandbox evidence. Planning never invokes this graph. There is no merge node.
"""
from langgraph.graph import StateGraph, START, END
from agent.state import AgentState


def build_publication_graph(publish):
    def create_pull_request(state):
        pr = publish()
        return {'pr_number': pr.number, 'pr_url': pr.url, 'pr_branch': pr.branch,
                'stage': 'completed', 'status': 'completed'}
    builder = StateGraph(AgentState)
    builder.add_node('create_pull_request', create_pull_request)
    builder.add_edge(START, 'create_pull_request')
    builder.add_edge('create_pull_request', END)
    return builder.compile()
