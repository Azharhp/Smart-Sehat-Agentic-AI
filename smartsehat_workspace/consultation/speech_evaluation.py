"""Exact speech/fact scoring for externally prepared, reported reference data.

No generated fixture is counted as native-speaker or clinical validation.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import date
import hashlib
import json
import math
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from .languages import VOICE_LANGUAGES

Language = Literal["Urdu", "English", "Sindhi", "Punjabi", "Pashto", "Saraiki", "Balochi"]


class FactLabel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    key: str = Field(min_length=1, max_length=100)
    value: bool | float | str | None
    unit: str | None = Field(default=None, max_length=50)


class SpeechSample(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    sample_id: str = Field(min_length=1, max_length=100)
    language: Language
    dialect: str = Field(min_length=1, max_length=100)
    recording_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source: Literal["synthetic", "consented-speaker"]
    speaker_id: str = Field(min_length=1, max_length=100)
    consent_recorded: bool
    consent_reference: str | None = Field(default=None, max_length=200)
    reference_reviewer: str = Field(min_length=2, max_length=200)
    reference_checked_against_audio: bool
    reviewed_on: date
    reference_transcript: str = Field(min_length=1, max_length=30000)
    hypothesis_transcript: str = Field(max_length=30000)
    provider: str = Field(min_length=1, max_length=150)
    model: str = Field(min_length=1, max_length=150)
    device: str = Field(min_length=1, max_length=200)
    noise: str = Field(min_length=1, max_length=200)
    network: str = Field(min_length=1, max_length=200)
    latency_ms: float = Field(ge=0, le=3600000)
    facts_annotated: bool = False
    reference_facts: list[FactLabel] = Field(default_factory=list, max_length=200)
    proposed_facts: list[FactLabel] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def checked(self):
        from .timeutils import local_today
        if self.source == "consented-speaker" and (self.consent_recorded is not True or len((self.consent_reference or "").strip()) < 2):
            raise ValueError("A speaker recording requires recorded consent.")
        if self.reference_checked_against_audio is not True or self.reviewed_on > local_today():
            raise ValueError("A real reviewer must check the reference against audio on a nonfuture date.")
        for facts in (self.reference_facts, self.proposed_facts):
            if len({f.key for f in facts}) != len(facts):
                raise ValueError("Fact labels require unique keys; record conflicting hypotheses as separate samples.")
        if not self.facts_annotated and (self.reference_facts or self.proposed_facts):
            raise ValueError("Mark facts_annotated true only after reviewing the complete reference fact labels.")
        if not normalise(self.reference_transcript).strip():
            raise ValueError("Reference must contain actual words.")
        return self


class SpeechDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["smartsehat-speech-evaluation-1"]
    samples: list[SpeechSample] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique(self):
        if len({s.sample_id for s in self.samples}) != len(self.samples):
            raise ValueError("Sample identifiers must be unique.")
        identities = {(s.recording_sha256, s.language, s.provider, s.model) for s in self.samples}
        if len(identities) != len(self.samples):
            raise ValueError("The same recording/language/model cannot be counted twice in one dataset.")
        return self


def normalise(text: str) -> str:
    """NFC/casefold, decimal digits to ASCII, punctuation to spaces.

    Preserve letter distinctions; no transliteration or dialect correction.
    """
    chars = []
    for char in unicodedata.normalize("NFC", text).casefold():
        if char.isdecimal():
            chars.append(str(unicodedata.decimal(char)))
        elif unicodedata.category(char)[0] in ("L", "N", "M"):
            chars.append(char)
        elif unicodedata.category(char) != "Cf":
            chars.append(" ")
    return " ".join("".join(chars).split())


def edit_distance(reference, hypothesis) -> int:
    """Myers bit-vector Levenshtein, exact for words or Unicode characters."""
    if not reference:
        return len(hypothesis)
    equal = {}
    for index, symbol in enumerate(reference):
        equal[symbol] = equal.get(symbol, 0) | (1 << index)
    positive, negative, score, high = ~0, 0, len(reference), 1 << (len(reference) - 1)
    for symbol in hypothesis:
        matches = equal.get(symbol, 0)
        vertical = matches | negative
        horizontal = (((matches & positive) + positive) ^ positive) | matches
        plus = negative | ~(horizontal | positive)
        minus = positive & horizontal
        score += bool(plus & high) - bool(minus & high)
        plus = (plus << 1) | 1
        minus <<= 1
        positive, negative = minus | ~(vertical | plus), plus & vertical
    return score


def _signature(label: FactLabel) -> tuple:
    # bool(false), zero, absent and unknown must stay distinct.
    value = label.value
    kind = "number" if type(value) in (float, int) else type(value).__name__
    return kind, value, label.unit


def score_sample(sample: SpeechSample) -> dict:
    reference, hypothesis = normalise(sample.reference_transcript), normalise(sample.hypothesis_transcript)
    ref_chars, hyp_chars = reference.replace(" ", ""), hypothesis.replace(" ", "")
    expected = {f.key: f for f in sample.reference_facts}
    proposed = {f.key: f for f in sample.proposed_facts}
    facts = {"matched": 0, "wrong_value_or_unit": 0, "omitted": 0, "spurious": 0,
             "negation_errors": 0, "number_or_unit_errors": 0, "medicine_label_errors": 0}
    for key, label in expected.items():
        if key not in proposed:
            facts["omitted"] += 1
        elif _signature(label) == _signature(proposed[key]):
            facts["matched"] += 1
        else:
            facts["wrong_value_or_unit"] += 1
            other = proposed[key]
            facts["negation_errors"] += type(label.value) is bool and type(other.value) is bool and label.value != other.value
            facts["number_or_unit_errors"] += type(label.value) in (float, int) or label.unit != other.unit
            facts["medicine_label_errors"] += "medicine" in key.casefold()
    facts["spurious"] = len(set(proposed) - set(expected))
    words = len(reference.split())
    word_errors = edit_distance(reference.split(), hypothesis.split())
    character_errors = edit_distance(ref_chars, hyp_chars)
    return {"sample_id": sample.sample_id, "recording_sha256": sample.recording_sha256, "source": sample.source,
        "language": sample.language, "dialect": sample.dialect, "provider": sample.provider, "model": sample.model,
        "device": sample.device, "noise": sample.noise, "network": sample.network, "latency_ms": sample.latency_ms,
        "reference_words": words, "word_errors": word_errors, "wer": word_errors / words,
        "reference_characters": len(ref_chars), "character_errors": character_errors,
        "cer": character_errors / len(ref_chars), "fact_labels": facts, "reference_fact_count": len(expected),
        "fact_evaluation_available": sample.facts_annotated}


def _aggregate(rows: list[dict]) -> dict:
    words, chars = sum(r["reference_words"] for r in rows), sum(r["reference_characters"] for r in rows)
    latency = sorted(r["latency_ms"] for r in rows)
    labels = {key: sum(r["fact_labels"][key] for r in rows) for key in rows[0]["fact_labels"]}
    return {"samples": len(rows), "unique_recordings": len({r["recording_sha256"] for r in rows}),
        "reference_words": words, "word_errors": sum(r["word_errors"] for r in rows),
        "wer": sum(r["word_errors"] for r in rows) / words,
        "reference_characters": chars, "cer": sum(r["character_errors"] for r in rows) / chars,
        "latency_p50_ms": latency[math.ceil(.5 * len(latency)) - 1], "latency_p95_ms": latency[math.ceil(.95 * len(latency)) - 1],
        "samples_with_fact_labels": sum(r["fact_evaluation_available"] for r in rows), "fact_labels": labels}


def evaluate_dataset(data: dict) -> dict:
    dataset = SpeechDataset.model_validate(data)
    rows = [score_sample(sample) for sample in dataset.samples]
    groups = defaultdict(list)
    dimensions = ("source", "language", "dialect", "provider", "model", "device", "noise", "network")
    for row in rows:
        groups[tuple(row[key] for key in dimensions)].append(row)
    canonical = json.dumps(dataset.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, allow_nan=False)
    return {"format": "smartsehat-speech-results-1", "dataset_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "reported_only": True, "reviewer_identity_verified": False, "clinical_validation_established": False,
        "normalisation": "Unicode NFC, casefold, decimal digits to ASCII, punctuation/space collapse; letter distinctions preserved; CER excludes spaces",
        "warning": "Reported reference quality and consent require external verification. Synthetic results never establish native-speaker accuracy. WER/CER alone do not establish clinical safety. No automatic language activation.",
        "groups": [{**dict(zip(dimensions, keys)), **_aggregate(group)} for keys, group in sorted(groups.items())], "samples": rows}


def evaluation_template() -> dict:
    return {"instructions": "Use consented/pseudonymised recordings and a real bilingual reviewer. Fill references independently of model hypotheses. Include negatives, duration, numbers/units, medicine names and code-switching. Record actual device/noise/network and end-to-end latency. An empty template is not an evaluation.",
        "dataset_template": {"format": "smartsehat-speech-evaluation-1", "samples": []},
        "schema": SpeechDataset.model_json_schema(), "languages": list(VOICE_LANGUAGES),
        "review_status": "Pending real language/device data"}


class SpeechEvaluationWorkflow:
    def speech_evaluations(self) -> list[dict]:
        with self.connect() as db:
            return [self._decode(row["data"]) for row in db.execute("SELECT data FROM consultation_speech_evaluations ORDER BY seq")]

    def report_speech_evaluation(self, dataset: dict) -> dict:
        parsed = SpeechDataset.model_validate(dataset).model_dump(mode="json")
        result = evaluate_dataset(parsed)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # A repeated upload is idempotent. References and results remain encrypted.
            existing = db.execute("SELECT 1 FROM consultation_speech_evaluations WHERE dataset_hash=?", (result["dataset_sha256"],)).fetchone()
            if not existing:
                db.execute("INSERT INTO consultation_speech_evaluations(dataset_hash,data) VALUES(?,?)", (result["dataset_sha256"], self._encode({"dataset": parsed, "results": result})))
        return result

    def speech_readiness(self) -> list[dict]:
        # Do not pool models/devices/dialects, or count duplicate uploads twice.
        samples = {}
        for report in self.speech_evaluations():
            for sample in report["dataset"]["samples"]:
                identity = (sample["recording_sha256"], sample["language"], sample["source"])
                samples.setdefault(identity, sample)
        return [{"language": language, "synthetic_recordings": sum(s["language"] == language and s["source"] == "synthetic" for s in samples.values()),
            "reported_speaker_recordings": sum(s["language"] == language and s["source"] == "consented-speaker" for s in samples.values()),
            "independent_language_validation": "pending"} for language in VOICE_LANGUAGES]
