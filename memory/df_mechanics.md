# Dwarf Fortress Adventure Mode — Key Mechanics Reference

## Creature Danger Tiers
- **Tier 1 (weak)**: kobolds, giant rats, small animals — kill quickly, low XP
- **Tier 2 (moderate)**: goblins, bandits, wolves, trolls — can kill an unprepared adventurer; check HP before engaging
- **Tier 3 (dangerous)**: ogres, giant cave spiders (webbing!), large predators — fight carefully or avoid
- **Tier 4 (severe)**: dragons, rocs, forgotten beasts, night trolls — do not engage unless very well equipped
- **Megabeasts**: titans, forgotten beasts, dragons — almost certainly fatal to engage without legendary skills and excellent armor

## Combat Fundamentals
- **Weapon type matters**: slashing vs blunt vs piercing damage varies by creature type. Undead (skeletons) resist slashing — use blunt. Living creatures with tough hide → piercing/slashing better than blunt.
- **Anatomy targeting**: attacking limbs can disarm/cripple. Head/neck attacks are high-risk, high-reward. Torso attacks most reliable.
- **Size disadvantage**: fighting creatures much larger than your adventurer is risky — they hit harder, you need skill or good weapon reach to compensate.
- **Multiple opponents**: never fight 2+ enemies simultaneously when below 60% health. Each enemy gets its own attack opportunities.
- **Retreat**: fleeing is valid. A running adventurer is harder to hit. Run toward a chokepoint (doorway, narrow passage) to fight 1-on-1.
- **FLEE when**: hp < 50%, multiple hostiles, exhaustion_critical, or any hostile you can't defeat. Use `flee` action to route away automatically.
- **YIELD**: surrendering stops combat immediately. Enemies *may* accept — intelligent races (goblins, bandits, humans) usually do. Animals almost never yield. Use `yield` before HP drops to 25%.
- **Attack selection (v50+)**: `attack` opens a targeting UI. The game picks the closest enemy. In melee, target weapons and weapon hands to disarm; target legs to slow; torso for reliable damage.
- **Wrestling**: `A_WRESTLE` (unarmed grapple) — pin then disarm or bite. Useful when weapon is broken or to capture targets.
- **Surprise and ambush**: sneaking before combat gives first-strike advantage and may prevent aggro. Use `sneak` toggle before approaching dangerous enemies.
- **Jump tackle**: jumping then attacking knocks enemies down, creating a huge advantage. Jump is unlocked by Climber/Jumper skill.

## Physiological Needs
- **Hunger**: adventurers need to eat periodically (every ~75k ticks). Starvation (>150k) causes progressive debuffs then death. Hunt animals, forage, or visit towns to buy food. Use `eat_N` to consume food items from inventory.
- **Thirst**: water needed more frequently (~50k ticks). Dehydration (>100k) kills faster than starvation. Rivers, wells, and waterskins are water sources. Use `drink_N` from inventory.
- **Sleep**: drowsiness builds over ~8 in-game hours (~58k ticks). Use `sleep` to rest until dawn (safe locations only). **DANGER**: sleeping outdoors at night risks bogeymen attacks (magical creatures that swarm in the dark) — ALWAYS sleep in a building, inn, or structure. Ask owners for permission if needed.
- **Exhaustion**: combat and running deplete stamina. Fighting while exhausted severely reduces combat effectiveness. Use `wait_long` to partially recover, or `sleep` to fully recover.
- **Wounds**: injuries degrade over time without rest. Rest promotes natural healing. Major wounds (severed limbs, organ damage) need medical attention. Check `health_pct` — below 30% is critical.

## Economy & Towns
- **Trading**: merchants buy/sell items at market value. Never pick up items in shops without completing a trade — the merchant will call guards immediately.
- **Quest rewards**: giving information (location of enemies, etc.) builds reputation. Completing kill/retrieve quests earns payment and reputation.
- **Theft**: taking items from shops or sleeping people causes immediate hostility. Town guards will attack.

## Site Types & What to Expect
- **Town/hamlet**: safe base, merchants, quest givers, healers. Use as resupply points.
- **Fortress (dwarven)**: usually hostile to non-dwarves. Approach carefully; may have traps.
- **Goblin fortress**: always hostile. Expect organized resistance. High reward/high risk.
- **Dungeon/crypt**: undead common; traps possible; often holds notable loot.
- **Lair**: single dangerous creature. Check creature tier before engaging.
- **Wilderness**: random encounters. Predators attack on sight. Avoid night travel — nocturnal predators are more active.

## Movement & Exploration
- **Fast travel**: the overworld map. Use to cover large distances quickly. Dangerous animals can interrupt travel; you'll be spawned in the wilderness.
- **Staircases**: `<` = stair up, `>` = stair down. Use these to navigate dungeon floors.
- **Locked doors**: some doors require keys or can be kicked open (strength-dependent).
- **Swimming**: adventurers can drown in deep water. Avoid unless you have swimmer skill.
- **Climbing**: rough terrain (trees, cliffs) can sometimes be climbed with Climber skill. Failure means falling damage.

## Conversation System
- **Starting a conversation**: `talk` opens a menu listing available NPCs. The listed NPC may not be the one you approached — DF picks who's in earshot. **Always talk to whoever is listed.**
- **Greeting phase**: You'll see "Bypass greeting", "Reply to greeting", etc. Choose **"Bypass greeting"** to skip directly to the topic menu.
- **Topic menu**: Lists conversation topics like "Ask about troubles", "Ask for directions", "Bring up specific incident", "Tell about yourself", etc. Pick topics that match your goal.
- **"Change the subject"**: Returns to the topic menu from any sub-topic. Use it to ask about multiple things in one conversation.
- **Getting information**: Ask about troubles/incidents to learn about lairs and quests. Ask for directions to learn about nearby sites. Spread rumors to share knowledge.
- **Full conversations**: Don't escape after one exchange. Explore 2-3 topics before ending. Each topic can reveal different information.
- **"Nevermind"**: Ends the current topic but stays in conversation. Use "escape" to fully exit.

## Skills & Leveling
- Skills improve with use. Fight weaker enemies to train safely. Dodge trains by being attacked and not getting hit.
- **Key combat skills**: Fighter, Armor User (damage reduction), Dodger (avoidance), specific weapon skills
- **Key utility skills**: Swimmer, Climber, Ambusher (stealth)
- Social skills (Persuader, Negotiator) improve conversation outcomes and quest success rates.
