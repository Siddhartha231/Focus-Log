# lets you run "python -m focuslog ..." without installing the command
import sys

from focuslog.cli import main

if __name__ == "__main__":
    sys.exit(main())
