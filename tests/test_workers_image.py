"""The image worker reads a GGUF repo's file and base, and passes the checkpoint's own sigma schedule."""
import unittest
from unittest.mock import patch

from omni.registry import huggingface
from omni.workers import gguf, image


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


class DefaultSteps(unittest.TestCase):
    def steps(self, family):
        with patch.object(image, "family", family):
            return image._default_steps()

    def test_each_family_runs_the_sampler_it_was_trained_for(self):
        self.assertEqual(self.steps("sdxl-lightning"), 4)
        self.assertEqual(self.steps("stable-diffusion-v1-5"), 25)

    def test_qwen_uses_the_configured_step_count(self):
        self.assertEqual(self.steps("qwen-image-2"), image.default_steps)


class Source(unittest.TestCase):
    def source(self, manifest, fetch=lambda download: "local"):
        with patch.object(huggingface, "load_manifest", return_value=manifest), \
                patch.object(huggingface, "bundle") as bundle, \
                patch.object(huggingface, "save_manifest") as save, \
                patch.object(gguf, "_fetch", side_effect=fetch):
            bundle.return_value = [huggingface.Part("u/q-gguf", ["m-Q8_0.gguf"], 7), huggingface.Part("q/base", ["vae/*"], 3)]
            return gguf.source("image", "u/q-gguf"), bundle, save

    def test_a_pulled_bundle_loads_from_its_manifest_without_the_hub(self):
        manifest = [huggingface.Part("u/q-gguf", ["m-Q8_0.gguf"], 7), huggingface.Part("q/base", ["vae/*"], 3)]
        paths, bundle, _ = self.source(manifest)
        self.assertEqual(paths, ("local", "local"))
        bundle.assert_not_called()

    def test_a_model_nobody_pulled_is_resolved_once_and_remembered(self):
        paths, bundle, save = self.source(None)
        self.assertEqual(paths, ("local", "local"))
        bundle.assert_called_once()
        save.assert_called_once()

    def test_a_plain_checkpoint_has_no_gguf_parts(self):
        paths, _, _ = self.source([huggingface.Part("Qwen/Qwen-Image-2.1", None, 9)])
        self.assertEqual(paths, (None, None))


class Checkpoint(unittest.TestCase):
    def test_a_unet_only_repo_gives_its_weights_and_the_base_folder(self):
        manifest = [huggingface.Part("b/lightning", ["unet.safetensors"], 5), huggingface.Part("s/base", ["vae/*"], 2)]
        with patch.object(huggingface, "load_manifest", return_value=manifest), \
                patch.object(gguf, "_fetch", side_effect=lambda download: "local"):
            self.assertEqual(gguf.checkpoint("image", "b/lightning"), ("local", "local"))


class Fetch(unittest.TestCase):
    def test_the_cached_copy_is_used_first(self):
        calls = []

        def download(**kwargs):
            calls.append(kwargs)
            return "path"

        self.assertEqual(gguf._fetch(download), "path")
        self.assertEqual(calls, [{"local_files_only": True}])

    def test_a_missing_copy_is_downloaded(self):
        def download(local_files_only=False):
            if local_files_only:
                raise OSError("not cached")
            return "downloaded"

        self.assertEqual(gguf._fetch(download), "downloaded")


