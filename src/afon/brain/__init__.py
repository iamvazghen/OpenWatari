"""Brain: the 24/7 orchestrator (VPS).

Routes each utterance to a direct answer (freellmapi) or delegates to the OpenClaw
fleet (Gateway agent RPC + agent.wait). Holds markdown memory/personality/skills,
the proactive scheduler, and the Telegram / MCP channels.
"""
