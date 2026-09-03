"""Qwen ASR endpoint test — v3: correct DashScope multimodal format.
Error taught us: type must be 'audio' (not 'file'), field 'audio' (not 'file_url').
"""
from __future__ import annotations
import base64
import json
import os

from dotenv import load_dotenv

load_dotenv()
import requests

KEY = os.environ.get("DASHSCOPE_API_KEY")
AUDIO = "assets/audio/tb_cough_fever.wav"

CANDIDATES = [
    ("private-deployment",
     "https://ws-ah8evok5ajq7vsm0.ap-southeast-1.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"),
    ("dashscope-intl",
     "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"),
]

if not KEY:
    print("[SKIP] DASHSCOPE_API_KEY missing in .env")
    raise SystemExit(0)

with open(AUDIO, "rb") as f:
    b64 = base64.b64encode(f.read()).decode("ascii")

payload = {
    "model": "qwen3-asr-flash",
    "input": {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "audio", "audio": "data:audio/wav;base64," + b64},
                ],
            }
        ]
    },
}
headers = {"Authorization": f"Bearer {KEY}",
           "Content-Type": "application/json"}

winner = None
for name, url in CANDIDATES:
    print(f"\n=== Trying {name}: {url}")
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=120)
        print("HTTP", r.status_code)
        try:
            print(json.dumps(r.json(), ensure_ascii=False, indent=2)[:800])
        except Exception:
            print(r.text[:800])
        if r.status_code == 200:
            winner = (name, url)
            break
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {str(e)[:200]}")

print("\n" + "=" * 60)
if winner:
    print(f"WINNER: {winner[0]}")
    print(f"Put this in config.yaml -> endpoints.qwen_asr:")
    print(f'  qwen_asr: "{winner[1]}"')
else:
    print("No endpoint returned HTTP 200. Paste BOTH outputs above to your")
    print("advisor — the 4xx error text names the exact request-field fix.")