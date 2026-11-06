class AIError(RuntimeError):
    status_code = 502


class AIConfigurationError(AIError):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
