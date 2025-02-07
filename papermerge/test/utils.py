import re

from pdfminer.high_level import extract_text
from papermerge.core.models import DocumentVersion
from papermerge.core.storage import abs_path


def pdf_content(
        document_version: DocumentVersion,
        clean: bool = False
) -> str:
    """Returns text content of file associated with given document version

    :param clean: if True - replace non-alpha numeric characters with space

    :return: content (as string) of pdf file associated with document version
    """
    file_path = abs_path(document_version.document_path.url)
    text = extract_text(file_path)
    stripped_text = text.strip()

    if clean:
        # replace old non-alpha numeric characters with space
        cleaned_text = re.sub('[^0-9a-zA-Z]+', ' ', stripped_text)
        return cleaned_text

    return stripped_text
