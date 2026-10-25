"""Service-layer exception types shared across services."""


class ObjectAlreadyExistsError(ValueError):
    """Raised when an object with the given identifier already exists.

    Subclasses ``ValueError`` so legacy callers that catch ``ValueError`` for
    duplicate-object detection continue to work.
    """


class PortConflictError(ValueError):
    """Raised when a runtime server cannot bind because the requested port
    (or the auto-allocated port) is already in use or otherwise unavailable.

    Subclasses ``ValueError`` so legacy callers that catch ``ValueError`` for
    creation failures continue to work.
    """


class ObjectInUseError(ValueError):
    """Raised when an object cannot be deleted because other objects depend on it.

    Subclasses ``ValueError`` so legacy callers that catch ``ValueError`` continue to work.
    """

    def __init__(self, object_type: str, object_uuid: str, dependents: list):
        self.object_type = object_type
        self.object_uuid = object_uuid
        self.dependents = dependents
        super().__init__(str(self))

    def __str__(self) -> str:
        dep_str = ", ".join(f"{t} {u}" for t, u in self.dependents)
        return (
            f"{self.object_type} {self.object_uuid} cannot be deleted because the "
            f"following objects depend on it: {dep_str}"
        )


class ToolProviderUnavailable(RuntimeError):
    """Raised when a provider-backed tool is executed but no active plugin
    provides its ``endpoint.provider``.

    Subclasses ``RuntimeError`` so generic execution error handling still
    catches it; the tools API maps it to 422 before that.
    """

    def __init__(self, provider: str, tool_name: str):
        self.provider = provider
        self.tool_name = tool_name
        super().__init__(str(self))

    def __str__(self) -> str:
        return (
            f"Tool '{self.tool_name}' needs provider '{self.provider}', which is not "
            "available in this store (no active plugin provides it)."
        )
