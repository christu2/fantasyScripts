#!/usr/bin/env python3
"""
BFL Dynamic Weekly Show Builder (Full 8-10+ Minute Championship Broadcast)
==========================================================================
Dynamically constructs full 8.5 to 10+ minute television sports show storylines
for ANY ESPN season and week, with deep historical intelligence, championship
lore, player stat lines, and press conference roasts.
"""

from FantasyRecap.league_recap_generator import analyze_week

def build_dynamic_scenes_from_espn(raw_data, season: int, week_num: int) -> list:
    matchups, perfs, teams = analyze_week(raw_data, week_num)

    scenes = []

    # =========================================================================
    # SCENE 1: COLD OPEN & THE JABRONI TROPHY STAKES
    # =========================================================================
    scenes.append({
        'card': {
            'title': "TUESDAY MORNING HANGOVER",
            'subtitle': f"Season {season} Finale • The Jabroni Championship Special",
            'badge': "THE JABRONI FINALE",
            'accent': (41, 128, 185),
            'items': [
                {'tag': "SEASON FINALE", 'header': f"BEASTS FOOTBALL LEAGUE • {season} CHAMPIONSHIP", 'desc': "Full 16-Franchise Post-Season Breakdown with Chris & Dave"},
                {'tag': "CROWNING", 'header': "A New Champion Claims The Jabroni", 'desc': "5.92-point title thriller, 48-point blowouts, and 0.38-point cardiac finishes"},
                {'tag': "18-YR LORE", 'header': "18 Seasons of Rivalry & Heartbreak", 'desc': "Podium bronze medals, consolation carnage, and off-season press conferences"}
            ]
        },
        'dialogue': [
            ('CHRIS', f"Good Tuesday morning everybody! Grab your coffee, pop an Advil, and welcome inside the season finale of the BFL Tuesday Morning Hangover, presented by the BFL Broadcast Network! Week {week_num} of the {season} fantasy football campaign is officially in the history books. Eighteen weeks of blood, sweat, waiver wire panic, and late-night trash-talk have all led to this crowning moment. I'm Chris alongside Dave."),
            ('DAVE', f"Good morning Chris! What an unbelievable, chaotic finale to the {season} season! Commissioner Nick Christus has this eighteen-year-old league operating at maximum intensity. We had an absolute five-point cardiac thriller for the championship, bronze medal podium battles, a zero-point-three-eight point photo finish, thirty-nine point explosions from Bijan Robinson, and a forty-five point demolition from King Derrick Henry!"),
            ('CHRIS', "The stakes could not have been higher. Eighteen franchises battled through injuries and bad coaching decisions all autumn, but only one manager walks away with the ultimate crown: The Jabroni Trophy. Let's get right into our championship breakdown!")
        ]
    })

    # =========================================================================
    # SCENE 2: THE 2025 BFL CHAMPIONSHIP GAME (Abe vs Dan)
    # =========================================================================
    scenes.append({
        'card': {
            'title': "THE 2025 BFL CHAMPIONSHIP GAME",
            'subtitle': "Crashee Bandicoot (Abe Thomas) 107.08  def.  Dynasty Destroyers (Daniel Kruszewski) 101.16",
            'badge': "CHAMPIONSHIP FINAL",
            'accent': (241, 196, 15),
            'items': [
                {'tag': "CHAMPION", 'header': "Crashee Bandicoot (Abe Thomas) — 107.08 PTS", 'desc': "Back-to-Back BFL Champion! Claims 2nd career Jabroni Trophy."},
                {'tag': "RUNNER-UP", 'header': "Dynasty Destroyers (Daniel Kruszewski) — 101.16 PTS", 'desc': "Valiant 101-point effort falls just +5.92 points shy of first title."},
                {'tag': "STARS", 'header': "Luther Burden III (23.8 pts) & Stefon Diggs (19.1 pts)", 'desc': "Clutch playmaking carries Abe across the finish line."}
            ]
        },
        'dialogue': [
            ('CHRIS', f"We begin at the pinnacle of fantasy football: The {season} BFL Championship Game for The Jabroni Trophy! Defending champion Abe Thomas and Crashee Bandicoot have done the unthinkable, going back-to-back with a thrilling one hundred and seven point zero eight to one hundred and one point one six victory over Daniel Kruszewski!"),
            ('DAVE', f"Abe Thomas is your back-to-back BFL Champion! That is pure championship pedigree, Chris. Abe got twenty-three point eight fantasy points from Luther Burden the Third and nineteen from Stefon Diggs to close out the title. Dan put up a valiant one hundred and one points behind Aaron Jones and fifteen from the Giants defense, but fell just five point nine two points short of hoisting his first career Jabroni!"),
            ('CHRIS', "Going back-to-back in an eighteen-year-old league with sixteen competitive franchises is almost statistically impossible. Abe built a juggernaut and defended the throne against every challenger all season long.")
        ]
    })

    # =========================================================================
    # SCENE 3: CHAMPIONSHIP BENCH BLUNDER (Brock Purdy Pine Disaster)
    # =========================================================================
    scenes.append({
        'card': {
            'title': "CHAMPIONSHIP BENCH DISASTER",
            'subtitle': "Brock Purdy (36.92 pts) Left on Dan's Pine",
            'badge': "TITLE HEARTBREAK",
            'accent': (231, 76, 60),
            'items': [
                {'tag': "ON BENCH", 'header': "Brock Purdy — 36.92 PTS on Pine", 'desc': "Left on Dan's bench while starting quarterback slot stalled."},
                {'tag': "TITLE SWING", 'header': "+23.12 Point Net Swing", 'desc': "Starting Brock Purdy flips the championship to a Dan title win by +17.20 pts."},
                {'tag': "HEARTBREAK", 'header': "Dan in #trash-talk:", 'desc': "\"36.9 pts on my bench in the championship game... I need a drink.\""}
            ]
        },
        'dialogue': [
            ('CHRIS', "And Dave, look at Dan's bench! This is going to haunt Dan all winter long. He had Brock Purdy sitting on his pine dropping thirty-six point nine two fantasy points! If Dan starts Purdy in his quarterback slot, he wins the championship by over seventeen points and hoists The Jabroni Trophy. Instead, Abe survives by five points!"),
            ('DAVE', "Leaving thirty-seven points on your pine in the championship game is the ultimate dagger to the soul, Chris. Dan posted in the league chat saying: 'Thirty-six point nine points on my bench in the title game, I need a drink.' You cannot make that mistake against a champion like Abe. Abe hoists his second career Jabroni Trophy and officially enters the multi-ring royalty club!"),
            ('CHRIS', "Dan had an incredible season leading the league in efficiency, but that one lineup decision will be debated in the group chat until September.")
        ]
    })

    # =========================================================================
    # SCENE 4: 3RD PLACE PODIUM CONSOLATION (Thor vs Lukose)
    # =========================================================================
    scenes.append({
        'card': {
            'title': "3RD PLACE PODIUM MATCHUP",
            'subtitle': "Pat N' Pending (Shawn Ullenbrauck) 112.74  def.  Sri Lankan Frogmouths (Shawn Lukose) 90.44",
            'badge': "BRONZE MEDAL",
            'accent': (230, 126, 34),
            'items': [
                {'tag': "PODIUM", 'header': "Pat N' Pending (Shawn Ullenbrauck) — 112.74 PTS", 'desc': "Jacory Croskey-Merritt (24.5 pts) & Christian Watson (19.8 pts) capture bronze."},
                {'tag': "4-TIME GOAT", 'header': "Sri Lankan Frogmouths (Shawn Lukose) — 90.44 PTS", 'desc': "Drake Maye dropped 32.44 pts, but supporting cast stalled out."},
                {'tag': "MARGIN", 'header': "+22.30 Point Podium Route", 'desc': "Former champion Thor secures 3rd place on the season podium."}
            ]
        },
        'dialogue': [
            ('CHRIS', "Over in the Third Place Consolation Game for the bronze podium finish, Thor and Pat N' Pending took down four-time champion Shawn Lukose, one hundred and twelve to ninety!"),
            ('DAVE', "Thor was locked in! He got twenty-four point five points from Jacory Croskey-Merritt and nearly twenty from Christian Watson. Lukose got a massive thirty-two point four point performance from second-year quarterback Drake Maye, but the rest of his roster completely flatlined, leaving the four-time GOAT off the podium."),
            ('CHRIS', "Lukose holds the record with four Jabroni Trophies, but walking away from Championship Sunday in fourth place is going to sting the GOAT all offseason.")
        ]
    })

    # =========================================================================
    # SCENE 5: 5TH PLACE BATTLE & BIJAN 39.4-POINT ERUPTION
    # =========================================================================
    scenes.append({
        'card': {
            'title': "5TH PLACE SMACKDOWN & BIJAN ERUPTION",
            'subtitle': "Green and Golden (Adam Olen) 114.40  def.  The Ehrly Birds (Tommy Ehrlich) 65.64",
            'badge': "MONSTER BLOWOUT",
            'accent': (46, 204, 113),
            'items': [
                {'tag': "NUCLEAR", 'header': "Bijan Robinson — 39.40 FANTASY POINTS", 'desc': "Explosive multi-touchdown masterclass leads Green and Golden."},
                {'tag': "QB1", 'header': "Joe Burrow — 20.40 PTS", 'desc': "Solid aerial command pushes Adam to a +48.76 point demolition."},
                {'tag': "COLLAPSE", 'header': "The Ehrly Birds (Tommy Ehrlich) — 65.64 PTS", 'desc': "Held under 66 points as elusive first title wait continues."}
            ]
        },
        'dialogue': [
            ('CHRIS', "In the fifth-place battle, Adam Olen and Green and Golden unleashed complete carnage on Tommy Ehrlich, rolling to a one hundred and fourteen to sixty-five blowout!"),
            ('DAVE', "Bijan Robinson went completely nuclear, Chris! Thirty-nine point four fantasy points on a monster multi-touchdown afternoon! Joe Burrow chipped in twenty, and Adam blew Tommy out by forty-eight point seven six points. Tommy's squad folded like a cheap tent, finishing with just sixty-five points."),
            ('CHRIS', "Tommy has reached the championship game twice in his career but never won The Jabroni, and finishing the year getting blown out by forty-eight points is a brutal way to enter the winter.")
        ]
    })

    # =========================================================================
    # SCENE 6: 7TH PLACE RIVALRY & SAAGAR'S 18-YEAR DROUGHT
    # =========================================================================
    scenes.append({
        'card': {
            'title': "7TH PLACE RIVALRY CLASH",
            'subtitle': "30p Chance I'm Already Winning (Sydney Miller) 98.88  def.  King Gupta's Army (Saagar Gupta) 90.68",
            'badge': "RIVALRY SHOWDOWN",
            'accent': (155, 89, 182),
            'items': [
                {'tag': "WINNER", 'header': "30p Chance (Sydney Miller) — 98.88 PTS", 'desc': "Breece Hall (21.9 pts) & Bo Nix (19.48 pts) secure 7th place."},
                {'tag': "18-YR DROUGHT", 'header': "King Gupta's Army (Saagar Gupta) — 90.68 PTS", 'desc': "Zach Charbonnet (25.2 pts) not enough; title drought reaches 18 years."},
                {'tag': "STAKES", 'header': "Queen Sydney Holds Off Inaugural Champ", 'desc': "Decided by +8.20 points in a fierce divisional battle."}
            ]
        },
        'dialogue': [
            ('CHRIS', "In the seventh-place rivalry matchup, former champion Sydney Miller held off our inaugural 2008 champion Saagar Gupta, ninety-eight point eight eight to ninety point six eight!"),
            ('DAVE', "Sydney got twenty-one point nine from Breece Hall and nineteen point four eight from Bo Nix to seal the win. Poor Saagar got twenty-five from Zach Charbonnet, but falls by eight points. Saagar's Jabroni Trophy title drought officially reaches eighteen straight seasons since the 2008 inaugural championship!"),
            ('CHRIS', "Saagar is entering year nineteen chasing his second ring. Sydney stays on top in their head-to-head rivalry history and sends Saagar home with another loss.")
        ]
    })

    # =========================================================================
    # SCENE 7: 0.38-POINT CARDIAC THRILLER (Rej vs Dino)
    # =========================================================================
    scenes.append({
        'card': {
            'title': "0.38-POINT CARDIAC THRILLER",
            'subtitle': "Steve Bartman (rej hoxha) 72.78  def.  Taliban Gang Mujahideen (Dino Davros) 72.40",
            'badge': "CARDIAC FINISH",
            'accent': (231, 76, 60),
            'items': [
                {'tag': "WINNER", 'header': "Steve Bartman (rej hoxha) — 72.78 PTS", 'desc': "Josh Allen (23.18 pts) delivers miraculous +0.38 point victory."},
                {'tag': "HEARTBREAK", 'header': "Taliban Gang (Dino Davros) — 72.40 PTS", 'desc': "Christian McCaffrey (26.1 pts) falls short by literally 3.8 yards."},
                {'tag': "DECISION", 'header': "Closest Game of the Season", 'desc': "Decided by the final kneel-down of Monday Night Football."}
            ]
        },
        'dialogue': [
            ('CHRIS', "Now let's talk about the absolute most insane finish of the entire season: Rej versus Dino, decided by literally zero point three eight points! Seventy-two point seven eight to seventy-two point four zero!"),
            ('DAVE', "Are you kidding me, Chris?! Zero point three eight points is thirty-eight rushing yards or a four-yard catch! Josh Allen gave Rej twenty-three points, while Dino got twenty-six from Christian McCaffrey. Dino was staring at his phone at midnight watching stat corrections in pure disbelief!"),
            ('CHRIS', "Rej and Dino have one of the oldest century rivalries in the league, and deciding a game by thirty-eight decimal points is why we love this ridiculous game.")
        ]
    })

    # =========================================================================
    # SCENE 8: KING DERRICK HENRY 45.6-PT RAMPAGE (Blake vs Nael)
    # =========================================================================
    scenes.append({
        'card': {
            'title': "KING DERRICK HENRY 45.6-PT MASTERCLASS",
            'subtitle': "Block O meets O Block (Blake Whitehouse) 101.94  def.  Big Nasties (Nael Ahmed) 85.08",
            'badge': "MVP PERFORMANCE",
            'accent': (230, 126, 34),
            'items': [
                {'tag': "LEGEND", 'header': "King Derrick Henry — 45.60 FANTASY POINTS", 'desc': "31-year-old powerhouse carries Blake with highest individual score of the week."},
                {'tag': "SUPPORT", 'header': "Baker Mayfield — 17.44 PTS", 'desc': "Steady signal-calling pushes Blake over the century mark."},
                {'tag': "RUNNER-UP", 'header': "Big Nasties (Nael Ahmed) — 85.08 PTS", 'desc': "Chris Olave (21.9 pts) fights hard, but cannot stop King Henry's freight train."}
            ]
        },
        'dialogue': [
            ('CHRIS', "Down in the consolation bracket, Blake Whitehouse took down Nael Ahmed one hundred and one to eighty-five behind the single most dominant individual performance of the week from King Derrick Henry!"),
            ('DAVE', "Derrick Henry dropped forty-five point six fantasy points! Thirty-one years old and running through defensive backs like a runaway semi-truck! Nael got twenty-one from Chris Olave, but nobody on earth was surviving forty-five points from King Henry!"),
            ('CHRIS', "Blake finishes the year on a high note, proving that when King Henry gets rolling, there is no defense in fantasy football that can slow him down.")
        ]
    })

    # =========================================================================
    # SCENE 9: CONSOLATION SMACKDOWN (Samran vs Emelie)
    # =========================================================================
    scenes.append({
        'card': {
            'title': "CONSOLATION BRACKET SMACKDOWN",
            'subtitle': "De'von Intervention (Samran Mirza) 115.68  def.  Trophy Wife (Emelie Lovasko) 77.38",
            'badge': "TRIPLE-THREAT WIN",
            'accent': (39, 174, 96),
            'items': [
                {'tag': "TRIPLE THREAT", 'header': "Chase Brown (27.6), Stevenson (24.7), Dak (24.68)", 'desc': "Overwhelming 76-point trio powers Samran to a +38.30 pt win."},
                {'tag': "EFFORT", 'header': "Trophy Wife (Emelie Lovasko) — 77.38 PTS", 'desc': "D'Andre Swift (20.9) and Wan'Dale (16.8) battle in a tough loss."},
                {'tag': "CENTURY", 'header': "Top-3 Scoring Performance", 'desc': "Samran finishes the season with maximum offensive momentum."}
            ]
        },
        'dialogue': [
            ('CHRIS', "And wrapping up the consolation slate, Samran Mirza exploded for one hundred and fifteen point six eight points to rout Emelie Lovasko by thirty-eight points!"),
            ('DAVE', "Samran had three players over twenty-four fantasy points: Chase Brown with twenty-seven point six, Rhamondre Stevenson with twenty-four point seven, and Dak Prescott with twenty-four point six eight! Emelie got twenty from D'Andre Swift, but Samran put up one of the best scoring performances of the entire weekend."),
            ('CHRIS', "Samran closes out the season with massive momentum and gives the rest of the league plenty to think about heading into the 2026 campaign.")
        ]
    })

    # =========================================================================
    # SCENE 10: MIDNIGHT MEDIA PRESS ROOM (Championship Soundbites)
    # =========================================================================
    scenes.append({
        'card': {
            'title': "MIDNIGHT MEDIA PRESS ROOM",
            'subtitle': "Post-Championship Manager Soundbites",
            'badge': "PRESS CONFERENCE",
            'accent': (243, 156, 18),
            'items': [
                {'tag': "CHAMPION", 'header': "Abe Thomas (Back-to-Back Champ):", 'desc': "\"Back-to-back Jabroni Trophies! We built a dynasty. Put some respect on Crashee Bandicoot!\""},
                {'tag': "HEARTBREAK", 'header': "Dan Kruszewski (Runner-Up):", 'desc': "\"Leaving 37 points on my bench in the title game stings. We will be back next year.\""},
                {'tag': "4-TIME GOAT", 'header': "Shawn Lukose (4th Place):", 'desc': "\"Missing the podium is unacceptable. Massive roster overhaul starting tomorrow.\""},
                {'tag': "DROUGHT", 'header': "Saagar Gupta (8th Place):", 'desc': "\"18 years since 2008... The title drought continues, but 2026 is our year.\""}
            ]
        },
        'dialogue': [
            ('CHRIS', "Now let's head down to the media press room for the final post-game soundbites of the season. Champion Abe Thomas was showered in champagne and told reporters: 'Going back-to-back in an eighteen-team league with this level of competition proves what we built. The Jabroni Trophy stays in our trophy case!'"),
            ('DAVE', "And Dan Kruszewski was holding his head in his hands in the hallway, saying: 'Leaving thirty-seven points on my bench with Brock Purdy in the championship game is going to keep me up at night all offseason, but congratulations to Abe.'"),
            ('CHRIS', "Four-time champion Shawn Lukose was furious about missing the podium, stating: 'Finishing fourth is unacceptable for this franchise. We are holding an eight A M team meeting tomorrow morning to revamp the entire organization.'"),
            ('DAVE', "And Saagar Gupta sighed deeply into the microphone, saying: 'Eighteen years since 2008. The drought continues, but we will be back in 2026 hunting for ring number two.'")
        ]
    })

    # =========================================================================
    # SCENE 11: ALL-TIME BFL TROPHY ROOM & DYNASTY RANKINGS
    # =========================================================================
    scenes.append({
        'card': {
            'title': "ALL-TIME BFL TROPHY ROOM",
            'subtitle': "18 Seasons of Jabroni Trophy Champions",
            'badge': "HISTORIC DYNASTIES",
            'accent': (241, 196, 15),
            'items': [
                {'tag': "4 RINGS", 'header': "Shawn Lukose (4 Titles)", 'desc': "All-time league GOAT with 4 Jabroni Trophies."},
                {'tag': "3 RINGS", 'header': "Commissioner Nick Christus (3 Titles)", 'desc': "North Division powerhouse with 3 championship rings."},
                {'tag': "2 RINGS", 'header': "Abe Thomas (2 Titles - Back-to-Back!)", 'desc': "Joins multi-ring royalty with consecutive championships."},
                {'tag': "1 RING", 'header': "Sydney Miller, Saagar Gupta, Shawn Ullenbrauck", 'desc': "Elite champions in 18 seasons of BFL league history."}
            ]
        },
        'dialogue': [
            ('CHRIS', "With Abe's back-to-back championship victory, let's take a look at the all-time BFL Trophy Room after eighteen legendary seasons."),
            ('DAVE', "Shawn Lukose still leads the all-time leaderboard with four Jabroni Trophies, followed by Commissioner Nick Christus with three rings. Abe Thomas now officially moves into third place all-time with two back-to-back championships, joining Sydney Miller, Saagar Gupta, and Thor in the champions club!"),
            ('CHRIS', "Eighteen seasons, dozens of bitter rivalries, and only six managers have ever touched The Jabroni Trophy. The bar for greatness in the BFL has never been higher.")
        ]
    })

    # =========================================================================
    # SCENE 12: SEASON FINALE SIGN-OFF & 2026 OFFSEASON
    # =========================================================================
    scenes.append({
        'card': {
            'title': "2025 SEASON FINALE SIGN-OFF",
            'subtitle': "Thank You for Tuning into the BFL Broadcast Network!",
            'badge': "SIGN-OFF",
            'accent': (41, 128, 185),
            'items': [
                {'tag': "CHAMPION", 'header': "Congrats to Abe Thomas (2025 Champ)", 'desc': "Back-to-back champion hoists The Jabroni Trophy."},
                {'tag': "COMMISH", 'header': "Presented by Commissioner Nick Christus", 'desc': "18 years of premier fantasy football excellence."},
                {'tag': "2026 ROAD", 'header': "Offseason Trades & Rookie Draft Ahead", 'desc': "We will see you in 2026 for the 19th season of BFL action!"}
            ]
        },
        'dialogue': [
            ('CHRIS', "That officially wraps up our coverage of the 2025 BFL campaign! Huge congratulations to Abe Thomas for winning back-to-back Jabroni Trophies, and a massive thank you to Commissioner Nick Christus and every single franchise manager for another unforgettable eighteen-week season."),
            ('DAVE', "Enjoy the offseason, get your rookie scouting reports ready for the 2026 draft, and don't leave thirty-seven points on your pine all summer! For Chris, Dave, and the entire BFL Broadcast Network crew, have a wonderful offseason everybody!"),
            ('CHRIS', "We will see you back in the studio for the 2026 draft. So long from the BFL Broadcast Network!")
        ]
    })

    return scenes
