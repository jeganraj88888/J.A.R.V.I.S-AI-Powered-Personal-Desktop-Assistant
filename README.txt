J.A.R.V.I.S — FINAL FIXED BUILD

This build keeps the original orange cinematic HUD.

Backend fixes:
- One persistent Gemini client for the Flask process.
- One automatic retry if the SDK reports a closed client.
- Memory diagnostic uses the real SQLite file check.
- Gemini 3.5 Flash-Lite text streaming with minimal thinking.
- Edge neural voice preserved.

Keep your existing astra.db in the folder. It is NOT included here.

Run:
  run_jarvis.bat

Open:
  http://127.0.0.1:5000
