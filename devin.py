#!/usr/bin/env python3

import os
from typing import Optional

import requests
from dotenv import load_dotenv

# Load variables from .env if it exists
load_dotenv()

API_BASE = ""
API_KEY = os.environ.get("DEVIN_API_KEY")


def create_session(prompt: str, title: Optional[str] = None) -> dict:
    if not API_KEY:
        raise RuntimeError("DEVIN_API_KEY environment variable is not set.")

    payload = {
        "prompt": prompt,
    }

    if title is not None:
        payload["title"] = title

    response = requests.post(
        f"https://api.devin.ai/v1/sessions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )

    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    session = create_session(
        prompt="In the GitHub repository Pintrue/superset, find one function in the superset/ package that issues an outbound HTTP request (e.g. via the requests library) without a timeout, and add a sensible timeout argument. Open a pull request with only that minimal change and a short description.",
        title="Add HTTP timeout",
    )

    print("session_id:", session.get("session_id"))
    print("url:       ", session.get("url"))