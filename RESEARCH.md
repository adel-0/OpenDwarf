# Research Grounding & Critical Evaluation

**Question this document answers** (2026-06-10): the north star — an autonomous LLM adventurer strong enough to descend to the underworld and kill demons — is state-of-the-art-or-beyond for LLMs in games. What does the field actually know, what can we borrow, and does our harness + NORTHSTAR plan survive first-principles scrutiny? Verdict up front: **the plan is sound and unusually well-positioned, with four design deltas** (folded into NORTHSTAR Part II) and a risk register that should temper timeline expectations, not ambition.

---

## 1. What the goal actually requires (decomposition)

"Kill a demon in hell" decomposes into capabilities, each with a different
difficulty character:

| Requirement | Nature of difficulty | Class |
|---|---|---|
| ~10⁴–10⁵ sound game actions per life | throughput, not intelligence | engineering |
| Combat skill grinding to legendary-adjacent | repetition under safety rules | engineering + policy |
| Not dying to swingy combat (DF kills fast) | risk calibration + reaction speed | policy + knowledge |
| Provisioning across weeks of travel | bookkeeping | engineering |
| Finding a cavern-connected descent route | *rare knowledge* + world luck | knowledge + search |
| Multi-life persistence (deaths WILL happen) | memory across lives | engineering |
| Novel-situation handling (DF generates novelty) | open-world coverage | flywheel |
| Strategic judgment ("are we ready for hell?") | genuine LLM reasoning | model quality |

Note what is **not** on the list: per-step action selection intelligence. The decomposition itself says most of the problem is harness, not model — which the literature below confirms emphatically.

## 2. What the field knows (and what we take from each result)

### 2.1 BALROG / NetHack — the sober baseline ([ICLR 2025](https://arxiv.org/abs/2411.13543), [leaderboard](https://balrogai.com/))
The closest published analogue to our goal is NetHack ascension. As of 2026, **no LLM has come close**: the best agent (GPT 5.2) reaches dungeon level 10, ~12.5% of the progression metric. Two findings matter for us:
- **The knowing–doing gap**: GPT-4o dies eating rotten food *while correctly identifying it as dangerous when asked*. Knowledge in the prompt does not become behavior. → Our answer is structural: **Policy-as-code**. Safety rules the LLM writes are *enforced by deterministic code*, not re-decided per turn. This is arguably our strongest design decision and the literature validates it.
- **Vision/ASCII maps make things worse, not better**: spatial reasoning is the single most documented LLM weakness in roguelikes. → We already offload it entirely (ChunkMap + A*; the LLM picks `goto_*` intents, never reads tiles to navigate). We should treat any future temptation to have the LLM "look at the map and decide" as a known anti-pattern.

### 2.2 Independent NetHack post-mortem, 2026 ([kenforthewin](https://kenforthewin.github.io/blog/posts/nethack-agent/))
A practitioner attempt with frontier models, valuable because it's recent and brutally honest:
- "The harness can be just as important as the model." Autoexplore and expressive multi-step APIs (code loops instead of per-turn tool calls) were the biggest wins — i.e., exactly our Skill/Behavior direction, independently rediscovered.
- **Failure mode we must engineer against**: agents trapped in "pointless waiting patterns, hundreds of turns on non-existent threats." Reactivity over proactivity. → Validates our `STALLED` interrupt; the delta we adopt is a *progress watchdog* on behaviors (state-delta check, not just step count).
- Even the model that built the harness (Opus-class) played poorly in sustained loops — in-context brilliance ≠ sustained-play competence. → Don't assume the Tactician resolves interrupts well just because it's smart in chat; this is why the eval harness gates changes.

### 2.3 Voyager — the flywheel, validated ([arXiv 2305.16291](https://arxiv.org/abs/2305.16291))
The canonical result for open-ended game agents: an ever-growing **skill library
of executable code** + automatic curriculum + self-verification, no fine-tuning,
3.3× more items / 15.3× faster tech-tree progress than prior SOTA in Minecraft.
- Validates our flywheel (escape-hatch logs → offline skill-writing sessions → eval-gated commits) as the *load-bearing* mechanism, not a nice-to-have. Voyager's skills were LLM-written at runtime; ours are written offline by a stronger model with live verification — slower per skill, far more reliable, appropriate for a game where errors mean permadeath rather than respawn.
- The delta we adopt: Voyager's **self-verification** step. A skill isn't "done" when written; it's done when an automated check confirms the postcondition. Our eval harness (M4) is that check — which is why M4 must not slip behind M2.

### 2.4 Cradle / GCC ([arXiv 2403.03186](https://arxiv.org/abs/2403.03186))
Completed 40-minute RDR2 missions from pixels with six modules: information gathering, self-reflection, task inference, **skill curation as code functions**, action planning, memory. Confirms the same convergent architecture (skills as code, reflection, episodic memory). We deliberately diverge on input: Cradle fights the perception problem (pixels); DFHack gives us structured state for free. **Our single biggest structural advantage over all published game-agent work is that DF exposes ground-truth state via RPC.** Spend that advantage — never screen-scrape what a struct can tell us (already a CLAUDE.md principle).

### 2.5 Claude Plays Pokémon / PokeAgent ([ZenML writeup](https://www.zenml.io/llmops-database/building-and-deploying-a-pokemon-playing-llm-agent-at-anthropic), [PokeAgent Challenge](https://arxiv.org/html/2603.15563v2))
- The Pokémon agent's key memory mechanism — a knowledge base that **persists facts across summarization boundaries** — is our memory system's design too; digests-not-transcripts is the right call for arbitrarily long lives.
- The PokeAgent critique — prior "X plays Pokémon" efforts *conflated model performance with harness engineering* — names a choice we should make explicitly: **OpenDwarf is not a benchmark; it is an achievement system.** We are harness-maximalist on purpose. The "no cheating" line stays at game-state integrity (read state, simulate inputs, never mutate), not at "the LLM must do everything unaided."

### 2.6 Dwarf Fortress prior art
[df-ai](https://github.com/BenLubar/df-ai) (rule-based, fortress mode, no LLM), plus two 2026-era nascent blog projects ([trine](https://blog.trine.dev/posts/2026-02-28-df-ai-exp/), [Dev|Journal](https://earezki.com/ai-news/2026-03-14-teaching-an-ai-to-play-dwarf-fortress-the-idea/)) at the "connect to DFHack and try things" stage — both arriving at the same conclusions we did (structured state over screen-scraping; LLM for exceptions, code for routine). **Nothing reusable exists; nobody is ahead of us on adventure mode.** The user's prior — no framework to adopt — is correct. BALROG/Voyager are sources of *lessons*, not code: their environments (gym wrappers, Minecraft APIs) share nothing with DFHack RPC, and our substrate layers (RPC client, ChunkMap, registry) are already the DF-specific equivalents.

## 3. First-principles audit of our plan

Strip away the documents and re-derive: an agent reaches the underworld iff it (a) takes enough actions, (b) mostly correct ones, (c) survives its mistakes or learns across deaths, (d) knows the rare route-finding facts, (e) handles novelty without halting. Check each against the harness + NORTHSTAR plan:

- **(a) Throughput** — the Behavior/Policy layer is the answer and the math in NORTHSTAR §1 is right. The literature adds confidence: every successful system (Voyager, Cradle, the NetHack harness) moved multi-step competence out of the per-token loop. *No change needed.*
- **(b) Correctness** — split into routine (deterministic skills: fine) and judgment calls (interrupts). Weak point found: **NORTHSTAR assigns interrupts to a cheap Tactician, but BALROG shows cheap models are worst exactly at judgment under ambiguity.** Delta: an *escalation path* — Tactician can punt a hard interrupt to the Director-class model (see §4).
- **(c) Survival & lives** — deaths begin the moment `grind_combat` ships (M2), but postmortem wiring was scheduled in M4. That ordering loses the learning from precisely the deaths that teach the most. Delta: death detection + postmortems move into M2.
- **(d) Rare knowledge (the descent)** — the plan treats finding a cavern-connected route purely as a runtime search problem. First-principles observation: **we control world generation.** Cave count, cave visibility ("reveal all caves" world-gen/d_init options), world size and savagery are pre-game dials. Choosing a world rich in known cave entrances converts the long-pole knowledge problem into a configuration choice — with zero in-game cheating (world gen settings are a player prerogative). Delta: "world selection as a difficulty dial" added to NORTHSTAR §7.
- **(e) Novelty** — escape hatch (built) + flywheel (specced M4). The Voyager
  evidence says this loop is what makes open-ended coverage *compound*; the
  risk is cadence discipline, not design. *No change needed.*

One more honest finding: nothing in the literature suggests the plan is *missing a layer*. The convergent architecture across all successful systems — code skills underneath, LLM judgment on top, memory across episodes, verification gating growth — is the architecture we already specced. The remaining risk is execution and DF-specific unknowns, not design.

## 4. Design deltas adopted (now in NORTHSTAR Part II)

1. **Tactician→Director escalation** (M1): an interrupt the Tactician fails to resolve (parse failure, repeated identical interrupt, or explicit `"escalate": true` in its decision JSON) is re-asked with `caller="tactical_escalated"`, which tiering maps to the strongest model. Cheap where possible, smart where it matters.
2. **Progress watchdog** (M1): `STALLED` fires on *state-delta* stagnation
   (position + inventory + nearby-unit hash unchanged for N behavior steps), not
   merely step counting — direct counter to the documented waiting-loop pathology.
3. **Postmortems into M2** (was M4): death detection + postmortem generation
   ship with `grind_combat`, because that's when deaths start.
4. **World selection lever** (§7): generate/select the campaign world for high
   cave count + visible caves before the long campaign begins.

## 5. Risk register (fair, not flattering)

| Risk | Severity | Mitigation |
|---|---|---|
| DF combat swinginess: even legendary adventurers die to ambush; per-life success probability may stay low | High | Many-lives design (postmortems, campaign state); policy conservatism; sparring companions later (out of M-scope but the safest grind known to players) |
| Cheap Tactician misjudges interrupts | High | Escalation path (delta 1); eval scenarios for interrupt decisions |
| Adventure-mode jank: v0.53 bugs in travel/conversation/sleep interactions | Medium-High | Live-verify discipline (already enforced); escape hatch absorbs the weird |
| Demon endgame may exceed what one adventurer can do even legendary | Medium | Chokepoint tactics knowledge; "kill *a* demon" not "clear hell"; Director decides readiness; acceptable if late |
| Flywheel cadence decays (nobody runs the review sessions) | Medium | Make `logs/REVIEW.md` generation one command; schedule it |
| Token cost of long campaigns despite autopilot | Low-Medium | Digest memory keeps prompts flat; tiering puts volume on cheap models; measure $/skill-level-up as a tracked metric |
| Model spatial reasoning needed somewhere unanticipated | Low | Anti-pattern rule (§2.1): never give the LLM a navigation-by-map task; extend A*/skills instead |

**Timeline honesty**: M1–M2 are weeks. M3 depends on fast-travel verification (unknown unknowns). The descent (M5+) is gated by world luck + knowledge even with the world-selection lever. "An overnight run gains 3 combat levels unattended" is a 2026 result; "a demon dies" has no schedulable date — the literature says nobody has done anything comparable, which is exactly why the attempt is worth making and worth documenting.

## 6. Sources

- [BALROG: Benchmarking Agentic LLM and VLM Reasoning On Games (ICLR 2025)](https://arxiv.org/abs/2411.13543) · [balrogai.com](https://balrogai.com/)
- [It's 2026. Can LLMs Play NetHack Yet?](https://kenforthewin.github.io/blog/posts/nethack-agent/)
- [Voyager: An Open-Ended Embodied Agent with LLMs](https://arxiv.org/abs/2305.16291)
- [Cradle: Towards General Computer Control (RDR2 case study)](https://arxiv.org/abs/2403.03186)
- [Building a Pokémon-Playing LLM Agent at Anthropic (ZenML LLMOps DB)](https://www.zenml.io/llmops-database/building-and-deploying-a-pokemon-playing-llm-agent-at-anthropic)
- [The PokeAgent Challenge](https://arxiv.org/html/2603.15563v2)
- [df-ai (rule-based DF AI)](https://github.com/BenLubar/df-ai) · [trine DF/LLM experiments](https://blog.trine.dev/posts/2026-02-28-df-ai-exp/) · [Dev|Journal DF agent](https://earezki.com/ai-news/2026-03-14-teaching-an-ai-to-play-dwarf-fortress-the-idea/)
