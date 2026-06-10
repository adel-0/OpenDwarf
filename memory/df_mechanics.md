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
- **Blocking and dodging**: shield users can block; dodging moves you 1 tile — both require free space. Fights in narrow corridors heavily favor attackers.
- **Stance and distance**: staying at reach-weapon range keeps you outside an opponent's close-combat zone. Close-range wrestlers lose this advantage.
- **Fatigue in combat**: every swing costs stamina. Fighting exhausted = drastically reduced skill. If you're running low, kite or disengage.
- **Pain and stun**: significant blows cause pain that degrades combat effectiveness. Being stunned leaves you vulnerable to follow-up attacks.

## Physiological Needs
- **Hunger**: adventurers need to eat periodically (every ~75k ticks). Starvation (>150k) causes progressive debuffs then death. Hunt animals, forage, or visit towns to buy food. Use `eat_N` to consume food items from inventory.
- **Thirst**: water needed more frequently (~50k ticks). Dehydration (>100k) kills faster than starvation. Rivers, wells, and waterskins are water sources. Use `drink_N` from inventory.
- **Sleep**: drowsiness builds over ~8 in-game hours (~58k ticks). Use `sleep` to rest until dawn (safe locations only). **DANGER**: sleeping outdoors at night risks bogeymen attacks (magical creatures that swarm in the dark) — ALWAYS sleep in a building, inn, or structure. Ask owners for permission if needed.
- **Exhaustion**: combat and running deplete stamina. Fighting while exhausted severely reduces combat effectiveness. Use `wait_long` to partially recover, or `sleep` to fully recover.
- **Wounds**: injuries degrade over time without rest. Rest promotes natural healing. Major wounds (severed limbs, organ damage) need medical attention. Check `health_pct` — below 30% is critical.
- **Temperature**: in freezing biomes, adventurers can develop frostbite. Move to a warm structure regularly in cold climates. Swimming in cold water is especially dangerous.
- **Drowning**: swimming in deep water without Swimmer skill is fatal. Avoid deep water unless skilled. Even with skill, fighting in water is severely penalized.

## Social Rules — CRITICAL: Read Before Any Social Action
- **Theft = exile or death**: taking ANY item from a shop, market stall, or sleeping person without buying triggers immediate guard aggro. The entire civilization may become hostile. There is NO grace period. Do NOT pick up items in shops.
- **Crime scope**: crimes are tracked per-civilization. Being banished from one human civ does not affect dwarven civs. Crimes against member NPCs affect their whole civ.
- **Fame gates companions and services**: low-fame adventurers get fewer conversation options. Completing quests, killing notable enemies, and winning legendary fights raise fame. Fame affects recruitment success.
- **Respect and titles**: addressing lords and leaders by title opens more topics. Winning in conversation with persuasion/flattery skills improves outcome.
- **Guards and soldiers**: town guards will attack if you attack a civilian or steal. Barracks soldiers follow orders from their lord. Do not start fights in towns.
- **Reputation from rumors**: spreading true information (locations of enemies, events you witnessed) builds positive reputation. False rumors can backfire.
- **Hiring companions**: you need fame ≥ 5 (approximately) and must ask a soldier/warrior NPC about "joining your party". They may refuse based on fame or mood.

## Economy & Trading — CRITICAL
- **Merchants**: buy items via the trade screen (Enter shop → select item → confirm purchase). NEVER take items without completing the purchase.
- **Currency**: coins of various metals (copper → silver → gold → platinum). Know what you have before trading.
- **Market prices**: items are priced by material, quality, and rarity. Masterwork items command high prices.
- **Quest rewards**: giving information (location of enemies, etc.) builds reputation. Completing kill/retrieve quests earns payment and reputation.
- **Quest givers**: usually local lords, militia commanders, or tavern owners. Ask about "troubles", "agreements", or "specific incidents".
- **Buy food**: merchants in towns sell prepared food and drinks. This is the easiest way to address hunger/thirst. Talk to them and use the trade screen.

## Site Types & What to Expect
- **Town/hamlet**: safe base, merchants, quest givers, healers. Use as resupply points. Contains inns (safe to sleep), shops (buy goods), taverns.
- **Fortress (dwarven)**: usually allied if you're a dwarf; approach carefully if not. May have guards.
- **Goblin fortress**: always hostile. Expect organized resistance. High reward/high risk.
- **Dungeon/crypt**: undead common; traps possible; often holds notable loot. Light sources help.
- **Lair**: single dangerous creature. Check creature tier before engaging. Loot after you kill it.
- **Bandit camp**: bandits/outlaws. Medium difficulty. Quest targets often found here.
- **Wilderness**: random encounters. Predators attack on sight. Avoid night travel — nocturnal predators are more active.
- **Cave entrance**: leads underground. May connect to the cavern layers. Often has cave creatures.

## Movement & Exploration
- **Fast travel**: the overworld map. Use to cover large distances quickly. Dangerous animals can interrupt travel; you'll be spawned in the wilderness.
- **Staircases**: `<` = stair up, `>` = stair down. Use these to navigate dungeon floors.
- **Locked doors**: some doors require keys or can be kicked open (strength-dependent).
- **Swimming**: adventurers can drown in deep water. Avoid unless you have swimmer skill.
- **Climbing**: rough terrain (trees, cliffs) can sometimes be climbed with Climber skill. Failure means falling damage.
- **Pathfinding note**: always prefer `goto_site`/`goto_unit`/`explore` over step-by-step movement for distance travel. Single move_* steps for precise positioning only.

## Conversation System — CRITICAL for Quest Acquisition
- **Starting a conversation**: `talk` opens a menu listing available NPCs. The listed NPC may not be the one you approached — DF picks who's in earshot. **Always talk to whoever is listed.**
- **Greeting phase**: You'll see "Bypass greeting", "Reply to greeting", etc. Choose **"Bypass greeting"** to skip directly to the topic menu.
- **Topic menu**: Lists conversation topics. Key topics for quests:
  - "Ask about troubles" → local threats, bounties, quests
  - "Ask for directions" → site locations
  - "Ask about local rumors" → world events
  - "Ask about agreements" → existing quests
  - "Bring up specific incident" → dig into a rumor
  - "Tell about yourself" → reputation building
  - "Ask about any recent visitors" → info on travelers
- **"Change the subject"**: Returns to topic menu. Use to ask multiple things.
- **Getting quests**: Ask a local lord or militia commander about "troubles". They assign kill/retrieve quests. Accept with "I'll handle it" or similar.
- **Re-talking to same NPC**: You can end and restart a conversation with the same NPC to ask more topics. They remember previous conversations.
- **Information spreading**: every NPC potentially knows different things. Talk to multiple people (innkeeper, guards, merchants) for comprehensive local knowledge.
- **Full conversations**: don't escape after one exchange. Explore 2-3 topics per NPC. Each topic can reveal different information.
- **Getting directions**: asking "directions" gives you a compass direction and rough distance. This is how you locate quest targets and towns.

## Skills & Leveling
- Skills improve with use. Fight weaker enemies to train safely. Dodge trains by being attacked and not getting hit.
- **Key combat skills**: Fighter (all combat), Armor User (damage reduction), Dodger (avoidance), specific weapon skills (Swords, Maces, etc.)
- **Key utility skills**: Swimmer (water safety), Climber (terrain traversal, jump-tackle unlock), Ambusher (sneak effectiveness)
- **Social skills**: Persuader, Negotiator, Flatterer — improve conversation outcomes, quest success rates, companion recruitment
- **Skill practice**: using a skill in any context (even practice sparring, swimming in shallow water, talking to NPCs) advances it

## Equipment Management
- **Armor slots**: head, body, arms (left/right), legs, feet. Max protection = all slots covered.
- **Weapon in each hand**: you can wield weapons in both hands. Offhand weapon = reduced effectiveness but extra attack chance.
- **Shield**: blocks attacks. Only useful in the offhand (not with a two-handed weapon). Greatly reduces damage taken.
- **Quality**: masterwork > exceptional > fine > well-crafted > standard. Higher quality = better effectiveness AND higher trade value.
- **Material**: steel > iron > bronze > copper (for metal). Iron is common and sufficient for most encounters. Steel is a significant upgrade.
- **Encumbrance**: too much weight slows you. Drop items you don't need.
- **Inventory modes**: Weapon (wielded, in hand), Worn (armor, on body), Hauled (carried but not equipped). Items must be wielded/worn to provide their benefits.

## Night Creatures & Special Dangers
- **Bogeymen**: appear outdoors at night (after dark). Swarm the adventurer. Almost certainly fatal without high skill or companion guards. Sleep indoors ALWAYS. If caught outside, sprint to any structure.
- **Night trolls**: rare, very dangerous. Avoid engagement without strong party.
- **Necromancers**: can raise the dead. Nearby undead often means a necromancer is at work. Reanimated corpses attack.
- **Vampires**: appear as normal NPCs in towns. Can be a lord or guard. They're immortal — only beheading kills them permanently.
- **Werecreatures**: bite during combat can transmit the curse. Werewolves/werebears are very strong. Wound from them = potential transformation next full moon.

## Post-Combat Actions
- After killing an enemy, you can:
  - Loot their corpse (items drop on the ground — use `pickup_N`)
  - Butcher the body (if you have a cutting weapon or knife) for meat
  - Leave quickly if more enemies might come
- After defeating a notable enemy (named NPC, quest target), remember to check for quest completion and return to the quest giver.

## Wilderness Survival
- **Foraging**: some plants can be eaten raw. Use `pickup_N` on PLANT items, then `eat_N`.
- **Hunting**: attack and kill animals for meat. Butchering requires a cutting tool (knife, sword). MEAT items from corpses can be eaten raw.
- **Water sources**: rivers, streams, and wells provide water. Adjacent to a water tile, you can drink directly. Waterskins can be filled and carried.
- **Fire**: campfires provide light and warmth. Crafting a fire requires materials (not yet modeled in the agent's action set).

## Getting Out of Trouble — Priority Order
1. **FLEE** if any of: HP < 50%, multiple hostiles, exhausted, overwhelmed
2. **YIELD** if cornered and can't flee (intelligent enemies may accept)
3. **Choke point** (doorway/corridor) if you must fight outnumbered
4. **Rest/recover** in a safe building before re-engaging
5. **Ask for help** — town guards will fight hostiles that attack in town

## Known v50 Interface Details
- The `attack` action opens a target/attack selection UI when enemies are present. If only one adjacent enemy, it may auto-select.
- The `talk` action opens a list of nearby NPCs. If multiple are present, pick the most relevant one.
- `escape` / `LEAVESCREEN` always backs out of the current menu/dialog.
- `SELECT` confirms the currently highlighted option.
- `CURSOR_UP`/`CURSOR_DOWN` navigate menu lists.
