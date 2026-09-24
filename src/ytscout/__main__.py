"""Allow `python -m ytscout`."""

import sys

from ytscout.cli import main

if __name__ == "__main__":
    sys.exit(main())
