"""One place that knows how this process reaches Gemini.

Local development uses an API key from `.env`. The hosted deployment has no key
at all: it runs as a Cloud Run service account and calls Vertex AI through
Application Default Credentials. ADK's own Gemini wrapper builds a google-genai
Client with no explicit credentials, which honours the same three variables
(GOOGLE_GENAI_USE_VERTEXAI, GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION), so the
agents and the direct callers here take the same route without coordination.
"""

from __future__ import annotations

from typing import Any

from app.config import settings


def make_client(**kwargs: Any) -> Any:
    """A google-genai Client for the configured route (Vertex AI or API key)."""
    from google import genai

    if settings.use_vertex:
        # Explicit project/location beat a stray GOOGLE_API_KEY in the
        # environment (google-genai's documented precedence), so a developer's
        # .env cannot silently flip a Vertex deployment onto the key.
        return genai.Client(
            vertexai=True,
            project=settings.gcp_project or None,
            location=settings.vertex_location,
            **kwargs,
        )
    return genai.Client(api_key=settings.google_api_key, **kwargs)


def route_label() -> str:
    """For logs and the bench report: never the credential itself."""
    if settings.use_vertex:
        return f"vertex:{settings.vertex_location}"
    return "api-key" if settings.google_api_key else "none"
