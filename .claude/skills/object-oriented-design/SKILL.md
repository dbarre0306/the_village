---
name: object-oriented-design
description: Use when a class or file has grown large with mixed responsibilities, when code branches on a type/kind field to pick behavior, when a class repeatedly reads another object's fields to compute something, when a package/module should expose publicly versus keep internal, or when a cluster of free functions keeps passing the same 2-3 parameters to each other.
---

# Object-Oriented Design

## Overview

A class should have one reason to change, hide internals behind a narrow public surface, and let polymorphism replace branching on type. These patterns are grounded in two real refactors:

- `the_village/discussion/` (see `discussion.py`, `speaker.py`, `ai_speaker.py`, `human_speaker.py`, `reply_chain.py`, `__init__.py`), which split a 400-line `DiscussionRunner` God object into a coordinator plus focused collaborators.
- `the_village/pick_victim/kill_next_victim.py` → `werewolf_pack.py`, which turned a module of free functions that all threaded `state`, `player_agents`, and `rng` through each other into a single `WereWolfPack` class.

## When to Use

Symptoms:

- One class mixes orchestration, external I/O, and formatting/computation.
- An `if/elif`/`match` dispatches on a `type`/`kind`/`channel` string, especially at more than one call site.
- A class reaches into another object's fields repeatedly to derive something (feature envy).
- A package exposes every internal helper class instead of one clear entry point.
- A module of free functions keeps re-passing the same handful of parameters (`state`, `rng`, ...) from one function to the next just to hand them down the call chain.

## Patterns

### 1. Extract Class (Single Responsibility)

`DiscussionRunner` did orchestration + AI prompting + human I/O + reply-threading in one file. It was split into `Discussion` (coordinator), `_AiSpeaker`/`_HumanSpeaker` (a `_Speaker` hierarchy), and `_ReplyChain` (a standalone collaborator for exactly one behavior: following a chain of replies).

**Rule:** when a class does two or more of {orchestrate a sequence, talk to an external system, run a distinct sub-algorithm}, extract the sub-algorithm into its own class — and give it only the state it needs, not the whole parent. `_ReplyChain` takes `speakers: dict[str, _Speaker]`, not the whole `Discussion`.

### 2. Template Method (shared wrapper, varying hook)

```python
class _Speaker:
    async def speak(self, addressed_by):        # public: shared logging + delivery
        message = await self._speak(addressed_by)
        if message is not None:
            self._log_message(message)
            await self._bridge.outbox.put(message)
        return message

    @abstractmethod
    async def _speak(self, addressed_by): ...    # subclass hook: how content is produced
```

`_AiSpeaker._speak()` runs a CrewAI crew; `_HumanSpeaker._speak()` waits on the bridge for input. Both get logging and delivery for free without duplicating it.

**Rule:** when N subclasses share a wrapping algorithm around one step that legitimately varies, put the wrapper in the base class as the public method and make the varying step an abstract hook.

### 3. Polymorphism Over Conditionals (decide once, at construction)

`Discussion` holds `dict[str, _Speaker]` and calls `speaker.speak(...)` everywhere — it never asks "is this player human or AI" at the call site. That decision is made exactly once, in `_build_speaker()`, which returns the right `_Speaker` subclass.

**Rule:** if you're asking "what kind of X is this" more than once, the branch belongs at construction time, producing an object that already knows how to behave — not scattered through every call site.

### 4. Tell, Don't Ask (put behavior with the data it changes)

Last-speaker tracking used to live in the orchestrator, which read and mutated state it didn't own. It moved onto `GameState` — the object that actually owns "who spoke last" — so callers ask `state.is_last_player_to_speak(player)` instead of recomputing it from raw fields.

**Rule:** if a class repeatedly reads another object's fields to derive something, move that computation onto the object owning the fields and call a method on it instead.

### 5. Encapsulation via Restricted Visibility (narrow public surface)

`Speaker`, `AiSpeaker`, `HumanSpeaker`, `ReplyChain`, `SpeakerOutput`, and `AddressResolution` were all renamed to underscore-prefixed (`_Speaker`, etc.), and the package's public surface was pinned down explicitly:

```python
# discussion/__init__.py
from .discussion import Discussion

# Explicitly define ONLY the public functions allowed outside the folder
__all__ = ["Discussion"]
```

That's Python's mechanism; every OO language has one. Java/Kotlin have `private`/package-private/`internal`; C# has `private`/`internal`; C++ has `private` plus header/implementation separation (or the pImpl idiom); Go has unexported (lowercase) identifiers; Rust has private-by-default with `pub(crate)`; TypeScript has `private`/`#` fields plus explicit `export` lists. The mechanism differs, but the shape of the fix is the same.

**Rule:** a package/module should expose exactly the classes callers need. Internal collaborators and DTOs get marked non-public (however the language spells that) and stay out of the public export surface — this is what lets `_Speaker`/`_ReplyChain` keep changing shape without breaking anything outside the package.

### 6. Combine Functions Into Class (shared parameters → fields)

`kill_next_victim.py` was a dozen free functions — `_eligible_targets(state)`, `_ensure_living_pack_leader(state, rng)`, `_order_pack(state, rng)`, `_build_tasks(order, player_agents, state, eligible)`, `_build_crew(tasks, order, player_agents)` — where almost every call re-passed `state`, and several also re-passed `rng` and `player_agents`, purely to hand them down to the next function. The top-level `kill_next_victim(state, player_agents, rng)` was mostly plumbing.

```python
# Before: every function repeats the same parameters
def _order_pack(state: GameState, rng: random.Random) -> list[str]: ...
def _ensure_living_pack_leader(state: GameState, rng: random.Random) -> None: ...

async def kill_next_victim(state, player_agents, rng=None) -> None:
    rng = rng or random.Random()
    _ensure_living_pack_leader(state, rng)
    order = _order_pack(state, rng)
    ...

# After: the shared parameters become fields, set once
class WereWolfPack:
    def __init__(self, state: GameState, player_agents: dict[str, Agent], rng=None):
        self._state = state
        self._player_agents = player_agents
        self._rng = rng or random.Random()

    def _order_pack(self) -> list[str]: ...
    def _ensure_living_pack_leader(self) -> None: ...

    async def kill_next_victim(self) -> None:
        self._ensure_living_pack_leader()
        order = self._order_pack()
        ...
```

**Rule:** when a group of free functions is always called together and repeatedly re-passes the same 2+ parameters just to forward them, that parameter set is an implicit object — make it explicit as constructor fields. Each method's signature shrinks to only what varies per call (`_build_tasks(order, eligible)` instead of `_build_tasks(order, player_agents, state, eligible)`), and the orchestrating function stops being parameter-threading plumbing.

**Watch for on conversion:** turning a free function into a method means every reference to a former parameter must now come from the instance instead. In languages where the receiver (`self`/`this`) is implicit class syntax — Java, C#, C++, Kotlin, Swift — the compiler enforces this for you: there's no way to "forget" the receiver, and a body that still refers to an undeclared local simply fails to compile. The risk is concentrated in languages where the receiver is just an ordinary, easy-to-typo parameter:

- **Python**, where `self` is a plain first parameter you write out by hand. Add `self` to the call site (`self._build_guardrail(eligible)`) but forget it in the `def` → the method still only declares `(eligible)`, so the instance-bound call now passes two arguments to a one-argument function: `TypeError: takes 1 positional argument but 2 were given`. Or keep the old parameter name in the `def` (e.g. `def _build_target_prompt(state, eligible)`) without adding `self` → argument *count* still matches, so nothing errors at definition or call time, but `state` now silently receives the class instance instead of the `GameState` it's named for. The body (`state.format_deaths()`) then fails with `AttributeError: 'WereWolfPack' object has no attribute 'format_deaths'` — or worse, succeeds silently if the instance happens to have a same-named attribute.
- **JavaScript/TypeScript**, where `this` is resolved dynamically by call site rather than lexical class structure — extracting or detaching a method (e.g. passing `obj.method` as a callback) silently loses the intended `this` unless bound or written as an arrow function.

Even in languages the compiler protects, the broader check still applies: audit every method body for a local variable or parameter that now shadows a same-named field, so it's reading stale state instead of `this.state`/`self._state`.

## Quick Reference

| Symptom                                                             | Pattern                                     |
| ------------------------------------------------------------------- | ------------------------------------------- |
| One class does orchestration + I/O + formatting                     | Extract Class                               |
| N subclasses share a wrapper around one varying step                | Template Method                             |
| `if/elif` on type/kind at multiple call sites                       | Polymorphism (decide once, at construction) |
| Class reads another object's fields repeatedly to compute something | Tell, Don't Ask                             |
| Package exposes internal/helper classes callers shouldn't touch     | Restrict visibility to a narrow public surface |
| Free functions keep re-passing the same 2+ parameters to each other | Combine Functions Into Class                |

## Common Mistakes

- **Extract Class, but hand it the whole parent's state** — the new class stays coupled to the old one instead of becoming independent. Give it only what it needs (`_ReplyChain` gets `speakers`, not `Discussion`).
- **A Template Method "hook" that isn't a hook** — if subclasses override the entire method instead of one clearly-delegated step, you have duplicated branching hidden across files, not a template.
- **Marking something non-public without gating the module's export surface** — the convention only works if other packages actually import through the declared public API (Python's `__all__`, an explicit `export`/module boundary, etc.); a "private" class still importable/reachable directly elsewhere isn't private.
- **Function-to-method conversion that leaves a body still reading the old parameter instead of the instance** — see Pattern 6's "Watch for on conversion." In receiver-as-plain-parameter languages (Python, JavaScript) this can fail loudly (`TypeError`), fail with a wrong-object error later, or not fail at all; in receiver-as-syntax languages (Java, C#, C++, Kotlin) the compiler mostly prevents it, but a locally-shadowed field is still worth checking for.
