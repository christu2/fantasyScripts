#!/bin/bash
# ==============================================================================
# BFL Live Game Desk Daemon Runner
# Runs during Thursday Night Football, Sunday slates (12:00 PM - 11:30 PM CT),
# and Monday Night Football to monitor win probabilities and post live alerts.
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT" || exit 1

SEASON=${1:-2025}
WEEK=${2:-1}
INTERVAL=${3:-60}

echo "⚡ Launching BFL Live Game Desk Daemon (Season $SEASON, Week $WEEK, Polling every ${INTERVAL}s)..."
python3 FantasyRecap/live_game_desk_daemon.py --season "$SEASON" --week "$WEEK" --interval "$INTERVAL"

