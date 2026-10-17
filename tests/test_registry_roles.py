"""Which repos each role's runtime will accept: the cached-repo filter and the search filters agree."""
import unittest

from omni.registry import roles


class Families(unittest.TestCase):
    def test_every_hugging_face_role_states_the_marker_its_runtime_needs(self):
        for role in roles.HF_ROLES:
            self.assertTrue(roles.family(role), role)

    def test_ollama_roles_are_only_filtered_by_pipeline(self):
        for role in roles.OLLAMA_ROLES:
            self.assertIsNone(roles.family(role), role)


class Supports(unittest.TestCase):
    def test_the_shipped_defaults_are_loadable_by_their_role(self):
        defaults = {
            "stt": "mlx-community/parakeet-tdt-0.6b-v3",
            "tts": "prince-canuma/Kokoro-82M-4bit",
            "image": "Qwen/Qwen-Image-2.1",
            "video": "dgrauet/ltx-2.5-mlx-q4",
        }
        for role, repo in defaults.items():
            self.assertTrue(roles.supports(role, repo), role)

    def test_image_no_longer_accepts_any_repo_that_merely_says_qwen_image(self):
        self.assertFalse(roles.supports("image", "someone/Qwen-Image-XL"))
        self.assertTrue(roles.supports("image", "Qwen/Qwen-Image-2.1"))

    def test_an_ollama_role_accepts_anything_the_search_already_filtered(self):
        self.assertTrue(roles.supports("chat", "qwen2.5:3b"))
        self.assertTrue(roles.supports("embed", "embeddinggemma-2"))

    def test_an_unrelated_repo_is_refused(self):
        self.assertFalse(roles.supports("video", "Qwen/Qwen-Image-2.1"))


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

    def test_bare_id_strips_the_hf_prefix_and_tag(self):
        self.assertEqual(roles.bare_repo_id("hf.co/mradermacher/OneJev1.5-0.8B-GGUF:Q8_0"),
                         "mradermacher/OneJev1.5-0.8B-GGUF")


class Matches(unittest.TestCase):
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
