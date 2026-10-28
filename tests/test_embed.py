"""Embedding task prompts, input validation and the truncation that must not change."""
import unittest

from omni.inference import embed
from omni.schemas import EMBED_MAX_CHARS, EMBED_MAX_INPUTS


class Prompt(unittest.TestCase):
    def test_document(self):
        self.assertEqual(embed.prompt("hello", "document"), "title: none | text: hello")

    def test_query(self):
        self.assertEqual(embed.prompt("hello", "query"), "task: search result | query: hello")

    def test_code_retrieval(self):
        self.assertEqual(embed.prompt("code: def f()", "query"), "task: code retrieval | query: def f()")

    def test_already_has_a_task_prefix(self):
        self.assertEqual(embed.prompt("task: custom | query: x", "query"), "task: custom | query: x")
        self.assertEqual(embed.prompt("title: t | text: x", "document"), "title: t | text: x")

    def test_truncates_to_the_hyperlight_length(self):
        got = embed.prompt("x" * (EMBED_MAX_CHARS + 500), "document")
        self.assertEqual(len(got), len("title: none | text: ") + EMBED_MAX_CHARS)


class Inputs(unittest.TestCase):
    def test_string_becomes_one_item(self):
        self.assertEqual(embed._texts({"input": "hi"}), ["hi"])

    def test_list_is_kept(self):
        self.assertEqual(embed._texts({"input": ["a", "b"]}), ["a", "b"])

    def test_bad_inputs_raise(self):
        for payload in ({}, {"input": []}, {"input": [""]}, {"input": [1]}, {"input": 3}):
            with self.assertRaises(ValueError):
                embed._texts(payload)

    def test_too_many_inputs(self):
        with self.assertRaises(ValueError):
            embed._texts({"input": ["x"] * (EMBED_MAX_INPUTS + 1)})


class Images(unittest.TestCase):
    def test_text_input_is_not_images(self):
        self.assertIsNone(embed._images({"input": "hi"}))
        self.assertIsNone(embed._images({"input": ["a", "b"]}))

    def test_data_url_is_stripped_to_base64(self):
        self.assertEqual(embed._images({"input": [{"image": "data:image/png;base64,AAA"}, {"image": "BBB"}]}), ["AAA", "BBB"])
        self.assertEqual(embed._images({"input": {"image": "CCC"}}), ["CCC"])

    def test_mixed_or_malformed_images_raise(self):
        for payload in ({"input": [{"image": "A"}, "text"]}, {"input": [{"image": ""}]}, {"input": [{"photo": "A"}]}):
            with self.assertRaises(ValueError):
                embed._images(payload)


class RunValidation(unittest.TestCase):
    def test_bad_input_type_fails_before_any_call(self):
        import asyncio
        with self.assertRaises(ValueError):
            asyncio.run(embed.run(object(), "m", {"input": "x", "input_type": "nope"}))

    def test_empty_input_fails_before_any_call(self):
        import asyncio
        with self.assertRaises(ValueError):
            asyncio.run(embed.run(object(), "m", {"input": []}))


if __name__ == "__main__":
    unittest.main()
