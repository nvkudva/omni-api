"""POST /v1/systemone on top of Ollama: each question is a lettered multiple-choice prompt, answered by the
next-token probabilities of the option letters (OneJev's qev-labels-v2 prompt, rendered as raw ChatML)."""
import asyncio
import json
import math

import httpx

from omni.errors import UpstreamError

LETTERS = "ABCDEFGHIJ"
TOP_LOGPROBS = 20
MAX_CONCURRENCY = 4
MAX_QUESTIONS = 32
MAX_STATE_CHARS = 20000
NOUL_WITHOUT_INSTRUCTIONS = "Is the statement true, or is the answer to the question yes?"
NOUL_DEFAULT_TRUE = "the statement is true / the answer is yes"
NOUL_DEFAULT_FALSE = "the statement is false / the answer is no"
SYSTEM_PROMPT = ("Apply the question to the state. Choose exactly one of the listed options. "
                 "Respond with only its uppercase letter, with no explanation or reasoning.")


def _text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _option(label, description) -> str:
    return label if description in (None, "") else f"{label}: {_text(description)}"


def question(spec: dict) -> dict:
    """A systemone question spec -> {type, question, options, names, legend}; ValueError when malformed."""
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
            instructions = NOUL_WITHOUT_INSTRUCTIONS
        options = [_option("yes", criteria.get("true") or NOUL_DEFAULT_TRUE),
                   _option("no", criteria.get("false") or NOUL_DEFAULT_FALSE)]
        return {"type": "noul", "question": _text(instructions), "options": options, "names": [True, False]}
    if instructions in (None, ""):
        raise ValueError("question without instructions")
    if kind == "choice":
        if isinstance(criteria, list):
            criteria = {str(name): None for name in criteria}
        if not isinstance(criteria, dict) or not 2 <= len(criteria) <= len(LETTERS):
            raise ValueError(f"choice criteria: a map or list of 2..{len(LETTERS)} options")
        names = list(criteria)
        options = [_option(name, criteria[name]) for name in names]
        return {"type": "choice", "question": _text(instructions), "options": options, "names": names}
    if kind == "score":
        if not isinstance(criteria, list) or not 2 <= len(criteria) <= len(LETTERS):
            raise ValueError(f"score criteria: an ordered list of 2..{len(LETTERS)} level descriptions")
        legend = [_text(level) for level in criteria]
        options = [f"level {index}: {level}" for index, level in enumerate(legend)]
        return {"type": "score", "question": _text(instructions), "options": options,
                "names": list(range(len(legend))), "legend": legend}
    raise ValueError(f"unknown question type {kind!r}")


def prompt(state: str, rendered: dict) -> str:
    count = len(rendered["options"])
    options = "\n".join(f"{LETTERS[index]}. {option}" for index, option in enumerate(rendered["options"]))
    header = "Rate the state:" if rendered["type"] == "score" else "Question:"
    user = (f"<state>\n{state}\n</state>\n\n{header} {rendered['question']}\n\nOptions:\n{options}\n\n"
            f"Answer with one letter: {', '.join(LETTERS[:count])}.")
    return (f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n<|im_start|>user\n{user}<|im_end|>\n"
            "<|im_start|>assistant\n<think>\n\n</think>\n\n")


def letter_probabilities(top_logprobs: list, count: int) -> list[float]:
    """Probability mass on each option letter among the top next tokens, renormalised; uniform if none appear."""
    probabilities = [0.0] * count
    for candidate in top_logprobs:
        if not isinstance(candidate, dict):
            continue
        token, logprob = candidate.get("token"), candidate.get("logprob")
        if not isinstance(token, str) or not isinstance(logprob, (int, float)) or isinstance(logprob, bool):
            continue
        letter = token.strip().upper()
        if len(letter) == 1 and letter in LETTERS[:count]:
            probabilities[LETTERS.index(letter)] += math.exp(logprob)
    total = sum(probabilities)
    if total == 0:
        return [1.0 / count] * count
    return [probability / total for probability in probabilities]


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


def answer(rendered: dict, probabilities: list[float]) -> dict:
    count = len(probabilities)
    best = max(range(count), key=probabilities.__getitem__)
    if rendered["type"] == "noul":
        return {"type": "noul", "noul": round(probabilities[0], 4)}
    if rendered["type"] == "choice":
        return {
            "type": "choice",
            "choice": rendered["names"][best],
            "confidence": round(_clip01((count * probabilities[best] - 1) / (count - 1)), 4),
            "x_p_max": round(probabilities[best], 4),
            "certainty": round(certainty(probabilities), 4),
            "probabilities": {name: round(p, 4) for name, p in zip(rendered["names"], probabilities)},
        }
    return {
        "type": "score",
        "score": round(sum(index * p for index, p in enumerate(probabilities)), 2),
        "confidence": round(score_confidence(probabilities), 4),
        "x_p_max": round(probabilities[best], 4),
        "certainty": round(certainty(probabilities), 4),
        "legend": {str(index): level for index, level in enumerate(rendered["legend"])},
        "probabilities": {str(index): round(p, 4) for index, p in enumerate(probabilities)},
    }


def top_logprobs(reply: dict) -> list:
    """The first-token logprob list from an Ollama reply, or [] when the reply has no usable logprobs."""
    entries = reply.get("logprobs")
    if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
        return []
    candidates = entries[0].get("top_logprobs")
    return candidates if isinstance(candidates, list) else []


async def _ask(client: httpx.AsyncClient, model: str, text: str) -> dict:
    response = await client.post("/api/generate", json={
        "model": model,
        "prompt": text,
        "raw": True,
        "stream": False,
        "logprobs": True,
        "top_logprobs": TOP_LOGPROBS,
        "options": {"num_predict": 1, "temperature": 0},
    })
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as error:
        raise UpstreamError("ollama returned a non-JSON decision") from error


async def run(client: httpx.AsyncClient, model: str, payload: dict) -> dict:
    """Score every question against the state; raises ValueError for a malformed request."""
    questions = payload.get("questions")
    if not isinstance(questions, dict) or not questions:
        raise ValueError("questions: a non-empty map of id -> question")
    if len(questions) > MAX_QUESTIONS:
        raise ValueError(f"questions: at most {MAX_QUESTIONS} per request")
    if "state" not in payload:
        raise ValueError("state is required")
    state = payload["state"]
    state = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False, indent=2)
    if len(state) > MAX_STATE_CHARS:
        raise ValueError(f"state: at most {MAX_STATE_CHARS} characters")
    rendered = {question_id: question(spec) for question_id, spec in questions.items()}

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def ask(item: dict) -> dict:
        async with semaphore:
            return await _ask(client, model, prompt(state, item))

    replies = await asyncio.gather(*(ask(item) for item in rendered.values()))
    answers = {}
    input_tokens = 0
    for (question_id, item), reply in zip(rendered.items(), replies):
        answers[question_id] = answer(item, letter_probabilities(top_logprobs(reply), len(item["options"])))
        input_tokens += reply.get("prompt_eval_count", 0) or 0
    output_tokens = len(replies)
    return {"model": model, "answers": answers,
            "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens,
                      "total_tokens": input_tokens + output_tokens}}
