import json
import logging

import magic

from django.http import (
    HttpResponseBadRequest,
    HttpResponseForbidden,
    Http404,
    HttpResponse
)
from django.core.cache import cache
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET
from django.urls import reverse
from django.shortcuts import (
    get_object_or_404,
    redirect
)
from django.utils.translation import gettext as _
from django.core.files.temp import NamedTemporaryFile
from django.core.paginator import Paginator
from django.db.models.functions import Lower

from rest_framework.settings import api_settings
from rest_framework.views import APIView
from rest_framework import status
from rest_framework.response import Response
from rest_framework_json_api.views import ModelViewSet
from rest_framework.parsers import FileUploadParser

from papermerge.core.serializers import (
    NodeSerializer,
    DocumentSerializer,
    FolderSerializer
)
from papermerge.core.models import (
    BaseTreeNode,
    Access,
    Folder,
    Automate,
    Tag
)
from papermerge.core import signal_definitions as signals
from papermerge.core.backup_restore import build_tar_archive
from papermerge.core.storage import default_storage

from .decorators import json_response
from .utils import sanitize_kvstore_list
from .mixins import RequireAuthMixin

logger = logging.getLogger(__name__)


PER_PAGE = 30


class NodesViewSet(RequireAuthMixin, ModelViewSet):
    serializer_class = NodeSerializer
    queryset = BaseTreeNode.objects.all()

    def get_queryset(self, *args, **kwargs):
        return BaseTreeNode.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user_id=self.request.user.pk)


class DocumentUploadView(RequireAuthMixin, APIView):
    parser_classes = [FileUploadParser]

    def put(self, request, parent_id=None, filename='default.pdf'):

        payload = request.data['file']
        default_properties = {
            'user_id': self.request.user.pk,
            'lang': self.request.user.preferences['ocr__OCR_Language'],
            'title': filename,
            'parent_id': parent_id,
        }
        serializer = DocumentSerializer(data=default_properties)
        serializer.is_valid(raise_exception=True)
        doc = serializer.create(
            user_id=self.request.user.pk,
            validated_data=default_properties,
            payload=payload
        )

        headers = self.get_success_headers(serializer.data)

        return Response(
            DocumentSerializer(doc).data,
            status=status.HTTP_201_CREATED,
            headers=headers
        )

    def get_success_headers(self, data):
        try:
            return {'Location': str(data[api_settings.URL_FIELD_NAME])}
        except (TypeError, KeyError):
            return {}


def _filter_by_tag(nodes, request_get_dict):
    tag = request_get_dict.get('tag', None)

    if tag:
        nodes = BaseTreeNode.objects.filter(
            tags__name__in=[tag]
        ).exclude(
            title=Folder.INBOX_TITLE
        )

    return nodes


def _order_by(nodes, request_get_dict):

    order_by = request_get_dict.get('order-by', '-type')

    # order_by might be empty string
    if not order_by:
        # instead of empty string, use default
        # order by type descending
        order_by = '-type'

    # Order by these fields. Presence of dash (minus character)
    # in front of the field toggles ascending or descending order (django way).
    if order_by in ('date', '-date', 'title', '-title', 'type', '-type'):
        field_map = {
            'type': 'polymorphic_ctype_id',
            '-type': '-polymorphic_ctype_id',
            'date': 'created_at',
            '-date': '-created_at',
            'title': 'title',
            '-title': '-title'
        }
        field = field_map[order_by]
        # case consistent ordering of title field
        if field == 'title':
            nodes = nodes.order_by(Lower('title').asc())
        elif field == '-title':
            nodes = nodes.order_by(Lower('title').desc())
        else:
            nodes = nodes.order_by(field)

    return nodes


def _get_node_kv(node_id) -> list:
    kv_list = []

    if node_id:
        parent_node = get_object_or_404(
            BaseTreeNode, id=node_id
        )

        for item in parent_node.kv.all():
            kv_list.append(item.to_dict())

    return kv_list


def _get_page_number(request_get) -> int:
    page_number = request_get.get('page', 1)

    if not page_number:
        page_number = 1

    return int(page_number)


def _get_pagination_dict(paginator, page_number) -> dict:

    page_obj = paginator.get_page(page_number)

    num_pages = paginator.num_pages

    # 1.   Number of pages < 7: show all pages;
    # 2.   Current page <= 4: show first 7 pages;
    # 3.   Current page > 4 and < (number of pages - 4): show current page,
    #       3 before and 3 after;
    # 4.   Current page >= (number of pages - 4): show the last 7 pages.

    if num_pages <= 7 or page_number <= 4:  # case 1 and 2
        pages = [x for x in range(1, min(num_pages + 1, 7))]
    elif page_number > num_pages - 4:  # case 4
        pages = [x for x in range(num_pages - 6, num_pages + 1)]
    else:  # case 3
        pages = [x for x in range(page_number - 3, page_number + 4)]

    if page_obj.has_previous():
        previous_page_number = page_obj.previous_page_number()
    else:
        previous_page_number = -1

    if page_obj.has_next():
        next_page_number = page_obj.next_page_number()
    else:
        next_page_number = -1

    return {
        'page_number': page_number,
        'pages': pages,
        'num_pages': num_pages,
        'page': {
            'has_previous': page_obj.has_previous(),
            'has_next': page_obj.has_next(),
            'previous_page_number': previous_page_number,
            'next_page_number': next_page_number,
        }
    }


def _get_nodes_perms_key(user, parent_id=None) -> str:
    """
    Returns key (as string) used to cache a list of nodes permissions.

    Key is based on (user_id, parent_id) pair i.e we cache
    all nodes' permissions dictionary of folder.
    If parent_id == None - we will cache all root documents of given user.
    """
    uid = user.id
    pid = parent_id or ''
    nodes_perms_key = f"user_{uid}_parent_id_{pid}_readable_nodes"

    return nodes_perms_key


def _get_nodes_perms(user, parent_id, nodes) -> dict:
    """
    Returns permissions dictionary for given list of nodes

    Retrieving all permissions for given set of nodes is slow.
    To speed up this operation we store the result of already
    computed dictionaries - in cache.
    Cache dictionary is (user_id, parent_id) pair.
    User ID is required because parent_id can be None (for root folder).
    """
    nodes_perms_key = _get_nodes_perms_key(user, parent_id)
    nodes_perms = cache.get(nodes_perms_key, {})

    if not nodes_perms:  # It in cache?
        # No, it is not in cache.
        # Compute.
        nodes_perms = user.get_perms_dict(
            nodes, Access.ALL_PERMS
        )
        # Store in cache.
        cache.set(nodes_perms_key, nodes_perms)

    return nodes_perms


@json_response
@login_required
@require_GET
def folder_view(request, node_id=None):
    """
    GET folder/
    GET folder/<int:parent_id>

    Returns json string of nodes which current user can read:

        * filtered by tag (optionally)
        * paginated
        * ordered by title, date, type (optionally)
    """
    nodes = BaseTreeNode.objects.filter(parent_id=node_id).exclude(
        title=Folder.INBOX_TITLE
    )

    nodes = _filter_by_tag(nodes, request.GET)
    nodes = _order_by(nodes, request.GET)

    nodes_list = []
    nodes_perms_key = _get_nodes_perms_key(request.user, node_id)
    nodes_perms = _get_nodes_perms(request.user, node_id, nodes)

    readable_nodes = []

    for node in nodes:
        if node.id in nodes_perms:
            if nodes_perms[node.id].get(Access.PERM_READ, False):
                readable_nodes.append(node)
        else:
            # If we are here, it means that for given pair (user_id, parent_id)
            # new node was introduced (document uploaded or folder created).
            # Retrieve permissions for that node
            node_perms = request.user.get_perms_dict([node], Access.ALL_PERMS)
            if node_perms[node.id].get(Access.PERM_READ, False):
                readable_nodes.append(node)
                nodes_perms[node.id] = node_perms

            # Invalidate existing cache
            cache.set(nodes_perms_key, {})

    page_number = _get_page_number(request_get=request.GET)
    paginator = Paginator(readable_nodes, PER_PAGE)
    page_obj = paginator.get_page(page_number)

    for node in page_obj.object_list:
        node_dict_key = f"node_{node.id}_dict"
        node_dict = cache.get(node_dict_key)

        if node_dict:
            nodes_list.append(node_dict)
            continue

        # node_dict was not found cache
        node_dict = node.to_dict()
        # and send user_perms to the frontend client

        node_dict['user_perms'] = nodes_perms[node.id]

        nodes_list.append(node_dict)
        cache.set(node_dict_key, node_dict)

    return {
        'current_nodes': nodes_list,
        'parent_id': node_id,
        'parent_kv': _get_node_kv(node_id),
        'pagination': _get_pagination_dict(
            paginator=paginator,
            page_number=page_number
        )
    }


@json_response
@login_required
def breadcrumb_view(request, parent_id=None):

    nodes = []

    node = None
    try:
        node = BaseTreeNode.objects.get(id=parent_id)
    except BaseTreeNode.DoesNotExist:
        pass

    if node:
        nodes = [
            item.to_dict() for item in node.get_ancestors(include_self=True)
        ]

    return {
        'nodes': nodes,
    }


@json_response
@login_required
def node_by_title_view(request, title):
    """
    Useful in case of special folders like inbox (and trash in future).
    Returns node id, children_count, title of specified node's title.
    Title specified is insensitive (i.e. INBOX = Inbox = inbox).
    """
    node = get_object_or_404(
        BaseTreeNode,
        title__iexact=title
    )

    return {
        'id': node.id,
        'title': node.title,
        'children_count': node.get_children().count(),
        'url': reverse('node_by_title', args=('inbox',))
    }


@json_response
@login_required
def node_view(request, node_id):
    """
    GET or DELETE /node/<node_id>
    """
    try:
        node = BaseTreeNode.objects.get(id=node_id)
    except BaseTreeNode.DoesNotExist:
        ret = {
            'node': node.to_dict()
        }
        return ret, HttpResponseBadRequest.status_code

    if request.method == "DELETE":
        if request.user.has_perm(Access.PERM_DELETE, node):
            node.delete()
        else:
            msg = f"{request.user.username} does not have" +\
                f" permission to delete {node.title}"

            return msg, HttpResponseForbidden.status_code

        return 'OK'

    if request.method in ("PUT", "POST"):
        if not request.user.has_perm(Access.PERM_WRITE, node):
            return "Permission denied", HttpResponseForbidden.status_code

        node_data = json.loads(request.body)
        if 'kvstore' in node_data:
            metadata = node_data['kvstore']
            if isinstance(metadata, list):
                node.kv.update(
                    sanitize_kvstore_list(metadata)
                )

    if not request.user.has_perm(Access.PERM_READ, node):
        return "Permission denied", HttpResponseForbidden.status_code

    node_dict = node.to_dict()
    # minor hack to enable autocomplete for tag editor
    # in document view
    node_dict['alltags'] = [
        tag.to_dict()
        for tag in Tag.objects.filter(user=request.user)
    ]

    return {
        'node': node_dict
    }


@json_response
@login_required
def nodes_view(request):
    """
    GET or POST /nodes/
    """

    if request.method == "POST":

        data = json.loads(request.body)
        node_ids = [item['id'] for item in data]
        queryset = BaseTreeNode.objects.filter(id__in=node_ids)

        automates = Automate.objects.filter(
            dst_folder__in=queryset
        )
        if automates.count():
            msg = _(
                "Following Automates have references to folders "
                "you are trying to delete: "
            )
            msg += ", ".join([auto.name for auto in automates])
            msg += _(
                ". Please delete mentioned Automates first."
            )

            return msg, HttpResponseBadRequest.status_code

        nodes_perms = request.user.get_perms_dict(
            queryset, Access.ALL_PERMS
        )
        node_titles = []
        for node in queryset:
            node_titles.append(
                node.title
            )
            if not nodes_perms[node.id].get(Access.PERM_DELETE, False):
                # if user does not have delete permission on
                # one node - forbid entire operation!
                msg = f"{request.user.username} does not have" +\
                    f" permission to delete {node.title}"

                return msg, HttpResponseForbidden.status_code
        # yes, user is allowed to delete all nodes,
        # proceed with delete opration
        queryset.delete()
        signals.nodes_deleted.send(
            sender='core.views.nodes.nodes_view',
            user_id=request.user.id,
            level=logging.INFO,
            message=_("Nodes deleted"),
            node_titles=node_titles,
            node_ids=node_ids
        )

        return 'OK'

    return 'OK'


@login_required
def node_download(request, id, version=0):
    """
    Any user with read permission on the node must be
    able to download it.

    Node is either documennt or a folder.
    """
    try:
        node = BaseTreeNode.objects.get(id=id)
    except BaseTreeNode.DoesNotExist:
        raise Http404("Node does not exists")

    if request.user.has_perm(Access.PERM_READ, node):

        if node.is_document():
            file_abs_path = default_storage.abspath(
                node.path().url(version=version)
            )
            mime_type = magic.from_file(file_abs_path, mime=True)
            try:
                file_handle = open(file_abs_path, "rb")
            except OSError:
                logger.error(
                    "Cannot open local version of %s" % node.path.url()
                )
                return redirect('admin:browse')

            resp = HttpResponse(
                file_handle.read(),
                content_type=mime_type
            )
            disposition = "attachment; filename=%s" % node.title
            resp['Content-Disposition'] = disposition
            file_handle.close()

            return resp
        else:  # node is a folder

            with NamedTemporaryFile(prefix="download_") as fileobj:
                # collected into an archive all direct children of
                # selected folder
                node_ids = [_node.id for _node in node.get_children()]
                build_tar_archive(
                    fileobj=fileobj,
                    node_ids=node_ids
                )
                # reset fileobj to initial position
                fileobj.seek(0)
                data = fileobj.read()
                resp = HttpResponse(
                    data,
                    content_type="application/x-tar"
                )
                disposition = f"attachment; filename={node.title}.tar"
                resp['Content-Disposition'] = disposition

                return resp

    return HttpResponseForbidden()


@login_required
def nodes_download(request):
    """
    Download multiple nodes (documents and folders) packed
    as tar.gz archive.
    """

    node_ids = request.GET.getlist('node_ids[]')
    nodes = BaseTreeNode.objects.filter(
        id__in=node_ids
    )
    nodes_perms = request.user.get_perms_dict(
        nodes, Access.ALL_PERMS
    )
    for node in nodes:
        if not nodes_perms[node.id].get(
            Access.PERM_READ, False
        ):
            msg = _(
                "%s does not have permission to read %s"
            ) % (request.user.username, node.title)

            return msg, HttpResponseForbidden.status_code

    with NamedTemporaryFile(prefix="download_") as fileobj:
        build_tar_archive(
            fileobj=fileobj,
            node_ids=node_ids
        )
        # reset fileobj to initial position
        fileobj.seek(0)
        data = fileobj.read()
        resp = HttpResponse(
            data,
            content_type="application/x-tar"
        )
        disposition = "attachment; filename=download.tar"
        resp['Content-Disposition'] = disposition

        return resp
