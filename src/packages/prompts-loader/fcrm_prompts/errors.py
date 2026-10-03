"""The exceptions ``fcrm_prompts`` raises. One base class so a caller (H2 at
startup, a lock test, the CLI) can catch "anything the prompt layer
refused" without a blind ``except``.
"""

from __future__ import annotations


class PromptsError(Exception):
    """Base class for every error raised by this package."""


class FrontMatterError(PromptsError):
    """A file's YAML front-matter is missing, unterminated or not a mapping."""


class SpecValidationError(PromptsError):
    """A prompt, skill, agent-spec or manifest file failed its model."""


class PromptNotFoundError(PromptsError, LookupError):
    """No prompt with the requested id (and version) is loaded."""


class SkillNotFoundError(PromptsError, LookupError):
    """``skill(name)`` named a skill file that does not exist."""


class SchemaResolutionError(PromptsError):
    """``schema(path)`` could not resolve to an allowed Pydantic model."""


class SectionNotFoundError(PromptsError, LookupError):
    """``section(pack, key)`` named a section the context pack lacks."""


class ConfigKeyError(PromptsError, LookupError):
    """``config(key)`` matched no value, or matched one in both files."""


class TemplateRenderError(PromptsError):
    """Rendering failed: a Jinja syntax error, an undefined variable, a
    sandbox security violation, or a template-function error. The original
    exception is chained as ``__cause__``."""


class LockFormatError(PromptsError):
    """A lock file line is not ``name<TAB>version<TAB>sha256``."""
