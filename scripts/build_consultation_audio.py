"""Prepare fixed question/danger prompt audio; no patient data is processed.

Generated recordings remain unreviewed. Playback never implies native-speaker
or clinical approval. Explicit --live is required to call the configured service.
"""
import argparse
import time
import json
import subprocess
import tempfile
from pathlib import Path
from smartsehat_workspace.consultation.clinical import config
from smartsehat_workspace.consultation.questions import QUESTIONS
from smartsehat_workspace.consultation.speech_audio import synthesize, text_identity, save_generated_prompt, recorded_prompt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=["English", "Urdu"], default="Urdu")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--provider", choices=["gemini", "windows", "espeak"], default="gemini")
    parser.add_argument("--scope", choices=["all", "danger", "questions"], default="all", help="Prioritize danger wording when generation capacity is limited.")
    parser.add_argument("--allow-local-script", action="store_true", help="Permit only the speech-helper child process to execute the local script; never changes system policy.")
    parser.add_argument("--limit", type=int, default=1, help="Maximum fixed prompts in this run; choose a larger explicit limit for a full bundle.")
    parser.add_argument("--pause-seconds", type=float, default=8, help="Pacing between generated prompts; cached prompts never wait.")
    args = parser.parse_args()
    if not 1 <= args.limit <= 200:
        parser.error("Choose a limit from 1 to 200.")
    if not 0 <= args.pause_seconds <= 60:
        parser.error("Choose pacing from 0 to 60 seconds.")
    field = "text_ur" if args.language == "Urdu" else "text_en"
    prompts = []
    if args.scope in ("all", "danger"):
        for group in config()["danger_signs"].values():
            prompts += [(q["id"], q["ur" if args.language == "Urdu" else "en"]) for q in group]
    if args.scope in ("all", "questions"):
        prompts += [(q["id"], q[field]) for q in QUESTIONS]
    unique = list(dict.fromkeys(text for _, text in prompts))[:args.limit]
    if args.provider in ("windows", "espeak"):
        pending = [text for text in unique if recorded_prompt(text, args.language) is None and synthesize(text, args.language)[0] is None]
        if pending:
            with tempfile.TemporaryDirectory(prefix="smartsehat-fixed-speech-") as folder:
                manifest = Path(folder) / "prompts.json"
                manifest.write_text(json.dumps([{"id": text_identity(text, args.language), "text": text} for text in pending], ensure_ascii=False), encoding="utf-8")
                if args.provider == "windows":
                    policy = ["-ExecutionPolicy", "Bypass"] if args.allow_local_script else []
                    command = ["powershell", "-NoProfile", "-NonInteractive", *policy, "-File", str(Path(__file__).with_name("render_fixed_speech.ps1")),
                               "-Manifest", str(manifest), "-Output", folder, "-Language", args.language]
                    origin = "Windows installed matching-language speech voice; unreviewed"
                else:
                    command = ["node", str(Path(__file__).parent / "offline-speech/render.mjs"), str(manifest), folder, args.language]
                    origin = "eSpeak NG offline matching-language synthetic voice; pronunciation and clinical review pending"
                completed = subprocess.run(command, capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if completed.returncode:
                    print("No matching local voice could prepare this bundle. No other language was substituted.")
                    print(completed.stderr.strip()[:1200])
                    return 1
                for text in pending:
                    content = (Path(folder) / (text_identity(text, args.language) + ".wav")).read_bytes()
                    save_generated_prompt(text, args.language, content, origin)
        print(f"Prepared/reused {len(unique)} {args.language} fixed prompts locally. No network calls or patient mutations. Human language/wording review remains pending.")
        return 0
    for index, text in enumerate(unique, 1):
        path, mode = synthesize(text, args.language, args.live)
        print(f"{index}/{len(unique)} {args.language}: {mode}", flush=True)
        if path is None:
            return 1
        if mode.startswith("Generated") and index < len(unique):
            time.sleep(args.pause_seconds)
    print("Prepared encrypted, content-bound fixed prompts. No patient case was changed. Human language/wording review remains pending.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
