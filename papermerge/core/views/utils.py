import io
import os
from pikepdf import Pdf
from collections import abc, namedtuple

from django.utils.html import escape

from papermerge.core.lib.path import PagePath
from papermerge.core.storage import abs_path, get_storage_instance
from papermerge.core.models import DocumentVersion


def sanitize_kvstore(kvstore_dict):
    """
    Creates a sanitized dictionary.

    Sanitizied dictionary contains only allowed keys and escaped values.
    """
    allowed_keys = [
        'id',
        'key',
        'value',
        'kv_type',
        'kv_format',
        'kv_inherited',
    ]

    sanitized_kvstore_dict = {}

    for allowed_key in allowed_keys:
        if allowed_key in kvstore_dict.keys():
            value = kvstore_dict.get(allowed_key, None)
            if isinstance(value, bool):
                allowed_value = value
            else:
                allowed_value = escape(kvstore_dict.get(allowed_key, None))

            sanitized_kvstore_dict[allowed_key] = allowed_value

    return sanitized_kvstore_dict


def sanitize_kvstore_list(kvstore_list):
    """
    Creates a new list of sanitized dictionaries.

    Sanitizied dictionary contains only allowed keys and escaped values.
    """
    if not isinstance(kvstore_list, list):
        raise ValueError("Expects list type as input")

    new_kvstore_list = [
        sanitize_kvstore(item) for item in kvstore_list
    ]

    return new_kvstore_list


PageRecycleMapItem = namedtuple(
    'PageRecycleMapItem', ['new_number', 'old_number']
)


class PageRecycleMap:
    """
    Given total pages and a list of deleted pages - returns
    a list of assignations of pages:
        [new_version_page_num, old_version_page_num]
    Which reads 'new_version_page_num' gets info from
    `old_version_page_num`.

    Example 1:
        total_pages: 6
        deleted_pages: [1, 2]
        returns: [
            [(1, 3),  (2, 4), (3, 5), (4, 6)]
            # page #1 gets info from prev page #3
            # page #2 ... #4
            ...
            # page #4 ... #6
        ]

    Example 2:
        total pages: 5
        deleted_pages [1, 5]
        returns: [
            [(1, 2), (2, 3), (3, 4)
        ]

    Example 3:
        total pages: 5
        deleted_pages [2, 3]
        returns: [
            [(1, 1), (2, 4), (3, 5)
            # page #1 stays unaffected
            # page #2 gets the info from page number 4
            # page #3 gets info from page #5
        ]
    """

    def __init__(self, total: int, deleted: list[int]):
        if not isinstance(deleted, abc.Sequence):
            raise ValueError('`deleted` expected to be a sequence')

        if total < len(deleted):
            raise ValueError('`total` < `deleted`')

        self.total = total
        self.deleted = deleted

        _pages = [
            page for page in range(1, self.total + 1)
            if page not in self.deleted
        ]
        _page_numbers = range(1, len(_pages) + 1)
        self.page_map = zip(_page_numbers, _pages)

    def __iter__(self):
        return self

    def __next__(self):
        item = next(self.page_map)
        if item:
            return PageRecycleMapItem(*item)

        raise StopIteration

    def __repr__(self):
        return (
            f"PageRecycleMap("
            f"total={self.total!r}, deleted={self.deleted!r}"
            f")"
        )


def collect_text_streams(version, page_numbers):
    pages_map = {page.number: page for page in version.pages.all()}

    streams = []
    for number in page_numbers:
        stream = io.StringIO(pages_map[number].text)
        streams.append(stream)

    return streams


def copy_pages_data_multi(
    src_old_version,
    dst_old_version,
    dst_new_version,
    position,
    page_numbers
):
    storage = get_storage_instance()
    page_map = [(pos, pos) for pos in range(1, position + 1)]

    if len(page_map) > 0:
        for src_page_number, dst_page_number in page_map:
            src_page_path = PagePath(
                document_path=dst_old_version.document_path,
                page_num=src_page_number
            )
            dst_page_path = PagePath(
                document_path=dst_new_version.document_path,
                page_num=dst_page_number
            )
        storage.copy_page(src=src_page_path, dst=dst_page_path)

    page_map = zip(
        page_numbers,
        [pos for pos in range(position + 1, position + len(page_numbers) + 1)]
    )

    for src_page_number, dst_page_number in page_map:
        src_page_path = PagePath(
            document_path=src_old_version.document_path,
            page_num=src_page_number
        )
        dst_page_path = PagePath(
            document_path=dst_new_version.document_path,
            page_num=dst_page_number
        )
        storage.copy_page(src=src_page_path, dst=dst_page_path)

    if dst_old_version is not None:
        dst_old_total_pages = dst_old_version.pages.count()
        _range = range(
            position + 1,
            dst_old_total_pages + 1
        )
        page_map = [(pos, pos + len(page_numbers)) for pos in _range]

        for src_page_number, dst_page_number in page_map:
            src_page_path = PagePath(
                document_path=dst_old_version.document_path,
                page_num=src_page_number
            )
            dst_page_path = PagePath(
                document_path=dst_new_version.document_path,
                page_num=dst_page_number
            )
            storage.copy_page(src=src_page_path, dst=dst_page_path)


def reuse_ocr_data(old_version, new_version, page_map):
    storage_instance = get_storage_instance()

    for new_number, old_number in page_map:
        src_page_path = PagePath(
            document_path=old_version.document_path,
            page_num=old_number
        )
        dst_page_path = PagePath(
            document_path=new_version.document_path,
            page_num=new_number
        )
        storage_instance.copy_page(
            src=src_page_path,
            dst=dst_page_path
        )


def reuse_text_field(old_version, new_version, page_map):
    streams = collect_text_streams(
        version=old_version,
        # list of old_version page numbers
        page_numbers=[item[1] for item in page_map]
    )

    # updates page.text fields and document_version.text field
    new_version.update_text_field(streams)


def reuse_text_field_multi(
    src_old_version,
    dst_old_version,
    dst_new_version,
    position,
    page_numbers
):
    page_map = [(pos, pos) for pos in range(1, position + 1)]
    streams = []
    if len(page_map) > 0 and dst_old_version is not None:
        streams.extend(
            collect_text_streams(
                version=dst_old_version,
                page_numbers=[item[1] for item in page_map]
            )
        )

    page_map = zip(
        [pos for pos in range(position + 1, len(page_numbers) + 1)],
        page_numbers
    )
    streams.extend(
        collect_text_streams(
            version=src_old_version,
            page_numbers=[item[1] for item in page_map]
        )
    )

    dst_new_total_pages = dst_new_version.pages.count()
    _range = range(
            position + 1 + len(page_numbers),
            dst_new_total_pages + 1
        )
    page_map = [(pos, pos - position - len(page_numbers)) for pos in _range]
    if dst_old_version is not None:
        streams.extend(
           collect_text_streams(
                version=dst_old_version,
                page_numbers=[item[1] for item in page_map]
           )
        )

    dst_new_version.update_text_field(streams)


def remove_pdf_pages(
    old_version: DocumentVersion,
    new_version: DocumentVersion,
    page_numbers: list[int]
):
    """
    :param old_version: is instance of DocumentVersion
    :param new_version:  is instance of DocumentVersion
    :param page_numbers: numbers of pages to delete. Numbering starts with 1.

    Notice that page numbering starts with 1 i.e. page_numbers=[1, 2] -
    will remove first and second pages.
    """
    # delete page from document's new version associated file
    pdf = Pdf.open(
        abs_path(old_version.document_path.url)
    )
    _deleted_count = 0
    for page_number in page_numbers:
        pdf.pages.remove(p=page_number - _deleted_count)
        _deleted_count += 1

    dirname = os.path.dirname(
        abs_path(new_version.document_path.url)
    )
    os.makedirs(dirname, exist_ok=True)
    pdf.save(abs_path(new_version.document_path.url))


def insert_pdf_pages(
    src_old_version: DocumentVersion,
    dst_old_version: DocumentVersion,
    dst_new_version: DocumentVersion,
    src_page_numbers: list[int],
    dst_position: int = 0
) -> None:
    """Inserts pages from source to destination at given position

    In case both `dst_old_version` and `dst_new_version` parameters
    are non-empty `DocumentVersion` instances - `insert_pdf_pages` will take
    `src_page_numbers` from `src_old_version` and
    insert them at `dst_position` of `dst_old_version` and will
    save result in `dst_new_version`.

    In case `dst_old_version` is None - `insert_pdf_pages` will
    take `src_page_numbers` from `src_old_version` and insert
    at position 0 of the newly created pdf. Newly created pdf will be saved
    at `dst_new_version`.

    Remarks:
    `dst_position` starts with 0.
    In `src_page_numbers` page numbering starts with 1 i.e.
    when `src_page_numbers=[1, 2]` means insert first and second pages from
    source document version.
    """
    src_old_pdf = Pdf.open(
        abs_path(src_old_version.document_path.url)
    )
    if dst_old_version is None:
        # case of total merge
        dst_old_pdf = Pdf.new()
        dst_position = 0
    else:
        dst_old_pdf = Pdf.open(
            abs_path(dst_old_version.document_path.url)
        )

    _inserted_count = 0
    for page_number in src_page_numbers:
        pdf_page = src_old_pdf.pages.p(page_number)
        dst_old_pdf.pages.insert(dst_position + _inserted_count, pdf_page)
        _inserted_count += 1

    dirname = os.path.dirname(
        abs_path(dst_new_version.document_path.url)
    )
    os.makedirs(dirname, exist_ok=True)
    dst_old_pdf.save(
        abs_path(dst_new_version.document_path.url)
    )


def total_merge(
    src_old_version: DocumentVersion,
    dst_new_version: DocumentVersion
) -> None:
    """
    Merge source document version with destination

    'Total' means 'all pages'.
    """
    # all pages of the source
    page_numbers = [page.number for page in src_old_version.pages.all()]

    insert_pdf_pages(
        src_old_version=src_old_version,
        dst_old_version=None,
        dst_new_version=dst_new_version,
        src_page_numbers=page_numbers,
        dst_position=0
    )
    # Total merge deletes source document.
    # Because all pages of the source are moved to destination, source's
    # last version remains "without pages". A document version without pages
    # does not make sense to stay around - thus we delete it!
    src_old_version.document.delete()


def partial_merge(
    src_old_version: DocumentVersion,
    src_new_version: DocumentVersion,
    dst_new_version: DocumentVersion,
    page_numbers: list[int]
) -> None:
    """Merge only some pages of the source document version with destination

    No all pages of the source are used, which means
    source document version IS NOT DELETED.

    'Partial' means 'not all pages'.
    """

    if len(page_numbers) >= src_old_version.pages.count():
        raise ValueError("Number of pages to remove exceeds source page count")

    # remove pages from the source document version
    remove_pdf_pages(
        old_version=src_old_version,
        new_version=src_new_version,
        page_numbers=page_numbers
    )

    page_map = list(
        PageRecycleMap(
            total=src_old_version.page_count,
            deleted=page_numbers
        )
    )

    reuse_ocr_data(
        old_version=src_old_version,
        new_version=src_new_version,
        page_map=page_map
    )

    reuse_text_field(
        old_version=src_old_version,
        new_version=src_new_version,
        page_map=page_map
    )

    # insert pages to the destination
    insert_pdf_pages(
        src_old_version=src_old_version,
        dst_old_version=None,
        dst_new_version=dst_new_version,
        src_page_numbers=page_numbers
    )

    copy_pages_data_multi(
        src_old_version=src_old_version,
        dst_old_version=None,
        dst_new_version=dst_new_version,
        position=0,
        page_numbers=page_numbers
    )

    reuse_text_field_multi(
        src_old_version=src_old_version,
        dst_old_version=None,
        dst_new_version=dst_new_version,
        position=0,
        page_numbers=page_numbers
    )


def reorder_pdf_pages(
    old_version,
    new_version,
    pages_data,
    page_count
):
    src = Pdf.open(abs_path(old_version.document_path.url))

    dst = Pdf.new()
    reodered_list = sorted(pages_data, key=lambda item: item['new_number'])

    for list_item in reodered_list:
        page = src.pages.p(list_item['old_number'])
        dst.pages.append(page)

    dirname = os.path.dirname(
        abs_path(new_version.document_path.url)
    )
    os.makedirs(dirname, exist_ok=True)
    dst.save(abs_path(new_version.document_path.url))


def rotate_pdf_pages(
    old_version,
    new_version,
    pages_data
):
    """
    ``pages`` data is a list of dictionaries. Each dictionary is expected
    to have following keys:
        - number
        - angle
    """
    src = Pdf.open(abs_path(old_version.document_path.url))

    for page_data in pages_data:
        page = src.pages.p(page_data['number'])
        page.rotate(page_data['angle'], relative=True)

    dirname = os.path.dirname(
        abs_path(new_version.document_path.url)
    )
    os.makedirs(dirname, exist_ok=True)
    src.save(abs_path(new_version.document_path.url))
