"""Laya and GLiNER2.5-Decide answer the same typed questions as Vega, through the same response shape."""
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from omni import config
from omni.inference import vega
from omni.registry import roles
from omni.workers import decide, decide_engines


def rendered(spec: dict) -> dict:
    return vega.question(spec)


class Families(unittest.TestCase):
    def test_each_repo_name_picks_its_engine_and_files(self):
        self.assertEqual(roles.backend("decide", "convaiinnovations/laya-multilingual"), "laya")
        self.assertEqual(roles.backend("decide", "fastino/GLiNER2.5-Decide"), "gliner2")
        self.assertEqual(roles.files("decide", "fastino/GLiNER2.5-Decide"), ["*.json", "model.safetensors"])

    def test_only_vega_reads_images(self):
        self.assertTrue(roles.reads_images("decide", "nvkudva/vega-onnx-q8"))
        self.assertFalse(roles.reads_images("decide", "convaiinnovations/laya"))
        self.assertFalse(roles.reads_images("decide", "fastino/GLiNER2.5-Decide"))
        self.assertTrue(roles.reads_images("stt", "openai/whisper-small"))

    def test_a_missing_engine_package_names_the_extra_that_brings_it(self):
        with patch("importlib.util.find_spec", return_value=None):
            with self.assertRaisesRegex(config.ConfigError, "media extra"):
                roles.require_backend_deps("decide", "fastino/GLiNER2.5-Decide")
            roles.require_backend_deps("decide", "nvkudva/vega-onnx-q8")


class LayaQuestions(unittest.TestCase):
    def test_questions_go_to_laya_in_its_own_shape(self):
        choice = rendered({"type": "choice", "instructions": "Which?", "criteria": {"a": "first", "b": None}})
        self.assertEqual(decide_engines.Laya._question(choice)["criteria"], {"a": "first", "b": "b"})
        plain = rendered({"type": "choice", "instructions": "Which?", "criteria": ["a", "b"]})
        self.assertEqual(decide_engines.Laya._question(plain)["criteria"], ["a", "b"])
        score = rendered({"type": "score", "instructions": "How?", "criteria": ["low", "high"]})
        self.assertEqual(decide_engines.Laya._question(score)["criteria"], ["low", "high"])

    def test_probabilities_follow_the_option_order(self):
        noul = rendered({"type": "noul", "instructions": "Yes?"})
        self.assertEqual(decide_engines.Laya._probabilities(noul, {"noul": 0.75}), [0.25, 0.75])
        choice = rendered({"type": "choice", "instructions": "Which?", "criteria": ["a", "b"]})
        self.assertEqual(decide_engines.Laya._probabilities(choice, {"probabilities": {"b": 0.8, "a": 0.2}}), [0.2, 0.8])


class GlinerLabels(unittest.TestCase):
    def test_labels_keep_option_order_and_descriptions(self):
        choice = rendered({"type": "choice", "instructions": "Which?", "criteria": {"a": "first", "b": None}})
        self.assertEqual(decide_engines.Gliner._labels(choice), (["a", "b"], {"a": "first", "b": "b"}))
        score = rendered({"type": "score", "instructions": "How?", "criteria": ["low", "high"]})
        self.assertEqual(decide_engines.Gliner._labels(score), (["0", "1"], {"0": "low", "1": "high"}))
        noul = rendered({"type": "noul", "instructions": "Yes?"})
        self.assertEqual(decide_engines.Gliner._labels(noul), (["no", "yes"], ["no", "yes"]))

    def test_scores_become_a_distribution_in_option_order(self):
        class Fake:
            def classify_text(self, text, tasks, include_confidence):
                return {"question": [{"label": "yes", "confidence": 3.0}, {"label": "no", "confidence": 1.0}]}

        engine = decide_engines.Gliner.__new__(decide_engines.Gliner)
        engine.model = Fake()
        self.assertEqual(engine._probabilities("text", rendered({"type": "noul", "instructions": "Yes?"})), [0.25, 0.75])


class TextOnlyEngines(unittest.TestCase):
    def test_an_image_is_refused_by_an_engine_that_reads_text_only(self):
        class TextOnly:
            reads_images = False

        with patch.object(decide.model, "use") as use:
            use.return_value.__enter__.return_value = TextOnly()
            with self.assertRaises(HTTPException) as caught:
                decide._decide_all("x", {}, b"png")
        self.assertEqual(caught.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
