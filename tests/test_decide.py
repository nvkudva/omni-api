"""The decision logic: question shapes, letter probabilities, certainty and answers."""
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


if __name__ == "__main__":
    unittest.main()
