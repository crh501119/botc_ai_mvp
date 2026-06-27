# Game Rules Implemented

## Script

No Greater Joy six-player subset:

- Townsfolk: Clockmaker, Investigator, Empath, Chambermaid, Artist, Sage
- Outsiders: Drunk, Klutz
- Minions: Scarlet Woman, Baron
- Demon: Imp

## Setup

Base six-player setup is 3 Townsfolk, 1 Outsider, 1 Minion, 1 Demon. If Baron is in play, setup is 2 Townsfolk, 2 Outsiders, Baron, Imp. Demon and Minion do not learn each other and Demon receives no bluffs.

## Role Notes

- Clockmaker and Investigator wake only on first night.
- Empath and Chambermaid wake every night while alive.
- Drunk is good Outsider, sees a not-in-play Townsfolk, and receives legal misinformation chosen by the constrained storyteller policy.
- Artist uses a constrained query DSL; unsupported questions do not spend the ability.
- Sage triggers only when killed by Demon night attack.
- Klutz death requires a public living-player choice; choosing evil causes evil win.
- Scarlet Woman becomes Imp only when the Demon dies with at least five players alive before death.
- Imp may self-kill and starpass to a living Minion.

## Voting

Each living player may nominate once per day and each player may be nominated once per day. Dead players cannot nominate and have one ghost vote for the whole game. Execution requires at least half living players rounded up. Highest valid vote total is executed; tied highest valid totals cause no execution.
