"""Gated Hugging Face repos surface as a structured accept-terms event, not a raw upstream error."""
import unittest

from omni.registry import pulling


class Gated(unittest.TestCase):
    def test_gated_event_carries_model_and_accept_url(self):
        event = pulling.gated_event("dgrauet/ltx-2.5-mlx-q4")
        self.assertEqual(event["code"], "gated")
        self.assertEqual(event["model"], "dgrauet/ltx-2.5-mlx-q4")
        self.assertEqual(event["accept_url"], "https://huggingface.co/dgrauet/ltx-2.5-mlx-q4")
        self.assertIn("terms accepted", event["error"])

    def test_is_gated_matches_hf_wording(self):
        self.assertTrue(pulling.is_gated(OSError("Cannot access gated repo for url https://huggingface.co/x/y")))
        self.assertTrue(pulling.is_gated(OSError("Access is restricted and you are not in the authorized list")))
        self.assertFalse(pulling.is_gated(OSError("network is unreachable")))


if __name__ == "__main__":
    unittest.main()
