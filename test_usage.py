"""Focused regression tests for the local usage reader and price calculator.

All reader input is synthesized in a temporary JSONL file.  These tests do
not inspect either user's Codex credentials or the live pricing endpoint.
"""

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


try:  # Works both as ``python -m unittest outputs.dashboard.test_usage`` and directly.
    from .codex_reader import parse
    from .identity import stable_id
    from . import omp_reader
    from .pricing import original_prices, parse_prices, value
    from .server import cycle_estimates
except ImportError:  # pragma: no cover - only used for direct script execution.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from codex_reader import parse
    from identity import stable_id
    import omp_reader
    from pricing import original_prices, parse_prices, value
    from server import cycle_estimates


def _session_meta(session="session-1", account="team-a"):
    return {
        "type": "session_meta",
        "payload": {
            "id": session,
            "originator": "codex-app",
            "model_provider": "openai",
            "account_id": account,
        },
    }


def _turn_context(model, account=None):
    payload = {"model": model}
    if account is not None:
        payload["account_id"] = account
    return {"type": "turn_context", "payload": payload}


def _token_count(timestamp, total, *, last=None):
    info = {"total_token_usage": total}
    if last is not None:
        info["last_token_usage"] = last
    return {
        "type": "event_msg",
        "timestamp": timestamp,
        "payload": {"type": "token_count", "info": info},
    }


def _write_jsonl(rows):
    temporary = TemporaryDirectory()
    path = Path(temporary.name) / "synthetic-session.jsonl"
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
    return temporary, path


class CodexReaderTests(unittest.TestCase):
    def test_cumulative_deltas_skip_duplicate_switch_model_and_deduct_cache(self):
        rows = [
            _session_meta(),
            _turn_context("gpt-5.6-sol"),
            _token_count(
                "2026-09-06T00:00:00Z",
                {
                    "input_tokens": 100,
                    "cached_input_tokens": 20,
                    "cache_write_input_tokens": 10,
                    "output_tokens": 5,
                    "reasoning_output_tokens": 3,
                },
            ),
            # Same cumulative signature must not be counted a second time.
            _token_count(
                "2026-09-06T00:00:01Z",
                {
                    "input_tokens": 100,
                    "cached_input_tokens": 20,
                    "cache_write_input_tokens": 10,
                    "output_tokens": 5,
                    "reasoning_output_tokens": 3,
                },
            ),
            _turn_context("gpt-5.6-luna"),
            _token_count(
                "2026-09-06T00:00:02Z",
                {
                    "input_tokens": 160,
                    "cached_input_tokens": 30,
                    "cache_write_input_tokens": 15,
                    "output_tokens": 9,
                    "reasoning_output_tokens": 5,
                },
            ),
        ]
        temporary, path = _write_jsonl(rows)
        try:
            events, quotas = parse(path)
        finally:
            temporary.cleanup()

        self.assertEqual([], quotas)
        self.assertEqual(2, len(events))
        first, second = events
        self.assertEqual(
            {
                "model": "gpt-5.6-sol",
                "input": 70,
                "cached": 20,
                "write": 10,
                "output": 5,
                "total": 105,
            },
            {key: first[key] for key in ("model", "input", "cached", "write", "output", "total")},
        )
        self.assertEqual(
            {
                "model": "gpt-5.6-luna",
                "input": 45,
                "cached": 10,
                "write": 5,
                "output": 4,
                "total": 64,
            },
            {key: second[key] for key in ("model", "input", "cached", "write", "output", "total")},
        )
        # reasoning_output_tokens is already represented by output_tokens in the
        # token-count envelope and must not be added as a second output stream.
        self.assertNotIn("reasoning", first)
        self.assertNotIn("reasoning", second)

    def test_reset_uses_last_token_usage_and_resumes_from_new_cumulative_total(self):
        rows = [
            _session_meta(),
            _turn_context("gpt-6-astra"),
            _token_count(
                "2026-09-06T00:00:00Z",
                {
                    "input_tokens": 100,
                    "cached_input_tokens": 20,
                    "cache_write_input_tokens": 10,
                    "output_tokens": 5,
                },
            ),
            # Cumulative totals fell after compaction/reset.  The explicit last
            # request is the only reliable delta for this envelope.
            _token_count(
                "2026-09-06T00:01:00Z",
                {
                    "input_tokens": 50,
                    "cached_input_tokens": 5,
                    "cache_write_input_tokens": 2,
                    "output_tokens": 3,
                },
                last={
                    "input_tokens": 8,
                    "cached_input_tokens": 2,
                    "cache_write_input_tokens": 1,
                    "output_tokens": 4,
                },
            ),
            # The next delta starts at the post-reset cumulative values, rather
            # than at last_token_usage.
            _token_count(
                "2026-09-06T00:02:00Z",
                {
                    "input_tokens": 60,
                    "cached_input_tokens": 6,
                    "cache_write_input_tokens": 3,
                    "output_tokens": 6,
                },
            ),
        ]
        temporary, path = _write_jsonl(rows)
        try:
            events, _ = parse(path)
        finally:
            temporary.cleanup()

        self.assertEqual(3, len(events))
        self.assertEqual(
            [(70, 20, 10, 5, 105), (5, 2, 1, 4, 12), (8, 1, 1, 3, 13)],
            [
                (event["input"], event["cached"], event["write"], event["output"], event["total"])
                for event in events
            ],
        )


class IdentityTests(unittest.TestCase):
    def test_team_identity_separates_members_and_normalizes_email_case(self):
        team = "shared-team-account"
        alice = stable_id(team, "alice@example.com")
        alice_upper = stable_id(team, "  ALICE@EXAMPLE.COM ")
        bob = stable_id(team, "bob@example.com")

        self.assertEqual(alice, alice_upper)
        self.assertNotEqual(alice, bob)


class OmpReaderTests(unittest.TestCase):
    def test_usage_components_are_summed_independently_without_reasoning_double_count(self):
        rows = [
            {
                "timestamp": "2026-09-06T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "responseId": "response-1",
                    "model": "gpt-5.6-sol",
                    "usage": {
                        "input": 100,
                        "cacheRead": 20,
                        "cacheWrite": 10,
                        "output": 5,
                        # Reasoning tokens are a subset of output in OMP data.
                        "reasoning": 3,
                    },
                },
            },
            {
                "timestamp": "2026-09-06T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "responseId": "response-2",
                    "model": "gpt-5.6-sol",
                    "usage": {
                        "input": 7,
                        "cacheRead": 2,
                        "cacheWrite": 1,
                        "output": 4,
                        "reasoning": 2,
                    },
                },
            },
        ]
        temporary, path = _write_jsonl(rows)
        try:
            events, quotas = omp_reader.parse(path)
        finally:
            temporary.cleanup()

        self.assertEqual([], quotas)
        self.assertEqual(2, len(events))
        self.assertEqual(
            [(100, 20, 10, 5, 135), (7, 2, 1, 4, 14)],
            [
                (event["input"], event["cached"], event["write"], event["output"], event["total"])
                for event in events
            ],
        )
        self.assertTrue(all("reasoning" not in event for event in events))

    def test_duplicate_response_ids_are_retained_for_service_layer_deduplication(self):
        rows = [
            {
                "timestamp": "2026-09-06T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "responseId": "same-response",
                    "model": "gpt-5.6-sol",
                    "usage": {"input": 2, "cacheRead": 0, "cacheWrite": 0, "output": 1},
                },
            },
            {
                # A cloned history can contain the same response ID at another
                # timestamp.  The parser preserves the source ID so the server
                # can decide whether the records are duplicates.
                "timestamp": "2026-09-06T00:00:02Z",
                "message": {
                    "role": "assistant",
                    "responseId": "same-response",
                    "model": "gpt-5.6-sol",
                    "usage": {"input": 2, "cacheRead": 0, "cacheWrite": 0, "output": 1},
                },
            },
        ]
        temporary, path = _write_jsonl(rows)
        try:
            events, _ = omp_reader.parse(path)
        finally:
            temporary.cleanup()

        self.assertEqual(["same-response", "same-response"], [event["id"] for event in events])


def _official_markdown():
    return """\
### Standard pricing data

| Model | Short context input | Short context cached input | Short context cache writes | Short context output | Long context input | Long context cached input | Long context cache writes | Long context output |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-6-astra | $10.00 | $1.00 | $12.50 | $50.00 | $20.00 | $2.00 | $25.00 | $75.00 |
| gpt-5.6-sol | $4.00 | $0.40 | $5.00 | $20.00 | $8.00 | $0.80 | $10.00 | $30.00 |
| gpt-5.6-terra | $2.00 | $0.20 | $2.50 | $12.00 | $4.00 | $0.40 | $5.00 | $18.00 |
| gpt-5.6-luna | $0.20 | $0.02 | $0.25 | $1.20 | $0.40 | $0.04 | $0.50 | $1.80 |
| gpt-5.5 (<272K context length) | $5.00 | $0.50 | - | $30.00 | $10.00 | $1.00 | - | $45.00 |

### Batch pricing data
"""


def _event(model, input_tokens, cached=0, write=0, output=0):
    return {
        "model": model,
        "input": input_tokens,
        "cached": cached,
        "write": write,
        "output": output,
    }


class PricingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prices = parse_prices(_official_markdown())

    def test_official_cached_rates_and_272000_boundary(self):
        # Input + cached input + cache write is exactly 272,000, so the short
        # context rates still apply at the boundary.
        short = _event("gpt-6-astra", 100_000, cached=100_000, write=72_000, output=1_000)
        self.assertAlmostEqual(2.05, value(short, self.prices), places=8)

        # One additional input token switches every component to long-context
        # official rates, including cached input and cache writes.
        long = _event("gpt-6-astra", 100_001, cached=100_000, write=72_000, output=1_000)
        self.assertAlmostEqual(4.07502, value(long, self.prices), places=8)

        rates = self.prices["models"]["gpt-6-astra"]
        self.assertEqual(272000, rates["threshold"])
        self.assertEqual([1.0, 2.0, 25.0], [rates["short"][1], rates["long"][1], rates["long"][2]])

    def test_unknown_model_and_unsupported_cache_write_return_none(self):
        self.assertIsNone(value(_event("model-that-is-not-in-the-snapshot", 1, output=1), self.prices))
        # The official table has no cache-write rate for gpt-5.5.  A non-zero
        # write count must therefore remain unknown instead of being priced as
        # an input token.
        self.assertIsNone(value(_event("gpt-5.5", 1, write=1, output=1), self.prices))

    def test_user_fixed_subscription_rates(self):
        prices=original_prices()
        self.assertEqual([1.0,0.1,1.25,6.0],prices['models']['gpt-5.6-luna']['short'])
        self.assertEqual([2.5,0.25,3.125,12.5],prices['models']['gpt-5.6-terra']['short'])
        self.assertEqual([5.0,0.25,6.25,30.0],prices['models']['gpt-5.6-sol']['short'])
        self.assertEqual([10.0,1.0,12.5,50.0],prices['models']['gpt-6-astra']['short'])


class CycleEstimateTests(unittest.TestCase):
    def test_uses_last_matching_observation_and_marks_partial_prices(self):
        cycles=[{'account':'a','start':'2026-09-01T00:00:00Z','end':'2026-09-08T00:00:00Z'}]
        observations=[
            {'account':'a','ts':'2026-09-03T00:00:00Z','resetsAt':1788825600,'usedPercent':20},
            {'account':'a','ts':'2026-09-06T00:00:00Z','resetsAt':1788825600,'usedPercent':40},
        ]
        events=[
            {'account':'a','ts':'2026-09-05T00:00:00Z','cost':10},
            {'account':'a','ts':'2026-09-05T01:00:00Z','cost':None},
            # Later than the percentage observation: excluded from the numerator.
            {'account':'a','ts':'2026-09-07T00:00:00Z','cost':100},
        ]
        row=cycle_estimates(cycles,observations,events)[0]
        self.assertEqual(40,row['usedPercent'])
        self.assertEqual(10,row['actualCost'])
        self.assertEqual(25,row['estimatedTotalCost'])
        self.assertEqual('partial',row['coverageStatus'])
        self.assertEqual(1,row['unknownPriceEvents'])

    def test_missing_observation_does_not_invent_estimate(self):
        cycle={'account':'a','start':'2026-09-01T00:00:00Z','end':'2026-09-08T00:00:00Z'}
        event={'account':'a','ts':'2026-09-05T00:00:00Z','cost':10}
        row=cycle_estimates([cycle],[],[event])[0]
        self.assertIsNone(row['estimatedTotalCost'])
        self.assertEqual('no-observation',row['coverageStatus'])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
