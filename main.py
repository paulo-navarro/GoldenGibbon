"""
GoldenGibbon – Crypto trading framework.
"""

__version__ = "0.1.0"


def main() -> None:
    """Application entry point – configure logging and launch."""
    from core.config import get_settings
    from core.logging import setup_logging

    settings = get_settings()
    setup_logging(settings.logging)


if __name__ == "__main__":
    main()
