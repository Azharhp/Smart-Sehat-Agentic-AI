# Five-minute fictional Ayaan demo

Start with `./run.ps1` on Windows, or `sh run.sh` on Linux/macOS. Use two tabs on the same local server: one on Consultation, the other on Doctor console. A phone must reach the laptop's LAN IP and port 8501; venue internet is unnecessary for the default offline model mode. The phone still needs its connection to the laptop.

1. **0:00–0:30:** Explain that the worker collects and confirms, rules protect, and the doctor makes treatment decisions. All data is fictional and delivery is simulated.
2. **0:30–1:20:** Create a case to show age units, consent and danger cards, or load fictional Ayaan to skip typing. Show the typed transcript/recording fallback and proposed-fact confirmation. Do not promise measured speech support.
3. **1:20–2:10:** Show the browser breath counter (a real minute takes a real minute; use the loaded RR 46 for the timed demo). Show SpO2 94. Open Dawa Check in the saved consultation steps to show Panadol + Calpol and the duplicate ingredient flag.
4. **2:10–2:40:** Alternatively start from “Load Ayaan with wrong age”. Correct 14 years to 14 months in the correction panel. The case changes from GREEN to AMBER; saved facts remain and old output is stale.
5. **2:40–3:50:** Generate a packet, confirm patient and worker read-back, and send. In Doctor console acknowledge and send “Recheck SpO2”. The worker's sent view receives the task. Enter 89; RED interrupts. Create an oxygen-capable simulated referral/pre-alert immediately. In Doctor console save advice/referral and Urdu patient instructions.
6. **3:50–4:15:** Open Patient card, download the print-ready pictogram HTML and show simulated Day-2 follow-up. A new danger sign re-escalates RED.
7. **4:15–4:45:** Generate a new packet if needed, inject a contradiction, and show that it is BLOCKED. Re-generate to clear the injected demo claim. Show the synthetic results and the pending Clinical Content Register.
8. **4:45–5:00:** Close: “SmartSehat makes sure that when a doctor is reached, they already have the whole case.”

Before presenting: save the standalone offline form locally; warm the transcript cache (`python -m scripts.warm_consultation_cache`); rehearse on the actual phone; record a backup video with your screen recorder. No backup video or actual device rehearsal has been produced by the software test suite. Keep a copy of `docs/vignette_results.md` on the presentation laptop.
