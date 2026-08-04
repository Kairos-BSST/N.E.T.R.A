"""
main.py
-------
Convenience entry point for the Signal Intake API.

Prefer running uvicorn directly from the backend/ directory:

    python -m uvicorn api:app --reload --port 8000

This module is kept for scripts / process managers that expect `python main.py`.
"""

import uvicorn


def main():
    uvicorn.run("api:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    main()
