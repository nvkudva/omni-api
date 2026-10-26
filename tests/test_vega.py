"""Vega's prompt, pooling and calibration: the parts that run without the model graphs."""
import math
import unittest

import numpy as np

from omni.inference import vega

CONFIG = {
    "max_len": 4096, "opt_max_tokens": 32, "query_first_max_state": 4096, "n_steps": 12,
    "memory_chunks": 4, "memory_min_tokens": 8, "memory_chunk_tokens": 4,
    "calibration": {"theta": [[0.0] * 5] * 3, "conformal": {"choice": {"0.1": 0.75}, "boolean": {"0.1": 0.6}},
                    "abstain_threshold": {"0.1": 0.6}, "unbound_threshold": 2.0, "default_risk": "0.1"},
    "adapters": [{"name": "public_intents", "gate_threshold": 0.5,
                  "calibration": {"theta": [[0.0] * 5] * 3, "conformal": {"choice": {"0.1": 0.8}},
                                  "abstain_threshold": {"0.1": 0.5}, "unbound_threshold": 9.0}}],
}


def encode(text: str) -> list[int]:
    """One token per character: enough to check spans and slices."""
    return [ord(char) for char in text]


def text_of(ids: list[int]) -> str:
    return "".join(chr(token) for token in ids)


class Question(unittest.TestCase):
    def test_choice_list_becomes_a_map(self):
        rendered = vega.question({"type": "choice", "instructions": "Pick", "criteria": ["A", "B"]})
        self.assertEqual(rendered["names"], ["A", "B"])
        self.assertEqual(vega.option_texts(rendered), ["A", "B"])

    def test_choice_options_carry_their_descriptions(self):
        rendered = vega.question({"instructions": "Pick", "criteria": {"A": "first", "B": None}})
        self.assertEqual(vega.option_texts(rendered), ["A - first", "B"])

    def test_noul_is_a_boolean_with_default_wording(self):
        rendered = vega.question({"type": "noul", "instructions": "Is it?"})
        self.assertEqual(rendered["kind"], "boolean")
        self.assertEqual(vega.option_texts(rendered), ["no - the statement does not hold", "yes - the statement holds"])

    def test_noul_criteria_replace_the_wording(self):
        rendered = vega.question({"type": "noul", "instructions": "Is it?", "criteria": {"true": "spam", "false": "fine"}})
        self.assertEqual(vega.option_texts(rendered), ["no - fine", "yes - spam"])

    def test_noul_without_anything_is_an_error(self):
        with self.assertRaises(ValueError):
            vega.question({"type": "noul"})

    def test_score_levels(self):
        rendered = vega.question({"type": "score", "instructions": "Rate", "criteria": ["low", "high"]})
        self.assertEqual(vega.option_texts(rendered), ["level 0 - low", "level 1 - high"])

    def test_bad_questions(self):
        for spec in ("nope", {"type": "mystery", "instructions": "?"}, {"criteria": ["A", "B"]},
                     {"instructions": "?", "criteria": ["only"]}, {"type": "score", "instructions": "?", "criteria": "x"}):
            with self.assertRaises(ValueError, msg=spec):
                vega.question(spec)


class Prompt(unittest.TestCase):
    def build(self, state="abc", config=CONFIG):
        rendered = vega.question({"instructions": "Pick", "criteria": ["A", "B"]})
        return vega.build_prompt(encode, state, rendered, config)

    def test_spans_cover_their_text(self):
        prompt = self.build()
        text = text_of(prompt["ids"])
        start, stop = prompt["state"]
        self.assertEqual(text[start:stop], "abc")
        self.assertTrue(text.startswith("Question to keep in mind while reading (choice): Pick"))
        start, stop = prompt["options"][1]
        self.assertEqual(text[start:stop], "\n* B")
        self.assertTrue(text.endswith("\nOutcome:"))

    def test_a_long_state_drops_the_query_first_preamble(self):
        prompt = self.build("x" * 5000, {**CONFIG, "max_len": 8000})
        self.assertTrue(text_of(prompt["ids"]).startswith("Observation:"))

    def test_a_state_that_does_not_fit_is_refused_not_cut(self):
        with self.assertRaises(ValueError):
            self.build("x" * 300, {**CONFIG, "max_len": 200})

    def test_an_empty_state_falls_back_to_the_header_span(self):
        start, stop = self.build("")["state"]
        self.assertEqual(start, 0)
        self.assertGreater(stop, 0)


class Pooling(unittest.TestCase):
    def test_features_chunks_and_options(self):
        length = 40
        layer13 = np.arange(length, dtype=np.float32)[:, None].repeat(vega.HIDDEN_SIZE, axis=1)
        layer19 = layer13 * 2
        prompt = {"ids": list(range(length)), "state": (4, 20), "question": (20, 30), "options": [(30, 34), (34, 38)]}
        pooled = vega.pool(CONFIG, layer13, layer19, prompt)
        self.assertEqual(pooled["features"].shape, (2, 3, vega.HIDDEN_SIZE))
        self.assertAlmostEqual(float(pooled["features"][0, 0, 0]), 11.5)  # mean of 4..19
        self.assertAlmostEqual(float(pooled["features"][1, 2, 0]), 78.0)  # final token of layer 19
        self.assertAlmostEqual(float(pooled["options"][1, 0]), 71.0)  # 2 * mean(34..37)
        self.assertEqual(pooled["chunk_mask"].tolist(), [True, True, True, True])
        self.assertAlmostEqual(float(pooled["chunks"][0, 0]), 2 * 5.5)  # first quarter: tokens 4..7

    def test_a_short_state_has_no_memory_chunks(self):
        layer = np.ones((12, vega.HIDDEN_SIZE), dtype=np.float32)
        prompt = {"ids": list(range(12)), "state": (2, 6), "question": (6, 8), "options": [(8, 10), (10, 12)]}
        self.assertFalse(vega.pool(CONFIG, layer, layer, prompt)["chunk_mask"].any())


class Calibration(unittest.TestCase):
    def engine(self, logits=(2.0, 0.0), gates=(0.0,), nearest=0.0):
        return {"logits": list(logits), "kinetic": 0.0, "nearest": nearest, "steps": 12, "gates": list(gates)}

    def rendered(self):
        return vega.question({"instructions": "?", "criteria": ["A", "B"]})

    def test_route_picks_the_open_gate_or_the_base_engine(self):
        self.assertEqual(vega.route([0.2, 0.9], 0.5), 1)
        self.assertEqual(vega.route([0.2, 0.4], 0.5), -1)
        self.assertEqual(vega.route([], 0.5), -1)

    def test_temperature_one_is_a_plain_softmax(self):
        got = vega.calibrate([[0.0] * 5] * 3, 0, self.engine(), 12)
        self.assertAlmostEqual(got[0], math.exp(2) / (math.exp(2) + 1))
        self.assertAlmostEqual(sum(got), 1.0)

    def test_the_base_engine_decides_with_a_prediction_set(self):
        decision = vega.decide_one(CONFIG, self.rendered(), self.engine())
        self.assertIsNone(decision["adapter"])
        self.assertEqual(decision["kept"], [0])
        self.assertFalse(decision["abstain"])
        self.assertFalse(decision["unbound"])

    def test_an_open_gate_uses_the_adapter_calibration(self):
        decision = vega.decide_one(CONFIG, self.rendered(), self.engine(logits=(0.1, 0.0), gates=(0.9,)))
        self.assertEqual(decision["adapter"], "public_intents")
        self.assertEqual(decision["kept"], [0, 1])  # near a coin flip: both options stay in the set
        # confidence 0.525 clears the adapter's abstain threshold (0.5) but not the base engine's (0.6)
        self.assertFalse(decision["abstain"])

    def test_a_far_particle_is_unbound(self):
        self.assertTrue(vega.decide_one(CONFIG, self.rendered(), self.engine(nearest=5.0))["unbound"])


class Answers(unittest.TestCase):
    def decision(self, probabilities, kept=(0,)):
        return {"probabilities": probabilities, "kept": list(kept), "abstain": False, "unbound": False,
                "settle_steps": 12, "adapter": None}

    def test_choice_answer(self):
        rendered = vega.question({"instructions": "?", "criteria": ["A", "B"]})
        got = vega.answer(rendered, self.decision([0.9, 0.1]))
        self.assertEqual(got["choice"], "A")
        self.assertEqual(got["probabilities"], {"A": 0.9, "B": 0.1})
        self.assertEqual(got["prediction_set"], ["A"])

    def test_noul_answer_is_the_probability_of_yes(self):
        rendered = vega.question({"type": "noul", "instructions": "Is it?"})
        got = vega.answer(rendered, self.decision([0.2, 0.8], kept=(1,)))
        self.assertEqual((got["type"], got["noul"], got["prediction_set"]), ("noul", 0.8, [True]))

    def test_score_answer(self):
        rendered = vega.question({"type": "score", "instructions": "?", "criteria": ["low", "high"]})
        got = vega.answer(rendered, self.decision([0.25, 0.75]))
        self.assertEqual(got["score"], 0.75)
        self.assertEqual(got["legend"], {"0": "low", "1": "high"})

    def test_certainty_and_score_confidence(self):
        self.assertAlmostEqual(vega.certainty([1.0, 0.0]), 1.0)
        self.assertAlmostEqual(vega.certainty([0.5, 0.5]), 0.0)
        self.assertAlmostEqual(vega.score_confidence([0.0, 0.0, 1.0]), 1.0)
