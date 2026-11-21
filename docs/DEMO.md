# Demo video script (about 5½ minutes, Windows)

Show that it's a **standalone desktop app**: no extension anywhere. File uploads need a logged-in account on both sites.

## Before recording

1. In the project folder: `.venv\Scripts\activate`, then `python -m upload_guard`. In the window, click **Install certificate** (confirm Windows' prompt), then **Turn protection on**. Check that the pill says **Protected**.
2. In `chrome://extensions`, make sure no Upload Guard extension is loaded (remove the old one if it's there).
3. Open chatgpt.com and perplexity.ai in your normal Chrome, logged in, and open the `samples` folder in Explorer.
4. Turn on Do Not Disturb.

## Scenes and narration

**1. Intro (0:00–0:35)**: show the README diagram.
> "This is Sensitive Upload Guard, a standalone desktop DLP. There's no browser extension. A local inspecting proxy sees everything my browser sends to ChatGPT and Perplexity, checks it for WALLED_SENSITIVE before it leaves the computer, and asks in a native Windows dialog. It also recolours the AI's answers."

**2. The app (0:35–1:15)**: show the Upload Guard window.
> "The app starts its inspection engine. The two one-time steps: I trusted its local certificate, which is unique to this PC, and turned protection on. That routes only ChatGPT and Perplexity through it; everything else goes direct. Both are reversible here." Show `chrome://extensions`: "and no extension is installed."

**3. ChatGPT clean prompt (1:15–1:35)**: `What is the capital of France? Answer in one sentence.`
> "A clean prompt goes straight through, and the answer is recoloured, injected by the proxy, not an extension."

**4. ChatGPT sensitive prompt (1:35–2:20)**: `Summarise this roadmap: WALLED_SENSITIVE Project Falcon launches in March.`
> "The send request is held at the proxy, and a native Task Dialog shows the prompt." **Cancel**. "It never left the PC, and ChatGPT keeps my text in the box." Press Enter again, then **Continue**. "Continue forwards the original request unchanged."

**5. ChatGPT files (2:20–3:20)**
- `clean.pdf`: "uploads with no dialog."
- `sensitive.txt`, then **Cancel**: "the upload bytes were inspected before transmission; blocked."
- `sensitive.pdf`, then **Continue**: "found on page 2; uploaded unchanged."
- Drag `sensitive.png` in, then **Cancel**: "text inside an image, read with Windows OCR."

**6. Perplexity (3:20–4:30)**: a clean prompt (recoloured answer); a `WALLED_SENSITIVE` prompt with **Continue**; `sensitive-scanned.pdf` with **Cancel** ("a scanned PDF, page 1 image"); `clean.png` with no dialog.

**7. Activity and the hidden leak (4:30–5:10)**: show the app's activity list and counts.
> "Every decision is logged, with only a hash of the content. One thing a page-level tool would miss: Perplexity's analytics beacon also carries the prompt text. Upload Guard drops those unless I approved that exact text, so a cancelled prompt can't leak through telemetry either."

**8. Close (5:10–5:30)**: click **Turn protection off**, and show the tests passing (`python -m pytest -q`, and `tests/e2e/e2e-results.txt`).
> "Turning protection off restores my previous settings. Code, tests and architecture are on GitHub. Thanks for watching."

## Upload

YouTube **Unlisted**, or Google Drive "Anyone with the link". Check it plays in an Incognito window, with no sign-in.
