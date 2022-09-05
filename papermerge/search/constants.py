from django.conf import settings

DEFAULT_ALIAS = "default"

# Reserved field names
ID = getattr(settings, "PAPPERMERGE_SEARCH_ID_FIELD", "id")
DJANGO_CT = getattr(settings, "PAPPERMERGE_SEARCH_CT_FIELD", "django_ct")
DJANGO_ID = getattr(settings, "PAPPERMERGE_SEARCH_ID_FIELD", "django_id")
DOCUMENT_FIELD = getattr(settings, "PAPPERMERGE_SEARCH_DOCUMENT_FIELD", "text")
ALL_FIELD = "_all"

# Default operator. Valid options are AND/OR.
DEFAULT_OPERATOR = getattr(
    settings,
    "PAPPERMERGE_SEARCH_DEFAULT_OPERATOR",
    "AND"
)

# Default values on elasticsearch
FUZZINESS = getattr(settings, "PAPPERMERGE_SEARCH_FUZZINESS", "AUTO")
FUZZY_MIN_SIM = getattr(settings, "PAPPERMERGE_SEARCH_FUZZY_MIN_SIM", 0.5)
FUZZY_MAX_EXPANSIONS = getattr(
    settings,
    "PAPPERMERGE_SEARCH_FUZZY_MAX_EXPANSIONS",
    50
)

# Valid expression extensions.
VALID_FILTERS = {
    "contains",
    "exact",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "startswith",
    "range",
    "endswith",
    "content",
    "fuzzy",
}


FILTER_SEPARATOR = "__"

# The maximum number of items to display in a SearchQuerySet.__repr__
REPR_OUTPUT_SIZE = 20

# Number of SearchResults to load at a time.
ITERATOR_LOAD_PER_QUERY = getattr(
    settings,
    "PAPPERMERGE_SEARCH_ITERATOR_LOAD_PER_QUERY",
    10
)


# A marker class in the hierarchy to indicate that it handles search data.
class Indexable:
    papermerge_search_use_for_indexing = True
