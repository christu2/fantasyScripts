#!/usr/bin/env python3
"""
BFL League History & Drama Intelligence Engine
=============================================
Stores, calculates, and exposes deep historical storylines for every owner:
- Ring Count & Championship Years (2008-2025)
- Title Droughts (Years since last Super Bowl)
- Playoff Heartbreaks (Super Bowl Runner-Up Finishes)
- All-Time Playoff Records (Winners Bracket Only)
- In-Season Rematches & Head-to-Head Win Streaks
"""

import os
import sys
from datetime import datetime
from pathlib import Path

CURRENT_YEAR = 2026

# Complete Official BFL Super Bowl Honor Roll (2008-2025)
BFL_CHAMPIONSHIPS = [
    {'year': 2008, 'champ': 'Saagar Gupta', 'runner_up': 'Alex Shimomura', 'score': '98.00 - 71.00', 'margin': 27.00},
    {'year': 2009, 'champ': 'Shawn Lukose', 'runner_up': 'Maria Christus', 'score': '97.00 - 94.00', 'margin': 3.00},
    {'year': 2010, 'champ': 'Daniel Kruszewski', 'runner_up': 'Shawn Lukose', 'score': '90.00 - 79.00', 'margin': 11.00},
    {'year': 2011, 'champ': 'Abe Thomas', 'runner_up': 'Blake Whitehouse', 'score': '108.00 - 52.00', 'margin': 56.00},
    {'year': 2012, 'champ': 'Nick Christus', 'runner_up': 'Shawn Lukose', 'score': '86.00 - 82.00', 'margin': 4.00},
    {'year': 2013, 'champ': 'Shawn Ullenbrauck', 'runner_up': 'Tommy Ehrlich', 'score': '81.00 - 77.00', 'margin': 4.00},
    {'year': 2014, 'champ': 'Dino Davros', 'runner_up': 'Abe Thomas', 'score': '141.00 - 86.00', 'margin': 55.00},
    {'year': 2015, 'champ': 'Nick Christus', 'runner_up': 'Tommy Ehrlich', 'score': '66.00 - 48.00', 'margin': 18.00},
    {'year': 2016, 'champ': 'Nick Christus', 'runner_up': 'Blake Whitehouse', 'score': '99.00 - 95.00', 'margin': 4.00},
    {'year': 2017, 'champ': 'Adam Olen', 'runner_up': 'Shawn Lukose', 'score': '77.00 - 76.00', 'margin': 1.00},
    {'year': 2018, 'champ': 'Shawn Lukose', 'runner_up': 'Samran Mirza', 'score': '114.00 - 88.00', 'margin': 26.00},
    {'year': 2019, 'champ': 'Mattheos Houlis', 'runner_up': 'Ryan Olen', 'score': '96.16 - 87.24', 'margin': 8.92},
    {'year': 2020, 'champ': 'Dino Davros', 'runner_up': 'rej hoxha', 'score': '92.28 - 92.20', 'margin': 0.08},
    {'year': 2021, 'champ': 'Shawn Lukose', 'runner_up': 'rej hoxha', 'score': '133.38 - 80.60', 'margin': 52.78},
    {'year': 2022, 'champ': 'Abe Thomas', 'runner_up': 'Daniel Kruszewski', 'score': '108.66 - 52.58', 'margin': 56.08},
    {'year': 2023, 'champ': 'Shawn Lukose', 'runner_up': 'Samran Mirza', 'score': '102.56 - 82.72', 'margin': 19.84},
    {'year': 2024, 'champ': 'Sydney Miller', 'runner_up': 'Ryan Olen', 'score': '119.88 - 100.88', 'margin': 19.00},
    {'year': 2025, 'champ': 'Abe Thomas', 'runner_up': 'Daniel Kruszewski', 'score': '107.08 - 101.16', 'margin': 5.92},
]

# Owner Trophy Profiles
OWNER_PROFILES = {
    'Shawn Lukose': {
        'rings': 4,
        'champ_years': [2009, 2018, 2021, 2023],
        'runner_up_years': [2010, 2012, 2017],
        'tagline': "The 4-Time GOAT",
        'summary': "All-time BFL championship leader (4 Rings, 7 Super Bowl appearances)."
    },
    'Nick Christus': {
        'rings': 3,
        'champ_years': [2012, 2015, 2016],
        'runner_up_years': [],
        'tagline': "3-Time Champion (10-Year Drought)",
        'summary': "Dominant back-to-back champ (2015-2016), currently fighting a 10-year title drought."
    },
    'Abe Thomas': {
        'rings': 3,
        'champ_years': [2011, 2022, 2025],
        'runner_up_years': [2014],
        'tagline': "Defending BFL Champion",
        'summary': "Reigning 2025 champion & 3-time titleholder with championships across three different decades."
    },
    'Dino Davros': {
        'rings': 2,
        'champ_years': [2014, 2020],
        'runner_up_years': [],
        'tagline': "2-Time Champion",
        'summary': "Won the closest Super Bowl in league history in 2020 (0.08 pt win over Rej)."
    },
    'Daniel Kruszewski': {
        'rings': 1,
        'champ_years': [2010],
        'runner_up_years': [2022, 2025],
        'tagline': "2010 Champion & 2x Recent Finalist",
        'summary': "16-year title drought despite reaching the Super Bowl in both 2022 and 2025."
    },
    'Shawn Ullenbrauck': {
        'rings': 1,
        'champ_years': [2013],
        'runner_up_years': [],
        'tagline': "2013 Champion (13-Year Jabroni Drought)",
        'summary': "Won the 2013 Jabroni Trophy over Tommy; seeking his 2nd Jabroni."
    },
    'Adam Olen': {
        'rings': 1,
        'champ_years': [2017],
        'runner_up_years': [],
        'tagline': "2017 Jabroni Champion",
        'summary': "Won the legendary 1-point Super Bowl thriller over Lukose to hoist The Jabroni in 2017."
    },
    'Saagar Gupta': {
        'rings': 1,
        'champ_years': [2008],
        'runner_up_years': [],
        'tagline': "Inaugural Champion (18-Year Jabroni Drought)",
        'summary': "Won the first-ever Jabroni Trophy in 2008; currently enduring the league's longest active title drought (18 years)."
    },
    'Sydney Miller': {
        'rings': 1,
        'champ_years': [2024],
        'runner_up_years': [],
        'tagline': "2024 Jabroni Champion",
        'summary': "Hoisted the 2024 Jabroni Trophy; now competing as an independent franchise."
    },
    'Tommy Ehrlich': {
        'rings': 0,
        'champ_years': [],
        'runner_up_years': [2013, 2015],
        'tagline': "2x Finalist Chasing Jabroni #1",
        'summary': "Two Super Bowl runner-up finishes (2013, 2015); perpetually hunting for his first Jabroni Trophy."
    },
    'Blake Whitehouse': {
        'rings': 0,
        'champ_years': [],
        'runner_up_years': [2011, 2016],
        'tagline': "2x Finalist Chasing Jabroni #1",
        'summary': "Two Super Bowl appearances (2011, 2016); searching for that elusive first Jabroni Trophy."
    },
    'Samran Mirza': {
        'rings': 0,
        'champ_years': [],
        'runner_up_years': [2018, 2023],
        'tagline': "2x Finalist Chasing Jabroni #1",
        'summary': "Twice reached the mountaintop only to fall to Lukose; chasing his first career Jabroni."
    },
    'rej hoxha': {
        'rings': 0,
        'champ_years': [],
        'runner_up_years': [2020, 2021],
        'tagline': "2x Finalist Chasing Jabroni #1",
        'summary': "Back-to-back finalist (2020, 2021) including the heartbreaking 0.08-pt Super Bowl loss to Dino in the 2020 Jabroni clash."
    },
    'Nael Ahmed': {
        'rings': 0,
        'champ_years': [],
        'runner_up_years': [],
        'tagline': "Franchise Playoff Contender",
        'summary': "Consistently competitive franchise searching for its first Jabroni Trophy appearance."
    },
    'Alex Kite': {
        'rings': 0,
        'champ_years': [],
        'runner_up_years': [],
        'tagline': "West Division Contender",
        'summary': "High-variance powerhouse seeking its first franchise Super Bowl appearance."
    },
    'Nitesh Patel': {
        'rings': 0,
        'champ_years': [],
        'runner_up_years': [],
        'tagline': "Inaugural Solo Franchise",
        'summary': "Co-owned NMAfia in 2025; launching his first solo franchise season with Big Nasties."
    }
}

def get_owner_storyline_context(owner: str) -> dict:
    """Returns narrative context, title drought, and historical bio for an owner."""
    prof = OWNER_PROFILES.get(owner, {
        'rings': 0, 'champ_years': [], 'runner_up_years': [],
        'tagline': "Franchise Owner", 'summary': "BFL Franchise Manager."
    })
    
    rings = prof['rings']
    if rings > 0:
        last_champ = prof['champ_years'][-1]
        drought = CURRENT_YEAR - last_champ
        drought_str = "Defending Champion (2025)" if last_champ == 2025 else f"{drought}-year title drought (last won in {last_champ})"
    else:
        last_champ = None
        drought_str = "Chasing first career championship"
        
    return {
        'owner': owner,
        'rings': rings,
        'champ_years': prof['champ_years'],
        'runner_up_years': prof['runner_up_years'],
        'drought_str': drought_str,
        'tagline': prof['tagline'],
        'summary': prof['summary']
    }

if __name__ == "__main__":
    print("="*75)
    print("🏆 BFL ALL-TIME CHAMPIONSHIP ROLL & OWNER CONTEXT ENGINE")
    print("="*75)
    for owner in OWNER_PROFILES:
        ctx = get_owner_storyline_context(owner)
        print(f"{owner:<20} | Rings: {ctx['rings']:<2} | {ctx['drought_str']:<35} | {ctx['tagline']}")
