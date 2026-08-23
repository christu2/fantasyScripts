#!/bin/bash
# ==============================================================================
# BFL Automated Tuesday Morning Hangover Producer
# Runs every Tuesday Morning at 8:00 AM CT to fetch the newest ESPN boxscores,
# generate the animated TV studio show, and upload the combined post to Discord.
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT" || exit 1

# Auto-detect current season and current NFL week if not passed as arguments
SEASON=${1:-2025}
WEEK=${2:-1}

echo "🏈 Executing BFL Tuesday Morning Hangover for Season $SEASON, Week $WEEK..."
python3 FantasyRecap/tuesday_morning_hangover.py --season "$SEASON" --week "$WEEK"

