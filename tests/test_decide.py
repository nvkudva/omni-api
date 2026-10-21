"""The decision logic: question shapes, letter probabilities, certainty and answers."""
import asyncio
import math
import unittest

from omni.inference import decide


class Question(unittest.TestCase):
    def test_choice_from_mapping(self):
        rendered = decide.question({"type": "choice", "instructions": "Pick one",
                                    "criteria": {"A": "first", "B": None}})
        self.assertEqual(rendered["type"], "choice")
        self.assertEqual(rendered["names"], ["A", "B"])
        self.assertEqual(rendered["options"], ["A: first", "B"])

    def test_choice_from_list(self):
        rendered = decide.question({"instructions": "Pick", "criteria": ["A", "B", "C"]})
        self.assertEqual(rendered["names"], ["A", "B", "C"])

    def test_noul_with_instructions(self):
        rendered = decide.question({"type": "noul", "instructions": "Is it true?"})
        self.assertEqual(rendered["type"], "noul")
        self.assertEqual(rendered["names"], [True, False])

    def test_noul_without_instructions_uses_criteria(self):
        rendered = decide.question({"type": "noul", "criteria": {"true": "yes indeed"}})
        self.assertEqual(rendered["question"], decide.NOUL_WITHOUT_INSTRUCTIONS)

    def test_noul_without_anything_is_an_error(self):
        with self.assertRaises(ValueError):
            decide.question({"type": "noul"})

    def test_score(self):
        rendered = decide.question({"type": "score", "instructions": "Rate", "criteria": ["low", "mid", "high"]})
        self.assertEqual(rendered["names"], [0, 1, 2])
        self.assertEqual(rendered["legend"], ["low", "mid", "high"])

    def test_unknown_type(self):
        with self.assertRaises(ValueError):
            decide.question({"type": "mystery", "instructions": "?"})

    def test_non_object(self):
        with self.assertRaises(ValueError):
            decide.question("not a question")


class Probabilities(unittest.TestCase):
    def test_mass_on_letters_is_renormalised(self):
        candidates = [{"token": "A", "logprob": math.log(0.7)},
                      {"token": "B", "logprob": math.log(0.3)}]
        got = decide.letter_probabilities(candidates, 2)
        self.assertAlmostEqual(got[0], 0.7, places=6)
        self.assertAlmostEqual(got[1], 0.3, places=6)

    def test_ignores_other_tokens_and_junk(self):
        candidates = [{"token": "A", "logprob": 0.0}, {"token": "Z", "logprob": 0.0},
                      {"token": "B", "logprob": "x"}, "nope", {"token": "C", "logprob": True}]
        got = decide.letter_probabilities(candidates, 2)
        self.assertAlmostEqual(got[0], 1.0)
        self.assertAlmostEqual(got[1], 0.0)

    def test_uniform_when_no_letter_appears(self):
        self.assertEqual(decide.letter_probabilities([], 2), [0.5, 0.5])
        self.assertEqual(decide.letter_probabilities([{"token": "Z", "logprob": 0.0}], 3),
                         [1 / 3, 1 / 3, 1 / 3])


class Certainty(unittest.TestCase):
    def test_certain_and_flat(self):
        self.assertAlmostEqual(decide.certainty([1.0, 0.0]), 1.0)
        self.assertAlmostEqual(decide.certainty([0.5, 0.5]), 0.0)

    def test_score_confidence_in_range(self):
        self.assertAlmostEqual(decide.score_confidence([0.0, 0.0, 1.0]), 1.0)
        self.assertTrue(0.0 <= decide.score_confidence([0.3, 0.4, 0.3]) <= 1.0)


class Answers(unittest.TestCase):
    def test_choice_answer(self):
        rendered = {"type": "choice", "names": ["A", "B"]}
        got = decide.answer(rendered, [0.9, 0.1])
        self.assertEqual(got["choice"], "A")
        self.assertEqual(got["type"], "choice")
        self.assertEqual(got["probabilities"], {"A": 0.9, "B": 0.1})

    def test_noul_answer(self):
        got = decide.answer({"type": "noul"}, [0.8, 0.2])
        self.assertEqual(got, {"type": "noul", "noul": 0.8})

    def test_score_answer(self):
        rendered = {"type": "score", "legend": ["low", "high"], "names": [0, 1]}
        got = decide.answer(rendered, [0.25, 0.75])
        self.assertEqual(got["score"], 0.75)
        self.assertEqual(got["legend"], {"0": "low", "1": "high"})


class PromptText(unittest.TestCase):
    def test_prompt_carries_state_and_letters(self):
        rendered = decide.question({"instructions": "Pick", "criteria": {"A": "x", "B": "y"}})
        text = decide.prompt("the state", rendered)
        self.assertIn("the state", text)
        self.assertIn("A. A: x", text)
        self.assertIn("Answer with one letter: A, B", text)

    def test_top_logprobs_guards(self):
        self.assertEqual(decide.top_logprobs({}), [])
        self.assertEqual(decide.top_logprobs({"logprobs": []}), [])
        self.assertEqual(decide.top_logprobs({"logprobs": [{"top_logprobs": [1, 2]}]}), [1, 2])
        self.assertEqual(decide.top_logprobs({"logprobs": [{"top_logprobs": "x"}]}), [])


class Native(unittest.TestCase):
    def test_choice_list_becomes_a_criteria_map(self):
        spec = {"type": "choice", "instructions": "Pick", "criteria": ["A", "B"]}
        got = decide.native_question(spec)
        self.assertEqual(got["criteria"], {"A": None, "B": None})

    def test_noul_defaults_match_the_rendered_question(self):
        got = decide.native_question({"type": "noul", "criteria": {"true": "yes"}})
        self.assertEqual(got["instructions"], decide.NOUL_WITHOUT_INSTRUCTIONS)
        self.assertEqual(got["criteria"], {"true": "yes", "false": decide.NOUL_DEFAULT_FALSE})

    def test_native_probabilities_follow_the_option_order(self):
        rendered = decide.question({"instructions": "Pick", "criteria": {"A": None, "B": None}})
        reply = {"probabilities": {"B": 0.25, "A": 0.75}}
        self.assertEqual(decide.native_probabilities(rendered, reply), [0.75, 0.25])

    def test_noul_probability_pairs_with_its_complement(self):
        probabilities = decide.native_probabilities({"type": "noul"}, {"noul": 0.8})
        self.assertAlmostEqual(probabilities[0], 0.8)
        self.assertAlmostEqual(probabilities[1], 0.2)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, capabilities, native=None, generate=None):
        self.capabilities = capabilities
        self.native = native
        self.generate = generate
        self.calls = []

    async def post(self, path, json):
        self.calls.append((path, json))
        if path == "/api/show":
            return FakeResponse({"capabilities": self.capabilities})
        if path == "/v1/systemone":
            return FakeResponse(self.native)
        return FakeResponse(self.generate)


class Routing(unittest.TestCase):
    def setUp(self):
        decide._decision_models.clear()

    def payload(self):
        return {"state": "the state", "questions": {
            "pick": {"type": "choice", "instructions": "Pick", "criteria": ["A", "B"]},
            "flag": {"type": "noul", "instructions": "Is it?", "criteria": {"true": "yes", "false": "no"}},
        }}

    def test_a_decision_model_answers_through_native_systemone(self):
        client = FakeClient(["decision"], native={"answers": {
            "pick": {"type": "choice", "choice": "A", "probabilities": {"A": 0.75, "B": 0.25}},
            "flag": {"type": "noul", "noul": 0.8},
        }, "usage": {"input_tokens": 11, "output_tokens": 0}})
        got = asyncio.run(decide.run(client, "d1-omni", self.payload()))
        self.assertEqual([path for path, _ in client.calls], ["/api/show", "/v1/systemone"])
        self.assertEqual(client.calls[1][1]["questions"]["pick"]["criteria"], {"A": None, "B": None})
        self.assertEqual(got["answers"]["pick"]["choice"], "A")
        self.assertEqual(got["answers"]["pick"]["probabilities"], {"A": 0.75, "B": 0.25})
        self.assertEqual(got["answers"]["flag"], {"type": "noul", "noul": 0.8})
        self.assertEqual(got["usage"], {"prompt_tokens": 11, "completion_tokens": 0, "total_tokens": 11})

    def test_a_completion_model_keeps_the_letter_prompt(self):
        client = FakeClient(["completion"], generate={
            "logprobs": [{"top_logprobs": [{"token": "A", "logprob": 0.0}]}],
            "prompt_eval_count": 7,
        })
        got = asyncio.run(decide.run(client, "onejev", self.payload()))
        self.assertEqual([path for path, _ in client.calls], ["/api/show", "/api/generate", "/api/generate"])
        self.assertIn("the state", client.calls[1][1]["prompt"])
        self.assertEqual(got["answers"]["pick"]["choice"], "A")

    def test_the_capability_lookup_is_cached(self):
        client = FakeClient(["decision"], native={"answers": {
            "pick": {"type": "choice", "choice": "A", "probabilities": {"A": 0.6, "B": 0.4}},
            "flag": {"type": "noul", "noul": 0.7},
        }})
        asyncio.run(decide.run(client, "d1-omni", self.payload()))
        asyncio.run(decide.run(client, "d1-omni", self.payload()))
        self.assertEqual([path for path, _ in client.calls].count("/api/show"), 1)


if __name__ == "__main__":
    unittest.main()
