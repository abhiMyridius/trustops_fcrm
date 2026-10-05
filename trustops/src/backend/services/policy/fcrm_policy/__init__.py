"""S3 policy-service.

``fcrm_policy.search.rrf`` is pure: rank fusion over two or more ranked
lists, with no dependency on S3 Vectors, Mongo text search, or Bedrock
embeddings — those are the retrieval *sources*; this module only combines
whatever ranked ids they return. ``fcrm_policy.search.filters`` validates
metadata filter keys against the Index Contract, catching a typo'd filter
key before it silently returns zero results.
"""

from __future__ import annotations
