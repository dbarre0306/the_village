---
name: object-oriented-design
description: Use when a class or file has grown large with mixed responsibilities, when code branches on a type/kind field to pick behavior, when a class repeatedly reads another object's fields to compute something, or when deciding what a package/module should expose publicly versus keep internal.
---

# Object-Oriented Design

## Overview

A class should have one reason to change, hide internals behind a narrow public surface, and let polymorphism replace branching on type. These five patterns are grounded in the real refactor of `the_village/discussion/` (see `discussion.py`, `speaker.py`, `ai_speaker.py`, `human_speaker.py`, `reply_chain.py`, `__init__.py`), which split a 400-line `DiscussionRunner` God object into a coordinator plus focused collaborators.

## When to Use

Symptoms:

- One class mixes orchestration, external I/O, and formatting/computation.
- An `if/elif`/`match` dispatches on a `type`/`kind`/`channel` string, especially at more than one call site.
- A class reaches into another object's fields repeatedly to derive something (feature envy).
- A package exposes every internal helper class instead of one clear entry point.

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

### 5. Encapsulation via Underscore Convention + `__all__`

`Speaker`, `AiSpeaker`, `HumanSpeaker`, `ReplyChain`, `SpeakerOutput`, and `AddressResolution` were all renamed to underscore-prefixed (`_Speaker`, etc.), and the package's public surface was pinned down explicitly:

```python
# discussion/__init__.py
from .discussion import Discussion

# Explicitly define ONLY the public functions allowed outside the folder
__all__ = ["Discussion"]
```

**Rule:** a package should expose exactly the classes callers need. Internal collaborators and DTOs get a leading underscore and stay out of `__all__` — this is what lets `_Speaker`/`_ReplyChain` keep changing shape without breaking anything outside the package.

## Quick Reference

| Symptom                                                             | Pattern                                     |
| ------------------------------------------------------------------- | ------------------------------------------- |
| One class does orchestration + I/O + formatting                     | Extract Class                               |
| N subclasses share a wrapper around one varying step                | Template Method                             |
| `if/elif` on type/kind at multiple call sites                       | Polymorphism (decide once, at construction) |
| Class reads another object's fields repeatedly to compute something | Tell, Don't Ask                             |
| Package exposes internal/helper classes callers shouldn't touch     | Underscore-prefix + `__all__` gate          |

## Common Mistakes

- **Extract Class, but hand it the whole parent's state** — the new class stays coupled to the old one instead of becoming independent. Give it only what it needs (`_ReplyChain` gets `speakers`, not `Discussion`).
- **A Template Method "hook" that isn't a hook** — if subclasses override the entire method instead of one clearly-delegated step, you have duplicated branching hidden across files, not a template.
- **Underscore-prefixing without gating `__init__.py`** — the convention only works if other packages actually import through `__all__`; a private class still imported directly elsewhere isn't private.
