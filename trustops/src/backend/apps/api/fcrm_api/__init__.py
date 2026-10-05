"""The composite ``api`` process (ADR-26): X1 and S1-S5 in one FastAPI app.

``fcrm_api.main:app`` is what the ``api`` container serves. Each service
stays a self-contained package with its own ``create_app`` and
``lifespan.register``; this package only mounts them, owns the shared
clients and starts them in order (see ``fcrm_api.registry`` and
``fcrm_api.lifespan``).
"""

__version__ = "0.1.0"
