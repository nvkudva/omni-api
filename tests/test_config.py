"""Config loading, validation, overrides and OLLAMA_HOST parsing."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omni import config


class Validate(unittest.TestCase):
    def test_real_catalog_is_valid(self):
        config.validate(config.load())

    def test_missing_section(self):
        settings = config.load()
        del settings["chat"]
        with self.assertRaises(config.ConfigError):
            config.validate(settings)

    def test_unknown_key(self):
        settings = config.load()
        settings["chat"]["mystery"] = 1
        with self.assertRaises(config.ConfigError):
            config.validate(settings)

    def test_an_image_gguf_names_its_file_and_base(self):
        settings = config.load()
        settings["image"].update({"model": "unsloth/Qwen-Image-2.1-Turbo-GGUF",
                                  "file": "qwen-image-2.1-turbo-Q8_0.gguf",
                                  "base": "Qwen/Qwen-Image-2.1-Turbo"})
        config.validate(settings)

    def test_an_optional_image_key_still_has_to_be_a_string(self):
        settings = config.load()
        settings["image"]["file"] = 8
        with self.assertRaises(config.ConfigError):
            config.validate(settings)

    def test_port_range(self):
        settings = config.load()
        settings["gateway"]["port"] = 0
        with self.assertRaises(config.ConfigError):
            config.validate(settings)

    def test_bool_is_not_an_int(self):
        settings = config.load()
        settings["gateway"]["port"] = True
        with self.assertRaises(config.ConfigError):
            config.validate(settings)

    def test_wrong_type(self):
        settings = config.load()
        settings["image"]["steps"] = "many"
        with self.assertRaises(config.ConfigError):
            config.validate(settings)

    def test_idle_and_steps_and_video(self):
        for mutate in (
            lambda s: s["gateway"].__setitem__("idle_unload_minutes", -1),
            lambda s: s["image"].__setitem__("steps", 0),
            lambda s: s["video"].__setitem__("fps", 0),
        ):
            settings = config.load()
            mutate(settings)
            with self.assertRaises(config.ConfigError):
                config.validate(settings)

    def test_idle_zero_is_valid_and_means_never(self):
        settings = config.load()
        settings["gateway"]["idle_unload_minutes"] = 0
        config.validate(settings)  # 0 is allowed: the model stays loaded forever
        self.assertEqual(config.idle_seconds(settings), 0)


class Overrides(unittest.TestCase):
    def test_save_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            user = Path(directory) / "config.toml"
            with patch.object(config, "USER_CONFIG", user):
                config.save_override("chat", "model", "my-chat")
                config.save_override("image", "steps", 12)
                loaded = config.load()
                self.assertEqual(loaded["chat"]["model"], "my-chat")
                self.assertEqual(loaded["image"]["steps"], 12)
                # the shipped value the override did not touch is still there
                self.assertEqual(loaded["video"]["fps"], config.load()["video"]["fps"])

    def test_save_override_rejects_unknown(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(config, "USER_CONFIG", Path(directory) / "config.toml"),
            self.assertRaises(config.ConfigError),
        ):
            config.save_override("chat", "nope", 1)

    def test_toml_value_rejects_none(self):
        with self.assertRaises(config.ConfigError):
            config.toml_value(None)


class Pins(unittest.TestCase):
    def test_a_missing_file_means_every_role_is_pinned(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(config, "PINS", Path(directory) / "pins.json"):
            self.assertEqual(config.load_pins(), list(config.ROLES))

    def test_a_saved_empty_choice_stays_empty(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(config, "PINS", Path(directory) / "pins.json"):
            config.save_pins([])
            self.assertEqual(config.load_pins(), [])

    def test_save_and_load_round_trip_in_role_order(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(config, "PINS", Path(directory) / "pins.json"):
            self.assertEqual(config.save_pins(["stt", "chat"]), ["chat", "stt"])
            self.assertEqual(config.load_pins(), ["chat", "stt"])

    def test_a_name_that_is_not_a_role_is_dropped(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(config, "PINS", Path(directory) / "pins.json"):
            self.assertEqual(config.save_pins(["stt", "nope"]), ["stt"])
            self.assertEqual(config.load_pins(), ["stt"])

    def test_a_corrupt_file_reads_as_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            pins = Path(directory) / "pins.json"
            pins.write_text("{not json")
            with patch.object(config, "PINS", pins):
                self.assertEqual(config.load_pins(), [])

    def test_a_file_of_the_wrong_shape_reads_as_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            pins = Path(directory) / "pins.json"
            pins.write_text('{"pinned": "stt"}')
            with patch.object(config, "PINS", pins):
                self.assertEqual(config.load_pins(), [])


class OllamaUrl(unittest.TestCase):
    def test_default(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(config.ollama_url(), f"http://127.0.0.1:{config.OLLAMA_DEFAULT_PORT}")

    def test_wildcard_becomes_loopback(self):
        with patch.dict("os.environ", {"OLLAMA_HOST": "0.0.0.0:9999"}):
            self.assertEqual(config.ollama_url(), "http://127.0.0.1:9999")

    def test_https_defaults_to_443(self):
        with patch.dict("os.environ", {"OLLAMA_HOST": "https://example.test"}):
            self.assertEqual(config.ollama_url(), "https://example.test:443")


if __name__ == "__main__":
    unittest.main()


class TrimLog(unittest.TestCase):
    def test_a_log_past_the_limit_keeps_only_its_newest_whole_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "w.log"
            line = b"x" * 99 + b"\n"
            log.write_bytes(line * (config.LOG_MAX_BYTES // len(line) + 10) + b"last\n")
            config.trim_log(log)
            data = log.read_bytes()
            self.assertLessEqual(len(data), config.LOG_KEEP_BYTES)
            self.assertTrue(data.endswith(b"last\n"))
            self.assertTrue(data.startswith(b"x" * 99 + b"\n"))

    def test_a_small_log_is_left_alone(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "w.log"
            log.write_bytes(b"a\nb\n")
            config.trim_log(log)
            self.assertEqual(log.read_bytes(), b"a\nb\n")
