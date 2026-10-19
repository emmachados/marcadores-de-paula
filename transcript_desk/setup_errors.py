"""Advice for recognised installer errors; the original log text is kept."""


def installation_advice(text, code=0):
    error = text.casefold()
    if "os error 448" in error:
        return "Windows rejected a runtime path as an untrusted mount point. Setup could not recover a usable Python interpreter. Copy the complete log, including the runtime folder, so the rejected path can be checked."
    if any(term in error for term in ("os error 206", "winerror 206", "filename or extension is too long", "path too long")):
        return "Windows reported a path-length limit. Copy the log to identify the path. A shorter local runtime folder may be needed; see the Windows troubleshooting guide."
    if any(term in error for term in ("os error 112", "winerror 112", "no space left", "not enough space on the disk", "disk full")):
        return "The disk is full. Free space on the runtime and temporary-file drives, then select Install engine again. Keep recording and review folders."
    if any(term in error for term in ("os error 32", "winerror 32", "used by another process", "sharing violation")):
        return "An installation file is in use. Close other copies of Marcadores de Paula and retry. If it remains locked, restart Windows and try again."
    if any(term in error for term in ("os error 5", "winerror 5", "access is denied", "permission denied")):
        return "Windows refused file access. Check the path shown in the log and Windows Security protection history. Restore access to the application folder before retrying."
    if any(term in error for term in ("certificate verify failed", "invalid peer certificate", "unknownissuer", "unknown issuer", "certificate_verify_failed")):
        return "A server certificate could not be verified. Check the computer's date and time. On a managed network, ask its administrator to check the trusted certificates or proxy. Keep certificate verification enabled."
    if any(term in error for term in ("timed out", "timeout", "dns error", "failed to lookup", "connection refused", "connection reset")):
        return "The download connection failed. Check the internet connection and any required network proxy, then retry. Completed downloads can be reused."
    if code & 0xFFFFFFFF in (0xC0000135, 0xC000007B) or any(term in error for term in ("dll load failed", "winerror 126", "winerror 193", "vcruntime", "msvcp140")):
        return "A required Windows library could not load. See the Windows troubleshooting guide for the Microsoft Visual C++ x64 runtime and copy this log to identify the missing library."
    return ""
