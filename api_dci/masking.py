"""individual's person mask on the Person records the DCI API gives and takes."""
import copy

from django.contrib.auth.models import AnonymousUser

PERSON_MASK = "Individual"


def mask_person(user, individual):
    """Masks ``individual`` in place by individual's person mask, the fields other modules register included,
    unless ``user`` holds the masked-data right. No user (a search run without one) gets the mask."""
    from individual.data_masking import mask_for_user

    return mask_for_user(user or AnonymousUser(), PERSON_MASK, individual)


def check_person_sent_back(user, individual, individual_data):
    """Raises django's ValidationError when ``individual_data`` gives a masked field of ``individual`` the
    string mask, for a user without the masked-data right."""
    from individual.data_masking import check_masked_values_sent_back

    check_masked_values_sent_back(user or AnonymousUser(), PERSON_MASK, individual, copy.deepcopy(individual_data))
