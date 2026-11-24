"""
Shared core infrastructure for the FantasyRecap suite:
- manifest: Canonical league configuration, owner identity, divisions, Discord maps, and history roll.
- espn_client: Resilient HTTP client for ESPN Fantasy & NFL Scoreboard APIs with retry/backoff.
- ops: Atomic file locking (fcntl.flock), structured logging, and #bot-ops failure alerts.
"""
