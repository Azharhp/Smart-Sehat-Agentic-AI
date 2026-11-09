"""Recording inspection and immutable transcript-to-fact provenance.

Signal checks describe the file, never language accuracy or clinical validity.
"""
from __future__ import annotations
from array import array
import hashlib
import io
import math
import sys
import uuid
import wave

from pydantic import BaseModel, ConfigDict, Field


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def inspect_audio(content: bytes) -> dict:
    result = {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content), "warnings": []}
    try:
        with wave.open(io.BytesIO(content), "rb") as clip:
            rate, channels, width, frames = clip.getframerate(), clip.getnchannels(), clip.getsampwidth(), clip.getnframes()
            result.update({"format": "wav", "sample_rate": rate, "channels": channels,
                           "duration_seconds": round(frames / rate, 3)})
            raw = clip.readframes(frames)
            if len(raw) != frames * channels * width:
                result["warnings"].append("WAV payload is truncated; listen to or replace the original.")
            if width not in (1, 2) or not raw:
                result["warnings"].append("Signal level unavailable for this PCM encoding.")
                return result
            samples = array("B" if width == 1 else "h", raw)
            if width == 2 and sys.byteorder != "little":
                samples.byteswap()
            # Bounded sampling keeps inspection responsive on long recordings.
            values = samples[::max(1, len(samples) // 100000)]
            peak = 128 if width == 1 else 32768
            values = [v - 128 for v in values] if width == 1 else values
            rms = math.sqrt(sum(v * v for v in values) / len(values)) / peak
            result["rms_dbfs"] = round(20 * math.log10(rms), 1) if rms else None
            result["clipped_fraction"] = round(sum(abs(v) >= peak - 1 for v in values) / len(values), 4)
            result["silent"] = all(v == 128 for v in samples) if width == 1 else not any(samples)
            if result["silent"]:
                result["warnings"].append("No nonzero signal detected; record again or enter the story manually.")
            elif rms < .0032:
                result["warnings"].append("Very quiet signal; check that the words are audible.")
            if result["clipped_fraction"] > .01:
                result["warnings"].append("Possible clipping; check for distorted words and numbers.")
            if frames / rate < 1:
                result["warnings"].append("Recording is shorter than one second.")
    except (wave.Error, EOFError, ValueError, ZeroDivisionError):
        result.update({"format": "other or unreadable WAV", "warnings": ["Signal measurements unavailable for this encoding; listen to the original recording."]})
    return result


class StoryEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transcript_fact_id: str = Field(pattern=r"^F\d+$")
    transcript_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    language: str
    extraction_mode: str = Field(max_length=1000)
    baseline_fact_id: str | None = None
    recording_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    recording_generation: int = Field(default=0, ge=0)
    review_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class VoiceWorkflow:
    def attach_story_recording(self, cid: str, path: str, language: str, *, experimental: bool = False,
                               reviewer: str = "", automatic: bool = False) -> dict:
        from .languages import VOICE_LANGUAGES
        from .privacy import read_media
        from .store import now
        if language not in VOICE_LANGUAGES:
            raise ValueError("Choose the recording language.")
        if experimental and len(reviewer.strip()) < 2:
            raise ValueError("Experimental transcription requires a named bilingual reviewer.")
        quality = inspect_audio(read_media(path))
        if not quality["bytes"] or quality["bytes"] > 20 * 1024 * 1024:
            raise ValueError("Use a nonempty recording smaller than 20 MB.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._case(db, cid)
            row = db.execute("SELECT data FROM consultation_drafts WHERE case_id=? AND section='story'", (cid,)).fetchone()
            draft = self._decode(row["data"]) if row else {}
            generation = ((draft.get("recordings") or {}).get(language) or {}).get("generation", 0) + 1
            current_text = draft.get("transcript", "") if draft.get("transcript_language") == language else (draft.get("transcripts_by_language") or {}).get(language, "")
            recording = {"path": str(path), "language": language, "recording_sha256": quality["sha256"], "quality": quality,
                         "generation": generation, "text_at_request_sha256": text_hash(current_text)}
            proposals = dict(draft.get("transcription_proposals") or {})
            proposals.pop(language, None)
            draft = {**draft, "recordings": {**(draft.get("recordings") or {}), language: recording}, "transcription_proposals": proposals,
                     "transcription_status": {**(draft.get("transcription_status") or {}), language: "Original recording retained; automatic response pending or manual review required."}}
            db.execute("INSERT INTO consultation_drafts(case_id,section,data,updated) VALUES(?,?,?,?) ON CONFLICT(case_id,section) DO UPDATE SET data=excluded.data,updated=excluded.updated", (cid, "story", self._encode(draft), now()))
            self._event(db, cid, "AUDIO_ATTACHED", {**recording, "automatic_transcription_enabled": automatic, "experimental": experimental,
                                                   "bilingual_reviewer": reviewer.strip() or None})
            self._worker_media_changed(db, cid)
            return recording

    def save_transcription_draft(self, cid: str, language: str, recording: dict, text: str | None, provider: str, *, experimental: bool = False, reviewer: str = "") -> dict | None:
        from .store import now
        if text is not None and (not text.strip() or len(text) > 30000):
            raise ValueError("Transcription must contain 1 to 30,000 characters.")
        if not isinstance(provider, str) or len(provider) > 1000:
            raise ValueError("Invalid transcription provenance.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM consultation_drafts WHERE case_id=? AND section='story'", (cid,)).fetchone()
            draft = self._decode(row["data"]) if row else {}
            current = (draft.get("recordings") or {}).get(language) or {}
            if not current or any(current.get(k) != recording.get(k) for k in ("recording_sha256", "generation")):
                raise ValueError("A newer recording replaced this request. Its late transcript was discarded.")
            current_text = draft.get("transcript", "") if draft.get("transcript_language") == language else (draft.get("transcripts_by_language") or {}).get(language, "")
            if text_hash(current_text) != current["text_at_request_sha256"]:
                raise ValueError("The transcript was edited while transcription was running. Keep the worker's correction and retry if needed.")
            proposal = None
            if text is not None:
                identity = text_hash(str([current["recording_sha256"], current["generation"], language, text, provider]))
                proposal = {**current, "id": identity, "text": text.strip(), "provider": provider, "experimental": experimental}
                draft["transcription_proposals"] = {**(draft.get("transcription_proposals") or {}), language: proposal}
                draft["transcripts_by_language"] = {**(draft.get("transcripts_by_language") or {}), language: text.strip()}
                if draft.get("voice_language", language) == language:
                    draft.update({"transcript": text.strip(), "transcript_language": language, "voice_language": language})
                draft["interpreters"] = {**(draft.get("interpreters") or {}), language: reviewer.strip()}
                self._event(db, cid, "TRANSCRIPTION", {"provider": provider, "language": language, "path": current["path"], "recording_sha256": current["recording_sha256"],
                    "recording_generation": current["generation"], "proposal_id": identity, "experimental": experimental, "status": "unconfirmed editable draft"}, "ai")
            draft["transcription_status"] = {**(draft.get("transcription_status") or {}), language: "Unconfirmed automatic draft; check against the retained recording." if text is not None else provider}
            db.execute("UPDATE consultation_drafts SET data=?,updated=? WHERE case_id=? AND section='story'", (self._encode(draft), now(), cid))
            return proposal

    def _validate_story_evidence(self, db, cid: str, evidence: dict, quote: str | None, key: str) -> dict:
        parsed = StoryEvidence.model_validate(evidence).model_dump()
        values = {f["key"]: f for f in self._facts(db, cid)}
        transcript = values.get("transcript")
        if not transcript or transcript["id"] != parsed["transcript_fact_id"] or text_hash(transcript["value"]) != parsed["transcript_sha256"] or values.get("transcript_language", {}).get("value") != parsed["language"]:
            raise ValueError("The source transcript changed. Review it and extract fresh proposals.")
        if not quote or quote.casefold() not in transcript["value"].casefold():
            raise ValueError("A story proposal must quote the exact current transcript.")
        row = db.execute("SELECT data FROM consultation_drafts WHERE case_id=? AND section='story'", (cid,)).fetchone()
        draft = self._decode(row["data"]) if row else {}
        edited = draft.get("transcript") if draft.get("transcript_language") == parsed["language"] else (draft.get("transcripts_by_language") or {}).get(parsed["language"])
        if edited is not None and text_hash(edited.strip()) != parsed["transcript_sha256"]:
            raise ValueError("The editable transcript has changed. Check it and extract fresh proposals before confirming facts.")
        recording = (draft.get("recordings") or {}).get(parsed["language"]) or (draft.get("transcription_proposals") or {}).get(parsed["language"]) or {}
        if recording.get("recording_sha256") != parsed["recording_sha256"] or recording.get("generation", 0) != parsed["recording_generation"]:
            raise ValueError("The source recording changed. Review the current recording and extract fresh proposals.")
        reviews = (self._decode(r["data"]) for r in db.execute("SELECT data FROM consultation_events WHERE case_id=? AND type='STORY_TRANSCRIPT_REVIEWED' ORDER BY seq DESC", (cid,)))
        review = next((r for r in reviews if r.get("review_id") == parsed["review_id"]), None)
        if not review or any(review.get(k) != parsed[k] for k in ("transcript_fact_id", "transcript_sha256", "language", "recording_sha256", "recording_generation")):
            raise ValueError("Proposal evidence does not match a recorded exact-source worker review.")
        if values.get(key, {}).get("id") != parsed["baseline_fact_id"]:
            raise ValueError("This answer changed after extraction. Dismiss the old proposal and review the current answer.")
        return parsed

    def confirm_story(self, cid: str, text: str, language: str, *, reviewed: bool,
                      reviewer: str = "", proposal_id: str | None = None, expected_fact_id: str | None = None,
                      expected_recording: dict | None = None) -> dict:
        from .languages import VOICE_LANGUAGES
        if language not in VOICE_LANGUAGES or reviewed is not True or not text.strip() or len(text) > 30000:
            raise ValueError("Check the complete transcript and select its language before confirming it (maximum 30,000 characters).")
        if not VOICE_LANGUAGES[language]["stt"] and len(reviewer.strip()) < 2:
            raise ValueError("A regional transcript requires a named bilingual reviewer/interpreter.")
        text = text.strip()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            values = {f["key"]: f for f in self._facts(db, cid)}
            current = values.get("transcript")
            if (current["id"] if current else None) != expected_fact_id:
                raise ValueError("The transcript changed in another session. Reload before reviewing it.")
            row = db.execute("SELECT data FROM consultation_drafts WHERE case_id=? AND section='story'", (cid,)).fetchone()
            draft = self._decode(row["data"]) if row else {}
            proposal = (draft.get("transcription_proposals") or {}).get(language)
            if proposal_id and (not proposal or proposal.get("id") != proposal_id):
                raise ValueError("The recording proposal changed. Reload and review the latest draft.")
            recording = (draft.get("recordings") or {}).get(language) or proposal or {}
            expected_recording = expected_recording or {}
            if recording.get("recording_sha256") != expected_recording.get("recording_sha256") or recording.get("generation", 0) != expected_recording.get("generation", 0):
                raise ValueError("The recording changed since this screen was reviewed. Reload and check the current recording.")
            if proposal_id and any((proposal or {}).get(k, 0 if k == "generation" else None) != recording.get(k, 0 if k == "generation" else None) for k in ("recording_sha256", "generation")):
                raise ValueError("The automatic draft belongs to an older recording. Review the current recording and transcript.")
            reviews = [self._decode(r["data"]) for r in db.execute("SELECT data FROM consultation_events WHERE case_id=? AND type='STORY_TRANSCRIPT_REVIEWED' ORDER BY seq", (cid,))]
            previous_review = next((r for r in reversed(reviews) if current and r["transcript_fact_id"] == current["id"]), {})
            same = current and current["value"] == text and values.get("transcript_language", {}).get("value") == language and previous_review and previous_review.get("recording_sha256") == recording.get("recording_sha256") and previous_review.get("recording_generation", 0) == recording.get("generation", 0)
            fact = current if same else self._append(db, cid, "transcript", text)
            reopened = []
            if not same:
                for old in values.values():
                    if old.get("evidence") and old["evidence"]["transcript_fact_id"] != fact["id"]:
                        self._append(db, cid, old["key"], None, reason_missing="Source transcript corrected; recheck this story fact.")
                        reopened.append(old["id"])
            if reopened:
                self._event(db, cid, "STORY_FACTS_REOPENED", {"previous_fact_ids": reopened, "new_transcript_fact_id": fact["id"]})
            if values.get("transcript_language", {}).get("value") != language:
                self._append(db, cid, "transcript_language", language)
            if reviewer.strip():
                self._append(db, cid, "transcript_interpreter", {"name": reviewer.strip(), "recording_language": language, "identity_verified": False})
            elif values.get("transcript_interpreter", {}).get("value") is not None:
                self._append(db, cid, "transcript_interpreter", None, reason_missing="Direct transcript review; no interpreter recorded.")
            review_id = uuid.uuid4().hex
            self._event(db, cid, "STORY_TRANSCRIPT_REVIEWED", {"review_id": review_id,
                "baseline_fact_ids": {f["key"]: f["id"] for f in self._facts(db, cid)},
                "transcript_fact_id": fact["id"], "transcript_sha256": text_hash(text),
                "language": language, "reviewer": reviewer.strip() or "worker", "reviewer_identity_verified": False,
                "transcription_proposal_id": proposal_id, "recording_sha256": recording.get("recording_sha256"), "recording_generation": recording.get("generation", 0),
                "provider": (proposal or {}).get("provider") if proposal_id else "manual or browser draft",
                "check": "Worker reports checking whole transcript, including negatives, numbers, units and medicine names"})
            from .store import now
            draft = {**draft, "transcript": text, "transcript_language": language, "voice_language": language, "source_fact_id": fact["id"],
                     "transcripts_by_language": {**(draft.get("transcripts_by_language") or {}), language: text},
                     "interpreters": {**(draft.get("interpreters") or {}), language: reviewer.strip()}}
            db.execute("INSERT INTO consultation_drafts(case_id,section,data,updated) VALUES(?,?,?,?) ON CONFLICT(case_id,section) DO UPDATE SET data=excluded.data,updated=excluded.updated",
                       (cid, "story", self._encode(draft), now()))
            resolved = {self._decode(r["data"])["proposal_id"] for r in db.execute("SELECT data FROM consultation_events WHERE case_id=? AND type='PROPOSAL_RESOLVED'", (cid,))}
            for old in self._facts(db, cid, False):
                if not old["confirmed"] and old["id"] not in resolved and old.get("evidence", {}).get("transcript_fact_id") not in (None, fact["id"]):
                    self._event(db, cid, "PROPOSAL_RESOLVED", {"proposal_id": old["id"], "dismissed": True, "reason": "Source transcript corrected"})
            self._recompute(db, cid)
            return {**fact, "review_id": review_id}

    def story_proposal_issue(self, cid: str, proposal: dict) -> str | None:
        if not proposal.get("evidence"):
            return None
        try:
            with self.connect() as db:
                self._validate_story_evidence(db, cid, proposal["evidence"], proposal.get("quote"), proposal["key"])
        except ValueError as exc:
            return str(exc)
        return None

    def propose_story_facts(self, cid: str, transcript_fact_id: str, proposals: list[dict], mode: str, *, review_id: str) -> list[dict]:
        from .ai import filter_story_proposals
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            values = {f["key"]: f for f in self._facts(db, cid)}
            transcript = values.get("transcript")
            if not transcript or transcript["id"] != transcript_fact_id:
                raise ValueError("Transcript changed while AI was extracting. Review the current transcript and try again.")
            reviews = [self._decode(r["data"]) for r in db.execute("SELECT data FROM consultation_events WHERE case_id=? AND type='STORY_TRANSCRIPT_REVIEWED' ORDER BY seq", (cid,))]
            if not any(r["transcript_fact_id"] == transcript_fact_id and r["transcript_sha256"] == text_hash(transcript["value"]) for r in reviews):
                raise ValueError("Review the exact source transcript before proposing facts.")
            review = next(r for r in reversed(reviews) if r["transcript_fact_id"] == transcript_fact_id)
            if review.get("review_id") != review_id:
                raise ValueError("A newer transcript review replaced this extraction. Use the latest review.")
            accepted = filter_story_proposals(transcript["value"], proposals)
            created = []
            # An extraction may be retried; retire older proposals of this same
            # story rather than showing duplicate or contradictory Confirm buttons.
            resolved = {self._decode(r["data"])["proposal_id"] for r in db.execute("SELECT data FROM consultation_events WHERE case_id=? AND type='PROPOSAL_RESOLVED'", (cid,))}
            for old in self._facts(db, cid, False):
                if not old["confirmed"] and old["id"] not in resolved and old.get("evidence", {}).get("transcript_fact_id") == transcript_fact_id:
                    self._event(db, cid, "PROPOSAL_RESOLVED", {"proposal_id": old["id"], "dismissed": True, "reason": "Replaced by new extraction of same transcript"})
            for proposal in accepted:
                evidence = {"transcript_fact_id": transcript_fact_id, "transcript_sha256": text_hash(transcript["value"]),
                    "language": values["transcript_language"]["value"], "extraction_mode": mode,
                    "baseline_fact_id": review["baseline_fact_ids"].get(proposal["key"]),
                    "recording_sha256": review.get("recording_sha256"), "recording_generation": review.get("recording_generation", 0), "review_id": review_id}
                created.append(self._append(db, cid, **proposal, source="ai_proposed", confirmed=False, evidence=evidence))
            self._event(db, cid, "EXTRACTION_COMPLETED", {"mode": mode, "count": len(created), "rejected_count": len(proposals) - len(accepted),
                "transcript_fact_id": transcript_fact_id, "transcript_sha256": text_hash(transcript["value"]), "language": values["transcript_language"]["value"]}, "ai")
            return created
