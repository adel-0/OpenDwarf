# Situational Knowledge Pack — Index

Topic files injected *situationally* (see NORTHSTAR II.3 knowledge injection),
unlike `memory/df_mechanics.md` which is always in the prompt prefix.

Facts are marked by provenance:
- **[wiki]** — verified against dwarffortresswiki.org 2026-06-10
- **[prior]** — community/classic-version knowledge, LIVE-VERIFY before relying on it in v0.53

| File | Inject when | Tags |
|---|---|---|
| `descent.md` | goal mentions underworld/descent/dark fortress; site_type=dark_fortress; underground below cavern depth | `descent`, `dark_fortress`, `underworld` |
| `demons.md` | demon/unique-demon present or targeted; inside underworld or spire | `demon`, `underworld`, `combat_endgame` |
| `training.md` | active grind_combat behavior; Director planning a training phase | `training`, `grind` |
| `powers.md` | site_type=necromancer_tower; goal mentions powers/immortality; character creation | `necromancy`, `powers`, `chargen` |
