"""Vega's decision pipeline, minus the model graphs: prompt, pooling and calibration.

A port of the browser runtime in nvkudva/vega-web (`app/src/vega/{prompt,decide,session}.ts`), itself a
port of the PyTorch reference. A frozen Qwen3.5-0.8B reads the state and one typed question; a small
physics engine turns the pooled hidden states into option logits; a per-type temperature and a
conformal threshold turn those into calibrated probabilities, a prediction set and an abstain flag.
Token-for-token equality with the reference prompt is the contract, so every slice bound and template
string below is load-bearing. Text states only (`views=1`); the image path is not ported.
"""
import json
import math

import numpy as np

QUESTION_TYPES = {"choice": 0, "score": 1, "boolean": 2}
MAX_OPTIONS = 14
HIDDEN_SIZE = 1024
MAX_INSTRUCTION_TOKENS = 256
MAX_PREAMBLE_TOKENS = 128
MIN_OPTION_TOKENS = 3
MIN_INSTRUCTION_TOKENS = 8
RESERVED_TOKENS = 16
TAU_RANGE = (0.2, 20.0)
DEFAULT_ALPHA = "0.1"

HEAD = "Observation:\n"
QUESTION = "\n\nMeasurement ({t}): {ins}\nPossible outcomes:"
OPTION = "\n* {o}"
END = "\nOutcome:"
QUERY_FIRST = "Question to keep in mind while reading ({t}): {ins}\n\n"


def serialize_state(state) -> str:
    return state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)


def _text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def question(spec: dict) -> dict:
    """A systemone question spec -> {type, ins, crit, names}; ValueError when malformed.

    `noul` is Vega's `boolean`: criteria may describe the true and false outcomes.
    """
    if not isinstance(spec, dict):
        # ValueError, not TypeError: the gateway maps it to a 400 with the same body as a bad field
        raise ValueError("each question: an object with type, instructions and criteria")  # noqa: TRY004
    kind = spec.get("type", "choice")
    criteria = spec.get("criteria")
    instructions = spec.get("instructions")
    if kind == "noul":
        if criteria is not None and not isinstance(criteria, dict):
            raise ValueError("noul criteria: a map of optional true/false descriptions")
        criteria = criteria or {}
        if instructions in (None, ""):
            if all(criteria.get(key) in (None, "") for key in ("true", "false")):
                raise ValueError("noul question without instructions: criteria must describe true or false")
            instructions = "Is the statement true, or is the answer to the question yes?"
        return {"type": "noul", "kind": "boolean", "ins": _text(instructions), "crit": criteria, "names": [True, False]}
    if instructions in (None, ""):
        raise ValueError("question without instructions")
    if kind == "choice":
        if isinstance(criteria, list):
            criteria = {str(name): None for name in criteria}
        if not isinstance(criteria, dict) or not 2 <= len(criteria) <= MAX_OPTIONS:
            raise ValueError(f"choice criteria: a map or list of 2..{MAX_OPTIONS} options")
        return {"type": "choice", "kind": "choice", "ins": _text(instructions), "crit": criteria, "names": list(criteria)}
    if kind == "score":
        if not isinstance(criteria, list) or not 2 <= len(criteria) <= MAX_OPTIONS:
            raise ValueError(f"score criteria: an ordered list of 2..{MAX_OPTIONS} level descriptions")
        legend = [_text(level) for level in criteria]
        return {"type": "score", "kind": "score", "ins": _text(instructions), "crit": criteria,
                "names": list(range(len(legend))), "legend": legend}
    raise ValueError(f"unknown question type {kind!r}")


def option_texts(rendered: dict) -> list[str]:
    """Option texts in label-index order. Boolean is always [no, yes], so index 1 is the probability of yes."""
    criteria = rendered["crit"]
    if rendered["kind"] == "choice":
        return [name if description in (None, "") else f"{name} - {_text(description)}"
                for name, description in criteria.items()]
    if rendered["kind"] == "score":
        return [f"level {index} - {_text(level)}" for index, level in enumerate(criteria)]
    no, yes = criteria.get("false"), criteria.get("true")
    return ["no - " + (_text(no) if no not in (None, "") else "the statement does not hold"),
            "yes - " + (_text(yes) if yes not in (None, "") else "the statement holds")]


def _fill(template: str, rendered: dict) -> str:
    return template.replace("{t}", rendered["kind"]).replace("{ins}", rendered["ins"])


def build_prompt(encode, state_text: str, rendered: dict, config: dict) -> dict:
    """Token ids and the spans pooling reads: state first, so its span never sees the question
    (causal attention), unless the state is short enough for the query-first preamble.

    `encode(text)` returns token ids with no BOS. A state that does not fit raises ValueError:
    a decision on a cut-off record is worse than none.
    """
    max_length = config["max_len"]
    options = option_texts(rendered)
    head = encode(HEAD)
    question_ids = encode(_fill(QUESTION, rendered))[:MAX_INSTRUCTION_TOKENS]
    option_ids = [encode(OPTION.replace("{o}", option))[:config["opt_max_tokens"]] for option in options]
    end = encode(END)
    fixed = len(head) + len(question_ids) + sum(len(ids) for ids in option_ids) + len(end)
    if fixed > max_length - RESERVED_TOKENS:
        # too many or too long options: shrink every option evenly, then the instructions
        per_option = max(MIN_OPTION_TOKENS, (max_length - RESERVED_TOKENS - len(head) - len(end) - min(len(question_ids), 64))
                         // max(1, len(option_ids)))
        option_ids = [ids[:per_option] for ids in option_ids]
        question_ids = question_ids[:max(MIN_INSTRUCTION_TOKENS, max_length - RESERVED_TOKENS - len(head) - len(end)
                                         - sum(len(ids) for ids in option_ids))]
        fixed = len(head) + len(question_ids) + sum(len(ids) for ids in option_ids) + len(end)
    state_ids = encode(state_text)
    preamble: list[int] = []
    if config["query_first_max_state"] and len(state_ids) < config["query_first_max_state"]:
        preamble = encode(_fill(QUERY_FIRST, rendered))[:min(MAX_PREAMBLE_TOKENS, MAX_INSTRUCTION_TOKENS)]
        fixed += len(preamble)
    room = max(0, max_length - fixed)
    if len(state_ids) > room:
        raise ValueError(f"state: {len(state_ids)} tokens, only {room} fit in this model's {max_length}")
    ids = [*preamble, *head]
    state_start = len(ids)
    ids += state_ids
    # an empty state still needs a span: fall back to the header tokens
    state_span = (state_start, len(ids)) if state_ids else (0, state_start)
    question_start = len(ids)
    ids += question_ids
    option_spans = []
    for piece in option_ids:
        start = len(ids)
        ids += piece
        option_spans.append((start, len(ids)))
    ids += end
    return {"ids": ids, "state": state_span, "question": (question_start, question_start + len(question_ids)),
            "options": option_spans}


def _mean(hidden: np.ndarray, start: int, stop: int) -> np.ndarray:
    return hidden[start:stop].mean(axis=0) if stop > start else np.zeros(HIDDEN_SIZE, dtype=np.float32)


def pool(config: dict, layer13: np.ndarray, layer19: np.ndarray, prompt: dict) -> dict:
    """The engine's inputs from two hidden-state layers, each shaped (tokens, hidden).

    Per layer: the state-span mean, the question-span mean and the final token. Per option: the span
    mean at the deepest layer. And the state cut into up to `memory_chunks` equal pieces, mean-pooled
    at the deepest layer, for the engine's memory reader.
    """
    last = len(prompt["ids"]) - 1
    features = np.stack([
        np.stack([_mean(layer, *prompt["state"]), _mean(layer, *prompt["question"]), layer[last]])
        for layer in (layer13, layer19)])
    options = np.stack([_mean(layer19, start, stop) for start, stop in prompt["options"]])
    chunk_count = config["memory_chunks"]
    chunks = np.zeros((chunk_count, HIDDEN_SIZE), dtype=np.float32)
    chunk_mask = np.zeros(chunk_count, dtype=bool)
    span_start, span_stop = prompt["state"]
    length = span_stop - span_start
    if length >= max(config["memory_min_tokens"], 2):
        used = max(2, min(chunk_count, length // max(1, config["memory_chunk_tokens"])))
        # token i of the span goes to chunk i * used // length
        owner = np.arange(length) * used // length
        for chunk in range(used):
            chunks[chunk] = layer19[span_start:span_stop][owner == chunk].mean(axis=0)
        chunk_mask[:used] = True
    return {"features": features, "options": options, "chunks": chunks, "chunk_mask": chunk_mask}


def route(gates: list[float], threshold: float) -> int:
    """The adapter whose gate is most open if it reaches the threshold, else -1 (the base engine)."""
    if not gates:
        return -1
    best = max(range(len(gates)), key=gates.__getitem__)
    return best if gates[best] >= threshold else -1


def calibrate(theta: list[list[float]], type_index: int, engine: dict, step_count: int) -> list[float]:
    """Thermal calibration: log tau = b + w . [log1p(kinetic), log1p(nearest), steps/n_steps, log k]."""
    logits = engine["logits"]
    physics = [math.log1p(engine["kinetic"]), math.log1p(engine["nearest"]),
               engine["steps"] / max(1, step_count), math.log(max(len(logits), 2))]
    weights = theta[type_index]
    log_tau = weights[0] + sum(weight * value for weight, value in zip(weights[1:], physics))
    tau = math.exp(min(max(log_tau, math.log(TAU_RANGE[0])), math.log(TAU_RANGE[1])))
    scaled = [value / tau for value in logits]
    peak = max(scaled)
    exponentials = [math.exp(value - peak) for value in scaled]
    total = sum(exponentials)
    return [value / total for value in exponentials]


def decide_one(config: dict, rendered: dict, engine: dict, alpha: str = DEFAULT_ALPHA) -> dict:
    """Calibrated probabilities for one question, in Vega's own label order, with its safety flags."""
    adapters = config["adapters"]
    adapter_index = route(engine["gates"], adapters[0]["gate_threshold"] if adapters else 0.5)
    calibration = config["calibration"] if adapter_index < 0 else adapters[adapter_index]["calibration"]
    probabilities = calibrate(calibration["theta"], QUESTION_TYPES[rendered["kind"]], engine, config["n_steps"])
    best = max(range(len(probabilities)), key=probabilities.__getitem__)
    confidence = probabilities[best]
    threshold = calibration["conformal"].get(rendered["kind"], {}).get(alpha)
    # the top option is always kept so the set is never empty
    kept = None if threshold is None else [
        index for index, p in enumerate(probabilities) if p >= max(1 - threshold, 1e-12) or index == best]
    risk = config["calibration"].get("default_risk", DEFAULT_ALPHA)
    return {
        "probabilities": probabilities,
        "kept": kept,
        "abstain": confidence < calibration["abstain_threshold"].get(risk, 0),
        "unbound": engine["nearest"] > calibration.get("unbound_threshold", math.inf),
        "settle_steps": engine["steps"],
        "adapter": adapters[adapter_index]["name"] if adapter_index >= 0 else None,
    }


def certainty(probabilities: list[float]) -> float:
    """1 - normalised entropy: 1 when all mass is on one option, 0 when flat."""
    entropy = -sum(p * math.log(p) for p in probabilities if p > 0)
    return max(0.0, 1.0 - entropy / math.log(len(probabilities)))


def _clip01(value: float) -> float:
    return min(1.0, max(0.0, value))


def score_confidence(probabilities: list[float]) -> float:
    """1 minus the expected distance from the likeliest level, over the mean distance of levels from the scale middle."""
    count = len(probabilities)
    likeliest = max(range(count), key=probabilities.__getitem__)
    spread = sum(p * abs(index - likeliest) for index, p in enumerate(probabilities))
    uniform = sum(abs(index - (count - 1) / 2) for index in range(count)) / count
    return _clip01(1.0 - spread / uniform) if uniform else 1.0


def answer(rendered: dict, decision: dict) -> dict:
    """The /v1/systemone answer for one question: the shape omni has always returned, plus Vega's flags."""
    probabilities = decision["probabilities"]
    count = len(probabilities)
    best = max(range(count), key=probabilities.__getitem__)
    extras = {"prediction_set": None if decision["kept"] is None else [_label(rendered, index) for index in decision["kept"]],
              "abstain": decision["abstain"], "unbound": decision["unbound"],
              "settle_steps": decision["settle_steps"], "adapter": decision["adapter"]}
    if rendered["type"] == "noul":
        return {"type": "noul", "noul": round(probabilities[1], 4), **extras}
    shared = {"x_p_max": round(probabilities[best], 4), "certainty": round(certainty(probabilities), 4)}
    if rendered["type"] == "choice":
        return {"type": "choice", "choice": rendered["names"][best],
                "confidence": round(_clip01((count * probabilities[best] - 1) / (count - 1)), 4), **shared,
                "probabilities": {name: round(p, 4) for name, p in zip(rendered["names"], probabilities)}, **extras}
    return {"type": "score", "score": round(sum(index * p for index, p in enumerate(probabilities)), 2),
            "confidence": round(score_confidence(probabilities), 4), **shared,
            "legend": {str(index): level for index, level in enumerate(rendered["legend"])},
            "probabilities": {str(index): round(p, 4) for index, p in enumerate(probabilities)}, **extras}


def _label(rendered: dict, index: int):
    """The name of option `index` in Vega's order; a boolean's order is [false, true]."""
    if rendered["type"] == "noul":
        return index == 1
    return rendered["names"][index]
