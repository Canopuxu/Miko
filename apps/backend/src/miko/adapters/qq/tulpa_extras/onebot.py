"""Transport errors expected by Tulpa's business modules."""
class OneBotError(ValueError):
    def __init__(self, message, code='qq_unavailable', *, retcode=None, http_status=None):
        super().__init__(message)
        self.code, self.retcode, self.http_status = code, retcode, http_status


class OneBotUncertain(OneBotError):
    pass
