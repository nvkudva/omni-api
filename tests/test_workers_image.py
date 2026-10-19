"""The image worker passes the checkpoint's own sigma schedule when it fits the step count."""
import unittest

from omni.workers import image


class SavedSigmas(unittest.TestCase):
    def test_matching_step_count_uses_the_saved_schedule(self):
        image._saved_sigmas = [1.0, 0.9, 0.8]
        try:
            self.assertEqual(image._sigmas_for(3), [1.0, 0.9, 0.8])
        finally:
            image._saved_sigmas = None

    def test_other_step_counts_fall_back_to_the_pipeline_default(self):
        image._saved_sigmas = [1.0, 0.9, 0.8]
        try:
            self.assertIsNone(image._sigmas_for(8))
        finally:
            image._saved_sigmas = None

    def test_no_saved_schedule_means_no_override(self):
        image._saved_sigmas = None
        self.assertIsNone(image._sigmas_for(8))
