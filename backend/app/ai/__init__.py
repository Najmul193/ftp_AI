"""FTP Intelligence -- the optional AI module.

Loaded only when `FTP_AI_MODULE=1`. With the flag off nothing in this package
is imported, so the platform behaves exactly as it does without it: no routes,
no scheduler, no permissions, no tables. The core never imports this package
(an import-linter contract enforces it), which is what makes the module
removable rather than merely switched off.
"""
