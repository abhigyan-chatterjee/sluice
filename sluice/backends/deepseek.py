"""DeepSeek: Paid; queries clip at 128 Unicode characters on a word boundary, and the truncation is reported in metadata.

Contract: ``references/endpoints.md#deepseek`` (lines 161-175) and PLAN.md:88-93. Implemented
in T6. Billed at $0.75 per 1,000 successful search requests; 402 is insufficient balance, 403 a policy refusal.
"""

from __future__ import annotations

from . import Backend, BackendSpec

SPEC = BackendSpec(
    id="deepseek",
    label="DeepSeek",
    tiers=("paid",),
    search=True,
    extract=True,
    secret_env="DEEPSEEK_API_KEY",
    summary="Paid; queries clip at 128 Unicode characters on a word boundary, and the truncation is reported in metadata.",
    task="T6",
)


class DeepseekBackend(Backend):
    """Billed at $0.75 per 1,000 successful search requests; 402 is insufficient balance, 403 a policy refusal."""

    spec = SPEC
