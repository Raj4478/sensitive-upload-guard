"""Read text out of images using the operating system's own OCR.

No extra install on the two main desktops:

* **Windows 10/11** - ``Windows.Media.Ocr`` (the engine behind the Snipping
  Tool's "Copy text"), called through Windows PowerShell 5.1, which can load
  WinRT types.
* **macOS** - the Vision framework (``VNRecognizeTextRequest``), called through
  JavaScript for Automation (``osascript -l JavaScript``).
* **Anywhere** - the ``tesseract`` command line, if installed, as a fallback.

Every backend gets the image as a temporary file and returns plain text.
Failures raise :class:`OCRUnavailable` so the caller can decide what to do
(the default policy treats an image it could not read as "needs a decision").
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30


class OCRUnavailable(RuntimeError):
    pass


_WINDOWS_PS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
function Await($op, [Type]$type) {
  $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result
}
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($env:UG_IMAGE)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$max = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
if ($bitmap.PixelWidth -gt $max -or $bitmap.PixelHeight -gt $max) {
  $scale = [Math]::Min($max / $bitmap.PixelWidth, $max / $bitmap.PixelHeight)
  $transform = New-Object Windows.Graphics.Imaging.BitmapTransform
  $transform.ScaledWidth = [uint32]($bitmap.PixelWidth * $scale)
  $transform.ScaledHeight = [uint32]($bitmap.PixelHeight * $scale)
  $bitmap = Await ($decoder.GetSoftwareBitmapAsync($decoder.BitmapPixelFormat, $decoder.BitmapAlphaMode,
    $transform, [Windows.Graphics.Imaging.ExifOrientationMode]::RespectExifOrientation,
    [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage)) ([Windows.Graphics.Imaging.SoftwareBitmap])
}
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { throw 'no OCR language installed' }
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
[Console]::OutputEncoding = [Text.Encoding]::UTF8
($result.Lines | ForEach-Object { $_.Text }) -join "`n"
"""

_MAC_JXA = r"""
ObjC.import('Vision'); ObjC.import('Foundation');
function run(argv) {
  const url = $.NSURL.fileURLWithPath(argv[0]);
  const req = $.VNRecognizeTextRequest.alloc.init;
  req.recognitionLevel = 0;            // accurate
  req.usesLanguageCorrection = false;  // keep tokens like WALLED_SENSITIVE intact
  const handler = $.VNImageRequestHandler.alloc.initWithURLOptions(url, $.NSDictionary.dictionary);
  if (!handler.performRequestsError($([req]), null)) throw new Error('Vision failed');
  const out = [];
  const results = req.results;
  for (let i = 0; i < results.count; i++) {
    out.push(results.objectAtIndex(i).topCandidates(1).objectAtIndex(0).string.js);
  }
  return out.join('\n');
}
"""


def _run(cmd: list[str], env: dict[str, str] | None = None) -> str:
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT_SECONDS, env=env, **kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OCRUnavailable(f"{cmd[0]}: {exc}") from exc
    if proc.returncode != 0:
        raise OCRUnavailable(f"{cmd[0]} failed: {proc.stderr.decode('utf-8', 'replace')[-300:]}")
    return proc.stdout.decode("utf-8", "replace")


def _windows(path: str) -> str:
    env = {**os.environ, "UG_IMAGE": path}
    return _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-Command", _WINDOWS_PS], env)


def _mac(path: str) -> str:
    return _run(["osascript", "-l", "JavaScript", "-e", _MAC_JXA, path])


def _tesseract(path: str) -> str:
    exe = shutil.which("tesseract")
    if not exe:
        raise OCRUnavailable("tesseract is not installed")
    return _run([exe, path, "stdout"])


def backends() -> list:
    order = []
    if sys.platform == "win32":
        order.append(_windows)
    elif sys.platform == "darwin":
        order.append(_mac)
    order.append(_tesseract)
    return order


def image_to_text(data: bytes, suffix: str = ".png") -> str:
    """OCR an image given as bytes. Tries each backend until one works."""
    fd, path = tempfile.mkstemp(prefix="upload-guard-", suffix=suffix or ".png")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        errors = []
        for backend in backends():
            try:
                return backend(path)
            except OCRUnavailable as exc:
                log.info("OCR backend %s unavailable: %s", backend.__name__, exc)
                errors.append(str(exc))
        raise OCRUnavailable("; ".join(errors))
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
