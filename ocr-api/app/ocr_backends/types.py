from dataclasses import dataclass, field


@dataclass
class OcrLine:
    text: str
    x0: int
    y0: int
    x1: int
    y1: int  # axis-aligned bounding box, original-image pixel coordinates


@dataclass
class OcrPage:
    lines: list[OcrLine] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Flat text for the .txt sidecar and AI metadata."""
        return "\n".join(line.text for line in self.lines)
