"""
The Individual queryset of a DCI Person search.

A search reads only the persons its caller may read (row security by location), and a filter on a field the
person mask masks is refused to a caller without the masked-data right: its results would tell the masked
values. The querysets are lazy, so building one checks the filters without reading the database.
"""
from django.contrib.auth.models import AnonymousUser


def visible_persons(user):
    """The non-deleted persons ``user`` may read; none without a user."""
    from individual.models import Individual

    if user is None:
        return Individual.objects.none()
    # Row security joins the person's memberships: a person of several households would come once per membership.
    persons = Individual.objects.filter(is_deleted=False)
    return persons.filter(id__in=Individual.get_queryset(None, user).values("id"))


def check_lookups(user, lookups):
    """Django's PermissionDenied when one of the Individual ``lookups`` reads a field masked to ``user``."""
    from individual.data_masking import check_filter_lookups
    from individual.models import Individual

    lookups = [lookup for lookup in lookups if lookup]
    if lookups:
        check_filter_lookups(user or AnonymousUser(), Individual, lookups)


def _apply(queryset, user, conditions):
    """``queryset`` filtered by each (lookup, value) of ``conditions``, once every lookup is checked."""
    check_lookups(user, [lookup for lookup, _ in conditions])
    for lookup, value in conditions:
        queryset = queryset.filter(**{lookup: value})
    return queryset


def _extract_filters_from_expression(expression_query):
    """Extract filters from expression query format"""
    filters = {}

    # Find $and or $or arrays if they exist
    filter_list = expression_query.get('$and', [])
    if not filter_list and isinstance(expression_query, dict) and '$and' not in expression_query:
        # Flat format
        filter_list = [{k: {"$eq": v}} for k, v in expression_query.items() if k != '@type']

    # Extract filters
    for item in filter_list:
        if isinstance(item, dict):
            for k, v in item.items():
                if isinstance(v, dict) and '$eq' in v:
                    filters[k] = v['$eq']
                else:
                    filters[k] = v

    return filters


def _field_conditions(filters):
    """The (lookup, value) conditions of the DCI field filters on Individual"""
    conditions = []
    if 'firstName' in filters:
        conditions.append(('first_name__icontains', filters['firstName']))

    if 'lastName' in filters:
        conditions.append(('last_name__icontains', filters['lastName']))

    if 'dob' in filters:
        conditions.append(('dob', filters['dob']))

    if 'gender' in filters:
        # Map DCI gender to OpenIMIS gender code
        gender_map = {
            'Male': 'M',
            'Female': 'F',
            'Other': 'O'
        }
        gender_code = gender_map.get(filters['gender'])
        if gender_code:
            conditions.append(('gender__code', gender_code))

    if 'phone' in filters:
        conditions.append(('phone__icontains', filters['phone']))

    if 'email' in filters:
        conditions.append(('email__icontains', filters['email']))

    return conditions


def _predicate_conditions(expression):
    """The (lookup, value) conditions of a single predicate expression"""
    attribute_name = expression.get('attribute_name', '')
    operator = expression.get('operator', 'eq')
    attribute_value = expression.get('attribute_value', '')

    # Map SPDCI operators to Django ORM
    # SPDCI operators: eq, gt, lt, ge, le, in
    if not attribute_name or not attribute_value:
        return []

    # Map attribute names to Individual model fields
    field_mapping = {
        'age': None,  # Requires calculation
        'first_name': 'first_name',
        'last_name': 'last_name',
        'firstName': 'first_name',
        'lastName': 'last_name',
        'dob': 'dob',
        'gender': 'gender__code',
        'phone': 'phone',
        'email': 'email',
    }

    field = field_mapping.get(attribute_name, attribute_name)
    if not field:
        return []

    # Apply operator
    if operator == 'eq':
        return [(field, attribute_value)]
    suffix = {'gt': 'gt', 'lt': 'lt', 'ge': 'gte', 'le': 'lte'}.get(operator)
    if suffix:
        return [(f'{field}__{suffix}', attribute_value)]
    if operator == 'in' and isinstance(attribute_value, list):
        return [(f'{field}__in', attribute_value)]
    return []


def _legacy_query(query_obj):
    """The filters of the legacy OpenIMIS format: query -> value -> expression -> query, or the flat query."""
    expression_q = query_obj.get('value', {}).get('expression', {}).get('query', {})
    if not expression_q:
        expression_q = query_obj
    return _extract_filters_from_expression(expression_q)


def sync_person_queryset(user, search_criteria):
    """The persons of a sync search, by the SPDCI query type of ``search_criteria``. Raises ValueError on an
    unsupported query type."""
    query_type = search_criteria.get('query_type', 'sync')
    query_obj = search_criteria.get('query', {})
    conditions = []

    if query_type == 'idtype-value':
        # SPDCI FR Standard: Search by identifier type and value
        # Format: {"type": "FARMER_ID", "value": "FARMER-TEST-001"}
        id_type = query_obj.get('type', '')
        id_value = query_obj.get('value', '')
        if id_type and id_value:
            # Every identifier type searches the person's UUID.
            conditions.append(('uuid__icontains', id_value))

    elif query_type == 'predicate':
        # SPDCI FR Standard: Search by predicate conditions
        # Format: [{"seq_num": 1, "expression1": {...}, "condition": "and", "expression2": {...}}]
        if isinstance(query_obj, list):
            for predicate in query_obj:
                expr1 = predicate.get('expression1', {})
                expr2 = predicate.get('expression2', {})
                conditions += _predicate_conditions(expr1)
                if expr2:
                    conditions += _predicate_conditions(expr2)

    elif query_type == 'expression':
        # SPDCI FR Standard: Expression query (implementation-specific)
        # Format: {"type": "...", "value": {"expression": {...}}}
        expression_query = query_obj.get('value', {}).get('expression', {})
        conditions = _field_conditions(_extract_filters_from_expression(expression_query))

    elif query_type == 'sync' or query_type == 'async':
        conditions = _field_conditions(_legacy_query(query_obj))

    else:
        raise ValueError(f"Unsupported query_type: {query_type}")

    return _apply(visible_persons(user), user, conditions)


def _async_conditions(search_criteria):
    """The conditions of an async search, whose query takes the legacy OpenIMIS format."""
    return _field_conditions(_legacy_query(search_criteria.get('query', {})))


def check_async_person_search(user, search_criteria):
    """Django's PermissionDenied when a filter of the async search reads a field masked to ``user``."""
    check_lookups(user, [lookup for lookup, _ in _async_conditions(search_criteria)])


def async_person_queryset(user, search_criteria):
    """The persons of an async search."""
    return _apply(visible_persons(user), user, _async_conditions(search_criteria))
