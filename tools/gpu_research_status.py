"""Print the optional offline research accelerator status as JSON."""

from __future__ import annotations

import json

from services.research.ml.accelerator import inspect_accelerator


def main() -> None:
    print(json.dumps(inspect_accelerator("auto").as_dict(), sort_keys=True))


if __name__ == "__main__":
    main()
