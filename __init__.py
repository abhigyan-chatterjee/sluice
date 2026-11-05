"""Sluice plugin entry point: registration only.

Hermes imports this file through the directory-plugin loader as
``hermes_plugins.<name>`` (``hermes_cli/plugins_loader.py:_load_directory_module``) and,
for ``hermes plugins validate``, from a bare interpreter with no third-party packages
(``hermes_cli/plugin_validate.py:_PROBE_SCRIPT``). So every import of Hermes or of a
runtime dependency happens inside :func:`register`, never at module scope. PLAN.md:53:
"``__init__.py`` registers the provider class and CLI commands only; imports lazy."

``plugins.isolation: host`` skips ``register_cli_command`` by design (it is a local-CLI
surface); the provider registration is object-based and travels across the process
boundary, so routing keeps working there.
"""

from __future__ import annotations

__version__ = "1.0.0"

__all__ = ["__version__", "register"]


def register(ctx) -> None:
    """Register the Sluice provider and the ``hermes sluice`` command tree.

    Imports are function-local on purpose: a module-scope ``from .sluice.provider import``
    would drag ``agent.*`` into the validation probe, which runs without Hermes on the
    import path.
    """
    from .sluice.cli import handle_command, setup_parser
    from .sluice.provider import SluiceWebSearchProvider

    register_provider = getattr(ctx, "register_web_search_provider", None)
    if register_provider is None:
        raise RuntimeError(
            "sluice requires Hermes >= 0.22.5: this build has no "
            "ctx.register_web_search_provider(), so the native web_search/web_extract "
            "tools cannot be served. Upgrade Hermes (see plugin.yaml requires_hermes)."
        )
    register_provider(SluiceWebSearchProvider(ctx))

    ctx.register_cli_command(
        name="sluice",
        help="Manage Sluice web search and extraction backends",
        setup_fn=setup_parser,
        handler_fn=handle_command,
        description=(
            "Inspect backends, probe health, test a query, switch the active web "
            "backend and clear caches. See `hermes sluice --help`."
        ),
    )
