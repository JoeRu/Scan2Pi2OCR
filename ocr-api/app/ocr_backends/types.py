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
    # LLM transcription (OCR_ENGINE=openrouter). lines still drive the PDF text layer.
    transcript: str | None = None
    transcript_model: str | None = None
    escalated: bool = False
    # What build_pdf puts in the invisible text layer: "tesseract" (lines only),
    # "block" (lines + transcript as an unpositioned block) or "positioned"
    # (lines are LLM lines with LLM boxes).
    pdf_text: str = "tesseract"

    @property
    def text(self) -> str:
        """Flat text for the .txt sidecar and AI metadata."""
        if self.transcript is not None:
            return self.transcript
        return "\n".join(line.text for line in self.lines)
