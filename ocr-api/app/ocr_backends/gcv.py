from pathlib import Path


class GoogleCloudVisionBackend:
    def run(self, pages: list[Path], language: str) -> str:
        raise NotImplementedError("Google Cloud Vision backend not yet implemented")
