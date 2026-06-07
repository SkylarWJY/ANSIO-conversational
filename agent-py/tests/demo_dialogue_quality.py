"""Smoke-test demo dialogue quality with the real LLM and fake retrieval data.

This is intentionally a harness, not a pytest test. It exercises the production
Assistant prompt/tools with deterministic Moss-like docs and prints:
- each founder turn
- assistant reply
- evidence card types published to the right rail
- Moss indexes queried
- whether the visible right-rail card sequence follows the demo chain
"""

from __future__ import annotations

import asyncio
import json

from livekit.agents import AgentSession

import agent as agent_module
from agent import Assistant


CARD_STEP = {
    "competitor_landscape": 1,
    "content_hits": 2,
    "similar_creators": 3,
    "kol_profile": 4,
    "alpha_ranking": 5,
    "bundle": 6,
    "content_strategy": 7,
    "roi_forecast": 8,
}


class FakeDoc:
    def __init__(self, text: str, score: float = 0.9, metadata: dict | None = None):
        self.text = text
        self.score = score
        self.metadata = metadata or {}


class FakeResult:
    def __init__(self, docs, time_taken_ms: float = 4.0):
        self.docs = docs
        self.time_taken_ms = time_taken_ms


class FakeMoss:
    def __init__(self, *args, **kwargs):
        self.query_calls = []

    async def load_indexes(self, names, *args, **kwargs):
        return None

    async def load_index(self, name, *args, **kwargs):
        return None

    async def query(self, index, query, options=None):
        self.query_calls.append((index, query, options))
        if index == agent_module.IDX_PRODUCTS:
            return FakeResult([
                FakeDoc("Cursor AI code editor", metadata={
                    "name": "Cursor", "category": "AI code editor",
                    "funding": "Series B",
                }),
                FakeDoc("GitHub Copilot AI pair programmer", metadata={
                    "name": "GitHub Copilot", "category": "AI pair programmer",
                    "funding": "Microsoft",
                }),
                FakeDoc("Replit AI cloud IDE", metadata={
                    "name": "Replit", "category": "Cloud IDE + AI",
                    "funding": "Series B",
                }),
                FakeDoc("Codeium AI completion", metadata={
                    "name": "Codeium", "category": "AI completion",
                    "funding": "Series C",
                }),
            ])
        if index == agent_module.IDX_CONTENT:
            return FakeResult([
                FakeDoc("Cursor creator marketing worked through workflow demos", metadata={
                    "kol_handle": "buildwithsam", "brand": "cursor",
                    "title": "Cursor workflow demo", "views": "120000",
                    "source": "YouTube", "doc_type": "case",
                }),
                FakeDoc("Developer tools convert through authentic builder stories", metadata={
                    "kol_handle": "indiedevtools", "brand": "cursor",
                    "title": "How I code faster with Cursor", "views": "95000",
                    "source": "Twitter", "doc_type": "strategy",
                }),
            ])
        return FakeResult([
            FakeDoc("Theo Bennett profile", metadata={
                "name": "Theo Bennett", "handle": "theobennett1",
                "platform": "YouTube", "followers": "48000",
                "niche": "tech", "engagement_pct": "7.4",
                "region": "US", "price_usd": "1800",
            }),
            FakeDoc("Priya Grant profile", metadata={
                "name": "Priya Grant", "handle": "priyabuilds",
                "platform": "X", "followers": "36000",
                "niche": "tech", "engagement_pct": "8.1",
                "region": "US", "price_usd": "1200",
            }),
            FakeDoc("Sam Xu profile", metadata={
                "name": "Sam Xu", "handle": "buildwithsam",
                "platform": "YouTube", "followers": "72000",
                "niche": "tech", "engagement_pct": "6.2",
                "region": "US", "price_usd": "2600",
            }),
        ])


class Pub:
    def __init__(self):
        self.published = []

    async def publish_data(self, payload, reliable=None):
        self.published.append(json.loads(payload.decode()))


class Room:
    def __init__(self):
        self.local_participant = Pub()


def _message_text(event) -> str | None:
    item = getattr(event, "item", None)
    if not item or getattr(item, "role", None) != "assistant":
        return None
    content = getattr(item, "content", "")
    if isinstance(content, list):
        return " ".join(str(x) for x in content)
    return str(content)


async def main() -> None:
    agent_module.MossClient = FakeMoss
    room = Room()
    assistant = Assistant(room=room, user_id="quality_demo")
    turns = [
        "Hi, we are building an AI coding tool and growth has stalled.",
        "Our target users are developers and indie hackers.",
        "Cursor is much bigger than us. Is it really useful to benchmark them?",
        "Who did Cursor work with, and what kind of creators should we find?",
        "If you had to pick two creators and a budget, what would you recommend?",
        "What content angles should we ask them to create?",
        "What ROI should we expect?",
    ]
    expected_min_step_by_turn = [1, 3, 2, 3, 5, 7, 8]
    max_seen_step = 0
    sequence_notes = []
    async with AgentSession() as session:
        await session.start(assistant)
        for i, turn in enumerate(turns, 1):
            before_cards = len(room.local_participant.published)
            before_queries = len(assistant._moss.query_calls)
            result = await session.run(user_input=turn)
            replies = [
                txt for event in result.events
                for txt in [_message_text(event)]
                if txt
            ]
            cards = room.local_participant.published[before_cards:]
            queries = assistant._moss.query_calls[before_queries:]
            card_types = [c.get("type") for c in cards]
            card_steps = [CARD_STEP[t] for t in card_types if t in CARD_STEP]
            expected_min = expected_min_step_by_turn[i - 1]
            if card_steps:
                earliest = min(card_steps)
                latest = max(card_steps)
                if latest > expected_min + 1 and max_seen_step < expected_min:
                    sequence_notes.append(
                        f"TURN {i}: jump risk, saw step {latest} before step {expected_min}"
                    )
                max_seen_step = max(max_seen_step, latest)
            elif expected_min > max_seen_step + 1:
                sequence_notes.append(
                    f"TURN {i}: no card while expected step {expected_min}"
                )
            print(f"\nTURN {i} USER: {turn}")
            for reply in replies:
                print(f"ASSISTANT: {reply}")
            print("TOOLS/QUERIES:", [q[0] for q in queries])
            print("CARDS:", card_types, "STEPS:", card_steps)

    visible_steps = [
        CARD_STEP[c.get("type")]
        for c in room.local_participant.published
        if c.get("type") in CARD_STEP
    ]
    compressed = []
    for step in visible_steps:
        if not compressed or compressed[-1] != step:
            compressed.append(step)
    print("\nVISIBLE STEP SEQUENCE:", compressed)
    missing = [step for step in range(1, 9) if step not in compressed]
    if missing:
        print("MISSING STEPS:", missing)
        sequence_notes.append(f"missing visible steps {missing}")
    if sequence_notes:
        print("SEQUENCE NOTES:")
        for note in sequence_notes:
            print("-", note)
    else:
        print("SEQUENCE NOTES: no obvious forward jumps")


if __name__ == "__main__":
    asyncio.run(main())
