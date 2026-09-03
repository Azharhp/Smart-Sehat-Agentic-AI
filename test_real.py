"""Real-mode integration test — requires API keys AND config agents:real.

SKIPS with a clear message if either is missing (exit 0). This test spends
real credits when it runs, so it is deliberately opt-in.
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SMARTSEHAT_DB",
                      os.path.join(tempfile.mkdtemp(prefix="smartsehat_real_"),
                                   "smartsehat.db"))

REPO = Path(__file__).resolve().parent
AUDIO = REPO / "assets" / "audio" / "tb_cough_fever.wav"
XRAY = REPO / "assets" / "xray" / "tb.jpg"
RX = REPO / "assets" / "rx" / "demo_rx.jpg"

REQUIRED_ENV = ["GROQ_API_KEY", "GOOGLE_API_KEY", "DASHSCOPE_API_KEY"]


def _skip(reason: str) -> int:
    print(f"[SKIP] test_real.py — {reason}")
    return 0


def _load_config_agents() -> str:
    try:
        import yaml
        with open(REPO / "config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return (cfg.get("agents") or "stub").lower()
    except Exception as e:
        print(f"[SKIP] cannot read config.yaml: {e}")
        return "stub"


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv()

    missing_keys = [k for k in REQUIRED_ENV if not os.getenv(k)]
    if missing_keys:
        return _skip(f"missing env keys: {missing_keys}. Add them to .env to run.")
    if _load_config_agents() != "real":
        return _skip("config.yaml has agents:stub — set agents:real to run.")

    for p in (AUDIO, XRAY, RX):
        if not p.exists():
            return _skip(f"missing demo asset: {p}")

    import seed
    from smartsehat import audit, corpus
    from smartsehat.graph import resume_case, run_case
    from smartsehat.router import ModelRouter

    seed.seed()
    results: list[tuple[str, bool, str]] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        results.append((name, cond, detail))
        print(f"[{'PASS' if cond else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")

    # -- Scenario A: full TB case (audio + x-ray)
    ckpt = os.path.join(os.path.dirname(os.environ["SMARTSEHAT_DB"]), "real_ckpt.db")
    case_id, thread_id, first = run_case(
        "PT-0001", "PM-2213",
        audio_path=str(AUDIO), image_path=str(XRAY),
        checkpoint_db=ckpt,
    )
    if "__interrupt__" not in first:
        check("real: first invoke reached transcript gate", False,
              f"result: {list(first.keys())}")
    else:
        check("real: first invoke reached transcript gate", True)

    stt_evs = [e for e in audit.get_case_timeline(case_id)
               if e["event_type"] in ("STT_RESULT", "STT_FAILED")]
    if stt_evs:
        first_stt = stt_evs[0]
        payload = json.loads(first_stt["payload_json"] or "{}")
        check("real: STT produced a non-empty transcript",
              first_stt["event_type"] == "STT_RESULT"
              and bool((payload.get("transcript") or "").strip()),
              f"provider={first_stt.get('provider_used')}, len={len(payload.get('transcript') or '')}")
    else:
        check("real: STT event was written", False)

    # Confirm the transcript verbatim, then approve.
    tx = json.loads(stt_evs[0]["payload_json"] or "{}").get("transcript") or ""
    resume_case(thread_id, action="confirm", transcript=tx, checkpoint_db=ckpt)

    # Now at HITL gate — pull the reasoning event before approving.
    reasoning_evs = [e for e in audit.get_case_timeline(case_id)
                     if e["event_type"] in ("REASONING_RESULT", "REASONING_DEGRADED")]
    if reasoning_evs and reasoning_evs[-1]["event_type"] == "REASONING_RESULT":
        payload = json.loads(reasoning_evs[-1]["payload_json"])
        ddx = payload.get("ddx") or []
        conf = payload.get("confidence")
        check("real: reasoning DDX parsed as a list", isinstance(ddx, list) and len(ddx) > 0,
              f"len={len(ddx)}")
        chunk_ids = set(corpus.get_chunk_map().keys())
        all_cits: list[str] = []
        for d in ddx:
            all_cits.extend(d.get("citations") or [])
        check("real: every citation is a real corpus id",
              all(c in chunk_ids for c in all_cits),
              f"citations={all_cits}")
        check("real: confidence is a float in [0,1]",
              isinstance(conf, (int, float)) and 0.0 <= float(conf) <= 1.0,
              f"conf={conf}")
    else:
        check("real: reasoning produced a REASONING_RESULT event", False,
              f"got={[e['event_type'] for e in reasoning_evs]}")

    vision_evs = [e for e in audit.get_case_timeline(case_id)
                  if e["event_type"] in ("VISION_FINDING", "VISION_FAILED")]
    if vision_evs and vision_evs[-1]["event_type"] == "VISION_FINDING":
        payload = json.loads(vision_evs[-1]["payload_json"])
        findings = payload.get("findings") or []
        check("real: vision produced a parseable findings list",
              isinstance(findings, list),
              f"n_findings={len(findings)}")
    else:
        check("real: vision produced a VISION_FINDING event", False)

    # Close the loop
    resume_case(thread_id, action="approve", checkpoint_db=ckpt)

    # -- Scenario B: prescription-image vision call
    router = ModelRouter()
    prescription_prompt = (
        "List every medicine you can see in this prescription image. "
        "Output ONLY JSON: {\"medicines\": [\"name1\", \"name2\", ...]}"
    )
    try:
        raw, spec = router.route_vision("vision_rx", str(RX), prescription_prompt)
        try:
            parsed = json.loads(raw)
        except Exception:
            import re as _re
            m = _re.search(r"\{.*\}", raw, _re.DOTALL)
            parsed = json.loads(m.group(0)) if m else {}
        meds = [str(m).lower() for m in (parsed.get("medicines") or [])]
        expected = ["paracetamol", "ibuprofen", "nimesulide", "amoxicillin",
                    "omeprazole", "multivitamin"]
        hits = sum(1 for e in expected if any(e in m for m in meds))
        check(f"real: prescription image identified >=4/6 expected medicines (got {hits})",
              hits >= 4,
              f"detected={meds} provider={spec}")
    except Exception as e:
        check("real: route_vision on demo_rx.jpg did not raise", False, str(e))

    fails = [n for n, ok, _ in results if not ok]
    print("\n===== SUMMARY =====")
    print(f"Total: {len(results)}  Passed: {len(results) - len(fails)}  Failed: {len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
