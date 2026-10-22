"""The Hugging Face search maps Hub records to the rows the playground table renders."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from huggingface_hub.errors import CacheNotFound

from omni.registry import huggingface


class FakeRepo:
    def __init__(self, id, downloads=0, likes=0, pipeline_tag=None, safetensors=None, gguf=None):
        self.id = id
        self.downloads = downloads
        self.likes = likes
        self.pipeline_tag = pipeline_tag
        self.safetensors = safetensors
        self.gguf = gguf


class FakeApi:
    def __init__(self, repos):
        self.repos = repos

    def list_models(self, **kwargs):
        return self.repos


class FakeSibling:
    def __init__(self, name, size):
        self.rfilename = name
        self.size = size


class FakeInfo:
    def __init__(self, siblings):
        self.siblings = siblings


class FakeSizeApi:
    def __init__(self, siblings):
        self.siblings = siblings

    def model_info(self, repo_id, files_metadata=False):
        return FakeInfo(self.siblings)


class DownloadPlan(unittest.TestCase):
    def plan(self, role, siblings):
        with patch.object(huggingface, "HfApi", return_value=FakeSizeApi(siblings)):
            return huggingface.download_plan(role, "unsloth/Qwen-Image-2.1-Turbo-GGUF")

    def test_a_quant_repo_downloads_only_the_eight_bit_file(self):
        plan = self.plan("image", [FakeSibling("turbo-F16.gguf", 14_230_000_000),
                                   FakeSibling("turbo-Q4_K_M.gguf", 4_200_000_000),
                                   FakeSibling("turbo-Q8_0.gguf", 7_640_000_000)])
        self.assertEqual(plan.files, ["turbo-Q8_0.gguf"])
        self.assertEqual(plan.size, 7_640_000_000)

    def test_other_roles_still_take_the_whole_repo(self):
        plan = self.plan("video", [FakeSibling("transformer.safetensors", 9_970_000_000),
                                   FakeSibling("vae.safetensors", 810_000_000)])
        self.assertIsNone(plan.files)
        self.assertEqual(plan.size, 10_780_000_000)

    def test_an_image_repo_without_a_gguf_downloads_everything(self):
        plan = self.plan("image", [FakeSibling("transformer/a.safetensors", 9_970_000_000)])
        self.assertIsNone(plan.files)
        self.assertEqual(plan.size, 9_970_000_000)


class Search(unittest.TestCase):
    def search(self, repos, role="image", query=""):
        with patch.object(huggingface, "HfApi", return_value=FakeApi(repos)):
            return huggingface.search(role, query)

    def test_bf16_params_become_bytes(self):
        rows = self.search([FakeRepo("Qwen/Qwen-Image-2.1-Turbo",
                                     safetensors={"parameters": {"BF16": 7115124736}, "total": 7115124736})])
        self.assertEqual(rows[0]["size_bytes"], 7115124736 * 2)

    def test_gguf_file_size_wins_over_params(self):
        rows = self.search([FakeRepo("some/Qwen-Image-2.1-Q8",
                                     safetensors={"parameters": {"BF16": 7115124736}, "total": 7115124736},
                                     gguf={"total": 7115124736, "totalFileSize": 2553333472})],
                           role="chat", query="qwen")
        self.assertEqual(rows[0]["size_bytes"], 2553333472)

    def test_missing_figures_are_none_not_zero(self):
        rows = self.search([FakeRepo("Qwen/Qwen-Image-2.1-Turbo")])
        self.assertIsNone(rows[0]["size_bytes"])


class MissingCache(unittest.TestCase):
    def setUp(self):
        huggingface.invalidate()
        self.addCleanup(huggingface.invalidate)

    def test_a_machine_with_no_cache_has_no_models(self):
        with tempfile.TemporaryDirectory() as empty, \
                patch.object(huggingface, "scan_cache_dir",
                             side_effect=CacheNotFound("missing", cache_dir=Path(empty) / "nope")):
            self.assertEqual(huggingface.installed(force=True), [])

    def test_deleting_from_a_missing_cache_is_not_found(self):
        with patch.object(huggingface, "scan_cache_dir", side_effect=CacheNotFound("missing", cache_dir="x")), \
                self.assertRaises(FileNotFoundError):
            huggingface.delete("a/b")
