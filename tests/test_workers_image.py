"""The image worker reads a GGUF repo's file and base, and passes the checkpoint's own sigma schedule."""
import unittest
from unittest.mock import patch

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


class Source(unittest.TestCase):
    def test_a_plain_checkpoint_loads_from_its_own_repo(self):
        with patch.object(image, "settings", {"image": {"model": "Qwen/Qwen-Image-2.1-Turbo"}}), \
             patch.object(image, "model_name", "Qwen/Qwen-Image-2.1-Turbo"), \
             patch.object(image, "_find_gguf", return_value=None):
            self.assertEqual(image._source(), (None, "Qwen/Qwen-Image-2.1-Turbo"))

    def test_a_gguf_repo_supplies_its_file_and_the_base_behind_it(self):
        with patch.object(image, "settings", {"image": {"model": "unsloth/Qwen-Image-2.1-Turbo-GGUF"}}), \
             patch.object(image, "model_name", "unsloth/Qwen-Image-2.1-Turbo-GGUF"), \
             patch.object(image, "_find_gguf", return_value="qwen-image-2.1-turbo-Q8_0.gguf"), \
             patch.object(image, "_base_repo", return_value="Qwen/Qwen-Image-2.1-Turbo"):
            self.assertEqual(image._source(),
                             ("qwen-image-2.1-turbo-Q8_0.gguf", "Qwen/Qwen-Image-2.1-Turbo"))

    def test_the_config_wins_over_discovery(self):
        settings = {"image": {"model": "x/y", "file": "own.gguf", "base": "a/b"}}
        with patch.object(image, "settings", settings), patch.object(image, "model_name", "x/y"), \
             patch.object(image, "_find_gguf") as find:
            self.assertEqual(image._source(), ("own.gguf", "a/b"))
            find.assert_not_called()


class FindGguf(unittest.TestCase):
    def test_the_q8_file_is_preferred(self):
        with patch("huggingface_hub.HfApi") as api:
            api.return_value.list_repo_files.return_value = ["m-Q4_K_M.gguf", "m-Q8_0.gguf"]
            self.assertEqual(image._find_gguf("some/repo"), "m-Q8_0.gguf")

    def test_the_only_file_wins_when_it_is_not_q8(self):
        with patch("huggingface_hub.HfApi") as api:
            api.return_value.list_repo_files.return_value = ["m-Q4_K_M.gguf"]
            self.assertEqual(image._find_gguf("some/repo"), "m-Q4_K_M.gguf")

    def test_an_unreachable_hub_reads_as_no_gguf(self):
        with patch("huggingface_hub.HfApi") as api:
            api.return_value.list_repo_files.side_effect = OSError("offline")
            self.assertIsNone(image._find_gguf("some/repo"))


class BaseRepo(unittest.TestCase):
    def test_the_card_names_the_checkpoint_behind_the_quantized_repo(self):
        with patch("huggingface_hub.HfApi") as api:
            api.return_value.model_info.return_value.cardData = {"base_model": "Qwen/Qwen-Image-2.1-Turbo"}
            self.assertEqual(image._base_repo("unsloth/Qwen-Image-2.1-Turbo-GGUF"), "Qwen/Qwen-Image-2.1-Turbo")

    def test_a_list_valued_base_model_uses_its_first_entry(self):
        with patch("huggingface_hub.HfApi") as api:
            api.return_value.model_info.return_value.cardData = {"base_model": ["a/b", "c/d"]}
            self.assertEqual(image._base_repo("some/repo"), "a/b")

    def test_a_card_without_a_base_falls_back_to_the_repo(self):
        with patch("huggingface_hub.HfApi") as api:
            api.return_value.model_info.return_value.cardData = None
            self.assertEqual(image._base_repo("some/repo"), "some/repo")
