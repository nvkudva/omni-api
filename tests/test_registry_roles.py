"""Which repos each role's runtime will accept: the cached-repo filter and the search filters agree."""
import unittest
import unittest.mock

from omni import config
from omni.registry import roles


class Families(unittest.TestCase):
    def test_every_hugging_face_role_states_the_marker_its_runtime_needs(self):
        for role in roles.HF_ROLES:
            self.assertTrue(roles.family(role), role)

    def test_ollama_roles_are_only_filtered_by_pipeline(self):
        for role in roles.OLLAMA_ROLES:
            self.assertFalse(roles.family(role), role)


class Supports(unittest.TestCase):
    def test_the_shipped_defaults_are_loadable_by_their_role(self):
        defaults = {
            "decide": "nvkudva/vega-onnx-q8",
            "stt": "istupakov/parakeet-tdt-0.6b-v2-onnx",
            "tts": "onnx-community/Kokoro-82M-v1.0-ONNX",
            "image": "Qwen/Qwen-Image-2.1-Turbo",
            "video": "QuantStack/Wan2.2-TI2V-5B-GGUF",
        }
        for role, repo in defaults.items():
            self.assertTrue(roles.supports(role, repo), role)

    def test_image_no_longer_accepts_any_repo_that_merely_says_qwen_image(self):
        self.assertFalse(roles.supports("image", "someone/Qwen-Image-XL"))
        self.assertTrue(roles.supports("image", "Qwen/Qwen-Image-2.1"))
        self.assertTrue(roles.supports("image", "Qwen/Qwen-Image-2.1-Turbo"))

    def test_an_ollama_role_accepts_anything_the_search_already_filtered(self):
        self.assertTrue(roles.supports("chat", "qwen2.5:3b"))
        self.assertTrue(roles.supports("embed", "embeddinggemma-2"))

    def test_an_unrelated_repo_is_refused(self):
        self.assertFalse(roles.supports("video", "Qwen/Qwen-Image-2.1"))


class Require(unittest.TestCase):
    def test_the_shipped_defaults_and_a_loadable_pin_pass(self):
        self.assertIsNone(roles.require("decide", "nvkudva/vega-onnx-q8"))
        self.assertIsNone(roles.require("stt", "hf.co/istupakov/parakeet-tdt-0.6b-v2-onnx:Q8_0"))

    def test_the_old_ollama_era_decide_pin_is_a_config_error(self):
        with self.assertRaisesRegex(config.ConfigError, "decide.model"):
            roles.require("decide", "hf.co/mradermacher/OneJev1.5-0.8B-GGUF:Q8_0")


class Workers(unittest.TestCase):
    def test_a_role_that_is_not_a_python_worker_is_always_available(self):
        self.assertIsNone(roles.unavailable_reason("chat"))

    def test_every_media_role_states_the_dependencies_it_needs(self):
        for role in roles.PYTHON_WORKERS:
            self.assertIn(role, roles.MEDIA_DEPS)

    def test_a_role_with_its_dependencies_installed_is_available_on_any_platform(self):
        with unittest.mock.patch.object(roles.sys, "platform", "linux"), \
             unittest.mock.patch.object(roles.importlib.util, "find_spec", return_value=object()):
            for role in ("stt", "tts", "video"):
                self.assertIsNone(roles.unavailable_reason(role), role)

    def test_the_image_worker_stays_on_metal(self):
        with unittest.mock.patch.object(roles.sys, "platform", "linux"), \
             unittest.mock.patch.object(roles.importlib.util, "find_spec", return_value=object()):
            self.assertIn("Metal", roles.unavailable_reason("image"))

    def test_a_missing_dependency_reads_as_not_installed(self):
        with unittest.mock.patch.object(roles.importlib.util, "find_spec", return_value=None):
            self.assertIn("media extra", roles.unavailable_reason("stt"))


class Normalize(unittest.TestCase):
    def test_a_model_page_url_becomes_the_repo_id(self):
        self.assertEqual(roles.normalize_repo_id("https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v3"),
                         "mlx-community/parakeet-tdt-0.6b-v3")

    def test_a_tree_suffix_is_dropped(self):
        self.assertEqual(roles.normalize_repo_id("huggingface.co/Qwen/Qwen-Image-2.1/tree/main"),
                         "Qwen/Qwen-Image-2.1")

    def test_an_ollama_hf_url_keeps_its_prefix_and_tag(self):
        self.assertEqual(roles.normalize_repo_id("hf.co/mradermacher/OneJev1.5-0.8B-GGUF:Q8_0"),
                         "hf.co/mradermacher/OneJev1.5-0.8B-GGUF:Q8_0")

    def test_hf_co_tree_url_is_dropped(self):
        self.assertEqual(roles.normalize_repo_id("https://hf.co/user/repo/tree/main"), "hf.co/user/repo")

    def test_bare_id_strips_the_hf_prefix_and_tag(self):
        self.assertEqual(roles.bare_repo_id("hf.co/mradermacher/OneJev1.5-0.8B-GGUF:Q8_0"),
                         "mradermacher/OneJev1.5-0.8B-GGUF")


class Matches(unittest.TestCase):
    def test_matches_tolerates_non_strings(self):
        self.assertFalse(roles.matches(123, "qwen2.5:3b"))
        self.assertFalse(roles.matches(None, None))

    def test_latest_is_the_same_model(self):
        self.assertTrue(roles.matches("embeddinggemma-2:latest", "embeddinggemma-2"))
        self.assertTrue(roles.matches("qwen2.5:3b", "qwen2.5:3b"))
        self.assertFalse(roles.matches("embeddinggemma-2:latest", "qwen2.5:3b"))
        self.assertEqual(roles.canonical("qwen2.5:latest"), "qwen2.5")


class SearchFilters(unittest.TestCase):
    def test_chat_is_pinned_to_text_generation_so_gguf_noise_is_filtered_out(self):
        self.assertEqual(roles.SEARCH_FILTERS["chat"]["pipeline_tag"], "text-generation")

    def test_every_role_has_a_filter(self):
        for role in roles.ROLES:
            self.assertIn(role, roles.SEARCH_FILTERS)


if __name__ == "__main__":
    unittest.main()


class DoctorRuntimes(unittest.TestCase):
    def test_every_worker_runtime_present_is_ok(self):
        from omni.commands import doctor
        with unittest.mock.patch.object(doctor.registry, "unavailable_reason", return_value=None):
            level, _, detail = doctor._check_runtimes()
        self.assertEqual(level, doctor.OK)
        self.assertIn("image", detail)

    def test_a_missing_runtime_is_a_warning_with_the_fix(self):
        from omni.commands import doctor
        reason = "video cannot run: this install has no mlx"
        with unittest.mock.patch.object(doctor.registry, "unavailable_reason", side_effect=lambda role: reason if role == "video" else None):
            level, _, detail = doctor._check_runtimes()
        self.assertEqual(level, doctor.WARN)
        self.assertTrue(detail.startswith("video cannot run here."))
        self.assertIn(reason, detail)


class Incompatible(unittest.TestCase):
    def test_mlx_weights_are_never_offered(self):
        self.assertFalse(roles.supports("tts", "mlx-community/Kokoro-82M-bf16"))
        self.assertFalse(roles.supports("video", "baa-ai/LTX-2.3-22B-RAM-12GB-MLX"))
