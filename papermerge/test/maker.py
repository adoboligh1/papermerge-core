from pathlib import Path
from papermerge.core.models import Document, User


RESOURCES = Path("")


def document(resource: str, user: User) -> Document:
    """Builds a document model with associated data

    ``resource`` is name of the file from ``tests/resources/`` folder.
    """
    doc = Document.objects.create_document(
        title=resource,
        lang="deu",
        user_id=user.pk,
        parent=user.home_folder
    )

    payload = open(RESOURCES / resource, 'rb')

    doc.upload(
        payload=payload,
        file_path=RESOURCES / resource,
        file_name=resource
    )
