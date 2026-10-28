"""The decide engines besides Vega: Laya and GLiNER2.5-Decide, each through its own package.

Both read text only and give a probability per option; the answer is built by `vega.answer`, so every
engine returns the same shape. Neither reports a prediction set or an unbound flag, so those stay empty.
"""
from pathlib import Path

from omni.inference import vega

# Laya's own "act" head says whether to decide or hand the question to a person
LAYA_ACT_THRESHOLD = 0.5


def _decision(probabilities: list[float], abstain: bool = False) -> dict:
    return {"probabilities": probabilities, "kept": None, "abstain": abstain, "unbound": False,
            "settle_steps": 0, "adapter": None}


def _normalized(scores: list[float]) -> list[float]:
    total = sum(scores)
    return [score / total for score in scores] if total > 0 else [1 / len(scores)] * len(scores)


class Laya:
    """Laya: an encoder with a decision head that answers every question of a request in one forward pass."""
    reads_images = False

    def __init__(self, folder: Path):
        import laya

        self.agent = laya.load(str(folder), device="cpu")

    @staticmethod
    def _question(rendered: dict) -> dict:
        question = {"type": rendered["type"], "instructions": rendered["ins"]}
        criteria = rendered["crit"]
        if rendered["type"] == "choice":
            described = any(description not in (None, "") for description in criteria.values())
            question["criteria"] = {name: description or name for name, description in criteria.items()} if described else list(criteria)
        elif rendered["type"] == "score":
            question["criteria"] = rendered["legend"]
        elif criteria:
            question["criteria"] = criteria
        return question

    @staticmethod
    def _probabilities(rendered: dict, answer: dict) -> list[float]:
        if rendered["type"] == "noul":
            yes = answer["noul"]
            return [1 - yes, yes]
        if rendered["type"] == "choice":
            return _normalized([answer["probabilities"][name] for name in rendered["names"]])
        return _normalized([answer["probabilities"][str(index)] for index in rendered["names"]])

    def decide_all(self, state_text: str, rendered: dict[str, dict]) -> tuple[dict, int]:
        result = self.agent.predict(state_text, {question_id: self._question(item) for question_id, item in rendered.items()})
        answers = {}
        for question_id, item in rendered.items():
            raw = result["answers"][question_id]
            act = raw.get("action", {}).get("act_probability", 1.0)
            answers[question_id] = vega.answer(item, _decision(self._probabilities(item, raw), act < LAYA_ACT_THRESHOLD))
        return answers, int(result.get("usage", {}).get("input_tokens", 0))


class Gliner:
    """GLiNER2.5-Decide: a label-set classifier. Asked for every label's softmax score, it gives a full distribution."""
    reads_images = False

    def __init__(self, folder: Path):
        from gliner2 import AutoExtractor

        self.model = AutoExtractor.from_pretrained(str(folder))

    @staticmethod
    def _labels(rendered: dict) -> tuple[list[str], dict | list]:
        """The label names in option order, and the labels as GLiNER takes them (named, with descriptions where given)."""
        criteria = rendered["crit"]
        if rendered["type"] == "choice":
            names = [str(name) for name in criteria]
            described = {str(name): description for name, description in criteria.items() if description not in (None, "")}
            return names, ({str(name): str(described.get(str(name), name)) for name in criteria} if described else names)
        if rendered["type"] == "score":
            names = [str(index) for index in rendered["names"]]
            return names, dict(zip(names, rendered["legend"], strict=True))
        no, yes = criteria.get("false"), criteria.get("true")
        if no or yes:
            return ["no", "yes"], {"no": str(no or "no"), "yes": str(yes or "yes")}
        return ["no", "yes"], ["no", "yes"]

    def _probabilities(self, state_text: str, rendered: dict) -> list[float]:
        names, labels = self._labels(rendered)
        task = {"labels": labels, "prompt": rendered["ins"], "multi_label": True, "cls_threshold": 0.0, "class_act": "softmax"}
        found = self.model.classify_text(state_text, {"question": task}, include_confidence=True)["question"]
        scores = {entry["label"]: entry["confidence"] for entry in found}
        return _normalized([scores.get(name, 0.0) for name in names])

    def decide_all(self, state_text: str, rendered: dict[str, dict]) -> tuple[dict, int]:
        # one call per question: scores of several questions in one call are not independent of each other
        answers = {question_id: vega.answer(item, _decision(self._probabilities(state_text, item)))
                   for question_id, item in rendered.items()}
        return answers, 0
