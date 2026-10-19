"""The Hugging Face search maps Hub records to the rows the playground table renders."""
import unittest
from unittest.mock import patch

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
