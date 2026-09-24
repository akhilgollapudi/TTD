#!/usr/bin/env python3
"""TTD booking assistant entry point. Supports both direct and package execution."""

if __package__:
    from .runtime import main
else:
    from runtime import main


if __name__ == "__main__":
    main()
