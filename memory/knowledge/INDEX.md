# Situational Knowledge Pack — Index

Topic files injected *situationally* (see NORTHSTAR II.3 knowledge injection),
unlike `memory/df_mechanics.md` which is always in the prompt prefix.

Facts are marked by provenance:
- **[wiki]** — verified against dwarffortresswiki.org 2026-06-10
- **[prior]** — community/classic-version knowledge, LIVE-VERIFY before relying on it in v0.53

| File | Inject when | Tags |
|---|---|---|
| `descent.md` | goal mentions underworld/descent/dark fortress; site_type=dark_fortress; underground below cavern depth; goal mentions cavern/stairs/spire | `descent`, `dark_fortress`, `underworld`, `underground`, `cavern`, `stairs_down`, `slade`, `spire` |
| `demons.md` | demon/unique-demon present or targeted; inside underworld or spire; site_type=dark_fortress | `demon`, `underworld`, `combat_endgame`, `dark_fortress`, `unique_demon`, `slade` |
| `training.md` | active grind_combat behavior; goal mentions skill/train/grind/spar; Director planning a training phase | `training`, `grind`, `skill`, `spar`, `combat_training`, `xp`, `level` |
| `powers.md` | site_type=necromancer_tower; goal mentions powers/immortality/necromancy/slab; character creation | `necromancy`, `powers`, `chargen`, `slab`, `immortality`, `necromancer`, `book`, `tower` |
