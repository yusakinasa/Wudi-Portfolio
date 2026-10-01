from abc import ABC, abstractmethod
from pathlib import Path


class PaperAnalyzer(ABC):
    name: str

    @abstractmethod
    def analyze(self, parsed: dict, metadata: dict, task_dir: Path) -> dict:
        """One isolated analysis; optional parsed['_figure_images'] maps IDs to
        in-memory WebP bytes. Never publish these internal bytes or local paths.
        """

    def cache_identity(self) -> dict:
        return {"name": self.name}
