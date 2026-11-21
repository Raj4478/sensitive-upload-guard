# Demo video script (about 5 minutes, Windows)

The brief asks for the required functionality on **both** ChatGPT and Perplexity. File uploads need a **logged-in** account on both sites, so log in first.

## Before recording

1. `python -m pip install -r requirements.txt`, then `python -m upload_guard install`.
2. In `chrome://extensions`: Developer mode → Load unpacked → `extension` folder. Pin the shield icon.
3. Run `python samples/make_samples.py`, and open the `samples` folder in Explorer so you can drag files from it.
4. Log in to chatgpt.com and perplexity.ai in two tabs. Turn on Windows Do Not Disturb.

## Scenes and narration

**1. Intro (0:00–0:30)**: show the README diagram.
> "This is Sensitive Upload Guard, a lightweight desktop DLP. A small browser extension holds each prompt and upload on ChatGPT and Perplexity, and a Python desktop app inspects it for the marker WALLED_SENSITIVE. If it's found, a native Windows dialog asks Continue or Cancel."

**2. Connected (0:30–0:45)**: click the shield icon.
> "The popup confirms the desktop app is connected through Chrome's native messaging. Here I can also set the response colour."

**3. ChatGPT, clean prompt (0:45–1:05)**: type `What is the capital of France?` and press Enter.
> "A normal prompt goes straight through: no dialog, no delay. And the answer appears in the new colour; that's the response recolouring."

**4. ChatGPT, sensitive prompt (1:05–1:50)**: type `Summarise this WALLED_SENSITIVE roadmap`, press Enter.
> "This prompt contains the marker. It's held before it's sent, and a native Windows dialog appears. It says it's a text prompt on ChatGPT and shows the prompt." Click **Cancel**. "Cancel: nothing was sent, and my text is still in the box." Press Enter again, click **Continue**. "Continue sends the original prompt unchanged."

**5. ChatGPT, files (1:50–2:50)**
- Attach `clean.pdf`. "Clean file: uploads normally."
- Attach `sensitive.txt`, then Cancel. "Text file with the marker: the dialog names the file. Cancel, and it's not attached."
- Attach `sensitive.pdf`, then Continue. "PDF: found on page 2. Continue uploads it unchanged."
- Drag `sensitive.png` into the chat, then Cancel. "Image: the text is read with Windows' built-in OCR. Drag-and-drop is covered too."

**6. Perplexity (2:50–4:10)**: repeat briefly:
- a clean prompt, and point out the recoloured answer
- `WALLED_SENSITIVE` prompt → Cancel → Continue
- `sensitive-scanned.pdf` → dialog "page 1 image (OCR)" → Cancel ("a scanned PDF with no text layer")
- `clean.png` → uploads with no dialog

**7. Under the hood (4:10–4:50)**
- Run `python -m upload_guard check samples\*`. "The same engine from the terminal."
- Open `%APPDATA%\UploadGuard\audit.log`. "Every decision is logged with a hash, never the content."
- Optionally, run `node run.mjs --live` results. "23 automated browser tests against the live sites, all passing."

**8. Close (4:50–5:00)**
> "The code, setup and architecture notes are on GitHub. Thanks for watching."

## Upload

YouTube **Unlisted**, or Google Drive "Anyone with the link". Open the link in a private window to confirm it plays **without signing in**; the brief counts sign-in-gated links as not submitted.
