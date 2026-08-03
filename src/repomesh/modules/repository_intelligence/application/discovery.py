"""LLM-powered repository discovery.

Replaces the original keyword-matching logic with a single LLM call: all
repository profiles (including their :class:`AutoCard` payloads) are assembled
into one prompt, DeepSeek returns a JSON array of candidates, and the results
are filtered + ranked.

Key design decisions (from the MVP plan):

* **temperature=0** — minimise non-determinism.
* **Hallucination filter** — any repo name returned by the LLM that is not in
  the catalog is silently dropped.
* **low_signal surfacing** — repositories flagged ``low_signal`` by the scanner
  are explicitly called out in the prompt so the model knows it may lack
  enough information; the candidate rationale will reflect that uncertainty.
* **JSON tolerance** — the raw response may be wrapped in markdown fences or
  contain extra prose; we extract the outermost ``[...]`` block before parsing,
  with one self-correcting retry.
* **Fallback** — if no LLM key is configured the service falls back to the
  original keyword-matching logic so tests and offline development still work.
"""

from __future__ import annotations

import json
import logging
import re
from collections