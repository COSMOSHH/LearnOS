import os

import PyPDF2
from docx import Document


class DocumentLoader:
    def __init__(self, file_path: str):
        self.file_path = file_path
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"File does not exist: {self.file_path}")

    def load(self) -> str:
        ext = os.path.splitext(self.file_path)[1].lower()
        if ext in [".txt", ".md"]:
            return self._load_text()
        if ext == ".pdf":
            return self._load_pdf()
        if ext in [".doc", ".docx"]:
            return self._load_word()
        raise ValueError(f"Unsupported file type: {ext}")

    def _load_text(self) -> str:
        with open(self.file_path, "r", encoding="utf-8") as file:
            return file.read()

    def _load_pdf(self) -> str:
        text = ""
        with open(self.file_path, "rb") as file:
            reader = PyPDF2.PdfReader(file)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        return text

    def _load_word(self) -> str:
        document = Document(self.file_path)
        return "\n".join([paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()])


class DirectoryLoader:
    def __init__(self, directory_path: str):
        self.directory_path = directory_path
        if not os.path.exists(self.directory_path):
            raise FileNotFoundError(f"Directory does not exist: {self.directory_path}")

    def load(self) -> list[dict]:
        documents = []
        for root, _, files in os.walk(self.directory_path):
            for file_name in files:
                file_path = os.path.join(root, file_name)
                try:
                    loader = DocumentLoader(file_path)
                    content = loader.load()
                    if content.strip():
                        documents.append({"text": content, "metadata": {"source": file_path}})
                except ValueError:
                    continue
        return documents
