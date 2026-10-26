"""The Hugging Face search maps Hub records to the rows the playground table renders."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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


class Bundle(unittest.TestCase):
    def bundle(self, role, siblings, pinned=None):
        with patch.object(huggingface, "HfApi", return_value=FakeSizeApi(siblings)), \
                patch.object(huggingface, "_pinned", return_value=pinned or {}), \
                patch.object(huggingface, "base_repo", return_value="Qwen/Qwen-Image-2.1-Turbo"), \
                patch.object(huggingface, "diffusers_files", side_effect=lambda repo: (repo, ["vae/*"])):
            return huggingface.bundle(role, "unsloth/Qwen-Image-2.1-Turbo-GGUF")

    def test_a_quant_repo_takes_the_eight_bit_file_and_the_base_behind_it(self):
        parts = self.bundle("image", [FakeSibling("turbo-F16.gguf", 14_230_000_000),
                                      FakeSibling("turbo-Q8_0.gguf", 7_640_000_000),
                                      FakeSibling("vae/model.safetensors", 300)])
        self.assertEqual(parts[0], huggingface.Part("unsloth/Qwen-Image-2.1-Turbo-GGUF", ["turbo-Q8_0.gguf"], 7_640_000_000))
        self.assertEqual(parts[1].repo, "Qwen/Qwen-Image-2.1-Turbo")
        self.assertEqual(parts[1].size, 300)

    def test_the_config_pins_win_over_discovery(self):
        parts = self.bundle("video", [FakeSibling("a-Q8_0.gguf", 1), FakeSibling("own.gguf", 2)],
                            pinned={"file": "own.gguf", "base": "a/b"})
        self.assertEqual(parts[0].files, ["own.gguf"])
        self.assertEqual(parts[1].repo, "a/b")

    def test_a_plain_checkpoint_is_the_whole_repo(self):
        parts = self.bundle("video", [FakeSibling("transformer.safetensors", 9_970_000_000),
                                      FakeSibling("vae.safetensors", 810_000_000)])
        self.assertEqual(parts, [huggingface.Part("unsloth/Qwen-Image-2.1-Turbo-GGUF", None, 10_780_000_000)])

    def test_a_speech_repo_takes_only_the_files_its_runtime_reads(self):
        parts = self.bundle("tts", [FakeSibling("config.json", 1), FakeSibling("tokenizer.json", 1),
                                    FakeSibling("onnx/model_quantized.onnx", 90), FakeSibling("onnx/model.onnx", 300),
                                    FakeSibling("voices/af.bin", 5)])
        self.assertEqual(parts[0].size, 97)


class HeldSize(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patcher = patch.object(huggingface.config, "BUNDLE_DIR", Path(folder.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(huggingface._cached_files.clear)

    def test_a_bundle_counts_only_once_every_part_is_cached(self):
        huggingface.save_manifest("image", "u/q-gguf", [huggingface.Part("u/q-gguf", ["m-Q8_0.gguf"], 7),
                                                        huggingface.Part("q/base", ["vae/*"], 3)])
        huggingface._cached_files["u/q-gguf"] = [("m-Q8_0.gguf", 7), ("m-F16.gguf", 14)]
        self.assertIsNone(huggingface.held_size("image", "u/q-gguf"))
        huggingface._cached_files["q/base"] = [("vae/a.safetensors", 3), ("transformer/x.safetensors", 40)]
        self.assertEqual(huggingface.held_size("image", "u/q-gguf"), 10)

    def test_a_diffusers_repo_without_a_manifest_is_not_runnable_yet(self):
        huggingface._cached_files["q/base"] = [("vae/a.safetensors", 3)]
        self.assertIsNone(huggingface.held_size("image", "q/base"))

    def test_a_speech_repo_needs_every_file_its_runtime_reads(self):
        huggingface._cached_files["k/kokoro"] = [("config.json", 1), ("tokenizer.json", 1)]
        self.assertIsNone(huggingface.held_size("tts", "k/kokoro"))
        huggingface._cached_files["k/kokoro"] += [("onnx/model_quantized.onnx", 90), ("voices/af.bin", 5)]
        self.assertEqual(huggingface.held_size("tts", "k/kokoro"), 97)


class BaseRepo(unittest.TestCase):
    def base(self, card):
        with patch.object(huggingface, "HfApi") as api:
            api.return_value.model_info.return_value.cardData = card
            return huggingface.base_repo("some/repo")

    def test_the_card_names_the_checkpoint_behind_the_quantized_repo(self):
        self.assertEqual(self.base({"base_model": "Qwen/Qwen-Image-2.1-Turbo"}), "Qwen/Qwen-Image-2.1-Turbo")

    def test_a_list_valued_base_model_uses_its_first_entry(self):
        self.assertEqual(self.base({"base_model": ["a/b", "c/d"]}), "a/b")

    def test_a_card_without_a_base_falls_back_to_the_repo(self):
        self.assertEqual(self.base(None), "some/repo")


class PreferredGguf(unittest.TestCase):
    def test_the_q8_file_is_preferred(self):
        self.assertEqual(huggingface.preferred_gguf(["m-Q4_K_M.gguf", "m-Q8_0.gguf"]), "m-Q8_0.gguf")

    def test_the_only_file_wins_when_it_is_not_q8(self):
        self.assertEqual(huggingface.preferred_gguf(["m-Q4_K_M.gguf", "README.md"]), "m-Q4_K_M.gguf")

    def test_no_gguf_is_none(self):
        self.assertIsNone(huggingface.preferred_gguf(["model.safetensors"]))


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

    def test_deleting_a_repo_drops_the_decide_workers_links_to_it(self):
        revision = SimpleNamespace(commit_hash="abc123")
        repo = SimpleNamespace(repo_id="a/vega", revisions=[revision])
        cache = MagicMock(repos=[repo])
        with tempfile.TemporaryDirectory() as home, \
                patch.object(huggingface, "scan_cache_dir", return_value=cache), \
                patch.object(huggingface.config, "HOME", Path(home)), \
                patch.object(huggingface.config, "BUNDLE_DIR", Path(home) / "bundles"):
            links = Path(home) / "decide" / "abc123"
            links.mkdir(parents=True)
            (links / "embed_q8.onnx").write_bytes(b"x")
            huggingface.delete("a/vega")
            self.assertFalse(links.exists())
        cache.delete_revisions.assert_called_once_with("abc123")


class FolderSize(unittest.TestCase):
    def test_a_finished_blob_linked_into_a_shared_store_still_counts(self):
        with tempfile.TemporaryDirectory() as root:
            shared = Path(root) / "blobs" / "ab"
            shared.mkdir(parents=True)
            (shared / "done").write_bytes(b"x" * 100)
            repo = Path(root) / "models--a--b"
            (repo / "blobs").mkdir(parents=True)
            (repo / "blobs" / "done").symlink_to(shared / "done")
            (repo / "blobs" / "other").symlink_to(shared / "done")
            (repo / "blobs" / "partial.incomplete").write_bytes(b"y" * 7)
            self.assertEqual(huggingface.folder_size(repo), 107)
