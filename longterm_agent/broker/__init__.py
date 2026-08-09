"""Broker layer: a parallel record of EXECUTIONS, never a source of truth for decisions.

`longterm_agent_results` stays the decision ledger. Nothing in this package writes to it,
and nothing here feeds grading — a broker outage must not be able to rewrite research
history. The two are joined for display only (stats.open_positions).
"""
