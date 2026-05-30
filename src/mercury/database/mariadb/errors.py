"""MariaDB connectivity errors."""


class MariaDbLiveError(Exception):
    """Live MariaDB operation failed."""


class MariaDbDriverMissingError(MariaDbLiveError):
    """pymysql is not installed."""
